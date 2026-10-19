"""Release inmutable, Compose y verificación que no confunde un proceso con un servidor."""
import importlib.util
import json
import os
import re
import secrets
import time
import zipfile

from . import addons
from .common import Error, LOG, atomic_write, create_only, read_json, run, sha256, write_json
from .classfile import integer_constant
from .versions import managed_jars

RUNTIME_FILES = ('Dockerfile', 'entrypoint.sh', 'probe.py', 'bluemap-setup.sh')
# Plugins gestionados que el operador puede desactivar para ahorrar recursos.
# Plugins que el operador puede desactivar (manage.sh plugin NOMBRE off).
OPTIONAL_PLUGINS = ('bluemap', 'mclan_skins')
# Plantillas Compose publicadas antes de la actual. Sólo un compose.yaml idéntico a
# una de ellas se considera gestionado y sustituible por manage.sh update.
PREVIOUS_TEMPLATES = {
    'f6e743cab95f8778c5e6e6c295b7c0a1d7e167d9f9db311efd9475ff90e02d60': '0.1.0',
    '30a994d1520b335cfa6d3d0a9542a75a7be6ed20ffe4348339558bc8e0046cab': '0.2.0',
    '08fb44ebac7c3f2e1706354139e54838adf599132c120ef83db157ebd24a85bd': '0.3.0',
}
NETHERNET_WEBRTC_PORT = 19133
BLUEMAP_PORT = 8100
# Paper con Moonrise (26.x) ya no escribe el mensaje vanilla de guardado final.
VANILLA_SAVED = 'All dimensions are saved'
MOONRISE_SAVED = ('Saving worlds', 'Halted I/O scheduler for world', 'All RegionFile I/O tasks to complete')


def pin_temurin(variant):
    """Descarga eclipse-temurin:<variant> para ARM64 y devuelve su digest inmutable."""
    tag = f"eclipse-temurin:{variant}"
    run(['docker', 'pull', '--platform', 'linux/arm64', tag])
    digests = json.loads(run(['docker', 'image', 'inspect', tag, '--format', '{{json .RepoDigests}}'], capture=True).stdout)
    digest = next((d for d in digests if re.fullmatch(r'(?:docker.io/library/)?eclipse-temurin@sha256:[a-f0-9]{64}', d)), None)
    if not digest:
        raise Error('No se pudo fijar el digest de Temurin.')
    return digest


