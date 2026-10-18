#!/usr/bin/env bash
# Entrada pequeña: la lógica comprobable vive en Python (biblioteca estándar).
set -Eeuo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Falta Python 3. Instálalo: sudo apt-get update && sudo apt-get install python3' >&2
    exit 1
fi
exec python3 -I "$PROJECT_DIR/scripts/main.py" install "$@"
