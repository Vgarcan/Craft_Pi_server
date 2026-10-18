#!/usr/bin/env python3
"""Operaciones privilegiadas del panel web.

Se ejecuta como root mediante una regla sudo limitada a este archivo. Sólo acepta los verbos de
VERBS con argumentos validados por patrones cerrados: nunca texto libre hacia la consola de Paper.
Las escrituras comparten el bloqueo de manage.sh y quedan registradas en logs/operations.log.
Imprime un objeto JSON {"ok": bool, ...} en stdout.
"""
import gzip
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from mclan import cli, host  # noqa: E402
from mclan.common import Error, LOG, run  # noqa: E402
from mclan.runtime import Runtime  # noqa: E402

CONTAINER = 'minecraft-lan-minecraft-1'
JAVA_NAME = re.compile(r'[A-Za-z0-9_]{3,16}')
# Los gamertags pueden llevar espacios; Floodgate los sustituye por "_". Se exige la forma sin
# espacios porque la consola separa argumentos por espacios.
GAMERTAG = re.compile(r'[A-Za-z0-9_]{1,16}')
BEDROCK_NAME = re.compile(r'\.[A-Za-z0-9_]{1,16}')
XUID = re.compile(r'[1-9][0-9]{0,19}')
COLOR = re.compile('§.')
DATA_UID = 10000
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


# --- Acceso a data/ ---------------------------------------------------------------------------
# data/ pertenece al usuario del contenedor (UID 10000): un plugin o servidor comprometido puede
# sustituir cualquier archivo o carpeta por un enlace, un FIFO o algo enorme. El helper es root, así
# que nunca sigue enlaces en ningún componente de la ruta, sólo lee archivos regulares y con tope.

def open_folder(data, relative='.'):
    descriptor = os.open(data, DIRECTORY)
    try:
        for part in Path(relative).parts:
            if part != '.':
                child = os.open(part, DIRECTORY, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
        return descriptor
    except OSError:
        os.close(descriptor)
        raise


def open_regular(folder_fd, name):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder_fd)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise OSError(f'{name} no es un archivo regular')
    return descriptor


def read_data(data, relative, limit, *, tail=False):
    """Contenido de data/<relative>; con tail=True, sólo los últimos `limit` bytes."""
    path = Path(relative)
    folder = open_folder(data, path.parent)
    try:
        descriptor = open_regular(folder, path.name)
    finally:
        os.close(folder)
    with os.fdopen(descriptor, 'rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        if tail and size > limit:
            stream.seek(size - limit)
        return stream.read(limit)


def write_data(data, relative, content):
    """Reemplazo atómico dentro de data/ sin seguir enlaces; el archivo queda para UID 10000."""
    path = Path(relative)
    folder = open_folder(data, path.parent)
    temporary = f'.{path.name}.mclan-new'
    try:
        try:
            os.unlink(temporary, dir_fd=folder)
        except FileNotFoundError:
            pass
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content.encode())
            stream.flush()
            os.fsync(stream.fileno())
            os.fchown(stream.fileno(), DATA_UID, DATA_UID)
        os.rename(temporary, path.name, src_dir_fd=folder, dst_dir_fd=folder)
    finally:
        os.close(folder)


def read_gzip_data(folder_fd, name, limit):
    descriptor = open_regular(folder_fd, name)
    with os.fdopen(descriptor, 'rb') as raw, gzip.GzipFile(fileobj=raw) as stream:
        return stream.read(limit)


def choice(*options):
    def check(value):
        if value not in options:
            raise ValueError(f'debe ser uno de: {", ".join(options)}')
        return value
    return check


def integer(low, high):
    def check(value):
        if not re.fullmatch(r'[0-9]{1,3}', value) or not low <= int(value) <= high:
            raise ValueError(f'debe ser un entero entre {low} y {high}')
        return str(int(value))
    return check


def motd(value):
    if not 1 <= len(value) <= 59 or any(ord(c) < 32 or c == '\x7f' for c in value) or '§' in value:
        raise ValueError('de 1 a 59 caracteres imprimibles, sin códigos de color')
    return value


BOOLEAN = choice('true', 'false')
# Ajustes editables desde la web. El resto de server.properties (autenticación, puertos, RCON)
# queda fuera porque el validador de manage.sh exige sus valores.
PROPERTIES = {
    'motd': motd,
    'difficulty': choice('peaceful', 'easy', 'normal', 'hard'),
    'gamemode': choice('survival', 'creative', 'adventure', 'spectator'),
    'max-players': integer(1, 20),
    'view-distance': integer(3, 12),
    'simulation-distance': integer(3, 10),
    'pvp': BOOLEAN,
    'allow-flight': BOOLEAN,
    'spawn-protection': integer(0, 64),
}