def shutdown_saved(log):
    """Evidencia de guardado: mensaje vanilla o la secuencia completa de Moonrise en orden."""
    if VANILLA_SAVED in log:
        return True
    position = 0
    for marker in MOONRISE_SAVED:
        position = log.find(marker, position)
        if position < 0:
            return False
        position += len(marker)
    return True


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
        # DISABLED_PLUGINS sólo aparece si hay alguno: un .env anterior sigue siendo válido.
        disabled = ' '.join(active.get('disabled_plugins', []))
        return (f"LAN_IP={active['lan_ip']}\nRELEASE_ID={active['id']}\n"
                f"RUNTIME_IMAGE={active['runtime_image']}\n" + (f"DISABLED_PLUGINS={disabled}\n" if disabled else ''))

    def compose(self, *arguments, capture=False, check=True, timeout=1200, pending=False):
        extra = ['-f', self.state / 'transaction.override.yaml'] if pending else []
        return run(['docker', 'compose', '--project-name', 'minecraft-lan',
                    '--project-directory', self.root, '--env-file', self.root / '.env',
                    '-f', self.root / 'compose.yaml', *extra, *arguments], capture=capture,
                   check=check, cwd=self.root, timeout=timeout)

    def runtime_drift(self, active):
        """True si el código Docker del proyecto ya no es el de la imagen activa."""
        expected = active.get('runtime_sources', {})
        return set(expected) != set(RUNTIME_FILES) or any(
            sha256(self.root / 'docker' / name) != digest for name, digest in expected.items())

    def template_drift(self, path=None):
        """False si coincide con la plantilla actual; True si es una plantilla gestionada anterior."""
        path = path or self.root / 'compose.yaml'
        if path.read_text() == (self.root / 'docker/compose.template.yaml').read_text():
            return False
        if sha256(path) in PREVIOUS_TEMPLATES:
            return True
        raise Error(f'{path.name} difiere de la plantilla gestionada; no se sobrescribirá. '
                    'Revisa los cambios y conserva ambos archivos iguales.')

    def validate(self, *, allow_drift=False):
        """allow_drift sólo lo usa update para detectar y aplicar una nueva versión del proyecto."""
        active = self.active()
        drift = self.template_drift() or self.runtime_drift(active)
        if drift and not allow_drift:
            raise Error('El proyecto se actualizó y los archivos Docker o Compose difieren de la instalación activa. '
                        'Ejecuta sudo ./manage.sh update para reconstruir y verificar con backup previo.')
        from .backups import tree
        for path in tree(self.data, self.root):
            if path.is_symlink():
                raise Error('No se admiten enlaces simbólicos dentro de data/.')
        properties = dict(line.split('=', 1) for line in (self.data / 'server.properties').read_text().splitlines()
                          if '=' in line and not line.startswith('#'))
        for key, value in {'server-ip': '', 'server-port': '25565', 'online-mode': 'true',
                           'enable-rcon': 'true', 'rcon.port': '25575', 'enable-status': 'true',
                           'enforce-secure-profile': 'false'}.items():
            if properties.get(key) != value:
                raise Error(f'server.properties necesita {key}={value}. Se conserva el archivo para tu revisión.')
        if (self.root / '.env').read_text() != self.env_text(active):
            raise Error('.env no coincide con active.json; revisa si una operación se interrumpió.')
        self.compose('config', '--quiet')
        release = self.root / 'releases' / active['id']
        for key, name in managed_jars(active):
            if sha256(release / name) != active[key]['sha256']:
                raise Error(f'Integridad de release incorrecta: {name}')
        return active

    def stage(self, lock, client, ip):
        release = self.root / 'releases' / lock['id']
        release.mkdir(parents=True, exist_ok=True)
        for key, name in managed_jars(lock):
            if key in addons.ADDONS:
                continue  # se compilan más abajo, con el JDK fijado
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
        existing = read_json(staged) if staged.exists() else {}
        lock['java_base'] = existing.get('java_base') or pin_temurin(f"{lock['java']}-jre-jammy")
        if any(key in lock for key in addons.ADDONS):
            # El JDK sólo compila los addons; la imagen del servidor sigue usando el JRE.
            lock['jdk_base'] = existing.get('jdk_base') or pin_temurin(f"{lock['java']}-jdk-jammy")
            self.build_addons(lock, release, existing)
        # La imagen se identifica también por el código de arranque/salud y la base Java.
        import hashlib
        material = lock['java_base'] + ''.join(sha256(self.root / 'docker' / n) for n in RUNTIME_FILES)
        image = 'mclan-runtime:' + hashlib.sha256(material.encode()).hexdigest()[:20]
        lock.update(runtime_image=image, lan_ip=ip)
        lock['runtime_sources'] = {name: sha256(self.root / 'docker' / name) for name in RUNTIME_FILES}
        if run(['docker', 'image', 'inspect', image], capture=True, check=False).returncode:
            run(['docker', 'build', '--platform', 'linux/arm64', '--build-arg',
                 f"JAVA_BASE={lock['java_base']}", '-t', image, self.root / 'docker'])
        write_json(staged, lock)
        release.chmod(0o755)
        return lock

    def build_addons(self, lock, release, existing):
        """Compila los addons de la release (o reutiliza los ya compilados con el mismo código)."""
        for key in [key for key in addons.ADDONS if key in lock]:
            lock[key] = dict(lock[key])
            jar = release / addons.ADDONS[key][1]
            if existing.get(key, {}).get('source_sha256') == lock[key]['source_sha256'] and jar.exists() \
                    and sha256(jar) == existing[key].get('sha256'):
                lock[key]['sha256'] = existing[key]['sha256']
                continue
            try:
                lock[key]['sha256'] = sha256(addons.build(self.root, release, lock, key))
            except Error as exc:
                # Un addon desactivado no bloquea la actualización: la release queda sin él.
                if key not in lock.get('disabled_plugins', []):
                    raise Error(f'{exc} Para actualizar sin él: manage.sh plugin {key} off, y repite.') from exc
                LOG.warning('%s no compila con esta release y está desactivado: se omite (%s).', key, exc)
                del lock[key]

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
        if not shutdown_saved(self.logs_since_start(info)):
            raise Error('No se ha confirmado el guardado de todas las dimensiones. '
                        'Se conserva data/; no se creará un backup consistente.')

    def logs_since_start(self, info):
        result = run(['docker', 'logs', '--since', info['State']['StartedAt'], info['Id']], capture=True)
        return result.stdout + result.stderr

    def start(self, timeout=900, *, pending=False, allow_drift=False):
        if (self.state / 'operation.json').exists() and not pending:
            raise Error('Hay una operación interrumpida. Usa restore con un backup completo; consulta recuperación.')
        self.validate(allow_drift=allow_drift)
        if pending:
            # Durante el primer arranque de una transacción, un reinicio del host
            # no debe reabrir automáticamente un mundo aún no verificado.
            write_json(self.state / 'transaction.override.yaml', {'services': {'minecraft': {'restart': 'no'}}})
        self.compose('up', '-d', '--no-build', 'minecraft', pending=pending)
        self.verify(timeout)

    def set_plugin(self, name, enabled):
        """Activa o desactiva un plugin opcional; el cambio se aplica en el siguiente arranque."""
        if name not in OPTIONAL_PLUGINS:
            raise Error(f'Plugin no gestionable: {name}')
        active = self.active()
        if name not in active:
            raise Error(f'La release activa no incluye {name}.')
        disabled = set(active.get('disabled_plugins', []))
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        active['disabled_plugins'] = sorted(disabled)
        self.activate(active)
        return active

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
        self.verify_nethernet(active, info)
        if plugin_active(active, 'bluemap'):
            # El mapa es accesorio: un fallo suyo (p. ej. sin Internet para las texturas) no debe
            # tumbar un update o restore cuyo Java y Bedrock funcionan.
            try:
                self.verify_bluemap(active)
            except Error as exc:
                LOG.warning('%s El servidor de juego está verificado.', exc)
        LOG.info('Verificación local correcta: Java %s; Bedrock %s; signaling NetherNet activo.',
                 java['version']['name'], bedrock['version'])
        print(f"Java: {active['lan_ip']}:25565 (Minecraft {active['minecraft']})")
        print(f"Bedrock: dirección {active['lan_ip']}, puerto 19132; compatibles: {active['compatibility']['bedrock']}")
        if plugin_active(active, 'bluemap'):
            print(f"Mapa BlueMap {active['bluemap']['version']}: http://{active['lan_ip']}:{BLUEMAP_PORT}/")
        print(f"NetherNet: signaling TCP 19132 y WebRTC UDP {NETHERNET_WEBRTC_PORT} en {active['lan_ip']} "
              '(no verificado con un cliente 26.60 hasta que exista).')
        print('Falta la prueba de entrada y juego desde otro dispositivo de tu LAN. No se ha simulado un jugador.')

    def verify_nethernet(self, active, info):
        """Geyser debe confirmar signaling en este arranque y el TCP publicado debe aceptar conexiones."""
        log = self.logs_since_start(info)
        # docker logs rota (3 × 10 MB): tras semanas en marcha la línea puede haber salido del log.
        # latest.log de Paper cubre toda la sesión y se lee dentro del contenedor, no como root.
        if 'Built-in signaling started on' not in log and self.compose(
                'exec', '-T', 'minecraft', 'grep', '-q', 'Built-in signaling started on', '/data/logs/latest.log',
                capture=True, check=False, timeout=30).returncode:
            raise Error('Geyser no confirma el signaling NetherNet; Bedrock 26.60+ no podría conectar. '
                        'Revisa manage.sh logs.')
        import socket
        try:
            socket.create_connection((active['lan_ip'], 19132), timeout=4).close()
        except OSError as exc:
            raise Error(f"El signaling NetherNet no responde en {active['lan_ip']}:19132/tcp.") from exc

    def verify_bluemap(self, active, timeout=90):
        """BlueMap arranca su web de forma asíncrona tras Paper: se espera a que sirva la página.
        En el primer arranque BlueMap genera los mapas después del entrypoint: se aplican aquí los
        ajustes de bluemap-setup.sh, dentro del contenedor y como su usuario, y se recarga."""
        result = self.compose('exec', '-T', 'minecraft', '/opt/mclan/bluemap-setup.sh', '/data/plugins/BlueMap',
                              capture=True, timeout=60)
        if 'changed' in result.stdout:
            LOG.info('Ajustes de BlueMap aplicados a mapas nuevos; recargando BlueMap.')
            self.exec_probe('rcon', 'bluemap', 'reload')
        from urllib.request import urlopen
        from urllib.error import URLError
        deadline = time.monotonic() + timeout
        while True:
            try:
                with urlopen(f"http://{active['lan_ip']}:{BLUEMAP_PORT}/", timeout=5) as response:
                    if response.status == 200:
                        return
            except (URLError, OSError):
                pass
            if time.monotonic() > deadline:
                raise Error(f"La web de BlueMap no responde en {active['lan_ip']}:{BLUEMAP_PORT}. Revisa manage.sh logs.")
            time.sleep(3)

    def verify_dump(self, active, protocol):
        result = self.exec_probe('dump')
        try:
            diagnostic = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise Error('Geyser no creó un diagnóstico offline interpretable.') from exc
        validate_diagnostic(diagnostic, active, protocol)

def plugin_active(active, name):
    return name in active and name not in active.get('disabled_plugins', [])


def validate_diagnostic(diagnostic, active, protocol):
    """Se comprueba la build realmente cargada, plugins activos y protocolo Java real."""
    plugins = diagnostic['bootstrapInfo']['plugins']
    enabled = {p['name'].lower() for p in plugins if p.get('enabled') is True}
    if not {'geyser-spigot', 'floodgate'} <= enabled:
        raise Error('Geyser y Floodgate no aparecen ambos habilitados en Paper.')
    for key, name in (('bluemap', 'BlueMap'), ('chunky', 'Chunky'), ('viaversion', 'ViaVersion'),
                      ('viabackwards', 'ViaBackwards'), ('mclan_skins', 'MclanSkins')):
        if plugin_active(active, key) and name.lower() not in enabled:
            raise Error(f'{name} forma parte de la release pero no aparece habilitado en Paper.')
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
                '# transport/webrtc-port/advertise-addresses los fija compose.yaml con propiedades JVM.\n'
                'bedrock:\n  address: 0.0.0.0\n  port: 19132\n  transport: both\n'
                f'  webrtc-port: {NETHERNET_WEBRTC_PORT}\n  clone-remote-port: false\n'
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
