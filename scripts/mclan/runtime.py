"""Release inmutable, Compose y verificación que no confunde un proceso con un servidor."""
import importlib.util
import json
import os
import re
import secrets
import time
import zipfile

from .common import Error, LOG, atomic_write, create_only, read_json, run, sha256, write_json
from .classfile import integer_constant


class Runtime:
    def __init__(self, root):
        self.root = root
        self.state = root / 'state'
        self.active_path = self.state / 'active.json'
        self.data = root / 'data'

    def active(self):
        if not self.active_path.exists():
            raise Error('No hay instalación activa. Ejecuta install.sh primero.')
        return read_json(self.active_path)

    def env_text(self, active):
        return (f"LAN_IP={active['lan_ip']}\nRELEASE_ID={active['id']}\n"
                f"RUNTIME_IMAGE={active['runtime_image']}\n")

    def compose(self, *arguments, capture=False, check=True, timeout=1200, pending=False):
        extra = ['-f', self.state / 'transaction.override.yaml'] if pending else []
        return run(['docker', 'compose', '--project-name', 'minecraft-lan',
                    '--project-directory', self.root, '--env-file', self.root / '.env',
                    '-f', self.root / 'compose.yaml', *extra, *arguments], capture=capture,
                   check=check, cwd=self.root, timeout=timeout)

    def validate(self):
        active = self.active()
        expected_sources = active.get('runtime_sources', {})
        if set(expected_sources) != {'Dockerfile', 'entrypoint.sh', 'probe.py'} or any(
                sha256(self.root / 'docker' / name) != digest for name, digest in expected_sources.items()):
            raise Error('Los archivos Docker difieren de la imagen activa. Recupera el proyecto de la misma versión.')
        for path in self.data.rglob('*'):
            if path.is_symlink():
                raise Error('No se admiten enlaces simbólicos dentro de data/.')
        properties = dict(line.split('=', 1) for line in (self.data / 'server.properties').read_text().splitlines()
                          if '=' in line and not line.startswith('#'))
        for key, value in {'server-ip': '', 'server-port': '25565', 'online-mode': 'true',
                           'enable-rcon': 'true', 'rcon.port': '25575', 'enable-status': 'true',
                           'enforce-secure-profile': 'false'}.items():
            if properties.get(key) != value:
                raise Error(f'server.properties necesita {key}={value}. Se conserva el archivo para tu revisión.')
        if (self.root / 'compose.yaml').read_text() != (self.root / 'docker/compose.template.yaml').read_text():
            raise Error('compose.yaml difiere de la plantilla gestionada; no se sobrescribirá. '
                        'Revisa los cambios y conserva ambos archivos iguales.')
        if (self.root / '.env').read_text() != self.env_text(active):
            raise Error('.env no coincide con active.json; revisa si una operación se interrumpió.')
        self.compose('config', '--quiet')
        release = self.root / 'releases' / active['id']
        for key, name in [('paper', 'paper.jar'), ('geyser', 'Geyser-Spigot.jar'), ('floodgate', 'floodgate-spigot.jar')]:
            if sha256(release / name) != active[key]['sha256']:
                raise Error(f'Integridad de release incorrecta: {name}')
        return active

    def stage(self, lock, client, ip):
        release = self.root / 'releases' / lock['id']
        release.mkdir(parents=True, exist_ok=True)
        for key, name in [('paper', 'paper.jar'), ('geyser', 'Geyser-Spigot.jar'), ('floodgate', 'floodgate-spigot.jar')]:
            client.download(lock[key], release / name)
            with zipfile.ZipFile(release / name) as jar:
                if key != 'paper' and 'plugin.yml' not in jar.namelist():
                    raise Error(f'{name} no es el plugin Spigot esperado.')
            (release / name).chmod(0o444)
        lock = dict(lock)
        with zipfile.ZipFile(release / 'Geyser-Spigot.jar') as jar:
            config_version = integer_constant(jar.read('org/geysermc/geyser/Constants.class'), 'CONFIG_VERSION')
        if config_version != 10:
            raise Error(f'Geyser usa el esquema {config_version}; este proyecto contempla el 10. '
                        'Revisa la configuración oficial antes de ampliar el soporte.')
        lock['geyser_config_version'] = config_version
        # Una misma resolución reutiliza su imagen/digest, sin mover etiquetas al reiniciar.
        staged = release / 'versions.lock.json'
        if staged.exists():
            existing = read_json(staged)
            lock['java_base'] = existing['java_base']
        else:
            tag = f"eclipse-temurin:{lock['java']}-jre-jammy"
            run(['docker', 'pull', '--platform', 'linux/arm64', tag])
            digests = json.loads(run(['docker', 'image', 'inspect', tag, '--format', '{{json .RepoDigests}}'], capture=True).stdout)
            digest = next((d for d in digests if re.fullmatch(r'(?:docker.io/library/)?eclipse-temurin@sha256:[a-f0-9]{64}', d)), None)
            if not digest:
                raise Error('No se pudo fijar el digest de Temurin.')
            lock['java_base'] = digest
        # La imagen se identifica también por el código de arranque/salud y la base Java.
        import hashlib
        material = lock['java_base'] + ''.join(sha256(self.root / 'docker' / n)
                                               for n in ('Dockerfile', 'entrypoint.sh', 'probe.py'))
        image = 'mclan-runtime:' + hashlib.sha256(material.encode()).hexdigest()[:20]
        lock.update(runtime_image=image, lan_ip=ip)
        lock['runtime_sources'] = {name: sha256(self.root / 'docker' / name)
                                   for name in ('Dockerfile', 'entrypoint.sh', 'probe.py')}
        if run(['docker', 'image', 'inspect', image], capture=True, check=False).returncode:
            run(['docker', 'build', '--platform', 'linux/arm64', '--build-arg',
                 f"JAVA_BASE={lock['java_base']}", '-t', image, self.root / 'docker'])
        write_json(staged, lock)
        release.chmod(0o755)
        return lock

    def activate(self, active):
        # La marca se escribe ANTES desde update/restore para detectar interrupciones.
        atomic_write(self.root / '.env', self.env_text(active))
        write_json(self.active_path, active)

    def container(self):
        result = self.compose('ps', '--all', '--quiet', 'minecraft', capture=True)
        identifiers = result.stdout.split()
        if len(identifiers) > 1:
            raise Error('Más de un contenedor para el servicio; revisar manualmente.')
        if not identifiers:
            return None
        return json.loads(run(['docker', 'inspect', identifiers[0]], capture=True).stdout)[0]

    def running(self):
        container = self.container()
        return bool(container and container['State']['Running'])

    def stop(self):
        container = self.container()
        if not container or not container['State']['Running']:
            return
        self.compose('stop', '--timeout', '180', 'minecraft', timeout=240)
        self.assert_clean_stop()

    def assert_clean_stop(self):
        info = self.container()
        if not info:
            if any(self.data.rglob('level.dat')):
                raise Error('Hay un mundo pero falta el contenedor: no se puede probar su cierre limpio.')
            return
        if (info['State']['Running'] or info['State'].get('OOMKilled')
                or info['State']['ExitCode'] not in (0, 143)):
            raise Error('Backup requiere un cierre limpio, sin OOM ni código de salida distinto de cero.')
        # La JVM puede devolver 143 tras ejecutar correctamente sus shutdown hooks.
        # Se exige además evidencia del guardado en ESTE arranque, nunca logs anteriores.
        result = run(['docker', 'logs', '--since', info['State']['StartedAt'], info['Id']],
                     capture=True)
        if 'All dimensions are saved' not in result.stdout + result.stderr:
            raise Error('No se ha confirmado el guardado de todas las dimensiones. '
                        'Se conserva data/; no se creará un backup consistente.')

    def start(self, timeout=900, *, pending=False):
        if (self.state / 'operation.json').exists() and not pending:
            raise Error('Hay una operación interrumpida. Usa restore con un backup completo; consulta recuperación.')
        self.validate()
        if pending:
            # Durante el primer arranque de una transacción, un reinicio del host
            # no debe reabrir automáticamente un mundo aún no verificado.
            write_json(self.state / 'transaction.override.yaml', {'services': {'minecraft': {'restart': 'no'}}})
        self.compose('up', '-d', '--no-build', 'minecraft', pending=pending)
        self.verify(timeout)

    def finish_transaction(self):
        (self.state / 'operation.json').unlink(missing_ok=True)
        info = self.container()
        if info and info['State']['Running']:
            run(['docker', 'update', '--restart', 'unless-stopped', info['Id']])

    def exec_probe(self, *args, check=True):
        return self.compose('exec', '-T', 'minecraft', 'python3', '/opt/mclan/probe.py',
                            *args, capture=True, check=check, timeout=40)

    def verify(self, timeout=900):
        active = self.active()
        deadline = time.monotonic() + timeout
        LOG.info('Esperando salud Java TCP y Bedrock UDP (máximo %s s)...', timeout)
        while time.monotonic() < deadline:
            info = self.container()
            if info:
                status = info['State']
                if status.get('OOMKilled') or status['Status'] in ('dead', 'exited') or info.get('RestartCount', 0) > 2:
                    raise Error('El contenedor no permanece estable. Revisa manage.sh logs.')
                if status.get('Health', {}).get('Status') == 'healthy':
                    break
            time.sleep(5)
        else:
            raise Error('Tiempo agotado esperando salud. No se ha verificado el servidor.')
        result = json.loads(self.exec_probe('health').stdout)
        self.verify_dump(active, result['java']['version']['protocol'])
        # La segunda sonda recorre los puertos PUBLICADOS del host, no sólo loopback interno.
        spec = importlib.util.spec_from_file_location('mclan_probe', self.root / 'docker/probe.py')
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        java = probe.java_status(active['lan_ip'])
        bedrock = probe.bedrock_status(active['lan_ip'])
        if java['version']['protocol'] != result['java']['version']['protocol']:
            raise Error('El puerto Java publicado no responde con el protocolo del contenedor.')
        if bedrock['protocol'] != result['bedrock']['protocol']:
            raise Error('El puerto Bedrock publicado no responde con el protocolo del contenedor.')
        if not re.search(rf"(?<![0-9.]){re.escape(active['minecraft'])}(?![0-9.])", java['version']['name']):
            raise Error('Java Status no anuncia la versión Minecraft fijada.')
        LOG.info('Verificación local correcta: Java %s; Bedrock %s.', java['version']['name'], bedrock['version'])
        print(f"Java: {active['lan_ip']}:25565 (Minecraft {active['minecraft']})")
        print(f"Bedrock: dirección {active['lan_ip']}, puerto 19132; compatibles: {active['compatibility']['bedrock']}")
        print('Falta la prueba de entrada y juego desde otro dispositivo de tu LAN. No se ha simulado un jugador.')

    def verify_dump(self, active, protocol):
        # OFFLINE es obligatorio: nunca subimos diagnósticos a dump.geysermc.org.
        dump = self.data / 'plugins/Geyser-Spigot/dump.json'
        previous = dump.read_bytes() if dump.exists() else None
        try:
            dump.unlink(missing_ok=True)
            self.exec_probe('rcon', 'geyser', 'dump', 'offline')
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if dump.exists():
                    try:
                        diagnostic = read_json(dump)
                        break
                    except json.JSONDecodeError:
                        pass
                time.sleep(1)
            else:
                raise Error('Geyser no creó un diagnóstico offline interpretable.')
            validate_diagnostic(diagnostic, active, protocol)
        finally:
            dump.unlink(missing_ok=True)
            if previous is not None:
                dump.write_bytes(previous)
                os.chown(dump, 10000, 10000)
                dump.chmod(0o600)


