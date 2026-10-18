"""Plugins propios del proyecto, compilados en la Pi a partir de addons/ (sin descargar JARs de terceros).

Se compilan contra las librerías exactas de la release (paper-api y dependencias incluidas en paper.jar,
y la API de Floodgate) en un contenedor JDK fijado por digest y sin red. El hash del código forma parte de
la identidad de la release: cambiar un addon produce una release nueva y `update` lo aplica con backup.
"""
import hashlib
from pathlib import Path
import re
import shutil
import zipfile
from .common import Error, run

ROOT = Path(__file__).resolve().parents[2]
# Clave del lock → (carpeta en addons/, JAR en releases/<id>/ y data/plugins).
ADDONS = {"mclan_skins": ("mclan-skins", "MclanSkins.jar")}
LIBRARY = re.compile(r"META-INF/libraries/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+\.jar")
# Plugins de la release que cada addon necesita para compilar (además de paper-api).
CLASSPATH = {"mclan_skins": ("floodgate-spigot.jar", "Geyser-Spigot.jar")}


def source_files(folder):
    files = sorted(p for p in folder.rglob('*') if not p.is_dir())
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise Error(f'addons/{folder.name}: sólo se admiten archivos normales ({path.relative_to(folder)}).')
    return files


def source_digest(folder):
    digest = hashlib.sha256()
    for path in source_files(folder):
        digest.update(path.relative_to(folder).as_posix().encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def describe(root=ROOT):
    """Entradas del lock: versión declarada en plugin.yml y hash del código."""
    entries = {}
    for key, (folder, _) in ADDONS.items():
        path = root / 'addons' / folder
        match = re.search(r'^version:\s*([0-9]+(?:\.[0-9]+)+)\s*$', (path / 'plugin.yml').read_text(), re.M)
        if not match:
            raise Error(f'addons/{folder}/plugin.yml no declara una versión válida.')
        entries[key] = {"version": match[1], "source_sha256": source_digest(path), "build": "local"}
    return entries


def build(root, release, lock, key):
    """Compila el addon en releases/<id>/<JAR> y devuelve su ruta. Requiere lock['jdk_base']."""
    folder, name = ADDONS[key]
    source = root / 'addons' / folder
    if source_digest(source) != lock[key]['source_sha256']:
        raise Error(f'El código de addons/{folder} cambió durante la actualización. Vuelve a intentarlo.')
    work = release / f'.build-{key}'
    shutil.rmtree(work, ignore_errors=True)
    try:
        classpath, src, out = work / 'cp', work / 'src', work / 'out'
        for folder_path in (classpath, src, out):
            folder_path.mkdir(parents=True)
        with zipfile.ZipFile(release / 'paper.jar') as jar:
            libraries = [n for n in jar.namelist() if LIBRARY.fullmatch(n)]
            if not any('/paper-api-' in n for n in libraries):
                raise Error('paper.jar no incluye paper-api; no se puede compilar el addon.')
            for index, member in enumerate(libraries):
                (classpath / f'{index:03d}-{member.rsplit("/", 1)[1]}').write_bytes(jar.read(member))
        for dependency in CLASSPATH[key]:
            shutil.copyfile(release / dependency, classpath / dependency)
        for path in source_files(source):
            target = src / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        java = [p.relative_to(src).as_posix() for p in sorted(src.rglob('*.java'))]
        resources = [n for n in ('plugin.yml', 'config.yml') if (src / n).exists()]
        script = ('set -eu; cd /src; javac --release 21 -proc:none -Xlint:-options -cp "/cp/*" -d /out/classes '
                  + ' '.join(java) + '; cp ' + ' '.join(resources) + ' /out/classes/; '
                  'jar --create --file /out/addon.jar -C /out/classes .')
        if any(not re.fullmatch(r'[A-Za-z0-9_./-]+', n) for n in java + resources):
            raise Error(f'addons/{folder}: nombres de archivo no admitidos.')
        run(['docker', 'run', '--rm', '--network', 'none', '--platform', 'linux/arm64',
             '-v', f'{classpath}:/cp:ro', '-v', f'{src}:/src:ro', '-v', f'{out}:/out',
             lock['jdk_base'], 'sh', '-c', script], timeout=600)
        with zipfile.ZipFile(out / 'addon.jar') as jar:
            if 'plugin.yml' not in jar.namelist():
                raise Error(f'{name} no contiene plugin.yml.')
        target = release / name
        target.unlink(missing_ok=True)
        shutil.move(out / 'addon.jar', target)
        target.chmod(0o444)
        return target
    finally:
        shutil.rmtree(work, ignore_errors=True)
