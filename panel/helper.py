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
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from mclan import cli, host  # noqa: E402
from mclan.common import Error, LOG, atomic_write, read_json, run  # noqa: E402
from mclan.runtime import Runtime  # noqa: E402

CONTAINER = 'minecraft-lan-minecraft-1'
JAVA_NAME = re.compile(r'[A-Za-z0-9_]{3,16}')
# Los gamertags pueden llevar espacios; Floodgate los sustituye por "_". Se exige la forma sin
# espacios porque la consola separa argumentos por espacios.
GAMERTAG = re.compile(r'[A-Za-z0-9_]{1,16}')
BEDROCK_NAME = re.compile(r'\.[A-Za-z0-9_]{1,16}')
XUID = re.compile(r'[1-9][0-9]{0,19}')
COLOR = re.compile('§.')


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


REJECTED = [
    ('bedrock', re.compile(r'\[Geyser-Spigot\] ([A-Za-z0-9_ ]{1,16}) has disconnected from the Java server '
                           r'because of .*not white-?listed')),
    ('java', re.compile(r'Disconnecting ([A-Za-z0-9_]{3,16}) \(/[^)]*\): You are not white-?listed')),
    ('java', re.compile(r'([A-Za-z0-9_]{3,16}) \(/[^)]*\) lost connection: You are not white-?listed')),
]


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
        folder = self.runtime.data / 'logs'
        cutoff = time.time() - hours * 3600
        older = [p for p in sorted(folder.glob('*.log.gz'), key=lambda p: p.stat().st_mtime)
                 if p.stat().st_mtime >= cutoff][-archives:]
        parts = []
        for path in older:
            try:
                with gzip.open(path, 'rt', errors='replace') as stream:
                    parts.append(stream.read(8 * 1024**2))
            except (OSError, EOFError):
                continue
        if (folder / 'latest.log').exists():
            parts.append((folder / 'latest.log').read_text(errors='replace')[-8 * 1024**2:])
        return '\n'.join(parts)

    def whitelist(self):
        path = self.runtime.data / 'whitelist.json'
        return read_json(path) if path.exists() else []

    def status(self):
        active = self.runtime.active()
        info = self.runtime.container()
        state = info['State'] if info else {}
        running = bool(state.get('Running'))
        properties = read_properties((self.runtime.data / 'server.properties').read_text())
        players = None
        if running and state.get('Health', {}).get('Status') == 'healthy':
            try:
                players = self.rcon('list')
            except Error:
                players = None
        whitelist = self.whitelist()
        backups = sorted((self.root / 'backups').glob('*.tar.gz'), key=lambda p: p.stat().st_mtime, reverse=True)
        return {
            'release': {key: active[key] for key in ('id', 'minecraft', 'lan_ip') if key in active}
            | {'paper': active['paper']['build'], 'geyser': active['geyser']['version'],
               'floodgate': active['floodgate']['version'],
               'bluemap': active.get('bluemap', {}).get('version'),
               'bedrock': active['compatibility']['bedrock']},
            'container': {'exists': bool(info), 'running': running, 'status': state.get('Status'),
                          'health': state.get('Health', {}).get('Status'),
                          'restarts': info.get('RestartCount') if info else None,
                          'started_at': state.get('StartedAt'),
                          'memory': container_memory(info['Id']) if info else None},
            'players': players,
            'host': {'temperature': temperature(), 'memory_limits': host.memory_limits()},
            'pending_operation': (self.runtime.state / 'operation.json').exists(),
            'project_drift': self.runtime.template_drift() or self.runtime.runtime_drift(active),
            'properties': {key: properties.get(key, '') for key in PROPERTIES} | {
                'white-list': properties.get('white-list', 'false')},
            'whitelist': [{'name': e.get('name'), 'uuid': e.get('uuid'),
                           'edition': 'bedrock' if str(e.get('uuid', '')).startswith('00000000-0000-0000-') else 'java'}
                          for e in whitelist],
            'rejected': rejected_attempts(self.paper_logs(), {str(e.get('name', '')).lower() for e in whitelist}),
            'backups': [{'name': p.name, 'size': p.stat().st_size, 'mtime': p.stat().st_mtime} for p in backups[:10]],
        }

    def logs(self, lines):
        return {'log': COLOR.sub('', self.server_log(tail=lines))}

    def manage(self, command):
        # manage.sh ya gestiona bloqueo, verificación y backups; se reutiliza tal cual.
        result = subprocess.run([str(self.root / 'manage.sh'), command], capture_output=True, text=True,
                                timeout=7200, check=False, stdin=subprocess.DEVNULL)
        return {'ok': result.returncode == 0, 'output': result.stdout + result.stderr}

    def floodgate_add(self, target):
        """Floodgate responde de forma asíncrona: se confirma en whitelist.json y en el log."""
        since = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        self.rcon('fwhitelist', 'add', target)
        for _ in range(10):
            time.sleep(1)
            for entry in self.whitelist():
                if target.lower() in (str(entry.get('uuid')).lower(), str(entry.get('name')).lower().lstrip('.')):
                    return {'ok': True, 'output': f"Añadido {entry.get('name')} ({entry.get('uuid')})."}
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
        entry = next((e for e in self.whitelist() if e.get('name') == name), None)
        if not entry:
            return {'ok': False, 'output': 'Ese jugador no está en la whitelist.'}
        return {'ok': True, 'output': self.rcon('whitelist', 'remove', name)}

    def whitelist_switch(self, value):
        return {'ok': True, 'output': self.rcon('whitelist', value)}

    def open_for(self, name, minutes):
        """Abre la whitelist hasta que entre ese jugador, lo añade y la vuelve a cerrar siempre."""
        targets = {name.lower(), '.' + name.lower()}
        since = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        joined = re.compile(r'\]: (\.?[A-Za-z0-9_]{1,16})\[/[^\]]*\] logged in with entity id')
        self.rcon('whitelist', 'off')
        try:
            deadline = time.monotonic() + minutes * 60
            while time.monotonic() < deadline:
                for match in joined.finditer(self.server_log(since=since)):
                    if match[1].lower() in targets:
                        output = self.rcon('whitelist', 'add', match[1])
                        return {'ok': True, 'output': f'{match[1]} entró y quedó añadido. {output}'}
                time.sleep(3)
            return {'ok': False, 'output': f'{name} no entró en {minutes} minutos; no se añadió a nadie.'}
        finally:
            self.rcon('whitelist', 'on')

    def set_properties(self, changes):
        running = self.runtime.running()
        self.runtime.stop()
        path = self.runtime.data / 'server.properties'
        atomic_write(path, edit_properties(path.read_text(), changes))
        os.chown(path, 10000, 10000)
        LOG.info('Ajustes cambiados desde el panel: %s', ', '.join(sorted(changes)))
        if running:
            self.runtime.start()
        return {'ok': True, 'output': 'Ajustes guardados' + (' y servidor reiniciado.' if running else '.')}

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
        expect(2)
        if not GAMERTAG.fullmatch(args[0]):
            raise ValueError('nombre no válido')
        return verb, [args[0], int(integer(1, 15)(args[1]))], True
    if verb == 'set-properties':
        return verb, [validate_changes(args)], True
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
