"""Acceso a data/ sin seguir enlaces.

data/ pertenece al usuario del contenedor (UID 10000): un plugin o servidor comprometido puede
sustituir cualquier archivo o carpeta por un enlace, un FIFO o algo enorme. El código que corre como
root nunca sigue enlaces en ningún componente de la ruta, sólo lee archivos regulares y con tope.
"""
import gzip
import os
from pathlib import Path
import stat

DATA_UID = 10000
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


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
