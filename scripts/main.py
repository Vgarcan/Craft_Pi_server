#!/usr/bin/env python3
"""Punto de entrada compartido para los dos scripts Bash.

Se ejecuta con python3 -I (sin PYTHONPATH ni directorio actual en la ruta de imports, porque corre
como root), así que el paquete se añade de forma explícita desde la ubicación de este archivo."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mclan.cli import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
