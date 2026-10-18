#!/usr/bin/env python3
"""Recalcula la luz cargando una vez cada chunk existente.

Se ejecuta DENTRO del contenedor, como su usuario, con el servidor en marcha y sin jugadores:
    sudo docker compose --project-name minecraft-lan --project-directory . --env-file .env \\
        -f compose.yaml exec -T minecraft python3 - < tools/relight.py
Hace falta tras importar mundos convertidos con Chunker, que escribe la luz de cielo a cero: el mapa
y el juego se ven oscuros y aparecen monstruos de día. Paper/Moonrise recalcula la luz de un chunk
sin marca starlight.light_version al cargarlo, y la guarda.

Efecto secundario: forceload deja cada chunk «en tick», y eso exige vecinos completos. En el borde
de la zona explorada se genera un anillo de 1-2 chunks de terreno nuevo, más chunks a medio generar
e invisibles. Es lo mismo que ocurre cuando un jugador se acerca al borde. Haz backup antes.
"""
from pathlib import Path
import struct
import sys
import time

sys.path.insert(0, '/opt/mclan')

WORLD = Path('/data/world/dimensions/minecraft')
BATCH = 256


def existing_chunks(region_dir):
    """Coordenadas de los chunks presentes según la cabecera de cada región (.mca)."""
    for path in sorted(region_dir.glob('r.*.*.mca')):
        _, rx, rz, _ = path.name.split('.')
        with path.open('rb') as stream:
            header = stream.read(4096)
        for index in range(len(header) // 4):
            if struct.unpack('>I', header[index * 4:index * 4 + 4])[0] >> 8:
                yield int(rx) * 32 + index % 32, int(rz) * 32 + index // 32


def command(rcon, text, retries=3):
    """La sonda RCON espera 4 s: un comando lento (save-all) sigue ejecutándose en el servidor."""
    for attempt in range(retries):
        try:
            return rcon(text)
        except (TimeoutError, OSError):
            if attempt == retries - 1:
                raise
            time.sleep(5)


def relight(rcon, world=WORLD, pause=4, log=print):
    started = time.monotonic()
    for dimension in ('overworld', 'the_nether', 'the_end'):
        chunks = list(existing_chunks(world / dimension / 'region'))
        if not chunks:
            continue
        log(f'{dimension}: {len(chunks)} chunks')
        prefix = f'execute in minecraft:{dimension} run '
        for start in range(0, len(chunks), BATCH):
            for x, z in chunks[start:start + BATCH]:
                command(rcon, f'{prefix}forceload add {x * 16} {z * 16}')
            time.sleep(pause)  # carga y cálculo de luz son asíncronos
            command(rcon, f'{prefix}forceload remove all')
            log(f'  {min(start + BATCH, len(chunks))}/{len(chunks)}')
    try:
        rcon('save-all flush')
    except (TimeoutError, OSError):
        log('save-all sigue en curso en el servidor; comprueba "Saved the game" en los logs.')
    log(f'Hecho en {time.monotonic() - started:.0f} s')


if __name__ == '__main__':
    import probe
    relight(probe.rcon, log=lambda text: print(text, flush=True))
