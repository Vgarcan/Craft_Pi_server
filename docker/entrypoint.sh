#!/bin/sh
# exec entrega SIGTERM directamente a Java; Paper guarda los mundos al cerrar.
set -eu
umask 077
[ "$(id -u)" = 10000 ] || { echo 'UID inesperado' >&2; exit 1; }
if [ ! -f /data/eula.txt ] || ! grep -Eq '^eula=true\r?$' /data/eula.txt; then
    echo 'Debes aceptar la EULA antes de iniciar.' >&2
    exit 1
fi
# Sólo se sustituyen los dos binarios gestionados; nunca mundos/configuraciones.
mkdir -p /data/plugins
if [ -L /data/plugins ] || [ -L /data/plugins/Geyser-Spigot.jar ] \
    || [ -L /data/plugins/floodgate-spigot.jar ]; then
    echo 'No se admiten enlaces en las rutas de los plugins gestionados.' >&2
    exit 1
fi
cp /opt/release/Geyser-Spigot.jar /data/plugins/Geyser-Spigot.jar
cp /opt/release/floodgate-spigot.jar /data/plugins/floodgate-spigot.jar
exec java "-Xms${JAVA_XMS:-512M}" "-Xmx${JAVA_XMX:-2048M}" \
    -XX:+UseG1GC -XX:+ExitOnOutOfMemoryError \
    -Dfile.encoding=UTF-8 -Dterminal.jline=false -Dterminal.ansi=false \
    -jar /opt/release/paper.jar --nogui
