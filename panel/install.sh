#!/usr/bin/env bash
# Instala o actualiza el panel web en la IP LAN de la instalación activa.
# Uso: sudo panel/install.sh [--reset-password | --uninstall]
# Repetirlo tras manage.sh rebind actualiza IP y certificado; conserva la contraseña.
set -Eeuo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
USER_NAME=mclan-panel
CONFIG_DIR=/etc/mclan-panel
UNIT=/etc/systemd/system/mclan-panel.service
SUDOERS=/etc/sudoers.d/mclan-panel
PORT=8443

[[ $EUID -eq 0 ]] || { echo 'Ejecuta con sudo.' >&2; exit 1; }
mode="${1:-install}"

if [[ $mode == --uninstall ]]; then
    systemctl disable --now mclan-panel.service 2>/dev/null || true
    rm -f "$UNIT" "$SUDOERS"
    systemctl daemon-reload
    echo "Panel retirado. Se conservan $CONFIG_DIR y el usuario $USER_NAME; bórralos a mano si no los necesitas."
    exit 0
fi
[[ $mode == install || $mode == --reset-password ]] || { echo "Opción desconocida: $mode" >&2; exit 1; }

[[ -f $PROJECT_DIR/state/active.json && -f $PROJECT_DIR/.env ]] || { echo 'No hay instalación activa del servidor.' >&2; exit 1; }
LAN_IP="$(sed -n 's/^LAN_IP=//p' "$PROJECT_DIR/.env")"
[[ $LAN_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'LAN_IP inválida en .env.' >&2; exit 1; }

# La regla sudo es tan segura como la imposibilidad de modificar lo que ejecuta: el helper y todo
# el código que importa deben pertenecer a root y no admitir escritura de grupo u otros.
check_root_owned() {
    local path=$1
    while [[ $path != / ]]; do
        read -r owner mode < <(stat -c '%u %a' "$path")
        if [[ $owner != 0 ]] || (( 8#$mode & 8#022 )); then
            echo "Inseguro para sudo: $path debe ser de root y sin escritura de grupo/otros." >&2
            exit 1
        fi
        path="$(dirname -- "$path")"
    done
}
check_root_owned "$PROJECT_DIR/panel/helper.py"
while IFS= read -r -d '' file; do check_root_owned "$file"; done \
    < <(find "$PROJECT_DIR/scripts" "$PROJECT_DIR/panel" "$PROJECT_DIR/tools" "$PROJECT_DIR/addons" \
             "$PROJECT_DIR/manage.sh" -print0)

if ! id "$USER_NAME" >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$USER_NAME"
fi

# /etc/mclan-panel es de root: certificado, clave (legible por el grupo del panel) y texturas. El panel
# sólo puede escribir en data/ (panel.json y la contraseña inicial), y root no abre nada ahí por su
# ruta (panel/install_config.py). Así un panel comprometido no puede redirigir con un enlace una
# escritura o un chown de este script, que corre como root.
[[ ! -L $CONFIG_DIR ]] || { echo "$CONFIG_DIR no puede ser un enlace." >&2; exit 1; }
mkdir -p "$CONFIG_DIR"
chown -h "root:$USER_NAME" "$CONFIG_DIR"
chmod 0750 "$CONFIG_DIR"
DATA_DIR="$CONFIG_DIR/data"
[[ ! -L $DATA_DIR ]] || rm -f "$DATA_DIR"
mkdir -p "$DATA_DIR"
chown -h "$USER_NAME:$USER_NAME" "$DATA_DIR"
chmod 0700 "$DATA_DIR"
# Organización anterior (hasta la 0.8.1): todo era del usuario del panel. Certificado y clave se
# conservan si son archivos regulares (el panel ya podía leer la clave: regenerarlos no añade nada y
# obligaría a aceptar otra huella); un enlace u otra cosa se descarta y se regenera. Las texturas se
# vuelven a extraer como root.
for leftover in tls.crt tls.key; do
    path="$CONFIG_DIR/$leftover"
    if [[ -L $path || ( -e $path && ! -f $path ) ]]; then rm -f "$path"; fi
done
if [[ -L $CONFIG_DIR/assets || ( -e $CONFIG_DIR/assets && $(stat -c %u "$CONFIG_DIR/assets") != 0 ) ]]; then
    rm -rf --one-file-system "${CONFIG_DIR:?}/assets"
fi

# Certificado autofirmado para la IP actual. Se renueva si cambió la IP o si clave y certificado no casan.
cert="$CONFIG_DIR/tls.crt" key="$CONFIG_DIR/tls.key"
matching() {
    [[ -f $cert && -f $key ]] && [[ "$(openssl x509 -in "$cert" -noout -pubkey 2>/dev/null)" == "$(openssl pkey -in "$key" -pubout 2>/dev/null)" ]]
}
if ! matching || ! openssl x509 -in "$cert" -noout -ext subjectAltName 2>/dev/null | grep -q "IP Address:$LAN_IP\$"; then
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 825 -subj '/CN=Craft Pi Server' \
        -addext "subjectAltName=IP:$LAN_IP" -keyout "$key.new" -out "$cert.new" 2>/dev/null
    mv -f "$key.new" "$key"
    mv -f "$cert.new" "$cert"
fi
chown -h "root:$USER_NAME" "$cert" "$key"
chmod 0640 "$key"
chmod 0644 "$cert"

# Texturas de Minecraft y catálogo de objetos para el panel: se extraen DENTRO del contenedor del
# cliente que BlueMap descargó de Mojang, y el host sólo acepta la lista cerrada. No se suben al
# repositorio. En una instalación nueva BlueMap descarga el cliente unos minutos después del primer
# arranque: se espera hasta MCLAN_ASSETS_WAIT segundos (600 por defecto) si el servidor está en marcha.
compose() {
    (cd "$PROJECT_DIR" && docker compose --project-name minecraft-lan --project-directory . --env-file .env -f compose.yaml "$@")
}
client_ready() {
    compose exec -T minecraft python3 -c \
        'import glob, sys; sys.exit(0 if glob.glob("/data/bluemap/minecraft-client-*.jar") else 1)' >/dev/null 2>&1
}
running="$(compose ps --status running --quiet minecraft 2>/dev/null || true)"
bluemap="$(python3 -I -c 'import json, sys; a = json.load(open(sys.argv[1]))
print("on" if "bluemap" in a and "bluemap" not in a.get("disabled_plugins", []) else "off")' "$PROJECT_DIR/state/active.json")"
wait_seconds="${MCLAN_ASSETS_WAIT:-600}"
[[ $wait_seconds =~ ^(0|[1-9][0-9]{0,4})$ ]] || wait_seconds=600
if [[ -n $running && $bluemap == on ]] && ! client_ready; then
    if (( wait_seconds >= 60 )); then limit="$((wait_seconds / 60)) min"; else limit="$wait_seconds s"; fi
    echo "Esperando a que BlueMap descargue el cliente de Minecraft para las texturas y el catálogo de objetos (máximo $limit)..."
    for ((waited = 0; waited < wait_seconds; waited += 10)); do
        client_ready && break
        sleep 10
    done
fi
assets="$CONFIG_DIR/assets"
install -d -m 0755 -o root -g root "$assets"
tarball="$(mktemp "$CONFIG_DIR/.assets.XXXXXX")"
trap 'rm -f "$tarball"' EXIT
if [[ -n $running ]] && compose exec -T minecraft python3 - < "$PROJECT_DIR/panel/extract_assets.py" > "$tarball" 2>/dev/null \
   && python3 -I "$PROJECT_DIR/panel/install_assets.py" "$assets" < "$tarball"; then
    :
else
    if [[ -z $running ]]; then reason='el servidor está parado'
    elif [[ $bluemap == off ]]; then reason='BlueMap está desactivado (sudo ./manage.sh plugin bluemap on)'
    else reason='BlueMap aún no ha descargado el cliente de Minecraft'; fi
    echo "Aviso: sin texturas ni catálogo de objetos porque $reason. El panel funciona con su estilo CSS;" >&2
    echo '       repite sudo panel/install.sh más tarde para añadirlos (conserva contraseña y certificado).' >&2
fi

# Biblioteca de mundos: imports/ la escriben el administrador (por ejemplo arrastrando desde VS Code)
# y el panel (subidas desde la web), por eso es del grupo mclan-panel con setgid; root sólo la lee y
# rechaza enlaces. exports/ la lee el panel para ofrecer las descargas.
install -d -m 2775 -o "${SUDO_USER:-root}" -g "$USER_NAME" "$PROJECT_DIR/imports"
install -d -m 2750 -o root -g "$USER_NAME" "$PROJECT_DIR/exports"
find "$PROJECT_DIR/exports" -maxdepth 1 -type f -exec chgrp "$USER_NAME" {} + -exec chmod 0640 {} +

config="$DATA_DIR/panel.json"
password_record=''
if [[ ! -e $config && ! -e $CONFIG_DIR/panel.json ]] || [[ $mode == --reset-password ]]; then
    if [[ $mode == --reset-password && -t 0 ]]; then
        read -r -s -p 'Nueva contraseña (mínimo 12 caracteres): ' first; echo
        read -r -s -p 'Repítela: ' second; echo
        [[ $first == "$second" ]] || { echo 'No coinciden.' >&2; exit 1; }
        password_record="$(printf '%s\n' "$first" | python3 -I "$PROJECT_DIR/panel/server.py" hash-password)"
    else
        initial="$(python3 -I -c 'import secrets; print(secrets.token_urlsafe(15))')"
        password_record="$(printf '%s\n' "$initial" | python3 -I "$PROJECT_DIR/panel/server.py" hash-password)"
    fi
fi
CERT="$cert" KEY="$key" BIND="$LAN_IP" PORT="$PORT" ASSETS="$assets" EXPORTS="$PROJECT_DIR/exports" \
IMPORTS="$PROJECT_DIR/imports" RECORD="$password_record" python3 -I -c '
import json, os
e = os.environ
print(json.dumps({"set": dict(bind=e["BIND"], port=int(e["PORT"]), cert=e["CERT"], key=e["KEY"],
                              map_url="http://" + e["BIND"] + ":8100/", assets_dir=e["ASSETS"],
                              exports_dir=e["EXPORTS"], imports_dir=e["IMPORTS"]),
                  "password": json.loads(e["RECORD"]) if e["RECORD"] else None}))' \
    | python3 -I "$PROJECT_DIR/panel/install_config.py" config "$CONFIG_DIR" "$USER_NAME"
if [[ -n $password_record ]]; then
    if [[ ${initial:-} ]]; then
        printf '%s\n' "$initial" | python3 -I "$PROJECT_DIR/panel/install_config.py" initial "$CONFIG_DIR"
    else
        python3 -I "$PROJECT_DIR/panel/install_config.py" drop-initial "$CONFIG_DIR"
    fi
fi

rule="$USER_NAME ALL=(root) NOPASSWD: /usr/bin/python3 -I $PROJECT_DIR/panel/helper.py *"
printf '# Gestionado por Craft Pi Server: el panel sólo puede ejecutar su helper validado.\n%s\n' "$rule" > "$SUDOERS.new"
chmod 0440 "$SUDOERS.new"
visudo -cf "$SUDOERS.new" >/dev/null
mv -f "$SUDOERS.new" "$SUDOERS"

cat > "$UNIT" <<EOF
[Unit]
Description=Craft Pi Server - panel web LAN
After=network-online.target docker.service
Wants=network-online.target

[Service]
User=$USER_NAME
Group=$USER_NAME
ExecStart=/usr/bin/python3 -I $PROJECT_DIR/panel/server.py serve --config $config
Restart=on-failure
RestartSec=5
# Reiniciar o parar el panel no debe cortar un backup o un reinicio ya lanzado por el helper.
KillMode=process
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
LockPersonality=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable mclan-panel.service >/dev/null 2>&1
systemctl restart mclan-panel.service

for _ in $(seq 1 20); do
    if python3 -I -c 'import socket,ssl,sys; c=ssl.create_default_context(); c.check_hostname=False; c.verify_mode=ssl.CERT_NONE
socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=2).close()' "$LAN_IP" "$PORT" 2>/dev/null; then
        echo "Panel activo: https://$LAN_IP:$PORT/"
        echo "Huella SHA-256 del certificado (compárala con la que muestre el navegador):"
        openssl x509 -in "$cert" -noout -fingerprint -sha256 | cut -d= -f2
        if [[ -f $DATA_DIR/initial-password ]]; then
            echo "Contraseña inicial: sudo cat $DATA_DIR/initial-password (cámbiala en Account)."
        fi
        exit 0
    fi
    sleep 1
done
echo 'El panel no responde. Revisa: sudo journalctl -u mclan-panel -n 50' >&2
exit 1
