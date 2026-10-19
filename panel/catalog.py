"""Listas cerradas de las acciones de jugador: las comparten el helper (valida) y la interfaz (dibuja).

Sólo datos. Cada identificador acaba en un comando de Minecraft, así que todos cumplen [a-z0-9_]+ y
existen en Java 26.3. Los textos visibles van en inglés, como el resto del panel.
"""

# Kits: clave → (nombre, descripción, objeto que lo representa, [(objeto, cantidad)]).
KITS = {
    'starter': ('Starter', 'Stone tools, food, torches and a bed for the first night.', 'stone_pickaxe', [
        ('stone_sword', 1), ('stone_pickaxe', 1), ('stone_axe', 1), ('stone_shovel', 1), ('bread', 16),
        ('torch', 32), ('white_bed', 1), ('crafting_table', 1)]),
    'miner': ('Miner', 'Iron tools, plenty of light and ladders to get back up.', 'iron_pickaxe', [
        ('iron_pickaxe', 1), ('iron_shovel', 1), ('torch', 64), ('ladder', 32), ('cooked_beef', 16),
        ('water_bucket', 1)]),
    'builder': ('Builder', 'Stacks of building blocks and scaffolding.', 'bricks', [
        ('oak_planks', 64), ('stone_bricks', 64), ('glass', 64), ('oak_stairs', 32), ('oak_slab', 32),
        ('scaffolding', 32)]),
    'explorer': ('Explorer', 'Map, compass, spyglass, a boat and supplies for a long trip.', 'compass', [
        ('map', 1), ('compass', 1), ('spyglass', 1), ('oak_boat', 1), ('iron_sword', 1), ('cooked_beef', 16),
        ('torch', 32), ('white_bed', 1)]),
    'farmer': ('Farmer', 'Hoe, seeds, crops, bone meal and water.', 'wheat', [
        ('iron_hoe', 1), ('wheat_seeds', 32), ('carrot', 16), ('potato', 16), ('bone_meal', 32), ('water_bucket', 1)]),
    'warrior': ('Warrior', 'Full iron armour, sword, shield, bow and golden apples.', 'iron_sword', [
        ('iron_helmet', 1), ('iron_chestplate', 1), ('iron_leggings', 1), ('iron_boots', 1), ('iron_sword', 1),
        ('shield', 1), ('bow', 1), ('arrow', 32), ('golden_apple', 2)]),
}

# Efectos: id → nombre. El icono es la textura mob_effect/<id>.png del cliente.
EFFECTS = {
    'night_vision': 'Night vision', 'speed': 'Speed', 'haste': 'Haste', 'jump_boost': 'Jump boost',
    'water_breathing': 'Water breathing', 'fire_resistance': 'Fire resistance', 'invisibility': 'Invisibility',
    'regeneration': 'Regeneration', 'strength': 'Strength', 'resistance': 'Resistance',
    'slow_falling': 'Slow falling', 'dolphins_grace': "Dolphin's grace", 'glowing': 'Glowing', 'luck': 'Luck',
}
DURATIONS = {'60': '1 min', '300': '5 min', '900': '15 min'}
EFFECT_LEVELS = {'1': 'I', '2': 'II'}

