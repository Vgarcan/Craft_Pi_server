"""Flujos de alto nivel. Los comandos de escritura comparten un bloqueo de proceso."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile
import zlib

from . import addons, backups, host, versions
from .common import Error, LOG, atomic_write, confirm, create_only, read_json, run, write_json
from .net import Client
from .runtime import Runtime, initialize_data

ROOT = Path(__file__).resolve().parents[2]


def parser():
    p = argparse.ArgumentParser(description='Minecraft LAN Server Installer — Raspberry Pi OS ARM64')
    actions = p.add_subparsers(dest='mode', required=True)
    install = actions.add_parser('install', help='Preparar e instalar sin sobrescribir datos existentes')
    install.add_argument('--check', action='store_true', help='Sólo comprobar el host y fuentes; sin APT ni Docker')
    install.add_argument('--accept-eula', action='store_true', help='Aceptas https://www.minecraft.net/eula')
    install.add_argument('--contact', help='URL HTTPS o email identificativo requerido por Paper')
    install.add_argument('--lan-ip', default=None, help='IPv4 LAN; auto sólo si existe una interfaz candidata')
    install.add_argument('--minecraft-version', default=None, help='auto o versión compatible con Geyser')
    install.add_argument('--timeout', type=int, default=900)
    install.add_argument('--disable-plugin', action='append', default=[], choices=['bluemap', 'mclan_skins'],
                         help='Instalar con este plugin opcional desactivado (repetible)')
    manage = actions.add_parser('manage', help='Administrar la instalación actual')
    commands = manage.add_subparsers(dest='command', required=True)
    for name in ('start', 'stop', 'restart', 'status', 'backup', 'verify'):
        sub = commands.add_parser(name)
        if name in ('start', 'restart', 'verify'):
            sub.add_argument('--timeout', type=int, default=900)
    logs = commands.add_parser('logs')
    logs.add_argument('--follow', action='store_true')
    logs.add_argument('--tail', type=int, default=100)
    restore = commands.add_parser('restore')
    restore.add_argument('archive', type=Path)
    restore.add_argument('--lan-ip', help='Sustituir la IP guardada al recuperar en otro host o tras un cambio DHCP')
    restore.add_argument('--timeout', type=int, default=900)
    update = commands.add_parser('update')
    update.add_argument('--plan', action='store_true', help='Resolver versiones sin descargar JARs ni tocar datos')
    update.add_argument('--minecraft-version', default=None, help='auto permite proponer una actualización de Minecraft')
    update.add_argument('--timeout', type=int, default=900)
    rebind = commands.add_parser('rebind', help='Cambiar la IP publicada después de un cambio DHCP')
    rebind.add_argument('ip')
    rebind.add_argument('--timeout', type=int, default=900)
    plugin = commands.add_parser('plugin', help='Activar o desactivar un plugin opcional (reinicia si estaba en marcha)')
    plugin.add_argument('name', choices=['bluemap', 'mclan_skins'])
    plugin.add_argument('state', choices=['on', 'off'])
    plugin.add_argument('--timeout', type=int, default=900)
    world = commands.add_parser('world', help='Biblioteca de mundos (hasta 50, uno activo)')
    actions = world.add_subparsers(dest='world_action', required=True)
    actions.add_parser('list')
    create = actions.add_parser('create')
    create.add_argument('name')
    create.add_argument('--seed', default='')
    activate = actions.add_parser('activate')
    activate.add_argument('id')
    activate.add_argument('--timeout', type=int, default=900)
    activate.add_argument('--yes', action='store_true', help='Sin confirmación interactiva (panel)')
    importer = actions.add_parser('import', help='Zip o carpeta de Java, o .mcworld de Bedrock')
    importer.add_argument('path', type=Path)
    importer.add_argument('name')
    exporter = actions.add_parser('export')
    exporter.add_argument('id')
    exporter.add_argument('--format', choices=['java', 'bedrock'], default='java')
    rename = actions.add_parser('rename')
    rename.add_argument('id')
    rename.add_argument('name')
    remove = actions.add_parser('delete', help='Archiva en backups/worlds y borra (nunca el activo)')
    remove.add_argument('id')
    remove.add_argument('--yes', action='store_true')
    pack = commands.add_parser('pack', help='Paquete de recursos (texturas) para Java y Bedrock; se aplica sin reiniciar')
    pack_actions = pack.add_subparsers(dest='pack_action', required=True)
    pack_actions.add_parser('list', help='Catálogo, variantes descargadas y selección actual (JSON)')
    use = pack_actions.add_parser('use', help='Elegir el paquete y sus variantes')
    use.add_argument('id')
    use.add_argument('--java', action='append', metavar='VERSION',
                     help='Variante Java (repetible): cada jugador recibe la de su versión. '
                          'Por defecto, la de la versión del servidor')
    use.add_argument('--no-java', action='store_true', help='Sólo para Bedrock')
    use.add_argument('--bedrock', choices=['on', 'off'], default='on')
    use.add_argument('--required', action='store_true',
                     help='Java: quien lo rechace no puede jugar. Por defecto es opcional')
    pack_actions.add_parser('off', help='Dejar de ofrecer paquete (los archivos se conservan)')
    pack_actions.add_parser('clean', help='Borrar las descargas que no usa la selección actual')
    console = commands.add_parser('command', help='Enviar un comando administrativo mediante RCON interno')
    console.add_argument('text', nargs='+', help='Usa comillas, por ejemplo: "whitelist add Victor"')
    return p


def logging_setup(root, writable):
    LOG.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    LOG.addHandler(handler)
    if writable:
        directory = root / 'logs'
        directory.mkdir(mode=0o700, exist_ok=True)
        handler = RotatingFileHandler(directory / 'operations.log', maxBytes=2*1024**2,
                                      backupCount=5, encoding='utf-8')
        handler.setFormatter(formatter)
        LOG.addHandler(handler)


@contextmanager
def locked(root):
    state = root / 'state'
    state.mkdir(mode=0o700, exist_ok=True)
    with (state / 'operation.lock').open('a') as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Error('Ya hay otra operación de escritura en curso.') from exc
        yield


def settings(root):
    path = root / 'config/settings.json'
    result = read_json(path) if path.exists() else read_json(root / 'config/settings.example.json')
    if not isinstance(result, dict) or not {'contact', 'lan_ip', 'minecraft_version', 'startup_timeout'} <= result.keys():
        raise Error('settings.json necesita contact, lan_ip, minecraft_version y startup_timeout; consulta el ejemplo.')
    return result


def guard_paths(root):
    # Evita seguir rutas gestionadas que un usuario haya sustituido por enlaces.
    for name in ('state', 'data', 'logs', 'backups', 'releases', 'config', '.env', 'compose.yaml',
                 'worlds', 'imports', 'exports', 'packs'):
        path = root / name
        if path.is_symlink():
            raise Error(f'No se admiten enlaces en rutas gestionadas: {name}')
    if any(c in str(root) for c in ('\n', '\r', '$')):
        raise Error('Usa una ruta de proyecto sin saltos de línea ni $.')


def install(runtime, args):
    current = settings(runtime.root)
    current.update({k: v for k, v in {'contact': args.contact, 'lan_ip': args.lan_ip,
                   'minecraft_version': args.minecraft_version}.items() if v is not None})
    client = Client(current['contact'])
    release, ip = host.preflight(runtime.root, current['lan_ip'])
    # Estas consultas prueban DNS/TLS/HTTPS de los proveedores que se van a usar.
    if args.check:
        lock = versions.resolve(client, current['minecraft_version'])
        client.fetch('https://download.docker.com/linux/debian/gpg')
        print(json.dumps({"host_preflight": "OK", "lan_ip": ip, "proposal": lock}, indent=2))
        print('No se ha instalado ni arrancado ningún servidor.')
        return
    if (runtime.state / 'operation.json').exists():
        raise Error('Hay una operación interrumpida. Consulta recuperación y usa restore.')
    if runtime.active_path.exists():
        host.setup_docker(release, client)
        active = runtime.validate()
        if args.lan_ip and args.lan_ip != active['lan_ip']:
            raise Error('Para cambiar la IP usa manage.sh rebind IP.')
        if args.minecraft_version and args.minecraft_version not in ('auto', active['minecraft']):
            raise Error('Para cambiar versiones usa manage.sh update --minecraft-version auto.')
        # No resolver latest ni regenerar configuraciones en una repetición.
        runtime.start(args.timeout)
        (runtime.state / 'installing.json').unlink(missing_ok=True)
        return
    if not args.accept_eula:
        raise Error('Lee https://www.minecraft.net/eula y repite con --accept-eula si la aceptas.')
    data = runtime.data
    installing = runtime.state / 'installing.json'
    if data.exists() and any(data.iterdir()) and not installing.exists():
        raise Error('data/ contiene datos sin estado gestionado. No se importarán ni sobrescribirán. '
                    'Conserva una copia y utiliza un directorio de proyecto nuevo.')
    lock = versions.resolve(client, current['minecraft_version'])
    if args.disable_plugin:
        lock['disabled_plugins'] = sorted(set(args.disable_plugin))
    host.free_ports(ip)
    host.setup_docker(release, client)
    lock = runtime.stage(lock, client, ip)
    write_json(installing, {"release": lock['id']})
    create_only(runtime.root / 'config/settings.json', json.dumps(current, indent=2) + '\n')
    initialize_data(runtime, args.accept_eula, lock['geyser_config_version'])
    create_only(runtime.root / 'compose.yaml', (runtime.root / 'docker/compose.template.yaml').read_text())
    runtime.activate(lock)
    runtime.validate()
    runtime.start(args.timeout)
    installing.unlink(missing_ok=True)


def update(runtime, args, client):
    active = runtime.validate(allow_drift=True)
    # Un git pull del proyecto puede traer otro código Docker o plantilla Compose
    # sin cambiar las builds: también exige reconstruir, respaldar y verificar.
    template_changed = runtime.template_drift()
    project_changed = template_changed or runtime.runtime_drift(active)
    requested = args.minecraft_version or active['minecraft']
    proposal = versions.resolve(client, requested)
    # Lo que el operador desactivó sigue desactivado en la release nueva.
    if active.get('disabled_plugins'):
        proposal['disabled_plugins'] = list(active['disabled_plugins'])
    # Un addon activado que la release activa no incluye (se desactivó sin compilar) hay que compilarlo.
    missing = [key for key in addons.ADDONS if key in proposal and key not in active
               and key not in active.get('disabled_plugins', [])]
    project_changed = project_changed or bool(missing)
    for label, lock in (('Actual', active), ('Propuesta', proposal)):
        extras = ''.join(f"; {name} {lock[key]['version']}" for key, name in versions.EXTRA_PLUGINS if key in lock)
        print(f"{label}: Minecraft {lock['minecraft']}; Paper {lock['paper']['build']}; "
              f"Geyser {lock['geyser']['build']}; Floodgate {lock['floodgate']['build']}{extras}")
    if project_changed:
        print('Proyecto: archivos Docker/Compose nuevos; se reconstruirá la imagen'
              + (' y se aplicará la plantilla Compose actual.' if template_changed else '.'))
    if args.plan:
        return
    if (runtime.state / 'operation.json').exists():
        raise Error('Primero resuelve la operación interrumpida mediante restore.')
    if proposal['id'] == active['id'] and not project_changed:
        LOG.info('Ya están instaladas estas builds. No se cambian datos.')
        return
    confirm('ACTUALIZAR', 'La actualización desconectará jugadores y puede convertir el mundo. '
            'Se hará un backup completo antes de activarla; no hay downgrade automático.')
    # Descargas/construcción antes de parar: un error de red no deja el servidor apagado.
    staged = runtime.stage(proposal, client, active['lan_ip'])
    was_running = runtime.running()
    runtime.stop()
    try:
        backup = backups.snapshot(runtime, 'pre-update')
    except BaseException:
        if was_running:
            # La instalación previa sigue intacta aunque el proyecto ya sea más nuevo.
            runtime.start(args.timeout, allow_drift=True)
        raise
    marker = runtime.state / 'operation.json'
    write_json(marker, {"operation": "update", "backup": str(backup), "from": active['id'], "to": staged['id']})
    try:
        if template_changed:
            # Sustituye sólo una plantilla gestionada anterior; queda copiada en el backup.
            atomic_write(runtime.root / 'compose.yaml', (runtime.root / 'docker/compose.template.yaml').read_text())
        runtime.activate(staged)
        runtime.validate()
        # Se verifica siempre la release nueva, aunque el servidor estuviera parado: así la marca no
        # se retira sin haber comprobado el mundo convertido. Después se respeta la parada.
        runtime.start(args.timeout, pending=True)
        if not was_running:
            runtime.stop()
            LOG.info('Actualización verificada; se conserva el servidor detenido como estaba.')
        runtime.finish_transaction()
    except BaseException:
        write_json(marker, {"operation": "update-failed", "backup": str(backup)})
        try:
            runtime.stop()
        except Error:
            LOG.error('No se confirmó un cierre limpio; revisa el contenedor antes de restaurar.')
        LOG.error('Actualización no verificada. Restaura el snapshot completo: %s', backup)
        raise


def manage_world(runtime, args, client):
    from .worlds import Library
    library = Library(runtime)
    action = args.world_action
    if action == 'list':
        print(json.dumps(library.describe(), indent=2, ensure_ascii=False))
    elif action == 'create':
        print(library.create(args.name, args.seed))
    elif action == 'rename':
        library.rename(args.id, args.name)
    elif action == 'import':
        print(library.import_world(args.path, args.name, client))
    elif action == 'export':
        print(library.export(args.id, args.format, client))
    elif action == 'activate':
        if not args.yes:
            confirm('CAMBIAR', 'Se parará el servidor (desconecta a los jugadores), se hará un backup y se activará otro mundo.')
        library.activate(args.id, args.timeout, client)
    elif action == 'delete':
        if not args.yes:
            confirm('BORRAR', 'El mundo se archivará en backups/worlds y después se borrará de la biblioteca.')
        print(library.delete(args.id) or 'Sin datos generados que archivar.')


def manage_pack(runtime, args, active, client):
    from .packs import Packs, served
    packs = Packs(runtime)
    action = args.pack_action
    if action == 'list':
        print(json.dumps(packs.describe(active['minecraft']), indent=2, ensure_ascii=False))
        return
    if action == 'clean':
        print(f'Liberados {packs.clean() / 1024**2:.1f} MB.')
        return
    if 'mclan_packs' not in active:
        raise Error('La release activa no incluye MclanPacks: ejecuta antes sudo ./manage.sh update.')
    if action == 'use':
        if args.no_java and args.java:
            raise Error('Usa --java o --no-java, no ambos.')
        packs.use(args.id, [] if args.no_java else args.java, args.bedrock == 'on', args.required,
                  client, active['minecraft'])
    else:
        packs.off()
    if not runtime.running():
        print('El servidor está parado: el paquete se aplicará al arrancar.')
        return
    reply = runtime.exec_probe('rcon', 'mclanpacks', 'reload').stdout.strip()
    print(reply)
    if not reply.startswith('Reloaded.') or 'Error:' in reply:
        raise Error('MclanPacks no confirmó la nueva selección; revisa manage.sh logs.')
    problems = served(active['lan_ip'], packs.selection())
    if problems:
        raise Error(' '.join(problems))
    current = packs.selection()
    if current and current['java']:
        print(f"Comprobado: cada variante Java se descarga de http://{active['lan_ip']}:8101/ con su SHA-1.")
    if current and current['bedrock']:
        print('Bedrock: los jugadores lo reciben al volver a conectar.')


def manage(runtime, args):
    command = args.command
    if os.environ.get('DOCKER_HOST') or os.environ.get('DOCKER_CONTEXT'):
        raise Error('Desactiva DOCKER_HOST/DOCKER_CONTEXT: sólo se administra Docker local.')
    endpoint = run(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], capture=True).stdout.strip()
    if endpoint != 'unix:///var/run/docker.sock':
        raise Error('El contexto Docker no corresponde al socket local del sistema.')
    current = settings(runtime.root)
    client = Client(current['contact'])
    if command == 'restore':
        confirm('RESTAURAR', 'Se sustituirán TODOS los datos actuales por el backup seleccionado. '
                'Sólo uses copias propias de confianza. Se preservará el estado anterior.')
        backups.restore(runtime, args.archive, client, args.timeout, ip_override=args.lan_ip)
        return
    if command == 'update':
        update(runtime, args, client)
        return
    if command == 'stop':
        # Debe seguir disponible aunque haya una marca de actualización fallida.
        runtime.stop()
        return
    # Consultar estado y logs debe seguir siendo posible tras un git pull pendiente de update.
    active = runtime.validate(allow_drift=command in ('status', 'logs'))
    if command == 'status':
        runtime.compose('ps', '--all')
        print(f"Release: {active['id']}; Minecraft {active['minecraft']}; LAN {active['lan_ip']}")
        if runtime.template_drift() or runtime.runtime_drift(active):
            print('ATENCIÓN: el proyecto tiene archivos Docker/Compose nuevos. Aplica sudo ./manage.sh update.')
        if not host.memory_limits():
            print('ATENCIÓN: el kernel no aplica límites de memoria a contenedores (cgroup memory desactivado).')
        if (runtime.state / 'operation.json').exists():
            print('ATENCIÓN: hay una operación incompleta. Consultar state/operation.json.')
    elif command == 'logs':
        if args.tail < 1 or args.tail > 10000:
            raise Error('--tail debe estar entre 1 y 10000.')
        runtime.compose('logs', '--tail', str(args.tail), *(['--follow'] if args.follow else []), 'minecraft', timeout=None)
    elif command == 'start':
        runtime.start(args.timeout)
    elif command == 'restart':
        if (runtime.state / 'operation.json').exists():
            raise Error('Hay una operación interrumpida: restart no podría volver a arrancar. Consulta recuperación.')
        runtime.stop()
        runtime.start(args.timeout)
    elif command == 'verify':
        runtime.verify(args.timeout)
    elif command == 'backup':
        if (runtime.state / 'operation.json').exists():
            raise Error('Operación incompleta: conserva los datos y restaura un snapshot anterior.')
        running = runtime.running()
        runtime.stop()
        try:
            print(backups.snapshot(runtime))
        finally:
            if running:
                runtime.start(int(current.get('startup_timeout', 900)))
    elif command == 'rebind':
        if (runtime.state / 'operation.json').exists():
            raise Error('Resuelve primero la operación interrumpida.')
        ip = host.lan_ip(args.ip)
        if ip == active['lan_ip']:
            return
        confirm('RECONFIGURAR', f'Se detendrá el servidor y se publicarán sus puertos en {ip}.')
        running = runtime.running()
        runtime.stop()
        host.free_ports(ip)
        active['lan_ip'] = ip
        runtime.activate(active)
        current['lan_ip'] = ip
        write_json(runtime.root / 'config/settings.json', current)
        if running:
            runtime.start(args.timeout)
    elif command == 'plugin':
        if (runtime.state / 'operation.json').exists():
            raise Error('Resuelve primero la operación interrumpida.')
        enabled = args.state == 'on'
        if (args.name not in active.get('disabled_plugins', [])) == enabled:
            print(f"{args.name} ya está {'activado' if enabled else 'desactivado'}.")
            return
        running = runtime.running()
        runtime.stop()
        active = runtime.set_plugin(args.name, enabled)
        LOG.info('Plugin %s %s.', args.name, 'activado' if enabled else 'desactivado')
        if enabled and args.name not in active:
            print(f'{args.name} no está compilado en la release activa: ejecuta sudo ./manage.sh update para compilarlo.')
        if running:
            runtime.start(args.timeout)
    elif command == 'world':
        manage_world(runtime, args, client)
    elif command == 'pack':
        if (runtime.state / 'operation.json').exists() and args.pack_action != 'list':
            raise Error('Resuelve primero la operación interrumpida.')
        manage_pack(runtime, args, active, client)
    elif command == 'command':
        text = ' '.join(args.text).strip().lstrip('/')
        if not text or '\n' in text or '\r' in text or len(text) > 2000:
            raise Error('Comando vacío o demasiado largo.')
        # También con espacio de nombres: minecraft:stop, bukkit:reload…
        if text.split()[0].lower().split(':')[-1] in ('stop', 'restart', 'reload'):
            raise Error('Usa manage.sh stop/restart; reload no está soportado.')
        confirm('EJECUTAR', 'Los comandos de consola tienen permisos de administrador y pueden cambiar el mundo.')
        print(runtime.exec_probe('rcon', text).stdout)


def main(argv=None):
    args = parser().parse_args(argv)
    writable = not (args.mode == 'install' and args.check)
    if writable and os.geteuid() != 0:
        print('Ejecuta este comando con sudo. No es necesario añadir tu usuario al grupo docker.', file=sys.stderr)
        return 1
    if sys.version_info < (3, 11):
        print('Se requiere Python 3.11 o superior.', file=sys.stderr)
        return 1
    os.umask(0o077)
    try:
        guard_paths(ROOT)
        logging_setup(ROOT, writable)
        operation = args.command if args.mode == 'manage' else ('preflight' if args.check else 'install')
        LOG.info('Operación: %s %s', args.mode, operation)
        runtime = Runtime(ROOT)
        if getattr(args, 'timeout', 900) < 10 or getattr(args, 'timeout', 900) > 7200:
            raise Error('--timeout debe estar entre 10 y 7200 segundos.')
        readonly = (not writable or (args.mode == 'manage' and (args.command in ('logs', 'status')
                     or (args.command == 'update' and args.plan)
                     or (args.command == 'world' and args.world_action == 'list')
                     or (args.command == 'pack' and args.pack_action == 'list'))))
        if readonly:
            (install if args.mode == 'install' else manage)(runtime, args)
        else:
            with locked(ROOT):
                (install if args.mode == 'install' else manage)(runtime, args)
        return 0
    except KeyboardInterrupt:
        LOG.error('Operación interrumpida. Comprueba estado y logs antes de reintentar.')
        return 130
    except (Error, OSError, ValueError, KeyError, TypeError, IndexError, EOFError,
            subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile, zlib.error) as exc:
        LOG.error('%s', exc)
        return 1
