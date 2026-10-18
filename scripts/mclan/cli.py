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

from . import backups, host, versions
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
    for name in ('state', 'data', 'logs', 'backups', 'releases', 'config', '.env', 'compose.yaml'):
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
    print(f"Actual: Minecraft {active['minecraft']}; Paper {active['paper']['build']}; "
          f"Geyser {active['geyser']['build']}; Floodgate {active['floodgate']['build']}")
    print(f"Propuesta: Minecraft {proposal['minecraft']}; Paper {proposal['paper']['build']}; "
          f"Geyser {proposal['geyser']['build']}; Floodgate {proposal['floodgate']['build']}")
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
        if was_running:
            runtime.start(args.timeout, pending=True)
        else:
            LOG.info('Actualización preparada; se conserva el servidor detenido. Verifica al iniciarlo.')
        runtime.finish_transaction()
    except BaseException:
        write_json(marker, {"operation": "update-failed", "backup": str(backup)})
        try:
            runtime.stop()
        except Error:
            LOG.error('No se confirmó un cierre limpio; revisa el contenedor antes de restaurar.')
        LOG.error('Actualización no verificada. Restaura el snapshot completo: %s', backup)
        raise


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
    elif command == 'command':
        text = ' '.join(args.text).strip().lstrip('/')
        if not text or '\n' in text or '\r' in text or len(text) > 2000:
            raise Error('Comando vacío o demasiado largo.')
        if text.split()[0].lower() in ('stop', 'restart', 'reload'):
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
                     or (args.command == 'update' and args.plan))))
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