def decode_value(value):
    """Valor tal como lo leería java.util.Properties para los escapes que aparecen aquí."""
    decoded = re.sub(r'\\(u[0-9a-fA-F]{4}|.)',
                     lambda m: chr(int(m[1][1:], 16)) if m[1][0] == 'u' and len(m[1]) == 5 else
                     {'n': '\n', 't': '\t'}.get(m[1], m[1]), value)
    # Recompone los pares sustitutos UTF-16 que deja \\uD83C\\uDFAE.
    return decoded.encode('utf-16-le', 'surrogatepass').decode('utf-16-le', 'replace')


def encode_value(value):
    """Escapes de java.util.Properties: barra invertida y no ASCII como \\uXXXX (UTF-16)."""
    out = []
    for character in value.replace('\\', '\\\\'):
        if ord(character) <= 126:
            out.append(character)
        else:
            raw = character.encode('utf-16-be')
            out += [f'\\u{int.from_bytes(raw[i:i + 2], "big"):04x}' for i in range(0, len(raw), 2)]
    return ''.join(out)


def read_properties(text):
    result = {}
    for line in text.splitlines():
        if '=' in line and not line.lstrip().startswith(('#', '!')):
            key, value = line.split('=', 1)
            result.setdefault(key.strip(), decode_value(value))
    return result


def edit_properties(text, changes):
    """Sustituye la primera aparición de cada clave y añade las que falten; conserva el resto."""
    lines = text.splitlines()
    pending = dict(changes)
    for index, line in enumerate(lines):
        if '=' in line and not line.lstrip().startswith(('#', '!')):
            key = line.split('=', 1)[0].strip()
            if key in pending:
                lines[index] = f'{key}={encode_value(pending.pop(key))}'
    lines += [f'{key}={encode_value(value)}' for key, value in pending.items()]
    return '\n'.join(lines) + '\n'


def validate_changes(pairs):
    changes = {}
    for pair in pairs:
        key, separator, value = pair.partition('=')
        if not separator or key not in PROPERTIES or key in changes:
            raise ValueError(f'ajuste no admitido: {key}')
        try:
            changes[key] = PROPERTIES[key](value)
        except ValueError as exc:
            raise ValueError(f'{key} {exc}') from exc
    if not changes:
        raise ValueError('no hay cambios')
    return changes


def floodgate_uuid(xuid):
    """Floodgate identifica a cada jugador Bedrock como UUID(0, XUID)."""
    return str(uuid.UUID(int=int(xuid)))


# Anclados al prefijo de línea de Paper ("[HH:MM:SS] [hilo/INFO]: "): un jugador no puede fabricar
# una entrada escribiendo ese texto en el chat, porque esas líneas empiezan por "<Nombre> ".
LINE = r'^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\] \[[^\]\n]+/INFO\]: '
REJECTED = [
    ('bedrock', re.compile(LINE + r'\[Geyser-Spigot\] ([A-Za-z0-9_ ]{1,16}) has disconnected from the Java server '
                           r'because of (?:§r)?You are not white-?listed on this server!$')),
    ('java', re.compile(LINE + r'Disconnecting ([A-Za-z0-9_]{3,16}) \(/[0-9a-fA-F.:\[\]]+\): '
                        r'You are not white-?listed on this server!$')),
    ('java', re.compile(LINE + r'([A-Za-z0-9_]{3,16}) \(/[0-9a-fA-F.:\[\]]+\) lost connection: '
                        r'You are not white-?listed on this server!$')),
]
# Entrada de un jugador en el log de Docker ("[HH:MM:SS INFO]: Nombre[/ip:puerto] logged in ...").
JOINED = re.compile(r'^\[[0-9:]{8} INFO\]: (\.?[A-Za-z0-9_]{1,16})\[/[^\]\n]*\] logged in with entity id [0-9]+', re.M)


def rejected_attempts(log, whitelisted):
    """Últimos intentos rechazados por whitelist, sin duplicados ni jugadores ya admitidos."""
    found = {}
    for line in log.splitlines():
        stamp = line[1:9] if line.startswith('[') else ''
        for edition, pattern in REJECTED:
            match = pattern.search(line)
            if match:
                name = match[1].replace(' ', '_')
                java_name = '.' + name if edition == 'bedrock' else name
                if java_name.lower() not in whitelisted:
                    found[(edition, name)] = {'edition': edition, 'name': name, 'last_seen': stamp}
    return list(found.values())[-20:]