# Encantamientos para el objeto que el jugador tiene en la mano: id → (nombre, nivel máximo).
ENCHANTMENTS = {
    'sharpness': ('Sharpness', 5), 'smite': ('Smite', 5), 'bane_of_arthropods': ('Bane of Arthropods', 5),
    'knockback': ('Knockback', 2), 'fire_aspect': ('Fire Aspect', 2), 'looting': ('Looting', 3),
    'sweeping_edge': ('Sweeping Edge', 3), 'efficiency': ('Efficiency', 5), 'silk_touch': ('Silk Touch', 1),
    'fortune': ('Fortune', 3), 'unbreaking': ('Unbreaking', 3), 'mending': ('Mending', 1),
    'protection': ('Protection', 4), 'fire_protection': ('Fire Protection', 4),
    'blast_protection': ('Blast Protection', 4), 'projectile_protection': ('Projectile Protection', 4),
    'feather_falling': ('Feather Falling', 4), 'respiration': ('Respiration', 3),
    'aqua_affinity': ('Aqua Affinity', 1), 'thorns': ('Thorns', 3), 'depth_strider': ('Depth Strider', 3),
    'frost_walker': ('Frost Walker', 2), 'soul_speed': ('Soul Speed', 3), 'swift_sneak': ('Swift Sneak', 3),
    'power': ('Power', 5), 'punch': ('Punch', 2), 'flame': ('Flame', 1), 'infinity': ('Infinity', 1),
    'luck_of_the_sea': ('Luck of the Sea', 3), 'lure': ('Lure', 3), 'loyalty': ('Loyalty', 3),
    'riptide': ('Riptide', 3), 'channeling': ('Channeling', 1), 'impaling': ('Impaling', 5),
    'multishot': ('Multishot', 1), 'piercing': ('Piercing', 4), 'quick_charge': ('Quick Charge', 3),
    'density': ('Density', 5), 'breach': ('Breach', 4), 'wind_burst': ('Wind Burst', 3),
}

PETS = {'wolf': 'Wolf', 'cat': 'Cat', 'parrot': 'Parrot'}
DIMENSIONS = {'overworld': 'Overworld', 'the_nether': 'Nether', 'the_end': 'The End'}
# Niveles de experiencia: valor → etiqueta ("reset" deja nivel y progreso a 0).
XP_STEPS = {'1': '+1', '5': '+5', '10': '+10', '30': '+30', '-1': '−1', '-5': '−5', '-10': '−10', 'reset': 'Reset'}
MESSAGE_KINDS = {'chat': 'Chat message', 'title': 'Big title', 'actionbar': 'Above the hotbar'}
MESSAGE_LIMIT = 100
GIVE_LIMIT = 64
# Mundo de Minecraft: límite de coordenadas horizontales y altura construible del mundo principal.
COORDINATE_LIMIT = 29_999_984
HEIGHT = (-64, 320)

# Acciones que necesitan al jugador conectado (las de política funcionan también sin él).
OFFLINE_OK = {'op', 'deop', 'ban', 'pardon'}
# Mensaje de éxito para el aviso del panel.
LABELS = {
    'op': 'Operator rights granted.', 'deop': 'Operator rights removed.', 'kick': 'Player kicked.',
    'ban': 'Player banned.', 'pardon': 'Player unbanned.', 'gamemode': 'Game mode changed.',
    'spawn': 'Teleported to the world spawn.', 'heal': 'Health restored.', 'feed': 'Hunger restored.',
    'give': 'Item given.', 'kit': 'Kit given.', 'clear': 'Inventory cleared.', 'xp': 'Experience changed.',
    'effect': 'Effect applied.', 'effects-clear': 'Effects removed.', 'tp-respawn': 'Teleported to their respawn point.',
    'tp-death': 'Teleported to their last death, protected for 30 seconds.', 'tp-to': 'Teleported.',
    'bring': 'Player brought here.', 'tp-coords': 'Teleported to the coordinates.', 'enchant': 'Item enchanted.',
    'fireworks': 'Fireworks launched.', 'thunder': 'Thunderclap!', 'pet': 'Pet delivered.', 'message': 'Message sent.',
}
# Campos del formulario o del JSON que lleva cada acción, en el orden en que los recibe el helper.
FIELDS = {
    'gamemode': ('mode',), 'give': ('item', 'count'), 'kit': ('kit',), 'xp': ('amount',),
    'effect': ('effect', 'duration', 'level'), 'tp-to': ('target',), 'bring': ('target',),
    'tp-coords': ('dimension', 'x', 'y', 'z'), 'enchant': ('enchantment', 'level'), 'pet': ('pet',),
    'message': ('kind', 'text'),
}
ACTIONS = tuple(LABELS)
