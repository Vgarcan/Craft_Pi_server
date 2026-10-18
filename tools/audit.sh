#!/usr/bin/env bash
# Auditoría de sólo lectura del host y del servidor. Uso: sudo tools/audit.sh [--verify]
# --verify añade manage.sh verify (genera y borra un diagnóstico offline de Geyser).
# Sale con 1 si alguna comprobación da FALLO; los AVISO no cambian el código de salida.
set -Eeuo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$PROJECT_DIR"
[[ $EUID -eq 0 ]] || { echo 'Ejecuta con sudo.' >&2; exit 2; }
[[ -f .env && -f state/active.json ]] || { echo 'No hay instalación activa en este directorio.' >&2; exit 2; }

failures=0
ok() { printf 'OK     %s\n' "$*"; }
warn() { printf 'AVISO  %s\n' "$*"; }
fail() { printf 'FALLO  %s\n' "$*"; failures=$((failures + 1)); }
section() { printf '\n== %s\n' "$*"; }
# expect NIVEL MENSAJE_OK MENSAJE_MAL COMANDO...: NIVEL es fail o warn.
expect() {
    local level=$1 good=$2 bad=$3
    shift 3
    if "$@"; then ok "$good"; else "$level" "$bad"; fi
}
has() { grep -q -- "$1" <<<"$log"; }
lacks() { ! has "$1"; }

env_value() { sed -n "s/^$1=//p" .env; }
LAN_IP="$(env_value LAN_IP)"
IMAGE="$(env_value RUNTIME_IMAGE)"
CONTAINER=minecraft-lan-minecraft-1

section 'Arranque del host'
boot="$(uptime -s)"
ok "Encendido desde $boot ($(uptime -p))"
target="$(systemctl get-default)"
if [[ $target == multi-user.target ]]; then ok 'Arranque sin escritorio (multi-user.target)'; else warn "Arranque por defecto: $target"; fi
if pgrep -x labwc >/dev/null || pgrep -x lightdm >/dev/null; then warn 'Hay una sesión gráfica en marcha'; else ok 'Sin sesión gráfica'; fi

section 'Límite de memoria'
if grep -qw memory /sys/fs/cgroup/cgroup.controllers; then ok 'cgroup de memoria activo'; else fail 'cgroup de memoria desactivado: mem_limit no se aplica'; fi
limit="$(docker inspect -f '{{.HostConfig.Memory}}' "$CONTAINER" 2>/dev/null || echo 0)"
usage="$(docker stats --no-stream --format '{{.MemUsage}}' "$CONTAINER" 2>/dev/null || true)"
if [[ $limit == 3221225472 && $usage != '0B / 0B' && -n $usage ]]; then ok "Límite del contenedor aplicado: $usage"
else fail "Límite del contenedor no efectivo (Memory=$limit, uso=${usage:-?})"; fi

section 'Contenedor'
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    fail 'No existe el contenedor del servidor'
else
    IFS='|' read -r status health restarts image policy started < <(docker inspect -f \
        '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}|{{.Config.Image}}|{{.HostConfig.RestartPolicy.Name}}|{{.State.StartedAt}}' "$CONTAINER")
    expect fail 'En marcha' "Estado: $status" test "$status" = running
    expect fail 'Salud: healthy' "Salud: ${health:-sin healthcheck}" test "$health" = healthy
    expect warn 'Sin reinicios del contenedor' "RestartCount=$restarts" test "$restarts" = 0
    expect fail "Imagen activa $image" "Imagen $image distinta de .env ($IMAGE)" test "$image" = "$IMAGE"
    expect warn 'Política de reinicio unless-stopped' "Política de reinicio: $policy" test "$policy" = unless-stopped
    if (( $(date -d "$started" +%s) >= $(date -d "$boot" +%s) )); then ok "Arrancado en este encendido ($started)"
    else warn "Arrancado antes del último encendido ($started)"; fi
fi

