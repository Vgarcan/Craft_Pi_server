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
    < <(find "$PROJECT_DIR/scripts" "$PROJECT_DIR/panel" "$PROJECT_DIR/tools" "$PROJECT_DIR/manage.sh" -print0)

if ! id "$USER_NAME" >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$USER_NAME"
fi
install -d -m 0700 -o "$USER_NAME" -g "$USER_NAME" "$CONFIG_DIR"

# Certificado autofirmado para la IP actual. Se renueva si cambió la IP.
cert="$CONFIG_DIR/tls.crt" key="$CONFIG_DIR/tls.key"
if [[ ! -f $cert ]] || ! openssl x509 -in "$cert" -noout -ext subjectAltName 2>/dev/null | grep -q "IP Address:$LAN_IP\$"; then
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 825 -subj '/CN=Craft Pi Server' \
        -addext "subjectAltName=IP:$LAN_IP" -keyout "$key.new" -out "$cert.new" 2>/dev/null
    mv -f "$key.new" "$key"
    mv -f "$cert.new" "$cert"
fi
chown "$USER_NAME:$USER_NAME" "$cert" "$key"
chmod 0600 "$key"
chmod 0644 "$cert"

config="$CONFIG_DIR/panel.json"
password_record=''
if [[ ! -f $config || $mode == --reset-password ]]; then
    if [[ $mode == --reset-password && -t 0 ]]; then
        read -r -s -p 'Nueva contraseña (mínimo 12 caracteres): ' first; echo
        read -r -s -p 'Repítela: ' second; echo
        [[ $first == "$second" ]] || { echo 'No coinciden.' >&2; exit 1; }
        password_record="$(printf '%s\n' "$first" | python3 -I "$PROJECT_DIR/panel/server.py" hash-password)"
        rm -f "$CONFIG_DIR/initial-password"
    else
        initial="$(python3 -I -c 'import secrets; print(secrets.token_urlsafe(15))')"
        password_record="$(printf '%s\n' "$initial" | python3 -I "$PROJECT_DIR/panel/server.py" hash-password)"
        (umask 077; printf '%s\n' "$initial" > "$CONFIG_DIR/initial-password")
        chown root:root "$CONFIG_DIR/initial-password"
    fi
fi
CONFIG="$config" BIND="$LAN_IP" PORT="$PORT" CERT="$cert" KEY="$key" RECORD="$password_record" python3 -I - <<'EOF'
import json, os
from pathlib import Path
path = Path(os.environ['CONFIG'])
config = json.loads(path.read_text()) if path.exists() else {}
config.update(bind=os.environ['BIND'], port=int(os.environ['PORT']), cert=os.environ['CERT'], key=os.environ['KEY'],
              map_url=f"http://{os.environ['BIND']}:8100/")
if os.environ['RECORD']:
    config['password'] = json.loads(os.environ['RECORD'])
temporary = path.with_suffix('.new')
temporary.write_text(json.dumps(config, indent=2) + '\n')
os.chmod(temporary, 0o600)
temporary.replace(path)
EOF
chown "$USER_NAME:$USER_NAME" "$config"

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
        if [[ -f $CONFIG_DIR/initial-password ]]; then
            echo "Contraseña inicial: sudo cat $CONFIG_DIR/initial-password (cámbiala en Cuenta)."
        fi
        exit 0
    fi
    sleep 1
done
echo 'El panel no responde. Revisa: sudo journalctl -u mclan-panel -n 50' >&2
exit 1
