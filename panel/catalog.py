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

# Casillas en el nombre que usan /item y /loot: barra rápida, inventario, armadura, mano secundaria y
# cofre de ender. En el .dat, Slot 0-8 es la barra y 9-35 el inventario.
SLOTS = (tuple(f'hotbar.{i}' for i in range(9)) + tuple(f'inventory.{i}' for i in range(27))
         + ('armor.head', 'armor.chest', 'armor.legs', 'armor.feet', 'weapon.offhand')
         + tuple(f'enderchest.{i}' for i in range(27)))
ARMOR_SLOTS = {'head': ('armor.head', 'Helmet'), 'chest': ('armor.chest', 'Chestplate'),
               'legs': ('armor.legs', 'Leggings'), 'feet': ('armor.feet', 'Boots')}
NAME_LIMIT = 50       # nombre personalizado de un objeto
STACK_LIMIT = 99      # cantidad máxima que admite una casilla

# Durabilidad máxima por objeto (Java 26.x), para la barra de desgaste cuando el objeto no la declara.
_TIERS = {'wooden': 59, 'stone': 131, 'copper': 190, 'iron': 250, 'golden': 32, 'diamond': 1561, 'netherite': 2031}
_ARMOR = {'leather': (55, 80, 75, 65), 'chainmail': (165, 240, 225, 195), 'copper': (121, 176, 165, 143),
          'iron': (165, 240, 225, 195), 'golden': (77, 112, 105, 91), 'diamond': (363, 528, 495, 429),
          'netherite': (407, 592, 555, 481)}
MAX_DURABILITY = {
    **{f'{tier}_{tool}': value for tier, value in _TIERS.items()
       for tool in ('sword', 'pickaxe', 'axe', 'shovel', 'hoe', 'spear')},
    **{f'{material}_{piece}': value for material, values in _ARMOR.items()
       for piece, value in zip(('helmet', 'chestplate', 'leggings', 'boots'), values)},
    'turtle_helmet': 275, 'bow': 384, 'crossbow': 465, 'trident': 250, 'shield': 336, 'elytra': 432, 'mace': 500,
    'fishing_rod': 64, 'shears': 238, 'flint_and_steel': 64, 'carrot_on_a_stick': 25, 'warped_fungus_on_a_stick': 100,
    'brush': 64, 'wolf_armor': 64,
}

# --- Reglas del juego para el editor de casillas ------------------------------------------------
# Qué encantamientos admite cada tipo de objeto (Java 26.x), su nivel máximo (ENCHANTMENTS) y cuáles
# no pueden ir juntos. Unbreaking y Mending valen para todo lo que tiene durabilidad.
_DAMAGE = ('sharpness', 'smite', 'bane_of_arthropods')
_TOOL = ('efficiency', 'fortune', 'silk_touch')
_ARMOR = ('protection', 'fire_protection', 'blast_protection', 'projectile_protection', 'thorns')
ENCHANTABLE = {
    'sword': _DAMAGE + ('knockback', 'fire_aspect', 'looting', 'sweeping_edge'),
    'spear': _DAMAGE + ('knockback', 'fire_aspect', 'looting'),
    'axe': _DAMAGE + _TOOL,
    'tool': _TOOL,
    'helmet': _ARMOR + ('respiration', 'aqua_affinity'),
    'chestplate': _ARMOR,
    'leggings': _ARMOR + ('swift_sneak',),
    'boots': _ARMOR + ('feather_falling', 'depth_strider', 'frost_walker', 'soul_speed'),
    'bow': ('power', 'punch', 'flame', 'infinity'),
    'crossbow': ('multishot', 'piercing', 'quick_charge'),
    'trident': ('loyalty', 'riptide', 'channeling', 'impaling'),
    'mace': ('density', 'breach', 'wind_burst', 'smite', 'bane_of_arthropods', 'fire_aspect'),
    'fishing_rod': ('luck_of_the_sea', 'lure'),
    'shears': ('efficiency',),
    'durable': (),  # escudo, élitros, mechero…: sólo Unbreaking y Mending
}
EXCLUSIVE = (
    {'sharpness', 'smite', 'bane_of_arthropods', 'density', 'breach'},
    {'protection', 'fire_protection', 'blast_protection', 'projectile_protection'},
    {'silk_touch', 'fortune'}, {'infinity', 'mending'}, {'riptide', 'loyalty'}, {'riptide', 'channeling'},
    {'multishot', 'piercing'}, {'depth_strider', 'frost_walker'},
)
_EXACT = {'bow': 'bow', 'crossbow': 'crossbow', 'trident': 'trident', 'mace': 'mace', 'fishing_rod': 'fishing_rod',
          'shears': 'shears', 'turtle_helmet': 'helmet', 'book': 'book', 'enchanted_book': 'book'}
