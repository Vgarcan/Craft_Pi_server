"""Primitivas de disco/procesos: sin shell=True, sin ejecutar configuración."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile

LOG = logging.getLogger("mclan")


class Error(RuntimeError):
    """Fallo operativo previsto que debe interrumpir la operación."""


def run(args, *, capture=False, check=True, timeout=1200, cwd=None):
    """Los argumentos se pasan separados; no se registran secretos de configuración."""
    LOG.info("Ejecutando: %s", args[0])
    environment = os.environ.copy()
    if str(args[0]) == 'docker':
        # Variables exportadas en la terminal no deben vencer al .env gestionado.
        for name in list(environment):
            if name in ('LAN_IP', 'RELEASE_ID', 'RUNTIME_IMAGE') or name.startswith('COMPOSE_'):
                del environment[name]
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=environment, text=True,
                            capture_output=capture, timeout=timeout, check=False)
    if check and result.returncode:
        # stderr de Docker puede incluir información de configuración: no volcarlo al log.
        raise Error(f"{args[0]} falló (código {result.returncode}). "
                    "Revisa su salida y la guía de diagnóstico.")
    return result


def atomic_write(path: Path, content: str, mode=0o600):
    """Reemplazo atómico dentro del mismo sistema de archivos."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".mclan-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def create_only(path, content, mode=0o600):
    """Nunca trunca una configuración preexistente ni sigue un enlace simbólico."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as out:
            out.write(content)
        path.chmod(mode)
        return True
    except FileExistsError:
        if path.is_symlink() or not path.is_file():
            raise Error(f"Ruta insegura: {path}")
        return False


def confirm(word, message):
    print(message)
    try:
        reply = input(f"Escribe {word} para continuar: ")
    except EOFError as exc:
        raise Error("Se requiere confirmación interactiva; operación cancelada.") from exc
    if reply != word:
        raise Error("Operación cancelada.")