def container_memory(container_id):
    base = Path(f'/sys/fs/cgroup/system.slice/docker-{container_id}.scope')
    try:
        current = int((base / 'memory.current').read_text())
        limit = (base / 'memory.max').read_text().strip()
        return {'used': current, 'limit': None if limit == 'max' else int(limit)}
    except (OSError, ValueError):
        return None


def world_spawn(data_dir):
    """Spawn del mundo (int array 'pos' de level.dat, formato 26.x) para centrar el mapa; None si no se lee."""
    try:
        folder = open_folder(data_dir, 'world')
        try:
            data = read_gzip_data(folder, 'level.dat', 1024**2)
        finally:
            os.close(folder)
        index = data.find(b'\x0b\x00\x03pos')
        if index < 0 or struct.unpack('>i', data[index + 6:index + 10])[0] != 3:
            return None
        return list(struct.unpack('>iii', data[index + 10:index + 22]))
    except (OSError, EOFError, struct.error, ValueError, gzip.BadGzipFile):
        return None


def bluemap_render(console):
    """Estado del render a partir de la salida de /bluemap."""
    text = console.lower()
    if 'stopped' in text or 'not running' in text:
        return 'paused'
    if 'idle' in text:
        return 'idle'
    if 'render' in text:
        return 'working'
    return None


