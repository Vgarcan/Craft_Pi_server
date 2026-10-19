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
# data/ es del usuario del contenedor: root lo lee sin seguir enlaces y con tope (mclan.safefs).
data_file() {
    python3 -I - "$PROJECT_DIR" "$1" 2>/dev/null <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + '/scripts')
from mclan.safefs import read_data
sys.stdout.write(read_data(Path(sys.argv[1]) / 'data', sys.argv[2], 256 * 1024).decode(errors='replace'))
EOF
}
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
for spec in tcp:25565 udp:19132 tcp:19132 udp:19133 tcp:8100 tcp:8101; do
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

section 'Plugins (arranque actual)'
# Estado de un plugin en la release activa: absent, off (desactivado por el operador) u on.
plugin_state() {
    python3 -I -c 'import json, sys; a = json.load(open("state/active.json")); k = sys.argv[1]
print("absent" if k not in a else "off" if k in a.get("disabled_plugins", []) else "on")' "$1"
}
if [[ ${started:-} ]]; then
    for pair in geyser:Geyser-Spigot floodgate:floodgate bluemap:BlueMap chunky:Chunky viaversion:ViaVersion \
                viabackwards:ViaBackwards mclan_skins:MclanSkins mclan_packs:MclanPacks; do
        key=${pair%%:*} name=${pair#*:} state="$(plugin_state "$key")"
        case $state in
            absent) ;;
            off) ok "$name desactivado por el operador" ;;
            *) expect fail "$name habilitado" "$name no se habilitó en este arranque" has "Enabling $name v" ;;
        esac
    done
    if [[ $(plugin_state mclan_skins) == on ]]; then
        expect warn 'MclanSkins enganchado a Geyser (skins Bedrock completas)' \
            'MclanSkins no puede modificar esta build de Geyser: Bedrock ve las skins de Geyser' lacks 'cannot be hooked'
    fi
fi
geyser_debug="$(data_file plugins/Geyser-Spigot/config.yml | sed -n 's/^debug-mode: *//p' || true)"
expect warn 'Geyser sin modo de depuración' 'Geyser en modo de depuración (debug-mode: true): llena el log' \
    test "$geyser_debug" != true

section 'Mapa BlueMap'
bluemap_state="$(plugin_state bluemap)"
if [[ $bluemap_state == off ]]; then
    ok 'BlueMap desactivado por el operador'
elif [[ $bluemap_state == on ]]; then
    expect fail "Web del mapa en http://$LAN_IP:8100/" "La web de BlueMap no responde en $LAN_IP:8100" \
        python3 -c 'import sys, urllib.request; sys.exit(urllib.request.urlopen(sys.argv[1], timeout=5).status != 200)' "http://$LAN_IP:8100/"
    if [[ ${started:-} ]]; then
        expect warn 'Recursos de Minecraft descargados por BlueMap' 'BlueMap no confirma la descarga de recursos (accept-download)' \
            lacks 'accept-download'
    fi
else
    warn 'La release activa no incluye BlueMap'
fi

section 'Paquetes de recursos'
packs_state="$(plugin_state mclan_packs)"
if [[ $packs_state == on ]]; then
    # Lo mismo que comprueba manage.sh verify: el puerto responde y cada variante Java llega con su SHA-1.
    packs_report="$(python3 -I - "$PROJECT_DIR" "$LAN_IP" 2>&1 <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + '/scripts')
from mclan.packs import Packs, served
from mclan.runtime import Runtime
current = Packs(Runtime(Path(sys.argv[1]))).selection()
problems = served(sys.argv[2], current)
print('\n'.join(problems) if problems else 'none' if not current else
      'ok ' + current['pack'] + ' Java ' + (', '.join(current['java']) or 'off') + ' Bedrock ' + ('on' if current['bedrock'] else 'off'))
EOF
)" || packs_report="packs/active.properties ilegible: $packs_report"
    case $packs_report in
        none) ok "Servidor de paquetes en http://$LAN_IP:8101/ (sin paquete elegido)" ;;
        ok\ *) ok "Paquete servido: ${packs_report#ok }" ;;
        *) warn "$packs_report" ;;
    esac
    if [[ ${started:-} ]]; then
        expect warn 'MclanPacks leyó la selección' 'MclanPacks no pudo leer packs/active.properties' lacks 'invalid pack selection'
    fi
elif [[ $packs_state == absent ]]; then
    warn 'La release activa no incluye MclanPacks (sudo ./manage.sh update)'
fi

section 'Panel web'
panel_port_ok() {
    grep -Eq "^tcp .* $LAN_IP:8443 " <<<"$listening" && ! grep -Eq '^tcp .* (0\.0\.0\.0|\*|\[::\]):8443 ' <<<"$listening"
}
if [[ -f /etc/systemd/system/mclan-panel.service ]]; then
    expect fail 'Servicio mclan-panel activo' 'Servicio mclan-panel inactivo (journalctl -u mclan-panel)' systemctl is-active --quiet mclan-panel
    expect fail "Panel sólo en https://$LAN_IP:8443/" 'El panel no escucha sólo en la IP LAN' panel_port_ok
    expect fail 'Regla sudo del panel limitada al helper' 'La regla sudo del panel no es la gestionada' \
        grep -qx "mclan-panel ALL=(root) NOPASSWD: /usr/bin/python3 -I $PROJECT_DIR/panel/helper.py \\*" /etc/sudoers.d/mclan-panel
    if [[ -f /etc/mclan-panel/data/initial-password || -f /etc/mclan-panel/initial-password ]]; then
        warn 'El panel conserva la contraseña inicial: cámbiala en Account'
    fi
    textures="$(find /etc/mclan-panel/assets -maxdepth 1 -name '*.png' 2>/dev/null | wc -l)"
    expect warn "Texturas del panel: $textures" 'Panel sin texturas: repite sudo panel/install.sh con el servidor en marcha' \
        test "$textures" -ge 100
    expect warn 'Catálogo de objetos del panel' 'Panel sin catálogo de objetos: repite sudo panel/install.sh' \
        test -s /etc/mclan-panel/assets/items.json
else
    warn 'Panel web no instalado (sudo panel/install.sh)'
fi

section 'Estado gestionado'
expect fail 'Sin operaciones pendientes' 'Operación incompleta: state/operation.json' test ! -e state/operation.json
attention="$(./manage.sh status 2>/dev/null | grep 'ATENCIÓN' || true)"
if [[ -z $attention ]]; then ok 'manage.sh status sin avisos'
else while read -r line; do warn "${line#ATENCIÓN: }"; done <<<"$attention"; fi
whitelist="$(data_file server.properties | sed -n 's/^white-list=//p' | tr -d '\r')"
entries="$(data_file whitelist.json | python3 -I -c 'import json, sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)"
if [[ $whitelist != true ]]; then warn 'Whitelist desactivada: cualquier cuenta válida de la LAN puede entrar'
elif [[ $entries == 0 ]]; then warn 'Whitelist activa y vacía: nadie puede entrar'
else ok "Whitelist activa con $entries jugador(es)"; fi

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
latest="$(find backups -maxdepth 1 -name '*.tar.gz' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2- || true)"
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