_SUFFIX = (('_sword', 'sword'), ('_spear', 'spear'), ('_pickaxe', 'tool'), ('_shovel', 'tool'), ('_hoe', 'tool'),
           ('_axe', 'axe'), ('_helmet', 'helmet'), ('_chestplate', 'chestplate'), ('_leggings', 'leggings'),
           ('_boots', 'boots'))


def item_kind(item_id):
    """Tipo de objeto a efectos de encantamientos (None: no se puede encantar)."""
    if item_id in _EXACT:
        return _EXACT[item_id]
    for suffix, kind in _SUFFIX:
        if item_id.endswith(suffix) and item_id in MAX_DURABILITY:
            return kind
    return 'durable' if item_id in MAX_DURABILITY else None


def allowed_enchantments(item_id):
    """Encantamientos que el juego permite en ese objeto (los libros admiten todos)."""
    kind = item_kind(item_id)
    if kind is None:
        return ()
    if kind == 'book':
        return tuple(ENCHANTMENTS)
    return ENCHANTABLE[kind] + ('unbreaking', 'mending')


def conflicts(enchantment, current):
    """Encantamientos ya presentes que no pueden convivir con `enchantment` (él mismo no cuenta)."""
    clash = set().union(*(group for group in EXCLUSIVE if enchantment in group)) - {enchantment}
    return sorted(clash & set(current))


_ONE = {'potion', 'splash_potion', 'lingering_potion', 'totem_of_undying', 'saddle', 'cake', 'enchanted_book',
        'writable_book', 'knowledge_book', 'debug_stick', 'goat_horn', 'spyglass', 'bundle', 'water_bucket',
        'lava_bucket', 'milk_bucket', 'powder_snow_bucket', 'cod_bucket', 'salmon_bucket', 'pufferfish_bucket',
        'tropical_fish_bucket', 'axolotl_bucket', 'tadpole_bucket', 'mushroom_stew', 'rabbit_stew', 'beetroot_soup',
        'suspicious_stew', 'minecart', 'chest_minecart', 'furnace_minecart', 'hopper_minecart', 'tnt_minecart',
        'command_block_minecart', 'iron_horse_armor', 'golden_horse_armor', 'diamond_horse_armor',
        'leather_horse_armor', 'shulker_box'}
_SIXTEEN = {'ender_pearl', 'snowball', 'egg', 'blue_egg', 'brown_egg', 'bucket', 'armor_stand', 'honey_bottle',
            'written_book'}


def max_stack(item_id):
    """Cuántos caben en una casilla, como en el juego."""
    if item_id in MAX_DURABILITY or item_id in _ONE or item_id.endswith(('_bed', '_boat', '_raft', '_shulker_box')) \
            or item_id.startswith('music_disc_'):
        return 1
    if item_id in _SIXTEEN or item_id.endswith(('_sign', '_banner')):
        return 16
    return 64


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
    'slot-enchant': 'Enchantment applied.', 'slot-unenchant': 'Enchantments removed.', 'slot-repair': 'Item repaired.',
    'slot-unbreakable': 'Item is now unbreakable.', 'slot-rename': 'Item renamed.', 'slot-count': 'Amount changed.',
    'slot-clear': 'Item deleted.', 'slot-set': 'Item placed.', 'slot-duplicate': 'Item duplicated.',
    'effect-remove': 'Effect removed.',
}
# Campos del formulario o del JSON que lleva cada acción, en el orden en que los recibe el helper.
FIELDS = {
    'gamemode': ('mode',), 'give': ('item', 'count'), 'kit': ('kit',), 'xp': ('amount',),
    'effect': ('effect', 'duration', 'level'), 'tp-to': ('target',), 'bring': ('target',),
    'tp-coords': ('dimension', 'x', 'y', 'z'), 'enchant': ('enchantment', 'level'), 'pet': ('pet',),
    'message': ('kind', 'text'),
    'slot-enchant': ('slot', 'enchantment', 'level'), 'slot-unenchant': ('slot',), 'slot-repair': ('slot',),
    'slot-unbreakable': ('slot',), 'slot-rename': ('slot', 'text'), 'slot-count': ('slot', 'count'),
    'slot-clear': ('slot',), 'slot-set': ('slot', 'item', 'count'), 'slot-duplicate': ('slot',),
    'effect-remove': ('effect',),
}
ACTIONS = tuple(LABELS)
