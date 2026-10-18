#!/usr/bin/env bash
# No depende del directorio desde el que se invoque ni evalúa archivos .env.
set -Eeuo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec python3 -I "$PROJECT_DIR/scripts/main.py" manage "$@"
