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
import struct
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from mclan import cli, host  # noqa: E402
from mclan.common import Error, LOG, run  # noqa: E402
from mclan import nbt  # noqa: E402
from mclan.safefs import open_folder, read_data, read_gzip_data, write_data  # noqa: E402
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
            raise ValueError(f'must be one of: {", ".join(options)}')
        return value
    return check


def integer(low, high):
    def check(value):
        if not re.fullmatch(r'[0-9]{1,3}', value) or not low <= int(value) <= high:
            raise ValueError(f'must be a whole number between {low} and {high}')
        return str(int(value))
    return check


def motd(value):
    if not 1 <= len(value) <= 59 or any(ord(c) < 32 or c == '\x7f' for c in value) or '§' in value:
        raise ValueError('must be 1 to 59 printable characters, without colour codes')
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
            raise ValueError(f'setting not allowed: {key}')
        try:
            changes[key] = PROPERTIES[key](value)
        except ValueError as exc:
            raise ValueError(f'{key} {exc}') from exc
    if not changes:
        raise ValueError('nothing to change')
    return changes


# Comandos del ciclo de vida: tienen botones propios que comprueban el cierre limpio.
BLOCKED_COMMANDS = {'stop', 'restart', 'reload'}
# Líneas del healthcheck (RCON cada 30 s): ruido que oculta el log real.
NOISE = ('Thread RCON Client',)


WORLD_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,31}')
IMPORT_FILE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._ -]{0,99}\.(?:zip|mcworld)')


def world_name(value):
    value = value.strip()
    if not 1 <= len(value) <= 40 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('a world name must be 1 to 40 printable characters')
    return value


def console_command(words):
    """Comando de juego para la consola web: una línea imprimible; nunca stop/restart/reload."""
    text = ' '.join(words).strip().lstrip('/')
    if not 1 <= len(text) <= 256 or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError('a command must be 1 to 256 printable characters on a single line')
    if text.split()[0].lower().split(':')[-1] in BLOCKED_COMMANDS:
        raise ValueError('use the Start, Stop and Restart buttons; reload is not supported')
    return text


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


UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
GAMEMODES = ('survival', 'creative', 'adventure', 'spectator')
PLAYER_ACTIONS = ('op', 'deop', 'kick', 'ban', 'pardon', 'gamemode', 'spawn', 'heal')
# Estadísticas destacadas del apartado minecraft:custom.
HEADLINE_STATS = ('play_time', 'deaths', 'mob_kills', 'player_kills', 'walk_one_cm', 'sprint_one_cm', 'swim_one_cm',
                  'fly_one_cm', 'boat_one_cm', 'jump', 'damage_dealt', 'damage_taken', 'time_since_death', 'leave_game',
                  'open_chest', 'traded_with_villager', 'animals_bred', 'fish_caught', 'sleep_in_bed')


def is_bedrock(uuid_text):
    return uuid_text.startswith('00000000-0000-0000-')


def top(mapping, limit=8):
    return [{'id': key.split(':', 1)[-1], 'count': value}
            for key, value in sorted(mapping.items(), key=lambda item: -item[1])[:limit] if isinstance(value, int)]


def items(entries):
    result = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get('id'), str):
            result.append({'slot': entry.get('Slot'), 'id': entry['id'].split(':', 1)[-1],
                           'count': entry.get('count', entry.get('Count', 1))})
    return result


