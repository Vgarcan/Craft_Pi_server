"""Biblioteca de mundos: hasta 50 guardados y uno activo, servido en los puertos de siempre.

El mundo activo vive en data/world, como siempre. Los demás se guardan fuera de data/, en
worlds/<id>/world (y su mapa de BlueMap en worlds/<id>/bluemap-maps), así que los backups normales
sólo contienen el mundo activo. Cambiar de mundo exige un cierre limpio y un backup verificado, deja
marca de operación y deshace el intercambio si el mundo nuevo no arranca.

Inventarios y datos de jugador viajan con cada mundo (Minecraft 26.x los guarda en world/players).
Whitelist, operadores y bans son del servidor y se comparten.
"""
from datetime import datetime, timezone
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

from . import backups, nbt
from .common import Error, LOG, read_json, run, sha256, write_json
from .safefs import DATA_UID, read_data, write_data

LIMIT = 50
ID = re.compile(r'[a-z0-9][a-z0-9-]{0,31}')
MARKER = '.mclan-world'  # en data/: id del mundo activo; viaja dentro de los backups
# Ajustes que recuerda cada mundo: se guardan al desactivarlo y se aplican al activarlo.
SETTINGS = ('difficulty', 'gamemode', 'pvp', 'allow-flight', 'spawn-protection', 'level-seed')
MAX_IMPORT_BYTES, MAX_IMPORT_FILES = 8 * 1024**3, 400_000
# Conversor Bedrock → Java, fijado por SHA-256 (publicado por GitHub para esta release).
CHUNKER = {'version': '1.21.0', 'sha256': '63148e587f5ee5a2ca7ebae1079b235c82ae4d1eff8f7a8a8a3398332513dda6',
           'url': 'https://github.com/HiveGamesOSS/Chunker/releases/download/1.21.0/chunker-cli-1.21.0.jar'}


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def valid_name(name):
    name = (name or '').strip()
    if not 1 <= len(name) <= 40 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise Error('El nombre del mundo debe tener de 1 a 40 caracteres imprimibles.')
    return name


def valid_seed(seed):
    seed = (seed or '').strip()
    if seed and (not re.fullmatch(r'-?[0-9]{1,19}', seed) or not -2**63 <= int(seed) < 2**63):
        raise Error('La semilla debe ser un número entero de 64 bits (o vacía para una aleatoria).')
    return seed


def real_dir(path):
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def tree_size(path):
    total = 0
    for folder, _, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    return total


def own_tree(path):
    """El mundo pertenece al usuario del contenedor; sin enlaces ni archivos especiales."""
    for folder, directories, files in os.walk(path, followlinks=False):
        os.chown(folder, DATA_UID, DATA_UID, follow_symlinks=False)
        os.chmod(folder, 0o700)
        for name in directories + files:
            target = os.path.join(folder, name)
            info = os.lstat(target)
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise Error(f'El mundo contiene un enlace o archivo especial: {name}')
            if stat.S_ISREG(info.st_mode):
                os.chown(target, DATA_UID, DATA_UID, follow_symlinks=False)
                os.chmod(target, 0o600)