section 'Puertos (sólo IP LAN)'
listening="$(ss -Htulpn)"
for spec in tcp:25565 udp:19132 tcp:19132 udp:19133; do
    proto=${spec%%:*} port=${spec##*:}
    if grep -Eq "^$proto .* $LAN_IP:$port " <<<"$listening"; then ok "$proto/$port en $LAN_IP"; else fail "$proto/$port no escucha en $LAN_IP"; fi
    if grep -Eq "^$proto .* (0\.0\.0\.0|\*|\[::\]):$port " <<<"$listening"; then fail "$proto/$port publicado en todas las interfaces"; fi
done
if grep -Eq ':25575 ' <<<"$listening"; then fail 'RCON 25575 visible en el host'; else ok 'RCON no publicado en el host'; fi

section 'Geyser y NetherNet (arranque actual)'
if [[ ${started:-} ]]; then
    log="$(docker logs --since "$started" "$CONTAINER" 2>&1 || true)"
    expect fail 'Transporte both (RakNet + NetherNet)' 'Transporte both no confirmado' has 'Transport set from system property: both'
    expect fail 'Signaling NetherNet iniciado' 'Signaling NetherNet no iniciado' has 'Built-in signaling started on'
    expect fail 'Sin aviso de NetherNet desactivado' 'Geyser avisa de NetherNet desactivado' lacks 'NetherNet support is NOT enabled'
    expect fail 'Geyser con acceso a servicios de Microsoft' 'Geyser sin cifrado: revisa la salida a Internet' lacks 'Unable to set up encryption'
fi

section 'Estado gestionado'
expect fail 'Sin operaciones pendientes' 'Operación incompleta: state/operation.json' test ! -e state/operation.json
attention="$(./manage.sh status 2>/dev/null | grep 'ATENCIÓN' || true)"
if [[ -z $attention ]]; then ok 'manage.sh status sin avisos'
else while read -r line; do warn "${line#ATENCIÓN: }"; done <<<"$attention"; fi
whitelist="$(sed -n 's/^white-list=//p' data/server.properties)"
entries="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' data/whitelist.json)"
if [[ $whitelist == true && $entries == 0 ]]; then warn 'Whitelist activa y vacía: nadie puede entrar'
else ok "white-list=$whitelist, $entries jugador(es) en whitelist.json"; fi

section 'Recursos'
available=$(( $(sed -n 's/^MemAvailable: *\([0-9]*\).*/\1/p' /proc/meminfo) / 1024 ))
swap_used=$(( ($(sed -n 's/^SwapTotal: *\([0-9]*\).*/\1/p' /proc/meminfo) - $(sed -n 's/^SwapFree: *\([0-9]*\).*/\1/p' /proc/meminfo)) / 1024 ))
expect warn "Memoria disponible ${available} MiB" "Memoria disponible baja: ${available} MiB" test "$available" -ge 1200
expect warn "Swap en uso ${swap_used} MiB" "Swap en uso ${swap_used} MiB" test "$swap_used" -lt 512
if command -v vcgencmd >/dev/null; then
    temp="$(vcgencmd measure_temp | sed 's/[^0-9.]//g')" throttled="$(vcgencmd get_throttled | cut -d= -f2)"
    expect warn "Temperatura ${temp} °C" "Temperatura ${temp} °C" test "${temp%.*}" -lt 70
    expect warn 'Sin throttling ni subtensión' "get_throttled=$throttled" test "$throttled" = 0x0
fi
free_gib="$(df -BG --output=avail . | tail -1 | tr -dc 0-9)"
expect fail "Disco libre ${free_gib} GiB" "Disco libre ${free_gib} GiB (<8)" test "$free_gib" -ge 8

section 'Backups'
latest="$(find backups -maxdepth 1 -name '*.tar.gz' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
if [[ -z $latest ]]; then warn 'No hay backups gestionados'
else
    age=$(( ($(date +%s) - $(stat -c %Y "$latest")) / 86400 ))
    count="$(find backups -maxdepth 1 -name '*.tar.gz' | wc -l)"
    ok "$count backup(s); el último tiene $age día(s): $latest"
    if (( age > 7 )); then warn 'El último backup tiene más de 7 días'; fi
fi
warn 'Recuerda: los backups de backups/ y /var/backups/craft-pi están en la misma microSD'

if [[ ${1:-} == --verify ]]; then
    section 'Verificación de protocolos'
    if ./manage.sh verify >/dev/null 2>&1; then ok 'manage.sh verify correcto'; else fail 'manage.sh verify falló (ver logs/operations.log)'; fi
fi

printf '\nResultado: %s\n' "$([[ $failures == 0 ]] && echo 'sin fallos' || echo "$failures fallo(s)")"
(( failures == 0 ))
