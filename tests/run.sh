#!/usr/bin/env bash
# No instala dependencias ni modifica el host. --strict exige también ShellCheck.
set -Eeuo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$PROJECT_DIR"
for script in install.sh manage.sh docker/entrypoint.sh docker/bluemap-setup.sh tests/run.sh tools/audit.sh panel/install.sh; do
    bash -n "$script"
done
python3 -m compileall -q scripts docker tests panel tools
python3 -m unittest discover -s tests -v
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    python3 tests/check_compose.py
else
    echo 'Plugin Compose ausente: validación real pendiente del host o de un binario independiente.'
fi
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck install.sh manage.sh docker/entrypoint.sh docker/bluemap-setup.sh tests/run.sh tools/audit.sh panel/install.sh
elif [[ "${1:-}" == --strict ]]; then
    echo 'Falta ShellCheck (sudo apt-get install shellcheck).' >&2
    exit 1
else
    echo 'ShellCheck no disponible: comprobación omitida.'
fi
