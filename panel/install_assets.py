"""Valida en el host el tar de texturas que produce extract_assets.py y lo instala.

El tar procede del contenedor, que es menos fiable que el host: sólo se aceptan nombres de la lista
cerrada, archivos regulares, firma PNG y un tamaño acotado. Uso (root, desde install.sh):
    python3 -I panel/install_assets.py DESTINO PROPIETARIO < assets.tar
"""
import importlib.util
import os
from pathlib import Path
import pwd
import sys
import tarfile

PNG = b'\x89PNG\r\n\x1a\n'
LIMIT = 256 * 1024


def allowed_names():
    spec = importlib.util.spec_from_file_location('extract_assets', Path(__file__).with_name('extract_assets.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.ASSETS)


def install(stream, destination, uid=None, gid=None):
    allowed = allowed_names()
    destination.mkdir(mode=0o755, exist_ok=True)
    installed = []
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            if member.name not in allowed or not member.isfile() or not 0 < member.size <= LIMIT:
                raise ValueError(f'Entrada no admitida en el tar de texturas: {member.name!r}')
            payload = archive.extractfile(member).read(LIMIT + 1)
            if not payload.startswith(PNG) or len(payload) != member.size:
                raise ValueError(f'{member.name} no es un PNG válido')
            temporary = destination / f'.{member.name}.new'
            temporary.write_bytes(payload)
            temporary.chmod(0o644)
            if uid is not None:
                os.chown(temporary, uid, gid)
            temporary.replace(destination / member.name)
            installed.append(member.name)
    for stale in destination.glob('*.png'):
        if stale.name not in installed:
            stale.unlink()
    return sorted(installed)


if __name__ == '__main__':
    owner = pwd.getpwnam(sys.argv[2])
    names = install(sys.stdin.buffer, Path(sys.argv[1]), owner.pw_uid, owner.pw_gid)
    print(f'{len(names)} texturas instaladas en {sys.argv[1]}')
