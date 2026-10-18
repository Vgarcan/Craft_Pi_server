#!/bin/sh
# exec entrega SIGTERM directamente a Java; Paper guarda los mundos al cerrar.
set -eu
umask 077
[ "$(id -u)" = 10000 ] || { echo 'UID inesperado' >&2; exit 1; }
if [ ! -f /data/eula.txt ] || ! grep -Eq '^eula=true\r?$' /data/eula.txt; then
    echo 'Debes aceptar la EULA antes de iniciar.' >&2
    exit 1
fi
# Sólo se sustituyen los JAR de plugins gestionados; nunca mundos/configuraciones.
mkdir -p /data/plugins
[ ! -L /data/plugins ] || { echo 'No se admiten enlaces en plugins/.' >&2; exit 1; }
# La copia hereda el modo 0444 del release y queda en 0400: cp no podría
# sobrescribirla en el siguiente arranque. Se copia aparte y se renombra.
# Un plugin gestionado ausente de la release (p. ej. tras restaurar una antigua) o desactivado por
# el operador (MCLAN_DISABLED_PLUGINS, p. ej. "bluemap") se retira y no consume recursos.
disabled() {
    case " ${MCLAN_DISABLED_PLUGINS:-} " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}
for jar in Geyser-Spigot.jar floodgate-spigot.jar BlueMap.jar; do
    if [ -L /data/plugins/"$jar" ]; then
        echo 'No se admiten enlaces en las rutas de los plugins gestionados.' >&2
        exit 1
    fi
    if [ "$jar" = BlueMap.jar ] && disabled bluemap; then
        rm -f /data/plugins/"$jar"
    elif [ -f /opt/release/"$jar" ]; then
        cp /opt/release/"$jar" /data/plugins/."$jar".mclan-new
        mv -f /data/plugins/."$jar".mclan-new /data/plugins/"$jar"
    else
        rm -f /data/plugins/"$jar"
    fi
done
if [ -f /data/plugins/BlueMap.jar ]; then
    /opt/mclan/bluemap-setup.sh /data/plugins/BlueMap >/dev/null
fi
# NetherNet (Bedrock 26.60+) anuncia direcciones WebRTC: la IP interna del
# contenedor no es alcanzable desde la LAN, así que se anuncia la del host.
# Estas propiedades prevalecen sobre plugins/Geyser-Spigot/config.yml.
set --
if [ "${GEYSER_TRANSPORT:-raknet}" != raknet ]; then
    case "${GEYSER_WEBRTC_PORT:-}" in
        ''|*[!0-9]*) echo 'GEYSER_WEBRTC_PORT inválido.' >&2; exit 1 ;;
    esac
    [ -n "${GEYSER_ADVERTISE_ADDRESSES:-}" ] || { echo 'Falta GEYSER_ADVERTISE_ADDRESSES.' >&2; exit 1; }
    set -- "-DgeyserTransport=$GEYSER_TRANSPORT" "-DgeyserWebrtcPort=$GEYSER_WEBRTC_PORT" \
        "-DgeyserAdvertiseAddresses=$GEYSER_ADVERTISE_ADDRESSES"
fi
exec java "-Xms${JAVA_XMS:-512M}" "-Xmx${JAVA_XMX:-2048M}" \
    -XX:+UseG1GC -XX:+ExitOnOutOfMemoryError \
    -Dfile.encoding=UTF-8 -Dterminal.jline=false -Dterminal.ansi=false \
    "$@" -jar /opt/release/paper.jar --nogui