def validate_diagnostic(diagnostic, active, protocol):
    """Se comprueba la build realmente cargada, plugins activos y protocolo Java real."""
    plugins = diagnostic['bootstrapInfo']['plugins']
    enabled = {p['name'].lower() for p in plugins if p.get('enabled') is True}
    if not {'geyser-spigot', 'floodgate'} <= enabled:
        raise Error('Geyser y Floodgate no aparecen ambos habilitados en Paper.')
    info = diagnostic['versionInfo']['mcInfo']
    if diagnostic['bootstrapInfo'].get('onlineMode') is not True:
        raise Error('El servidor Java cargado no tiene online-mode habilitado.')
    if int(info['javaProtocol']) != int(protocol) or active['minecraft'] not in info['javaVersions']:
        raise Error('La build cargada de Geyser no es compatible con el protocolo Java de Paper.')
    build = str(diagnostic['gitInfo']['buildNumber'])
    if build != str(active['geyser']['build']):
        raise Error('La build de Geyser cargada no coincide con el lock.')
    config = diagnostic['config']
    auth = config.get('java', {}).get('auth-type', config.get('java', {}).get('authType', ''))
    if str(auth).lower() != 'floodgate':
        raise Error('La configuración efectiva de Geyser no usa Floodgate.')
    if config.get('advanced', {}).get('bedrock', {}).get('validate-bedrock-login') is not True:
        raise Error('La instancia de Geyser no confirma la validación de identidad Bedrock.')