def safe_unzip(source, destination):
    """Extracción defensiva: sin rutas absolutas, '..', enlaces ni tamaños desproporcionados."""
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        if len(members) > MAX_IMPORT_FILES or sum(m.file_size for m in members) > MAX_IMPORT_BYTES:
            raise Error('El archivo es desproporcionado para un mundo.')
        for member in members:
            name = PurePosixPath(member.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in member.filename or stat.S_ISLNK(member.external_attr >> 16):
                raise Error(f'Entrada no admitida en el archivo: {member.filename}')
        destination.mkdir(parents=True, exist_ok=False)
        archive.extractall(destination)


def find_world_root(folder, marker='level.dat', depth=2):
    """Carpeta que contiene level.dat (el zip puede traer una carpeta envolvente)."""
    candidates = [folder]
    for _ in range(depth + 1):
        for candidate in candidates:
            if (candidate / marker).is_file() and not (candidate / marker).is_symlink():
                return candidate
        candidates = [c for parent in candidates for c in sorted(parent.iterdir())
                      if c.is_dir() and not c.is_symlink()]
    raise Error(f'No se encuentra {marker} en el archivo: no parece un mundo de Minecraft.')


def data_version(level_dat):
    try:
        return int(nbt.loads(level_dat.read_bytes())['Data']['DataVersion'])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def set_properties(text, changes):
    lines = text.splitlines()
    pending = dict(changes)
    for index, line in enumerate(lines):
        if '=' in line and not line.lstrip().startswith(('#', '!')):
            key = line.split('=', 1)[0].strip()
            if key in pending:
                lines[index] = f'{key}={pending.pop(key)}'
    return '\n'.join(lines + [f'{k}={v}' for k, v in pending.items()]) + '\n'


def read_properties(text):
    values = {}
    for line in text.splitlines():
        if '=' in line and not line.lstrip().startswith(('#', '!')):
            key, value = line.split('=', 1)
            values.setdefault(key.strip(), value)
    return values


class Library:
    def __init__(self, runtime):
        self.runtime, self.root = runtime, runtime.root
        self.dir = self.root / 'worlds'
        self.index = self.dir / 'library.json'
        self.data = runtime.data

    # --- Índice -----------------------------------------------------------------------------
    def load(self):
        if self.index.exists():
            library = read_json(self.index)
            if not isinstance(library, dict) or library.get('schema') != 1 or library.get('active') not in library.get('worlds', {}):
                raise Error('worlds/library.json no tiene el formato esperado; revísalo antes de continuar.')
            return library
        return {'schema': 1, 'active': 'main',
                'worlds': {'main': {'name': 'Main world', 'created': now(), 'source': 'existing', 'settings': {}}}}

    def save(self, library):
        self.dir.mkdir(mode=0o700, exist_ok=True)
        write_json(self.index, library)

    def slot(self, wid):
        if not ID.fullmatch(wid or ''):
            raise Error('Identificador de mundo no válido.')
        return self.dir / wid

    def new_id(self, name, library):
        base = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:24] or 'world'
        candidate, counter = base, 2
        while candidate in library['worlds'] or (self.dir / candidate).exists():
            candidate, counter = f'{base}-{counter}', counter + 1
        return candidate

    def describe(self):
        library = self.load()
        result = []
        for wid, meta in library['worlds'].items():
            active = wid == library['active']
            stored = real_dir(self.slot(wid) / 'world')
            result.append({'id': wid, 'active': active, 'name': meta.get('name', wid), 'source': meta.get('source'),
                           'created': meta.get('created'), 'last_active': meta.get('last_active'),
                           'size': meta.get('size'), 'generated': active or stored, 'seed': meta.get('seed', ''),
                           'needs_relight': bool(meta.get('needs_relight'))})
        return {'active': library['active'], 'limit': LIMIT, 'worlds': result}

    def register(self, library, name, **meta):
        if len(library['worlds']) >= LIMIT:
            raise Error(f'La biblioteca ya tiene {LIMIT} mundos. Exporta y borra alguno antes de añadir otro.')
        wid = self.new_id(name, library)
        library['worlds'][wid] = {'name': name, 'created': now(), 'settings': {}, **meta}
        return wid

    # --- Operaciones sencillas -------------------------------------------------------------
    def create(self, name, seed=''):
        name, seed = valid_name(name), valid_seed(seed)
        library = self.load()
        wid = self.register(library, name, source='new', seed=seed, settings={'level-seed': seed})
        self.save(library)
        LOG.info('Mundo creado en la biblioteca: %s (%s)', wid, name)
        return wid

    def rename(self, wid, name):
        library = self.load()
        if wid not in library['worlds']:
            raise Error('Ese mundo no existe.')
        library['worlds'][wid]['name'] = valid_name(name)
        self.save(library)

    # --- Importar -------------------------------------------------------------------------
    def import_world(self, source, name, client=None):
        name, source = valid_name(name), Path(source)
        if source.is_symlink() or not (source.is_file() or source.is_dir()):
            raise Error('Ruta de importación no válida.')
        library = self.load()
        if len(library['worlds']) >= LIMIT:
            raise Error(f'La biblioteca ya tiene {LIMIT} mundos.')
        needed = tree_size(source) if source.is_dir() else source.stat().st_size * 3
        if shutil.disk_usage(self.root).free < needed + 2 * 1024**3:
            raise Error('No hay espacio suficiente para importar el mundo con 2 GiB de margen.')
        bedrock = source.suffix.lower() == '.mcworld'
        staging = Path(tempfile.mkdtemp(prefix='import-', dir=self.runtime.state))
        try:
            if bedrock:
                world = self.convert_bedrock(source, staging, client)
            elif source.is_dir():
                world = find_world_root(source)
                shutil.copytree(world, staging / 'world', symlinks=True)
                world = staging / 'world'
            else:
                safe_unzip(source, staging / 'extracted')
                world = find_world_root(staging / 'extracted')
            server_version = data_version(self.data / 'world/level.dat') if real_dir(self.data / 'world') else None
            imported_version = data_version(world / 'level.dat')
            if server_version and imported_version and imported_version > server_version:
                raise Error('El mundo es de una versión de Minecraft más nueva que la del servidor: no se puede abrir.')
            own_tree(world)
            wid = self.register(library, name, source='import-bedrock' if bedrock else 'import-java',
                                original=source.name, needs_relight=bedrock, size=tree_size(world))
            slot = self.slot(wid)
            slot.mkdir(mode=0o700, parents=True)
            os.rename(world, slot / 'world')
            self.save(library)
            LOG.info('Mundo importado: %s desde %s', wid, source.name)
            return wid
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def chunker(self, client):
        jar = self.runtime.state / 'tools' / f"chunker-cli-{CHUNKER['version']}.jar"
        jar.parent.mkdir(mode=0o700, exist_ok=True)
        if client is None and not jar.exists():
            raise Error('Hace falta descargar el conversor Chunker: repite con acceso a Internet.')
        if client is not None:
            client.download(CHUNKER, jar)
        if sha256(jar) != CHUNKER['sha256']:
            raise Error('El conversor Chunker no coincide con su hash fijado.')
        jar.chmod(0o644)
        return jar

    def convert_bedrock(self, source, staging, client):
        """Chunker en un contenedor sin red ni capacidades, como el usuario del servidor."""
        jar = self.chunker(client)
        safe_unzip(source, staging / 'bedrock')
        bedrock = find_world_root(staging / 'bedrock')
        if not real_dir(bedrock / 'db'):
            raise Error('El .mcworld no contiene la base de datos del mundo (db/).')
        output = staging / 'out'
        output.mkdir(mode=0o700)
        for path in (staging / 'bedrock', output):
            for folder, directories, files in os.walk(path):
                os.chown(folder, DATA_UID, DATA_UID)
                for item in files:
                    os.chown(os.path.join(folder, item), DATA_UID, DATA_UID, follow_symlinks=False)
        active = self.runtime.active()
        fmt = 'JAVA_' + active['minecraft'].replace('.', '_')
        run(['docker', 'run', '--rm', '--network', 'none', '--user', f'{DATA_UID}:{DATA_UID}', '--cap-drop', 'ALL',
             '--security-opt', 'no-new-privileges:true', '--memory', '1500m', '-e', 'JAVA_TOOL_OPTIONS=-Xmx1g',
             '--entrypoint', 'java', '-v', f'{jar}:/tools/chunker.jar:ro', '-v', f'{bedrock}:/in', '-v', f'{output}:/out',
             '-w', '/tmp', active['runtime_image'], '-jar', '/tools/chunker.jar', '-i', '/in', '-o', '/out/world', '-f', fmt],
            capture=True, timeout=3600)
        return find_world_root(output)

    # --- Activar -------------------------------------------------------------------------
    def current_settings(self):
        text = read_data(self.data, 'server.properties', 256 * 1024).decode()
        values = read_properties(text)
        return {key: values[key] for key in SETTINGS if key in values}

    def apply_settings(self, settings):
        text = read_data(self.data, 'server.properties', 256 * 1024).decode()
        clean = {k: v for k, v in settings.items() if k in SETTINGS and re.fullmatch(r'[A-Za-z0-9_-]{0,20}', str(v))}
        write_data(self.data, 'server.properties', set_properties(text, clean))

    def swap_out(self, wid, moves):
        """Guarda el mundo activo (y su mapa) en su ranura de la biblioteca."""
        slot = self.slot(wid)
        slot.mkdir(mode=0o700, parents=True, exist_ok=True)
        if real_dir(slot / 'world'):
            raise Error(f'La ranura de {wid} ya contiene un mundo; revisa worlds/{wid} antes de continuar.')
        if (self.data / 'world').exists() and not real_dir(self.data / 'world'):
            raise Error('data/world no es una carpeta real; no se mueve.')
        for source, target in ((self.data / 'world', slot / 'world'),
                               (self.data / 'bluemap/web/maps', slot / 'bluemap-maps')):
            if real_dir(source) and not target.exists():
                os.rename(source, target)
                moves.append((source, target))

    def swap_in(self, wid, moves):
        slot = self.slot(wid)
        for source, target in ((slot / 'world', self.data / 'world'),
                               (slot / 'bluemap-maps', self.data / 'bluemap/web/maps')):
            if real_dir(source):
                if target.exists():
                    raise Error(f'{target} sigue ocupado; no se sobrescribe.')
                target.parent.mkdir(parents=True, exist_ok=True)
                os.rename(source, target)
                moves.append((source, target))

    @staticmethod
    def undo(moves):
        for source, target in reversed(moves):
            if real_dir(target) and not source.exists():
                os.rename(target, source)
        moves.clear()

    def write_marker(self, wid):
        write_data(self.data, MARKER, wid + '\n')

    def activate(self, target, timeout=900, client=None):
        library = self.load()
        current = library['active']
        if target not in library['worlds']:
            raise Error('Ese mundo no existe en la biblioteca.')
        if target == current:
            LOG.info('%s ya es el mundo activo.', target)
            return
        if (self.runtime.state / 'operation.json').exists():
            raise Error('Hay una operación interrumpida: resuélvela antes de cambiar de mundo.')
        running = self.runtime.running()
        self.runtime.stop()
        backup = backups.snapshot(self.runtime, f'pre-switch-{current}')
        marker = self.runtime.state / 'operation.json'
        write_json(marker, {'operation': 'world-switch', 'from': current, 'to': target, 'backup': str(backup)})
        before = read_data(self.data, 'server.properties', 256 * 1024).decode()
        moves = []
        try:
            library['worlds'][current]['settings'] = self.current_settings()
            library['worlds'][current]['size'] = tree_size(self.data / 'world') if real_dir(self.data / 'world') else None
            library['worlds'][current]['last_active'] = now()
            self.swap_out(current, moves)
            self.swap_in(target, moves)
            meta = library['worlds'][target]
            settings = dict(meta.get('settings') or {})
            if not real_dir(self.data / 'world'):
                settings['level-seed'] = meta.get('seed', '')  # mundo nuevo: Paper lo genera con esta semilla
            self.apply_settings(settings)
            self.write_marker(target)
            library['active'] = target
            meta['last_active'] = now()
            self.save(library)
            self.runtime.start(timeout, pending=True)
            if meta.get('needs_relight'):
                self.relight()
            self.refresh_map(moved_map=any(t == self.data / 'bluemap/web/maps' for _, t in moves))
            self.runtime.finish_transaction()
            meta.pop('needs_relight', None)  # sólo tras verificar: si algo falla se reintenta la próxima vez
            self.save(library)
            if not running:
                self.runtime.stop()
        except BaseException:
            LOG.error('El cambio a %s no se verificó; se vuelve a %s.', target, current)
            try:
                self.runtime.stop()
            except Error:
                LOG.error('No se confirmó el cierre del mundo nuevo; se conserva el backup %s.', backup)
                raise
            self.undo(moves)
            write_data(self.data, 'server.properties', before)
            self.write_marker(current)
            library['active'] = current
            self.save(library)
            marker.unlink(missing_ok=True)
            if running:
                self.runtime.start(timeout)
            raise
        LOG.info('Mundo activo: %s (antes %s). Backup previo: %s', target, current, backup)

    def relight(self):
        """Mundos convertidos con Chunker traen la luz a cero: se recalcula cargando cada chunk."""
        script = (self.root / 'tools/relight.py').read_text()
        self.runtime.compose('exec', '-T', 'minecraft', 'python3', '-c', script, capture=True, timeout=3600)

    def refresh_map(self, moved_map):
        """Sin teselas guardadas BlueMap cree que el mapa está al día: se fuerza el render."""
        active = self.runtime.active()
        if moved_map or 'bluemap' not in active or 'bluemap' in active.get('disabled_plugins', []):
            return
        for map_id in ('world', 'world_the_nether', 'world_the_end'):
            self.runtime.exec_probe('rcon', 'bluemap', 'force-update', map_id, check=False)

    # --- Exportar y borrar ---------------------------------------------------------------
    def export(self, wid, fmt='java', client=None):
        library = self.load()
        if wid not in library['worlds']:
            raise Error('Ese mundo no existe.')
        exports = self.root / 'exports'
        exports.mkdir(mode=0o750, exist_ok=True)
        stamp = backups.stamp().replace('.', '-')
        if wid == library['active']:
            running = self.runtime.running()
            self.runtime.stop()
            self.runtime.assert_clean_stop()
            try:
                return self.pack(self.data / 'world', exports, wid, stamp, fmt, client)
            finally:
                if running:
                    self.runtime.start()
        if not real_dir(self.slot(wid) / 'world'):
            raise Error('Ese mundo todavía no se ha generado: actívalo una vez antes de exportarlo.')
        return self.pack(self.slot(wid) / 'world', exports, wid, stamp, fmt, client)

    def pack(self, world, exports, wid, stamp, fmt, client):
        if fmt not in ('java', 'bedrock'):
            raise Error('Formato de exportación no válido.')
        target = exports / f'{wid}-{stamp}.{"zip" if fmt == "java" else "mcworld"}'
        source = world
        staging = None
        try:
            if fmt == 'bedrock':
                staging = Path(tempfile.mkdtemp(prefix='export-', dir=self.runtime.state))
                source = self.convert_java(world, staging, client)
            temporary = target.with_suffix('.partial')
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for folder, directories, files in os.walk(source, followlinks=False):
                    directories.sort()
                    for name in sorted(files):
                        path = Path(folder) / name
                        if not stat.S_ISREG(os.lstat(path).st_mode):
                            raise Error(f'El mundo contiene un enlace o archivo especial: {name}')
                        archive.write(path, path.relative_to(source).as_posix())
            with zipfile.ZipFile(temporary) as check:
                if check.testzip() is not None:
                    raise Error('La exportación no superó la verificación del zip.')
            temporary.replace(target)
            target.chmod(0o640)
            (target.parent / (target.name + '.sha256')).write_text(f'{sha256(target)}  {target.name}\n')
            LOG.info('Mundo %s exportado: %s', wid, target)
            return target
        finally:
            if staging:
                shutil.rmtree(staging, ignore_errors=True)
            target.with_suffix('.partial').unlink(missing_ok=True)

    def convert_java(self, world, staging, client):
        jar = self.chunker(client)
        copy = staging / 'java'
        shutil.copytree(world, copy, symlinks=True)
        output = staging / 'out'
        output.mkdir(mode=0o700)
        for path in (copy, output):
            for folder, directories, files in os.walk(path):
                os.chown(folder, DATA_UID, DATA_UID)
                for item in files:
                    os.chown(os.path.join(folder, item), DATA_UID, DATA_UID, follow_symlinks=False)
        active = self.runtime.active()
        formats = run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'java', '-v', f'{jar}:/tools/chunker.jar:ro',
                       active['runtime_image'], '-jar', '/tools/chunker.jar', '-f', '?'], capture=True, check=False, timeout=300)
        bedrock = sorted(set(re.findall(r'BEDROCK_\d+_\d+_\d+', formats.stdout + formats.stderr)),
                         key=lambda f: [int(n) for n in f.split('_')[1:]])
        if not bedrock:
            raise Error('Chunker no ofrece formatos Bedrock.')
        run(['docker', 'run', '--rm', '--network', 'none', '--user', f'{DATA_UID}:{DATA_UID}', '--cap-drop', 'ALL',
             '--security-opt', 'no-new-privileges:true', '--memory', '1500m', '-e', 'JAVA_TOOL_OPTIONS=-Xmx1g',
             '--entrypoint', 'java', '-v', f'{jar}:/tools/chunker.jar:ro', '-v', f'{copy}:/in', '-v', f'{output}:/out',
             '-w', '/tmp', active['runtime_image'], '-jar', '/tools/chunker.jar', '-i', '/in', '-o', '/out/world', '-f', bedrock[-1]],
            capture=True, timeout=3600)
        return find_world_root(output / 'world' if (output / 'world').is_dir() else output)

    def delete(self, wid):
        """Nunca sin copia: primero un archivo verificado en backups/worlds, después se borra."""
        library = self.load()
        if wid not in library['worlds']:
            raise Error('Ese mundo no existe.')
        if wid == library['active']:
            raise Error('No se puede borrar el mundo activo: activa otro antes.')
        slot = self.slot(wid)
        archive = None
        if real_dir(slot):
            archives = self.root / 'backups/worlds'
            archives.mkdir(mode=0o700, parents=True, exist_ok=True)
            archive = self.pack(slot / 'world', archives, wid, backups.stamp().replace('.', '-'), 'java', None) \
                if real_dir(slot / 'world') else None
            shutil.rmtree(slot)
        del library['worlds'][wid]
        self.save(library)
        LOG.info('Mundo %s borrado de la biblioteca; copia: %s', wid, archive)
        return archive

    # --- Restaurar backups -----------------------------------------------------------------
    def restore_handoff(self, staged_data):
        """Antes de que restore sustituya data/: si el backup es de otro mundo, el activo se guarda en
        la biblioteca y una copia de biblioteca del mundo entrante se aparta, sin perder nada."""
        library = self.load()
        current = library['active']
        try:
            incoming = (staged_data / MARKER).read_text().strip()
        except OSError:
            incoming = 'main'
        if not ID.fullmatch(incoming):
            incoming = 'main'
        moves = []
        if incoming != current:
            self.swap_out(current, moves)
            parked = self.slot(incoming) / 'world'
            if real_dir(parked):
                aside = self.runtime.state / f'pre-restore-world-{incoming}-{backups.stamp().replace(".", "-")}'
                os.rename(parked, aside)
                moves.append((parked, aside))
                LOG.warning('La copia de biblioteca de %s se aparta en %s', incoming, aside)
        def commit():
            if incoming not in library['worlds']:
                library['worlds'][incoming] = {'name': incoming, 'created': now(), 'source': 'restored', 'settings': {}}
            library['active'] = incoming
            self.save(library)
        def undo():
            self.undo(moves)
        return commit, undo
