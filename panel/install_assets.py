"""Valida en el host el tar de texturas que produce extract_assets.py y lo instala.

El tar procede del contenedor, que es menos fiable que el host: sólo se aceptan nombres de la lista
cerrada, archivos regulares, firma PNG y un tamaño acotado. Uso (root, desde install.sh):
    python3 -I panel/install_assets.py DESTINO PROPIETARIO < assets.tar
"""
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import sys
import tarfile

PNG = b'\x89PNG\r\n\x1a\n'
LIMIT = 256 * 1024
ICON = re.compile(r'(item|block|effect)_[a-z0-9_]{1,64}\.png')
ITEM_ID = re.compile(r'[a-z0-9_]{1,64}')
CATALOG, CATALOG_LIMIT, MAX_ITEMS = 'items.json', 1024 * 1024, 4000
MAX_FILES, MAX_TOTAL = 6000, 40 * 1024 * 1024


def allowed_names():
    spec = importlib.util.spec_from_file_location('extract_assets', Path(__file__).with_name('extract_assets.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.ASSETS)


def valid_catalog(payload):
    """items.json: {"items": {id: nombre}} con ids [a-z0-9_] y nombres imprimibles cortos."""
    try:
        items = json.loads(payload.decode('utf-8'))['items']
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return False
    return isinstance(items, dict) and 0 < len(items) <= MAX_ITEMS and all(
        isinstance(k, str) and ITEM_ID.fullmatch(k) and isinstance(v, str) and 0 < len(v) <= 80
        and all(ord(c) >= 32 and c != '\x7f' for c in v) for k, v in items.items())


def install(stream, destination, uid=None, gid=None):
    allowed = allowed_names()
    destination.mkdir(mode=0o755, exist_ok=True)
    installed, total = [], 0
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            total += max(member.size, 0)
            if len(installed) >= MAX_FILES or total > MAX_TOTAL:
                raise ValueError('El tar de texturas es desproporcionado')
            catalog = member.name == CATALOG
            if (member.name not in allowed and not ICON.fullmatch(member.name) and not catalog) or not member.isfile() \
                    or not 0 < member.size <= (CATALOG_LIMIT if catalog else LIMIT):
                raise ValueError(f'Entrada no admitida en el tar de texturas: {member.name!r}')
            payload = archive.extractfile(member).read((CATALOG_LIMIT if catalog else LIMIT) + 1)
            if len(payload) != member.size or not (valid_catalog(payload) if catalog else payload.startswith(PNG)):
                raise ValueError(f'{member.name} no es válido')
            temporary = destination / f'.{member.name}.new'
            temporary.write_bytes(payload)
            temporary.chmod(0o644)
            if uid is not None:
                os.chown(temporary, uid, gid)
            temporary.replace(destination / member.name)
            installed.append(member.name)
    for stale in [*destination.glob('*.png'), *destination.glob(CATALOG)]:
        if stale.name not in installed:
            stale.unlink()
    return sorted(installed)


if __name__ == '__main__':
    owner = pwd.getpwnam(sys.argv[2])
    names = install(sys.stdin.buffer, Path(sys.argv[1]), owner.pw_uid, owner.pw_gid)
    print(f'{len(names)} texturas instaladas en {sys.argv[1]}')