def initialize_data(runtime, accepted, config_version=10):
    data = runtime.data
    data.mkdir(mode=0o700, exist_ok=True)
    password = secrets.token_hex(24)
    create_only(data / 'server.properties',
                '# Perfil conservador para Raspberry Pi 5 de 4 GB.\n'
                'server-ip=\nserver-port=25565\nonline-mode=true\nenforce-secure-profile=false\n'
                'level-name=world\nview-distance=6\nsimulation-distance=4\nmax-players=6\n'
                'motd=Minecraft LAN - Raspberry Pi\nspawn-protection=16\ndifficulty=normal\n'
                'enable-status=true\nenable-query=false\nenable-rcon=true\nrcon.port=25575\n'
                f'rcon.password={password}\nbroadcast-rcon-to-ops=false\n')
    if accepted:
        create_only(data / 'eula.txt', '# EULA aceptada explícitamente por el operador.\neula=true\n')
    if not (data / 'eula.txt').exists() or 'eula=true' not in (data / 'eula.txt').read_text().splitlines():
        raise Error('EULA pendiente: https://www.minecraft.net/eula ; usa --accept-eula si la aceptas.')
    # YAML mínimo con el esquema moderno. Geyser completa sus valores por defecto.
    create_only(data / 'plugins/Geyser-Spigot/config.yml',
                '# El instalador sólo crea este archivo si no existe.\n'
                'bedrock:\n  address: 0.0.0.0\n  port: 19132\n  transport: raknet\n  clone-remote-port: false\n'
                'java:\n  auth-type: floodgate\n'
                'advanced:\n  bedrock:\n    validate-bedrock-login: true\n'
                'log-player-ip-addresses: false\ndebug-mode: false\n'
                f'config-version: {config_version}\n')
    # No hay archivos de jugadores previos en una instalación inicial gestionada.
    for path in [data, *data.rglob('*')]:
        if path.is_symlink():
            raise Error('No se admiten enlaces simbólicos en los datos gestionados.')
        os.chown(path, 10000, 10000)
        path.chmod(0o700 if path.is_dir() else 0o600)