def temperature():
    try:
        return int(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000
    except (OSError, ValueError):
        return None


class Panel:
    def __init__(self, root=ROOT):
        self.runtime = Runtime(root)
        self.root = root

    def rcon(self, *words):
        result = self.runtime.exec_probe('rcon', *words)
        return COLOR.sub('', result.stdout).strip()

    def server_log(self, since=None, tail=None):
        args = ['docker', 'logs', *(['--since', since] if since else []), *(['--tail', str(tail)] if tail else []), CONTAINER]
        result = run(args, capture=True, check=False)
        return result.stdout + result.stderr

    def paper_logs(self, hours=72, archives=40):
        """Logs de Paper en data/logs: sobreviven a recrear el contenedor, a diferencia de docker logs.
        Paper rota el archivo en cada arranque, así que se lee una ventana de tiempo, no un número fijo."""
        cutoff = time.time() - hours * 3600
        try:
            folder = open_folder(self.runtime.data, 'logs')
        except OSError:
            return ''
        parts = []
        try:
            entries = [(entry.stat(follow_symlinks=False).st_mtime, entry.name) for entry in os.scandir(folder)
                       if entry.name.endswith('.log.gz') and entry.is_file(follow_symlinks=False)]
            for mtime, name in sorted(entries)[-archives:]:
                if mtime >= cutoff:
                    try:
                        parts.append(read_gzip_data(folder, name, 4 * 1024**2).decode(errors='replace'))
                    except (OSError, EOFError, gzip.BadGzipFile):
                        continue
            try:
                parts.append(read_data(self.runtime.data, 'logs/latest.log', 8 * 1024**2, tail=True).decode(errors='replace'))
            except OSError:
                pass
        finally:
            os.close(folder)
        return '\n'.join(parts)

    def whitelist(self):
        try:
            entries = json.loads(read_data(self.runtime.data, 'whitelist.json', 1024**2))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            raise Error(f'whitelist.json no se puede leer de forma segura: {exc}') from exc
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise Error('whitelist.json no tiene el formato esperado.')
        return [{'name': str(e.get('name', '')), 'uuid': str(e.get('uuid', ''))} for e in entries]

    def status(self):
        active = self.runtime.active()
        info = self.runtime.container()
        state = info['State'] if info else {}
        running = bool(state.get('Running'))
        properties = read_properties(read_data(self.runtime.data, 'server.properties', 256 * 1024).decode(errors='replace'))
        players = None
        if running and state.get('Health', {}).get('Status') == 'healthy':
            try:
                players = self.rcon('list')
            except Error:
                players = None
        whitelist = self.whitelist()
        bluemap = None
        if 'bluemap' in active:
            enabled = 'bluemap' not in active.get('disabled_plugins', [])
            render = None
            if enabled and players is not None:
                try:
                    render = bluemap_render(self.rcon('bluemap'))
                except Error:
                    render = None
            bluemap = {'version': active['bluemap']['version'], 'enabled': enabled, 'render': render}
        backups = sorted((self.root / 'backups').glob('*.tar.gz'), key=lambda p: p.stat().st_mtime, reverse=True)
        return {
            'release': {key: active[key] for key in ('id', 'minecraft', 'lan_ip') if key in active}
            | {'paper': active['paper']['build'], 'geyser': active['geyser']['version'],
               'floodgate': active['floodgate']['version'],
               'bluemap': active.get('bluemap', {}).get('version'),
               'chunky': active.get('chunky', {}).get('version'),
               'bedrock': active['compatibility']['bedrock']},
            'container': {'exists': bool(info), 'running': running, 'status': state.get('Status'),
                          'health': state.get('Health', {}).get('Status'),
                          'restarts': info.get('RestartCount') if info else None,
                          'started_at': state.get('StartedAt'),
                          'memory': container_memory(info['Id']) if info else None},
            'players': players,
            'bluemap': bluemap,
            'spawn': world_spawn(self.runtime.data),
            'host': {'temperature': temperature(), 'memory_limits': host.memory_limits()},
            'pending_operation': (self.runtime.state / 'operation.json').exists(),
            'project_drift': self.runtime.template_drift() or self.runtime.runtime_drift(active),
            'properties': {key: properties.get(key, '') for key in PROPERTIES} | {
                'white-list': properties.get('white-list', 'false')},
            'whitelist': [dict(e, edition='bedrock' if e['uuid'].startswith('00000000-0000-0000-') else 'java')
                          for e in whitelist],
            'rejected': rejected_attempts(self.paper_logs(), {e['name'].lower() for e in whitelist}),
            'backups': [{'name': p.name, 'size': p.stat().st_size, 'mtime': p.stat().st_mtime} for p in backups[:10]],
        }

    def logs(self, lines):
        return {'log': COLOR.sub('', self.server_log(tail=lines))}

    def manage(self, *command):
        # manage.sh ya gestiona bloqueo, verificación y backups; se reutiliza tal cual.
        result = subprocess.run([str(self.root / 'manage.sh'), *command], capture_output=True, text=True,
                                timeout=7200, check=False, stdin=subprocess.DEVNULL)
        return {'ok': result.returncode == 0, 'output': result.stdout + result.stderr}

    def floodgate_add(self, target):
        """Floodgate responde de forma asíncrona: se confirma en whitelist.json y en el log."""
        since = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        self.rcon('fwhitelist', 'add', target)
        for _ in range(10):
            time.sleep(1)
            for entry in self.whitelist():
                if target.lower() in (entry['uuid'].lower(), entry['name'].lower().lstrip('.')):
                    return {'ok': True, 'output': f"Añadido {entry['name']} ({entry['uuid']})."}
            errors = [line for line in self.server_log(since=since).splitlines() if '[floodgate]' in line]
            if errors:
                return {'ok': False, 'output': COLOR.sub('', errors[-1])}
        return {'ok': False, 'output': 'Floodgate no confirmó el alta. Revisa los logs.'}

    def whitelist_add(self, edition, name):
        if edition == 'java':
            return {'ok': True, 'output': self.rcon('whitelist', 'add', name)}
        if edition == 'bedrock':
            return self.floodgate_add(name)
        return self.floodgate_add(floodgate_uuid(name))

    def whitelist_remove(self, name):
        entry = next((e for e in self.whitelist() if e['name'] == name), None)
        if not entry:
            return {'ok': False, 'output': 'Ese jugador no está en la whitelist.'}
        return {'ok': True, 'output': self.rcon('whitelist', 'remove', name)}

    def whitelist_switch(self, value):
        return {'ok': True, 'output': self.rcon('whitelist', value)}

    def open_for(self, edition, name, minutes):
        """Abre la whitelist hasta que entre ese jugador de esa edición, lo añade y la vuelve a cerrar
        siempre. Quien haya entrado en la ventana sin ser el elegido es expulsado al cerrarla."""
        target = ('.' + name if edition == 'bedrock' else name).lower()
        since = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        self.rcon('whitelist', 'off')
        try:
            deadline = time.monotonic() + minutes * 60
            while time.monotonic() < deadline:
                for joined in JOINED.findall(self.server_log(since=since)):
                    if joined.lower() == target:
                        output = self.rcon('whitelist', 'add', joined)
                        return {'ok': True, 'output': f'{joined} entró y quedó añadido. {output}'}
                time.sleep(3)
            return {'ok': False, 'output': f'{name} no entró en {minutes} minutos; no se añadió a nadie.'}
        finally:
            self.rcon('whitelist', 'on')
            allowed = {entry['name'].lower() for entry in self.whitelist()}
            for joined in set(JOINED.findall(self.server_log(since=since))):
                if joined.lower() not in allowed:
                    self.rcon('kick', joined, 'Whitelist reactivada')

    def set_properties(self, changes):
        running = self.runtime.running()
        self.runtime.stop()
        current = read_data(self.runtime.data, 'server.properties', 256 * 1024).decode()
        write_data(self.runtime.data, 'server.properties', edit_properties(current, changes))
        LOG.info('Ajustes cambiados desde el panel: %s', ', '.join(sorted(changes)))
        if running:
            self.runtime.start()
        return {'ok': True, 'output': 'Ajustes guardados' + (' y servidor reiniciado.' if running else '.')}

    def bluemap_render_switch(self, value):
        output = self.rcon('bluemap', 'start' if value == 'on' else 'stop')
        return {'ok': True, 'output': output or ('Render reanudado.' if value == 'on' else 'Render en pausa.')}

    def plugin(self, name, value):
        return self.manage('plugin', name, value)

    def audit(self):
        result = subprocess.run([str(self.root / 'tools/audit.sh')], capture_output=True, text=True,
                                timeout=120, check=False, stdin=subprocess.DEVNULL)
        return {'ok': result.returncode == 0, 'output': result.stdout + result.stderr}


def parse(argv):
    """Devuelve (verbo, argumentos normalizados, escribe) o lanza ValueError."""
    if not argv:
        raise ValueError('falta el verbo')
    verb, args = argv[0], argv[1:]
    def expect(count):
        if len(args) != count:
            raise ValueError(f'{verb} necesita {count} argumento(s)')
    if verb in ('status', 'audit'):
        expect(0)
        return verb, [], False
    if verb == 'logs':
        expect(1)
        return verb, [int(integer(1, 999)(args[0]))], False
    if verb in ('start', 'stop', 'restart', 'backup'):
        expect(0)
        return verb, [], False  # manage.sh toma su propio bloqueo
    if verb == 'whitelist-add':
        expect(2)
        edition, name = args
        pattern = {'java': JAVA_NAME, 'bedrock': GAMERTAG, 'xuid': XUID}.get(edition)
        if not pattern or not pattern.fullmatch(name):
            raise ValueError('nombre o XUID no válido')
        return verb, [edition, name], True
    if verb == 'whitelist-remove':
        expect(1)
        if not (JAVA_NAME.fullmatch(args[0]) or BEDROCK_NAME.fullmatch(args[0])):
            raise ValueError('nombre no válido')
        return verb, args, True
    if verb == 'whitelist':
        expect(1)
        return verb, [choice('on', 'off')(args[0])], True
    if verb == 'whitelist-open-for':
        expect(3)
        edition, name, minutes = args
        pattern = {'java': JAVA_NAME, 'bedrock': GAMERTAG}.get(edition)
        if not pattern or not pattern.fullmatch(name):
            raise ValueError('edición o nombre no válido')
        return verb, [edition, name, int(integer(1, 15)(minutes))], True
    if verb == 'set-properties':
        return verb, [validate_changes(args)], True
    if verb == 'bluemap-render':
        expect(1)
        return verb, [choice('on', 'off')(args[0])], True
    if verb == 'plugin':
        expect(2)
        return verb, [choice('bluemap')(args[0]), choice('on', 'off')(args[1])], False  # manage.sh bloquea
    raise ValueError(f'verbo desconocido: {verb}')


def execute(panel, verb, args):
    if verb == 'status':
        return {'ok': True, **panel.status()}
    if verb == 'logs':
        return {'ok': True, **panel.logs(*args)}
    if verb == 'audit':
        return panel.audit()
    if verb in ('start', 'stop', 'restart', 'backup'):
        return panel.manage(verb)
    if verb == 'whitelist-add':
        return panel.whitelist_add(*args)
    if verb == 'whitelist-remove':
        return panel.whitelist_remove(*args)
    if verb == 'whitelist':
        return panel.whitelist_switch(*args)
    if verb == 'whitelist-open-for':
        return panel.open_for(*args)
    if verb == 'bluemap-render':
        return panel.bluemap_render_switch(*args)
    if verb == 'plugin':
        return panel.plugin(*args)
    return panel.set_properties(*args)


def main(argv):
    try:
        verb, args, writes = parse(argv)
    except ValueError as exc:
        print(json.dumps({'ok': False, 'output': f'Petición rechazada: {exc}'}))
        return 2
    # SIGTERM (p. ej. reinicio del panel) debe ejecutar los finally, como reabrir la whitelist.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(1))
    try:
        if writes:
            cli.logging_setup(ROOT, True)
            LOG.info('Operación: panel %s', verb)
            with cli.locked(ROOT):
                result = execute(Panel(), verb, args)
        else:
            result = execute(Panel(), verb, args)
    except (Error, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        result = {'ok': False, 'output': str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