def profile_from(data, stats, advancements):
    """Perfil a partir del .dat, las estadísticas y los logros (todo ya leído y acotado)."""
    bukkit, paper = data.get('bukkit', {}), data.get('Paper', {})
    equipment = data.get('equipment', {}) if isinstance(data.get('equipment'), dict) else {}
    custom = stats.get('minecraft:custom', {})
    done = sorted(key.split(':', 1)[-1] for key, value in advancements.items()
                  if isinstance(value, dict) and value.get('done') and '/recipes/' not in key and 'recipes/' not in key)
    death = data.get('LastDeathLocation') or data.get('last_death_location')
    respawn = data.get('respawn') if isinstance(data.get('respawn'), dict) else None
    return {
        'name': bukkit.get('lastKnownName'),
        'first_played': bukkit.get('firstPlayed'), 'last_played': paper.get('LastSeen') or bukkit.get('lastPlayed'),
        'last_login': paper.get('LastLogin'),
        'vitals': {'health': data.get('Health'), 'food': data.get('foodLevel'), 'saturation': data.get('foodSaturationLevel'),
                   'xp_level': data.get('XpLevel'), 'xp_progress': data.get('XpP'), 'xp_total': data.get('XpTotal'),
                   'air': data.get('Air'), 'gamemode': GAMEMODES[data['playerGameType']] if data.get('playerGameType') in range(4) else None,
                   'abilities': {k: bool(v) for k, v in data.get('abilities', {}).items() if k in ('flying', 'mayfly', 'invulnerable', 'mayBuild')}},
        'location': {'dimension': str(data.get('Dimension', '')).split(':', 1)[-1], 'pos': data.get('Pos'),
                     'death': {'dimension': str(death.get('dimension', '')).split(':', 1)[-1], 'pos': death.get('pos')}
                     if isinstance(death, dict) else None,
                     'respawn': {'dimension': str(respawn.get('dimension', '')).split(':', 1)[-1], 'pos': respawn.get('pos')}
                     if respawn else None},
        'inventory': items(data.get('Inventory')), 'ender_chest': items(data.get('EnderItems')),
        'equipment': {slot: items([dict(value, Slot=slot)])[0] for slot, value in equipment.items()
                      if isinstance(value, dict) and items([value])},
        'selected_slot': data.get('SelectedItemSlot'),
        'stats': {'custom': {key: custom[f'minecraft:{key}'] for key in HEADLINE_STATS if f'minecraft:{key}' in custom},
                  'mined': top(stats.get('minecraft:mined', {})), 'crafted': top(stats.get('minecraft:crafted', {})),
                  'killed': top(stats.get('minecraft:killed', {})), 'killed_by': top(stats.get('minecraft:killed_by', {})),
                  'used': top(stats.get('minecraft:used', {})), 'picked_up': top(stats.get('minecraft:picked_up', {}))},
        'advancements': done,
    }


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

    def json_data(self, relative, limit=1024**2, default=None):
        try:
            return json.loads(read_data(self.runtime.data, relative, limit))
        except FileNotFoundError:
            return default
        except (OSError, ValueError):
            return default

    def online_names(self):
        try:
            listed = self.rcon('list')
        except Error:
            return None
        match = re.search(r'players online:?\s*(.*)$', listed)
        return {n.strip() for n in (match[1] if match else '').split(',') if n.strip()}

    def known_players(self):
        """Todos los jugadores conocidos: whitelist, caché de usuarios, ops, bans y datos del mundo."""
        names = {}
        for source in ('usercache.json', 'whitelist.json', 'ops.json', 'banned-players.json'):
            for entry in self.json_data(source, default=[]) or []:
                if isinstance(entry, dict) and UUID.fullmatch(str(entry.get('uuid', ''))) and entry.get('name'):
                    names.setdefault(entry['uuid'], str(entry['name']))
        try:
            folder = open_folder(self.runtime.data, 'world/players/data')
            try:
                files = [entry.name[:-4] for entry in os.scandir(folder)
                         if entry.name.endswith('.dat') and entry.is_file(follow_symlinks=False)]
            finally:
                os.close(folder)
        except OSError:
            files = []
        for uuid_text in files:
            if UUID.fullmatch(uuid_text) and uuid_text != '00000000-0000-0000-0000-000000000000':
                names.setdefault(uuid_text, None)
        return names

    def players(self):
        names = self.known_players()
        online = self.online_names() or set()
        ops = {e.get('uuid') for e in self.json_data('ops.json', default=[]) or [] if isinstance(e, dict)}
        banned = {e.get('uuid') for e in self.json_data('banned-players.json', default=[]) or [] if isinstance(e, dict)}
        listed = {e['uuid'] for e in self.whitelist()}
        result = []
        for uuid_text, name in names.items():
            try:
                data = nbt.loads(read_data(self.runtime.data, f'world/players/data/{uuid_text}.dat', 4 * 1024**2))
            except (OSError, ValueError):
                data = {}
            name = name or data.get('bukkit', {}).get('lastKnownName') or uuid_text
            result.append({'uuid': uuid_text, 'name': name, 'edition': 'bedrock' if is_bedrock(uuid_text) else 'java',
                           'online': name in online, 'op': uuid_text in ops, 'banned': uuid_text in banned,
                           'whitelisted': uuid_text in listed,
                           'last_played': data.get('Paper', {}).get('LastSeen') or data.get('bukkit', {}).get('lastPlayed')})
        return {'players': sorted(result, key=lambda p: (not p['online'], -(p['last_played'] or 0)))}

    def player(self, uuid_text):
        summary = next((p for p in self.players()['players'] if p['uuid'] == uuid_text), None)
        if summary is None:
            return {'ok': False, 'output': 'Unknown player.'}
        try:
            data = nbt.loads(read_data(self.runtime.data, f'world/players/data/{uuid_text}.dat', 4 * 1024**2))
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as exc:
            return {'ok': False, 'output': f'Player data could not be read safely: {exc}'}
        stats = (self.json_data(f'world/players/stats/{uuid_text}.json', default={}) or {}).get('stats', {})
        advancements = self.json_data(f'world/players/advancements/{uuid_text}.json', default={}) or {}
        profile = profile_from(data, stats if isinstance(stats, dict) else {}, advancements if isinstance(advancements, dict) else {})
        bans = [e for e in self.json_data('banned-players.json', default=[]) or [] if isinstance(e, dict) and e.get('uuid') == uuid_text]
        ops = [e for e in self.json_data('ops.json', default=[]) or [] if isinstance(e, dict) and e.get('uuid') == uuid_text]
        if summary['online']:
            # Posición y vida en vivo: el .dat sólo se guarda en el autosave.
            for key, path in (('pos', 'Pos'), ('health', 'Health'), ('food', 'foodLevel')):
                try:
                    reply = self.rcon('data', 'get', 'entity', summary['name'], path)
                except Error:
                    continue
                numbers = [float(n) for n in re.findall(r'-?\d+(?:\.\d+)?(?=[dfs]?\b)', reply.split('data:', 1)[-1])]
                if key == 'pos' and len(numbers) >= 3:
                    profile['location']['pos'] = numbers[:3]
                elif key != 'pos' and numbers:
                    profile['vitals'][key] = numbers[0]
        return {'ok': True, **summary, **profile, 'name': summary['name'],
                'xuid': int(uuid_text.replace('-', ''), 16) if is_bedrock(uuid_text) else None,
                'ban': {k: bans[0].get(k) for k in ('reason', 'created', 'source', 'expires')} if bans else None,
                'op_level': ops[0].get('level') if ops else None}

    def player_action(self, action, name, mode=None):
        if name not in {p['name'] for p in self.players()['players']}:
            return {'ok': False, 'output': 'Unknown player.'}
        commands = {
            'op': [('op', name)], 'deop': [('deop', name)], 'pardon': [('pardon', name)],
            'kick': [('kick', name, 'Kicked from the server panel')],
            'ban': [('ban', name, 'Banned from the server panel')],
            'gamemode': [('gamemode', mode, name)],
            'spawn': [('execute', 'in', 'minecraft:overworld', 'run', 'tp', name,
                       *[str(v) for v in (world_spawn(self.runtime.data) or [0, 100, 0])])],
            'heal': [('effect', 'give', name, 'minecraft:instant_health', '1', '10'),
                     ('effect', 'give', name, 'minecraft:saturation', '1', '10')],
        }[action]
        LOG.info('Acción de jugador desde el panel: %s %s %s', action, name, mode or '')
        return {'ok': True, 'output': ' '.join(self.rcon(*words) for words in commands)}

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
        world = {}
        if players is not None:
            for key, words in (('border', ('worldborder', 'get')), ('pregen', ('chunky', 'progress'))):
                if key == 'pregen' and 'chunky' not in active:
                    continue
                try:
                    world[key] = self.rcon(*words)
                except Error:
                    pass
        backups = sorted((self.root / 'backups').glob('*.tar.gz'), key=lambda p: p.stat().st_mtime, reverse=True)
        return {
            'release': {key: active[key] for key in ('id', 'minecraft', 'lan_ip') if key in active}
            | {'paper': active['paper']['build'], 'geyser': active['geyser']['version'],
               'floodgate': active['floodgate']['version'],
               'bluemap': active.get('bluemap', {}).get('version'),
               'chunky': active.get('chunky', {}).get('version'),
               'viaversion': active.get('viaversion', {}).get('version'),
               'bedrock': active['compatibility']['bedrock']},
            'container': {'exists': bool(info), 'running': running, 'status': state.get('Status'),
                          'health': state.get('Health', {}).get('Status'),
                          'restarts': info.get('RestartCount') if info else None,
                          'started_at': state.get('StartedAt'),
                          'memory': container_memory(info['Id']) if info else None},
            'players': players,
            'bluemap': bluemap,
            'world': world,
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
        raw = COLOR.sub('', self.server_log(tail=min(lines * 4, 4000))).splitlines()
        kept = [line for line in raw if not any(noise in line for noise in NOISE)]
        return {'log': '\n'.join(kept[-lines:])}

    def pulse(self):
        """Estado ligero para refrescar el panel cada pocos segundos (sin leer logs)."""
        info = self.runtime.container()
        state = info['State'] if info else {}
        players = None
        if state.get('Running') and state.get('Health', {}).get('Status') == 'healthy':
            try:
                players = self.rcon('list')
            except Error:
                players = None
        return {'running': bool(state.get('Running')), 'health': state.get('Health', {}).get('Status'),
                'players': players, 'memory': container_memory(info['Id']) if info else None,
                'temperature': temperature()}

    def console(self, text):
        LOG.info('Comando de consola del panel: %s', text)
        return {'ok': True, 'output': self.rcon(text) or '(no output)'}

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
                    return {'ok': True, 'output': f"Added {entry['name']} ({entry['uuid']})."}
            errors = [line for line in self.server_log(since=since).splitlines() if '[floodgate]' in line]
            if errors:
                return {'ok': False, 'output': COLOR.sub('', errors[-1])}
        return {'ok': False, 'output': 'Floodgate did not confirm the addition. Check the logs.'}

    def whitelist_add(self, edition, name):
        if edition == 'java':
            return {'ok': True, 'output': self.rcon('whitelist', 'add', name)}
        if edition == 'bedrock':
            return self.floodgate_add(name)
        return self.floodgate_add(floodgate_uuid(name))

    def whitelist_remove(self, name):
        entry = next((e for e in self.whitelist() if e['name'] == name), None)
        if not entry:
            return {'ok': False, 'output': 'That player is not on the whitelist.'}
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
                        return {'ok': True, 'output': f'{joined} joined and was added. {output}'}
                time.sleep(3)
            return {'ok': False, 'output': f'{name} did not join within {minutes} minutes; nobody was added.'}
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
        return {'ok': True, 'output': 'Settings saved' + (' and server restarted.' if running else '.')}

    def bluemap_render_switch(self, value):
        output = self.rcon('bluemap', 'start' if value == 'on' else 'stop')
        return {'ok': True, 'output': output or ('Render resumed.' if value == 'on' else 'Render paused.')}

    def plugin(self, name, value):
        return self.manage('plugin', name, value)

    def worlds(self):
        from mclan.worlds import Library
        library = Library(self.runtime).describe()
        def listing(folder, pattern):
            if not folder.is_dir():
                return []
            return [{'name': p.name, 'size': p.stat().st_size, 'mtime': p.stat().st_mtime}
                    for p in sorted(folder.iterdir(), key=lambda p: -p.stat().st_mtime)
                    if p.is_file() and not p.is_symlink() and pattern.fullmatch(p.name)][:50]
        return {**library, 'imports': listing(self.root / 'imports', IMPORT_FILE),
                'exports': listing(self.root / 'exports', re.compile(r'[a-z0-9-]+\.(?:zip|mcworld)'))}

    def world_create(self, name, seed=''):
        from mclan.worlds import Library
        return {'ok': True, 'output': f'Created world {Library(self.runtime).create(name, seed)}.'}

    def world_rename(self, wid, name):
        from mclan.worlds import Library
        Library(self.runtime).rename(wid, name)
        return {'ok': True, 'output': 'World renamed.'}

    def audit(self):
        result = subprocess.run([str(self.root / 'tools/audit.sh')], capture_output=True, text=True,
                                timeout=120, check=False, stdin=subprocess.DEVNULL)
        return {'ok': result.returncode == 0, 'output': result.stdout + result.stderr}


def parse(argv):
    """Devuelve (verbo, argumentos normalizados, escribe) o lanza ValueError."""
    if not argv:
        raise ValueError('missing verb')
    verb, args = argv[0], argv[1:]
    def expect(count):
        if len(args) != count:
            raise ValueError(f'{verb} needs {count} argument(s)')
    if verb in ('status', 'audit', 'pulse'):
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
            raise ValueError('invalid name or XUID')
        return verb, [edition, name], True
    if verb == 'whitelist-remove':
        expect(1)
        if not (JAVA_NAME.fullmatch(args[0]) or BEDROCK_NAME.fullmatch(args[0])):
            raise ValueError('invalid name')
        return verb, args, True
    if verb == 'whitelist':
        expect(1)
        return verb, [choice('on', 'off')(args[0])], True
    if verb == 'whitelist-open-for':
        expect(3)
        edition, name, minutes = args
        pattern = {'java': JAVA_NAME, 'bedrock': GAMERTAG}.get(edition)
        if not pattern or not pattern.fullmatch(name):
            raise ValueError('invalid edition or name')
        return verb, [edition, name, int(integer(1, 15)(minutes))], True
    if verb == 'set-properties':
        return verb, [validate_changes(args)], True
    if verb == 'console':
        return verb, [console_command(args)], True
    if verb == 'players':
        expect(0)
        return verb, [], False
    if verb == 'worlds':
        expect(0)
        return verb, [], False
    if verb == 'world-create':
        if len(args) not in (1, 2):
            raise ValueError('world-create needs a name and optionally a seed')
        seed = args[1] if len(args) == 2 else ''
        if seed and not re.fullmatch(r'-?[0-9]{1,19}', seed):
            raise ValueError('the seed must be a whole number')
        return verb, [world_name(args[0]), seed], True
    if verb == 'world-rename':
        expect(2)
        if not WORLD_ID.fullmatch(args[0]):
            raise ValueError('invalid world id')
        return verb, [args[0], world_name(args[1])], True
    if verb in ('world-activate', 'world-delete'):
        expect(1)
        if not WORLD_ID.fullmatch(args[0]):
            raise ValueError('invalid world id')
        return verb, args, False  # manage.sh toma el bloqueo
    if verb == 'world-export':
        expect(2)
        if not WORLD_ID.fullmatch(args[0]):
            raise ValueError('invalid world id')
        return verb, [args[0], choice('java', 'bedrock')(args[1])], False
    if verb == 'world-import':
        expect(2)
        if not IMPORT_FILE.fullmatch(args[0]):
            raise ValueError('invalid import file name')
        return verb, [args[0], world_name(args[1])], False
    if verb == 'player':
        expect(1)
        if not UUID.fullmatch(args[0]):
            raise ValueError('invalid UUID')
        return verb, args, False
    if verb == 'player-action':
        if len(args) not in (2, 3):
            raise ValueError('player-action needs an action, a name and optionally a game mode')
        action, name = choice(*PLAYER_ACTIONS)(args[0]), args[1]
        if not (JAVA_NAME.fullmatch(name) or BEDROCK_NAME.fullmatch(name)):
            raise ValueError('invalid name')
        if (action == 'gamemode') != (len(args) == 3):
            raise ValueError('only gamemode takes a game mode')
        return verb, [action, name, *([choice(*GAMEMODES)(args[2])] if len(args) == 3 else [])], True
    if verb == 'bluemap-render':
        expect(1)
        return verb, [choice('on', 'off')(args[0])], True
    if verb == 'plugin':
        expect(2)
        return verb, [choice('bluemap')(args[0]), choice('on', 'off')(args[1])], False  # manage.sh bloquea
    raise ValueError(f'unknown verb: {verb}')


def execute(panel, verb, args):
    if verb == 'status':
        return {'ok': True, **panel.status()}
    if verb == 'logs':
        return {'ok': True, **panel.logs(*args)}
    if verb == 'audit':
        return panel.audit()
    if verb == 'pulse':
        return {'ok': True, **panel.pulse()}
    if verb == 'console':
        return panel.console(*args)
    if verb == 'players':
        return {'ok': True, **panel.players()}
    if verb == 'worlds':
        return {'ok': True, **panel.worlds()}
    if verb == 'world-create':
        return panel.world_create(*args)
    if verb == 'world-rename':
        return panel.world_rename(*args)
    if verb == 'world-activate':
        return panel.manage('world', 'activate', args[0], '--yes')
    if verb == 'world-delete':
        return panel.manage('world', 'delete', args[0], '--yes')
    if verb == 'world-export':
        return panel.manage('world', 'export', args[0], '--format', args[1])
    if verb == 'world-import':
        source = panel.root / 'imports' / args[0]
        if source.is_symlink() or not source.is_file():
            return {'ok': False, 'output': 'That file is not in the imports folder.'}
        return panel.manage('world', 'import', str(source), args[1])
    if verb == 'player':
        return panel.player(*args)
    if verb == 'player-action':
        return panel.player_action(*args)
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
        print(json.dumps({'ok': False, 'output': f'Request rejected: {exc}'}))
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
