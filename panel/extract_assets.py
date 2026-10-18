"""Extrae del cliente de Minecraft una lista cerrada de texturas para el panel y las escribe como tar
en stdout. Se ejecuta DENTRO del contenedor, como su usuario, leyendo el JAR que BlueMap descargó de
Mojang. Las texturas son de Mojang: se usan sólo en la LAN y nunca se incluyen en el repositorio.

    docker compose ... exec -T minecraft python3 - < panel/extract_assets.py > assets.tar
"""
import glob
import io
import sys
import tarfile
import zipfile

BASE = 'assets/minecraft/textures/'
# Nombre publicado en el panel → ruta dentro del JAR.
ASSETS = {
    'logo.png': 'gui/title/minecraft.png',
    'edition.png': 'gui/title/edition.png',
    'hotbar.png': 'gui/sprites/hud/hotbar.png',
    'hotbar_selection.png': 'gui/sprites/hud/hotbar_selection.png',
    'xp_background.png': 'gui/sprites/hud/experience_bar_background.png',
    'xp_progress.png': 'gui/sprites/hud/experience_bar_progress.png',
    'heart.png': 'gui/sprites/hud/heart/full.png',
    'button.png': 'gui/sprites/widget/button.png',
    'button_highlighted.png': 'gui/sprites/widget/button_highlighted.png',
    'button_disabled.png': 'gui/sprites/widget/button_disabled.png',
    **{f'{name}.png': f'block/{name}.png' for name in (
        'grass_block_side', 'grass_block_top', 'dirt', 'stone', 'deepslate', 'deepslate_top', 'cobblestone',
        'oak_planks', 'obsidian', 'bedrock', 'command_block_front', 'bookshelf', 'crafting_table_front',
        'netherrack', 'end_stone', 'barrel_top', 'redstone_lamp_on')},
    **{f'{name}.png': f'item/{name}.png' for name in (
        'name_tag', 'map', 'filled_map', 'compass_00', 'clock_00', 'writable_book', 'book', 'chest_minecart',
        'ender_eye', 'spyglass', 'golden_apple', 'redstone', 'diamond', 'emerald', 'barrier', 'iron_door',
        'totem_of_undying', 'experience_bottle', 'written_book', 'ender_pearl', 'nether_star', 'bundle')},
}


def main():
    jars = sorted(glob.glob('/data/bluemap/minecraft-client-*.jar'))
    if not jars:
        sys.exit('No hay cliente de Minecraft descargado (BlueMap activo y con accept-download).')
    output = io.BytesIO()
    with zipfile.ZipFile(jars[-1]) as jar, tarfile.open(fileobj=output, mode='w') as archive:
        names = set(jar.namelist())
        for published, source in ASSETS.items():
            if BASE + source in names:
                payload = jar.read(BASE + source)
                info = tarfile.TarInfo(published)
                info.size, info.mode = len(payload), 0o644
                archive.addfile(info, io.BytesIO(payload))
    sys.stdout.buffer.write(output.getvalue())


if __name__ == '__main__':
    main()
