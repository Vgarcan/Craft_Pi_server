#!/bin/sh
# Ajustes de BlueMap para esta Pi. Se ejecuta como el usuario del contenedor (UID 10000), nunca como
# root del host: así un enlace dentro de data/ no puede redirigir una escritura privilegiada.
# Uso: bluemap-setup.sh DIRECTORIO_PLUGINS_BLUEMAP. Imprime "changed" si modificó algún mapa.
set -eu
umask 077
folder="$1"
[ ! -L "$folder" ] || { echo 'No se admiten enlaces en plugins/BlueMap.' >&2; exit 1; }
mkdir -p "$folder"
# Perfil para 4 núcleos: un hilo de render (recomendación de BlueMap) y sin métricas. accept-download
# acepta la EULA de Minecraft para descargar texturas; el entrypoint sólo llega aquí con eula=true.
if [ ! -e "$folder/core.conf" ] && [ ! -L "$folder/core.conf" ]; then
    cat > "$folder/core.conf" <<'EOF'
# Creado por Craft Pi Server sólo si faltaba. accept-download acepta la EULA de Minecraft
# (https://www.minecraft.net/eula), ya aceptada por el operador en eula.txt.
accept-download: true
data: "bluemap"
render-thread-count: 2
update-cooldown: 60
full-update-interval: 1440
region-file-check-interval: 5
scan-for-mod-resources: false
metrics: false
log: {
  file: "bluemap/logs/debug.log"
  append: false
}
EOF
fi
# Ajuste único de rendimiento y vista (marca craft-pi: tuning-1). Si el operador cambia después estos
# valores, se respetan. Dos hilos de render (4 núcleos; el render no usa el hilo principal) y una web
# que carga más detalle cerca (250 bloques) y la vista aproximada lejana del mundo pregenerado (4000).
tune() {
    file="$1"; shift
    if [ ! -f "$file" ] || [ -L "$file" ] || grep -q '^# craft-pi: tuning-1$' "$file"; then
        return 0
    fi
    sed "$@" "$file" > "$file.mclan-new"
    printf '\n# Ajuste de Craft Pi Server (una sola vez).\n# craft-pi: tuning-1\n' >> "$file.mclan-new"
    mv -f "$file.mclan-new" "$file"
    changed=1
}
changed=''
tune "$folder/core.conf" -e 's/^render-thread-count: 1$/render-thread-count: 2/'
tune "$folder/webapp.conf" -e 's/^hires-slider-default: 100$/hires-slider-default: 250/' \
    -e 's/^lowres-slider-default: 2000$/lowres-slider-default: 4000/'
# Paper 26.x (Moonrise) no deja la luz en el formato que BlueMap espera y sin este ajuste sólo se
# renderiza la vista lejana. Se aplica una vez por mapa; si el operador lo revierte, se respeta.
for conf in "$folder"/maps/*.conf; do
    if [ ! -f "$conf" ] || [ -L "$conf" ]; then
        continue
    fi
    grep -q '^# craft-pi: light-fix$' "$conf" && continue
    sed 's/^ignore-missing-light-data: false$/ignore-missing-light-data: true/' "$conf" > "$conf.mclan-new"
    printf '\n# Ajuste de Craft Pi Server para Paper/Moonrise (una sola vez).\n# craft-pi: light-fix\n' >> "$conf.mclan-new"
    mv -f "$conf.mclan-new" "$conf"
    changed=1
done
[ -z "$changed" ] || echo changed
