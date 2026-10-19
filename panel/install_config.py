"""Escrituras de root en la configuración del panel sin seguir enlaces. Las usa panel/install.sh.

/etc/mclan-panel es de root. Sólo /etc/mclan-panel/data pertenece al usuario del panel, que reescribe
ahí panel.json al cambiar la contraseña y borra la contraseña inicial. Root nunca abre ni cambia nada
de esa carpeta por su ruta: usa un descriptor del directorio, O_NOFOLLOW, temporales con O_EXCL y
renombra dentro del directorio. Así un panel comprometido no puede redirigir una escritura de root.

    python3 -I panel/install_config.py config DIR_CONFIG USUARIO < cambios.json
    python3 -I panel/install_config.py initial DIR_CONFIG < contraseña
    python3 -I panel/install_config.py drop-initial DIR_CONFIG

cambios.json: {"set": {clave: valor, ...}, "password": registro o null}.
"""
import json
import os
import pwd
import secrets
import stat
import sys

LIMIT = 64 * 1024
CONFIG, INITIAL = 'panel.json', 'initial-password'


class Unsafe(Exception):
    pass


def open_dir(path):
    try:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise Unsafe(f'{path} no es un directorio normal ({exc.strerror})') from exc


def read(dir_fd, name, owner=None):
    """Contenido de un archivo regular (o None si no existe). Un enlace o un archivo raro es un error."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Unsafe(f'{name} no es un archivo normal ({exc.strerror})') from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT or (owner is not None and info.st_uid != owner):
            raise Unsafe(f'{name} no es un archivo normal de confianza')
        return os.read(fd, LIMIT + 1)[:LIMIT]
    finally:
        os.close(fd)


def write(dir_fd, name, data, uid, gid, mode):
    temporary = f'.{name}.{secrets.token_hex(6)}'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=dir_fd)
    try:
        os.write(fd, data)
        os.fchmod(fd, mode)
        os.fchown(fd, uid, gid)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        os.unlink(temporary, dir_fd=dir_fd)
        raise
    os.close(fd)
    os.replace(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)


def unlink(dir_fd, name):
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        pass


def configure(base, user, changes):
    """Actualiza data/panel.json. Migra panel.json e initial-password de la organización anterior."""
    owner = pwd.getpwnam(user)
    root_fd, data_fd = open_dir(base), open_dir(os.path.join(base, 'data'))
    try:
        current = read(data_fd, CONFIG)
        legacy = read(root_fd, CONFIG)
        config = json.loads(current or legacy or b'{}')
        if not isinstance(config, dict):
            raise Unsafe('panel.json no contiene un objeto JSON')
        config.update(changes.get('set', {}))
        if changes.get('password'):
            config['password'] = changes['password']
        write(data_fd, CONFIG, (json.dumps(config, indent=2) + '\n').encode(), owner.pw_uid, owner.pw_gid, 0o600)
        old_initial = read(root_fd, INITIAL, owner=0)
        if old_initial is not None and read(data_fd, INITIAL) is None and not changes.get('password'):
            write(data_fd, INITIAL, old_initial, 0, 0, 0o600)
        for name in (CONFIG, INITIAL):
            unlink(root_fd, name)  # restos de la organización anterior
    finally:
        os.close(root_fd)
        os.close(data_fd)


def main(argv):
    command, base = argv[0], argv[1]
    if command == 'config':
        configure(base, argv[2], json.loads(sys.stdin.read()))
    elif command in ('initial', 'drop-initial'):
        data_fd = open_dir(os.path.join(base, 'data'))
        try:
            if command == 'initial':
                write(data_fd, INITIAL, sys.stdin.buffer.read(256), 0, 0, 0o600)
            else:
                unlink(data_fd, INITIAL)
        finally:
            os.close(data_fd)
    else:
        raise SystemExit(f'Orden desconocida: {command}')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (Unsafe, ValueError) as exc:
        sys.exit(f'Configuración del panel rechazada: {exc}')
