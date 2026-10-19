"""Panel web: validación del helper privilegiado y rutas HTTP sin Docker ni sudo."""
import http.server
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def load(name):
    spec = importlib.util.spec_from_file_location(f'panel_{name}', ROOT / 'panel' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load('helper')
server = load('server')
from mclan import nbt  # noqa: E402


def nbt_bytes(value, name=''):
    """Serializador NBT mínimo para pruebas (compuestos, listas, enteros, dobles y textos)."""
    import struct
    def tag(v):
        if isinstance(v, dict): return 10
        if isinstance(v, list): return 9
        if isinstance(v, float): return 6
        if isinstance(v, int): return 3
        return 8
    def payload(v):
        if isinstance(v, dict):
            return b''.join(bytes([tag(x)]) + struct.pack('>H', len(k)) + k.encode() + payload(x) for k, x in v.items()) + b'\x00'
        if isinstance(v, list):
            return bytes([tag(v[0]) if v else 1]) + struct.pack('>i', len(v)) + b''.join(payload(x) for x in v)
        if isinstance(v, float): return struct.pack('>d', v)
        if isinstance(v, int): return struct.pack('>i', v)
        raw = str(v).encode(); return struct.pack('>H', len(raw)) + raw
    return b'\x0a' + struct.pack('>H', len(name)) + name.encode() + payload(value)


def status(**changes):
    value = {
        'ok': True,
        'release': {'id': 'a' * 20, 'minecraft': '26.3', 'lan_ip': '192.168.1.50', 'paper': 205, 'geyser': '2.12.0',
                    'floodgate': '2.2.5', 'bluemap': '5.29', 'bedrock': '26.30-26.52'},
        'container': {'exists': True, 'running': True, 'status': 'running', 'health': 'healthy', 'restarts': 0,
                      'started_at': 'x', 'memory': {'used': 2**30, 'limit': 3 * 2**30}},
        'players': 'There are 1 of a max of 6 players online: <script>alert(1)</script>',
        'bluemap': {'version': '5.29', 'enabled': True, 'render': 'idle'},
        'spawn': [-32, 64, 0],
        'host': {'temperature': 48.2, 'memory_limits': True},
        'pending_operation': False, 'project_drift': False,
        'properties': {'motd': 'Hola <b>', 'difficulty': 'normal', 'gamemode': 'survival', 'max-players': '6',
                       'view-distance': '6', 'simulation-distance': '4', 'pvp': 'true', 'allow-flight': 'false',
                       'spawn-protection': '16', 'white-list': 'true'},
        'whitelist': [{'name': '.Alguien', 'uuid': '00000000-0000-0000-0009-01f1b0392c35', 'edition': 'bedrock'}],
        'rejected': [{'edition': 'bedrock', 'name': 'Otro', 'last_seen': '16:29:04'}],
        'backups': [],
    }
    value.update(changes)
    return value


class HelperValidationTests(unittest.TestCase):
    def test_only_closed_verbs_and_patterns_reach_the_console(self):
        self.assertEqual(helper.parse(['whitelist-add', 'java', 'Steve_1'])[1], ['java', 'Steve_1'])
        self.assertEqual(helper.parse(['whitelist-add', 'xuid', '2535412345678901'])[1][0], 'xuid')
        for argv in (['whitelist-add', 'java', 'a; stop'], ['whitelist-add', 'java', 'op Steve'],
                     ['whitelist-add', 'bedrock', 'two words'], ['whitelist-add', 'java', 'ab'],
                     ['whitelist-add', 'other', 'Steve'], ['whitelist-add', 'xuid', '0123'],
                     ['whitelist-remove', 'Steve extra'], ['whitelist', 'maybe'], ['logs', '5000'],
                     ['logs', '-1'], ['command', 'op', 'Steve'], ['stop', 'now'], [],
                     ['whitelist-open-for', 'bedrock', 'Steve', '60'], ['whitelist-open-for', 'bedrock', 'a b', '5'],
                     ['whitelist-open-for', 'Steve', '5'], ['whitelist-open-for', 'xuid', '123', '5']):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                helper.parse(argv)

    def test_only_safe_properties_are_editable(self):
        self.assertEqual(helper.parse(['set-properties', 'difficulty=hard', 'max-players=8'])[1],
                         [{'difficulty': 'hard', 'max-players': '8'}])
        for pair in ('online-mode=false', 'rcon.port=1', 'server-port=1', 'enable-rcon=false', 'white-list=false',
                     'max-players=0', 'max-players=99', 'view-distance=32', 'difficulty=insane', 'pvp=yes',
                     'motd=', 'motd=a\nb', 'motd=' + 'x' * 60, 'motd=§4rojo', 'difficulty'):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                helper.parse(['set-properties', pair])
        with self.assertRaises(ValueError):
            helper.parse(['set-properties'])
        with self.assertRaises(ValueError):
            helper.parse(['set-properties', 'pvp=true', 'pvp=false'])

    def test_properties_edit_preserves_unrelated_lines_and_escapes(self):
        original = '# Perfil\nonline-mode=true\nmotd=Viejo\nwhite-list=true\n'
        edited = helper.edit_properties(original, {'motd': 'Servidor de Víctor 🎮', 'pvp': 'false'})
        self.assertIn('# Perfil\nonline-mode=true\n', edited)
        self.assertIn('white-list=true', edited)
        self.assertTrue(edited.isascii())
        values = helper.read_properties(edited)
        self.assertEqual(values['motd'], 'Servidor de Víctor 🎮')
        self.assertEqual(values['pvp'], 'false')
        self.assertEqual(helper.read_properties('a=x\\\\y\nb=\\u00e9\n'), {'a': 'x\\y', 'b': 'é'})

    def test_new_verbs_are_validated(self):
        self.assertEqual(helper.parse(['bluemap-render', 'off'])[1], ['off'])
        self.assertEqual(helper.parse(['plugin', 'bluemap', 'on'])[1], ['bluemap', 'on'])
        for argv in (['plugin', 'geyser', 'off'], ['plugin', 'bluemap'], ['bluemap-render', 'pause']):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                helper.parse(argv)

    def test_bluemap_render_state_and_world_spawn(self):
        import gzip
        import struct
        self.assertEqual(helper.bluemap_render('BlueMap Status >\n ✔ 1 render-thread is idle'), 'idle')
        self.assertEqual(helper.bluemap_render('Render-Threads are STOPPED!'), 'paused')
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'data'
            (data / 'world').mkdir(parents=True)
            payload = b'\x0a\x00\x00\x0a\x00\x05spawn' + b'\x0b\x00\x03pos' + struct.pack('>iiii', 3, -32, 64, 0)
            with gzip.open(data / 'world/level.dat', 'wb') as stream:
                stream.write(payload)
            self.assertEqual(helper.world_spawn(data), [-32, 64, 0])
            (data / 'world').rename(data / 'real')
            (data / 'world').symlink_to(data / 'real', target_is_directory=True)
            self.assertIsNone(helper.world_spawn(data))
            self.assertIsNone(helper.world_spawn(Path(directory) / 'missing'))

    def test_console_commands_are_single_printable_lines_without_lifecycle(self):
        self.assertEqual(helper.parse(['console', '/give', '.Alguien', 'diamond', '3'])[1], ['give .Alguien diamond 3'])
        self.assertEqual(helper.parse(['console', 'time set day'])[1], ['time set day'])
        for argv in (['console', 'stop'], ['console', 'minecraft:stop'], ['console', 'RESTART'], ['console', 'bukkit:reload confirm'],
                     ['console', 'say hi\nstop'], ['console', 'say \x1b[31m'], ['console', ''], ['console'],
                     ['console', 'x' * 257]):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                helper.parse(argv)

    def test_logs_hide_healthcheck_rcon_noise(self):
        panel = helper.Panel.__new__(helper.Panel)
        panel.server_log = Mock(return_value='[1 INFO]: Thread RCON Client /127.0.0.1 started\n[2 INFO]: Done\n'
                                             '[3 INFO]: Thread RCON Client /127.0.0.1 shutting down\n[4 INFO]: §6joined\n')
        self.assertEqual(panel.logs(10)['log'], '[2 INFO]: Done\n[4 INFO]: joined')

    def test_texture_installer_accepts_only_listed_pngs(self):
        import io
        import tarfile
        installer = load('install_assets')
        def archive(entries):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode='w') as tar:
                for name, payload, kind in entries:
                    info = tarfile.TarInfo(name)
                    if kind == 'link':
                        info.type, info.linkname = tarfile.SYMTYPE, '/etc/shadow'
                    else:
                        info.size = len(payload)
                    tar.addfile(info, io.BytesIO(payload) if kind != 'link' else None)
            buffer.seek(0)
            return buffer
        png = b'\x89PNG\r\n\x1a\n' + b'0' * 20
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'assets'
            (target).mkdir()
            (target / 'stale.png').write_bytes(png)
            self.assertEqual(installer.install(archive([('logo.png', png, 'file'), ('dirt.png', png, 'file')]), target),
                             ['dirt.png', 'logo.png'])
            self.assertFalse((target / 'stale.png').exists())
            for bad in ([('../evil.png', png, 'file')], [('logo.png', b'<svg onload=x>', 'file')],
                        [('unknown.png', png, 'file')], [('logo.png', b'', 'link')],
                        [('logo.png', b'\x89PNG\r\n\x1a\n' + b'0' * (300 * 1024), 'file')]):
                with self.subTest(bad=bad[0][0]), self.assertRaises(ValueError):
                    installer.install(archive(bad), target)
            self.assertEqual((target / 'logo.png').read_bytes(), png)
            catalog = json.dumps({'items': {'diamond': 'Diamond', 'oak_log': 'Oak Log'}}).encode()
            self.assertIn('items.json', installer.install(archive([('effect_speed.png', png, 'file'),
                                                                   ('items.json', catalog, 'file')]), target))
            for bad in (b'not json', json.dumps({'items': {'Bad Id': 'x'}}).encode(),
                        json.dumps({'items': {'ok': 'line\nbreak'}}).encode(), json.dumps({'items': []}).encode()):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    installer.install(archive([('items.json', bad, 'file')]), target)
            self.assertEqual(sorted(p.name for p in target.iterdir()), ['effect_speed.png', 'items.json'])

    def test_item_catalog_comes_from_the_client_jar(self):
        import io
        import zipfile
        extract = load('extract_assets')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as jar:
            jar.writestr('assets/minecraft/items/diamond_sword.json', '{}')
            jar.writestr('assets/minecraft/items/oak_log.json', '{}')
            jar.writestr('assets/minecraft/items/mystery_thing.json', '{}')
            jar.writestr('assets/minecraft/items/../evil.json', '{}')
            jar.writestr('assets/minecraft/lang/en_us.json', json.dumps({'item.minecraft.diamond_sword': 'Diamond Sword',
                                                                         'block.minecraft.oak_log': 'Oak Log'}))
        with zipfile.ZipFile(buffer) as jar:
            items = json.loads(extract.catalog(jar, set(jar.namelist())))['items']
        self.assertEqual(items, {'diamond_sword': 'Diamond Sword', 'mystery_thing': 'Mystery Thing', 'oak_log': 'Oak Log'})
        self.assertTrue(load('install_assets').valid_catalog(json.dumps({'items': items}).encode()))

    def test_nbt_reader_roundtrip_and_limits(self):
        import gzip
        import struct
        data = {'Health': 15.5, 'foodLevel': 13, 'Dimension': 'minecraft:overworld', 'Pos': [1.0, 64.0, -2.0],
                'Inventory': [{'Slot': 0, 'id': 'minecraft:diamond_pickaxe', 'count': 1}], 'bukkit': {'lastKnownName': 'Steve'}}
        self.assertEqual(nbt.loads(gzip.compress(nbt_bytes(data))), data)
        with self.assertRaises(ValueError):
            nbt.loads(nbt_bytes(data)[:-5])
        deep = {}
        cursor = deep
        for _ in range(nbt.MAX_DEPTH + 2):
            cursor['x'] = {}
            cursor = cursor['x']
        with self.assertRaises(ValueError):
            nbt.loads(nbt_bytes(deep))
        huge = b'\x0a\x00\x00\x09\x00\x01L\x03' + struct.pack('>i', 10**9) + b'\x00'
        with self.assertRaises(ValueError):
            nbt.loads(huge)
        with self.assertRaises(ValueError):
            nbt.loads(b'\x08\x00\x00')

    def test_profile_summary_from_player_files(self):
        data = {'Health': 15.0, 'foodLevel': 13, 'XpLevel': 12, 'playerGameType': 1, 'Dimension': 'minecraft:the_nether',
                'Pos': [1.5, 70.0, -3.2], 'Inventory': [{'Slot': 0, 'id': 'minecraft:torch', 'count': 64}, {'id': 7}],
                'bukkit': {'lastKnownName': 'Steve', 'firstPlayed': 1}, 'Paper': {'LastSeen': 5},
                'abilities': {'flying': 1, 'mayfly': 1, 'walkSpeed': 0.1}}
        stats = {'minecraft:custom': {'minecraft:play_time': 72000, 'minecraft:deaths': 2},
                 'minecraft:mined': {'minecraft:stone': 10, 'minecraft:dirt': 50}}
        advancements = {'minecraft:story/root': {'done': True}, 'minecraft:recipes/misc/x': {'done': True},
                        'minecraft:story/mine_stone': {'done': False}}
        profile = helper.profile_from(data, stats, advancements)
        self.assertEqual(profile['vitals']['gamemode'], 'creative')
        self.assertEqual(profile['location']['dimension'], 'the_nether')
        self.assertEqual([(i['slot'], i['id'], i['count']) for i in profile['inventory']], [('hotbar.0', 'torch', 64)])
        self.assertEqual(profile['stats']['mined'][0], {'id': 'dirt', 'count': 50})
        self.assertEqual(profile['stats']['custom'], {'play_time': 72000, 'deaths': 2})
        self.assertEqual(profile['advancements'], ['story/root'])
        self.assertEqual(profile['vitals']['abilities'], {'flying': True, 'mayfly': True})
        self.assertEqual(profile['last_played'], 5)

    def test_items_carry_what_the_game_shows(self):
        sword = {'Slot': 9, 'id': 'minecraft:diamond_sword', 'count': 1, 'components': {
            'minecraft:enchantments': {'minecraft:sharpness': 5, 'minecraft:looting': 3}, 'minecraft:damage': 400,
            'minecraft:custom_name': {'text': 'Bob', 'extra': [{'text': "'s <b>"}]}}}
        old_format = {'Slot': -106, 'id': 'minecraft:shield', 'count': 1,
                      'components': {'minecraft:enchantments': {'levels': {'minecraft:unbreaking': 3}},
                                     'minecraft:custom_name': '{"text":"Wall"}', 'minecraft:unbreakable': {}}}
        book = {'Slot': 0, 'id': 'minecraft:enchanted_book', 'count': 1,
                'components': {'minecraft:stored_enchantments': {'minecraft:mending': 1}}}
        potion = {'Slot': 35, 'id': 'minecraft:potion', 'count': 2,
                  'components': {'minecraft:potion_contents': {'potion': 'minecraft:swiftness'}}}
        data = {'Inventory': [sword, old_format, potion, {'Slot': 77, 'id': 'minecraft:dirt'}],
                'EnderItems': [book, {'Slot': 30, 'id': 'minecraft:dirt'}],
                'equipment': {'head': {'id': 'minecraft:iron_helmet', 'count': 1, 'components': {'minecraft:damage': 10}},
                              'mainhand': {'id': 'minecraft:stick'}},
                'active_effects': [{'id': 'minecraft:speed', 'amplifier': 1, 'duration': 1210},
                                   {'id': 'minecraft:night_vision', 'amplifier': 'x', 'duration': -1}]}
        profile = helper.profile_from(data, {}, {})
        items = {i['slot']: i for i in profile['inventory']}
        self.assertEqual(sorted(items), ['armor.head', 'inventory.0', 'inventory.26', 'weapon.offhand'])
        self.assertEqual(items['inventory.0']['enchantments'], [['sharpness', 5], ['looting', 3]])
        self.assertEqual((items['inventory.0']['damage'], items['inventory.0']['max_damage']), (400, 1561))
        self.assertEqual(items['inventory.0']['name'], "Bob's <b>")
        self.assertEqual((items['weapon.offhand']['enchantments'], items['weapon.offhand']['name']), ([['unbreaking', 3]], 'Wall'))
        self.assertTrue(items['weapon.offhand']['unbreakable'])
        self.assertEqual((items['inventory.26']['potion'], items['inventory.26']['count']), ('swiftness', 2))
        self.assertEqual(items['armor.head']['max_damage'], 165)
        self.assertEqual(profile['ender_chest'][0]['slot'], 'enderchest.0')
        self.assertEqual(profile['ender_chest'][0]['stored_enchantments'], [['mending', 1]])
        self.assertEqual(len(profile['ender_chest']), 1)
        self.assertEqual(profile['effects'], [{'id': 'speed', 'level': 2, 'seconds': 60},
                                              {'id': 'night_vision', 'level': 1, 'seconds': -1}])

    def test_live_profile_reads_the_whole_player_once(self):
        panel = helper.Panel.__new__(helper.Panel)
        panel.runtime = Mock(data=Path('/nonexistent'))
        panel.players = Mock(return_value={'players': [{'uuid': '11111111-2222-3333-4444-555555555555', 'name': 'Steve',
                                                         'edition': 'java', 'online': True, 'op': False, 'banned': False,
                                                         'whitelisted': True, 'last_played': 1}]})
        panel.json_data = Mock(return_value=[])
        panel.rcon = Mock(return_value='Steve has the following entity data: {Health: 7.0f, foodLevel: 3, '
                          'Pos: [1.0d, 2.0d, 3.0d], SelectedItemSlot: 2, Inventory: [{Slot: 2b, id: "minecraft:bow", count: 1}]}')
        with patch.object(helper, 'read_data', side_effect=FileNotFoundError):
            profile = panel.player('11111111-2222-3333-4444-555555555555')
        self.assertTrue(profile['live'])
        self.assertEqual((profile['vitals']['health'], profile['location']['pos'], profile['selected_slot']), (7.0, [1.0, 2.0, 3.0], 2))
        self.assertEqual(profile['inventory'][0]['slot'], 'hotbar.2')
        self.assertEqual(panel.rcon.call_args_list[-1].args, ('data', 'get', 'entity', 'Steve'))
        panel.rcon.return_value = 'No entity was found'
        with patch.object(helper, 'read_data', side_effect=FileNotFoundError):
            self.assertFalse(panel.player('11111111-2222-3333-4444-555555555555')['live'])

    def test_slot_editor_actions(self):
        ok = (('slot-enchant', 'Steve', 'hotbar.0', 'sharpness', '5'), ('slot-enchant', 'Steve', 'armor.feet', 'mending', '1'),
              ('slot-rename', 'Steve', 'enderchest.26', 'Bob "the" blade'), ('slot-count', 'Steve', 'inventory.26', '99'),
              ('slot-set', 'Steve', 'weapon.offhand', 'totem_of_undying', '1'), ('slot-clear', 'Steve', 'armor.head'),
              ('effect-remove', 'Steve', 'speed'))
        for argv in ok:
            with self.subTest(argv=argv):
                helper.parse(['player-action', *argv])
        bad = (('slot-enchant', 'Steve', 'hotbar.9', 'sharpness', '1'), ('slot-enchant', 'Steve', 'hotbar.0', 'sharpness', '6'),
               ('slot-enchant', 'Steve', 'hotbar.0', 'mending', '2'), ('slot-set', 'Steve', 'hotbar.0', 'diamond_sword', '2'),
               ('slot-set', 'Steve', 'hotbar.0', 'ender_pearl', '17'),
               ('slot-enchant', 'Steve', 'hotbar.0', 'flight', '1'), ('slot-rename', 'Steve', 'hotbar.0', 'x' * 51),
               ('slot-rename', 'Steve', 'hotbar.0', 'a\nb'), ('slot-count', 'Steve', 'hotbar.0', '100'),
               ('slot-set', 'Steve', 'inventory.27', 'dirt', '1'), ('slot-clear', 'Steve', 'container.0'),
               ('effect-remove', 'Steve', 'minecraft:speed'), ('slot-clear', 'Steve', 'hotbar.0 @a'))
        for argv in bad:
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                helper.parse(['player-action', *argv])
        panel = helper.Panel.__new__(helper.Panel)
        steve = {'name': 'Steve', 'uuid': 'x', 'online': True}
        run = lambda action, *params: panel.player_commands(action, steve, list(params), {'Steve': steve})
        sword = {'Slot': 0, 'id': 'minecraft:diamond_sword', 'count': 1,
                 'components': {'minecraft:enchantments': {'minecraft:sharpness': 3}, 'minecraft:damage': 50}}
        panel.live_entity = Mock(return_value={'Inventory': [sword, {'Slot': 1, 'id': 'minecraft:diamond_pickaxe'},
                                                             {'Slot': 2, 'id': 'minecraft:dirt', 'count': 5}]})
        words = run('slot-enchant', 'hotbar.0', 'sharpness', '5')[0]
        self.assertEqual(words[:5], ('item', 'modify', 'entity', 'Steve', 'hotbar.0'))
        self.assertEqual(json.loads(words[5]), {'type': 'minecraft:set_enchantments', 'add': False,
                                                'enchantments': {'minecraft:sharpness': 5}})
        # Reglas del juego, contra el objeto que hay de verdad en la casilla.
        self.assertEqual(run('slot-enchant', 'hotbar.0', 'smite', '5'), 'Smite cannot be combined with Sharpness.')
        self.assertEqual(run('slot-enchant', 'hotbar.0', 'efficiency', '5'), 'A diamond sword cannot have Efficiency.')
        self.assertEqual(run('slot-enchant', 'hotbar.2', 'mending', '1'), 'A dirt cannot have Mending.')
        self.assertIsInstance(run('slot-enchant', 'hotbar.1', 'silk_touch', '1'), list)
        self.assertEqual(run('slot-enchant', 'hotbar.7', 'mending', '1'), 'That slot is empty.')
        self.assertEqual(run('slot-count', 'hotbar.0', '2'), 'A diamond sword stacks up to 1.')
        self.assertIsInstance(run('slot-count', 'hotbar.2', '64'), list)
        self.assertEqual(run('slot-repair', 'hotbar.2'), 'A dirt has no durability.')
        self.assertEqual(run('slot-unenchant', 'hotbar.1'), 'That item has no enchantments.')
        self.assertIsInstance(run('slot-unbreakable', 'hotbar.1'), list)
        panel.live_entity = Mock(return_value=None)
        self.assertEqual(run('slot-repair', 'hotbar.0'), 'Could not read their inventory.')
        panel.live_entity = Mock(return_value={'Inventory': [sword]})
        self.assertEqual(json.loads(run('slot-rename', 'hotbar.0', 'Bob "the" \\ blade')[0][5])['name']['text'], 'Bob "the" \\ blade')
        self.assertEqual(json.loads(run('slot-repair', 'hotbar.0')[0][5]), {'type': 'minecraft:set_damage', 'damage': 1.0})
        self.assertEqual(run('slot-clear', 'armor.head')[0], ('item', 'replace', 'entity', 'Steve', 'armor.head', 'with', 'minecraft:air'))
        self.assertEqual(run('slot-set', 'hotbar.3', 'bow', '1')[0][-2:], ('minecraft:bow', '1'))
        self.assertEqual(run('effect-remove', 'speed')[0], ('effect', 'clear', 'Steve', 'minecraft:speed'))
        panel.live_entity = Mock(return_value={'Inventory': [{'Slot': i, 'id': 'minecraft:dirt'} for i in range(4)]})
        self.assertEqual(run('slot-duplicate', 'hotbar.1')[0], ('item', 'replace', 'entity', 'Steve', 'hotbar.4', 'from',
                                                                'entity', 'Steve', 'hotbar.1'))
        self.assertEqual(run('slot-duplicate', 'hotbar.8'), 'That slot is empty.')
        panel.live_entity = Mock(return_value={'Inventory': [{'Slot': i, 'id': 'minecraft:dirt'} for i in range(36)]})
        self.assertIn('no free slot', run('slot-duplicate', 'hotbar.1'))

    def test_player_verbs_are_validated(self):
        self.assertEqual(helper.parse(['player-action', 'gamemode', '.Alguien', 'creative'])[1], ['gamemode', '.Alguien', 'creative'])
        self.assertEqual(helper.parse(['player-action', 'kick', 'Steve'])[1], ['kick', 'Steve'])
        for argv in (['player-action', 'gamemode', 'Steve'], ['player-action', 'kick', 'Steve', 'creative'],
                     ['player-action', 'rm', 'Steve'], ['player-action', 'op', 'Steve;stop'], ['player-action', 'op', '@a'],
                     ['player-action', 'gamemode', 'Steve', 'god'], ['player', '../../etc'], ['player', 'ABC'],
                     ['players', 'x']):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                helper.parse(argv)

    def test_root_writes_panel_config_without_following_links(self):
        import getpass
        import os
        tool = load('install_config')
        user = getpass.getuser()
        with tempfile.TemporaryDirectory() as directory, patch.object(tool.os, 'fchown'):
            base = Path(directory)
            (base / 'data').mkdir()
            # Organización anterior: panel.json y la contraseña inicial en la raíz.
            (base / 'panel.json').write_text(json.dumps({'password': {'hash': 'old'}, 'port': 1}))
            (base / 'initial-password').write_text('vieja\n')
            real_fstat = os.fstat  # la contraseña inicial antigua debe ser de root: se simula el propietario
            with patch.object(tool.os, 'fstat', side_effect=lambda fd: os.stat_result((*real_fstat(fd)[:4], 0, *real_fstat(fd)[5:]))):
                tool.configure(str(base), user, {'set': {'port': 8443}, 'password': None})
            config = json.loads((base / 'data/panel.json').read_text())
            self.assertEqual((config['port'], config['password']), (8443, {'hash': 'old'}))
            self.assertEqual((base / 'data/initial-password').read_text(), 'vieja\n')
            self.assertFalse((base / 'panel.json').exists() or (base / 'initial-password').exists())
            self.assertEqual((base / 'data/panel.json').stat().st_mode & 0o777, 0o600)
            # Un enlace plantado por el panel no se sigue: se rechaza sin tocar el destino.
            target = base / 'outside'
            target.write_text('intacto')
            (base / 'data/panel.json').unlink()
            (base / 'data/panel.json').symlink_to(target)
            with self.assertRaises(tool.Unsafe):
                tool.configure(str(base), user, {'set': {'port': 1}})
            self.assertEqual(target.read_text(), 'intacto')
            with self.assertRaises(tool.Unsafe):
                tool.open_dir(str(target))
            # Contraseña inicial: se escribe nueva y se borra al fijar otra.
            with patch('sys.stdin', Mock(buffer=Mock(read=lambda n: b'nueva\n'))):
                tool.main(['initial', str(base)])
            self.assertEqual((base / 'data/initial-password').read_text(), 'nueva\n')
            tool.main(['drop-initial', str(base)])
            self.assertFalse((base / 'data/initial-password').exists())
        self.assertIn('install_config.py" config "$CONFIG_DIR"', (ROOT / 'panel/install.sh').read_text())

    def test_hardened_player_inputs(self):
        for text in ('hola\u202eolleh', 'zero\u200bwidth', 'surrogate\ud800'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                helper.parse(['player-action', 'message', 'Steve', 'chat', text])
        self.assertEqual(helper.parse(['player-action', 'message', 'Steve', 'chat', 'Hola 😀'])[1][-1], 'Hola 😀')
        panel = helper.Panel.__new__(helper.Panel)
        panel.players = Mock(return_value={'players': [
            {'name': 'Steve', 'uuid': '11111111-2222-3333-4444-555555555555', 'online': True},
            {'name': 'Steve', 'uuid': '66666666-2222-3333-4444-555555555555', 'online': True}]})
        panel.rcon = Mock(return_value='ok')
        self.assertIn('Several known players', panel.player_action('op', 'Steve')['output'])
        panel.rcon.assert_not_called()
        panel.runtime = Mock(data=Path('/nonexistent'))
        with patch.object(helper, 'read_data', return_value=b''), patch.object(helper.nbt, 'loads', return_value={}), \
             patch.object(helper, 'profile_from', return_value={'location': {'death': {'dimension': 'overworld',
                                                                                         'pos': [float('inf'), 1, 2]}}}):
            self.assertIsNone(panel.stored_location('x', 'death'))
        app = server.App({'bind': '127.0.0.1', 'port': 1, 'password': {}}, helper=['/bin/true'])
        self.assertFalse(app.helper(['player-action', 'message', 'Steve', 'chat', 'nul\x00byte'])['ok'])

    def test_player_actions_only_for_known_players(self):
        panel = helper.Panel.__new__(helper.Panel)
        panel.players = Mock(return_value={'players': [
            {'name': 'Steve', 'uuid': '11111111-2222-3333-4444-555555555555', 'online': True},
            {'name': '.Alex', 'uuid': '00000000-0000-0000-0009-01fbcf3158a4', 'online': False}]})
        panel.rcon = Mock(return_value='ok')
        panel.runtime = Mock(data=Path('/nonexistent'))
        self.assertFalse(panel.player_action('op', 'Mallory')['ok'])
        panel.rcon.assert_not_called()
        panel.player_action('gamemode', 'Steve', 'creative')
        self.assertEqual(panel.rcon.call_args.args, ('gamemode', 'creative', 'Steve'))
        panel.player_action('ban', 'Steve')
        self.assertEqual(panel.rcon.call_args.args[:2], ('ban', 'Steve'))
        # Sin conexión sólo funcionan las políticas; el resto se rechaza antes de tocar RCON.
        panel.rcon.reset_mock()
        self.assertIn('not online', panel.player_action('give', '.Alex', 'diamond', '1')['output'])
        self.assertIn('not online', panel.player_action('tp-to', 'Steve', '.Alex')['output'])
        panel.rcon.assert_not_called()
        self.assertTrue(panel.player_action('op', '.Alex')['ok'])
        panel.rcon.return_value = 'Unknown item <--[HERE]'
        self.assertFalse(panel.player_action('give', 'Steve', 'nope', '1')['ok'])

    def test_new_player_actions_are_validated(self):
        valid = {
            ('give', 'Steve', 'diamond_sword', '1'), ('kit', 'Steve', 'starter'), ('xp', 'Steve', '-5'),
            ('xp', 'Steve', 'reset'), ('effect', '.Alex', 'night_vision', '300', '2'), ('tp-to', 'Steve', '.Alex'),
            ('tp-coords', 'Steve', 'overworld', '-120', 'surface', '45'), ('tp-coords', 'Steve', 'the_nether', '8', '70', '-8'),
            ('enchant', 'Steve', 'sharpness', '5'), ('pet', 'Steve', 'wolf'), ('message', 'Steve', 'chat', 'Hola "chicos" \\ 😀'),
            ('heal', 'Steve'), ('feed', 'Steve'), ('fireworks', 'Steve'), ('thunder', 'Steve'), ('tp-death', 'Steve'),
        }
        for argv in valid:
            with self.subTest(argv=argv):
                self.assertEqual(helper.parse(['player-action', *argv])[0], 'player-action')
        invalid = (
            ('give', 'Steve', 'Diamond', '1'), ('give', 'Steve', 'diamond', '65'), ('give', 'Steve', 'diamond', '0'),
            ('give', 'Steve', 'diamond 1 ; op @a', '1'), ('give', 'Steve', 'diamond'), ('kit', 'Steve', 'creative'),
            ('xp', 'Steve', '1000'), ('effect', 'Steve', 'wither', '300', '1'), ('effect', 'Steve', 'speed', '86400', '1'),
            ('effect', 'Steve', 'speed', '300', '3'), ('tp-to', 'Steve', 'Steve'), ('tp-to', 'Steve', '@a'),
            ('tp-coords', 'Steve', 'the_nether', '1', 'surface', '1'), ('tp-coords', 'Steve', 'overworld', '1', '400', '1'),
            ('tp-coords', 'Steve', 'overworld', '99999999', '64', '1'), ('tp-coords', 'Steve', 'overworld', '~', '64', '1'),
            ('enchant', 'Steve', 'silk_touch', '2'), ('enchant', 'Steve', 'sharpness', '6'), ('pet', 'Steve', 'ender_dragon'),
            ('message', 'Steve', 'chat', ''), ('message', 'Steve', 'chat', 'a' * 101), ('message', 'Steve', 'chat', 'line\nbreak'),
            ('message', 'Steve', 'chat', '§cred'), ('message', 'Steve', 'book', 'hi'), ('heal', 'Steve', 'extra'),
        )
        for argv in invalid:
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                helper.parse(['player-action', *argv])

    def test_player_action_commands(self):
        panel = helper.Panel.__new__(helper.Panel)
        panel.runtime = Mock(data=Path('/nonexistent'))
        steve = {'name': 'Steve', 'uuid': '00000000-0000-0000-0009-01fbcf3158a4', 'online': True}
        known = {'Steve': steve, 'Alex': {'name': 'Alex', 'uuid': 'x', 'online': True},
                 'Offline': {'name': 'Offline', 'uuid': 'y', 'online': False}}
        run = lambda action, *params: panel.player_commands(action, steve, list(params), known)
        self.assertEqual(run('give', 'diamond', '5'), [('give', 'Steve', 'minecraft:diamond', '5')])
        self.assertEqual(len(run('kit', 'warrior')), len(helper.catalog.KITS['warrior'][3]))
        self.assertEqual(run('xp', 'reset'), [('xp', 'set', 'Steve', '0', 'levels'), ('xp', 'set', 'Steve', '0', 'points')])
        self.assertEqual(run('effect', 'speed', '60', '2'), [('effect', 'give', 'Steve', 'minecraft:speed', '60', '1')])
        self.assertEqual(run('bring', 'Alex'), [('tp', 'Alex', 'Steve')])
        self.assertEqual(run('tp-to', 'Offline'), 'Offline is not online.')
        self.assertEqual(run('tp-coords', 'overworld', '10', 'surface', '-20'),
                         [('spreadplayers', '10', '-20', '0', '1', 'false', 'Steve')])
        self.assertEqual(run('tp-coords', 'the_end', '0', '70', '0')[0][:6], ('execute', 'in', 'minecraft:the_end', 'run', 'tp', 'Steve'))
        # El texto viaja como componente JSON escapado: las comillas no pueden cerrar el comando.
        words = run('message', 'chat', 'hi "there" \\ {x}')[0]
        self.assertEqual(words[:2], ('tellraw', 'Steve'))
        self.assertEqual(json.loads(words[2])[1]['text'], 'hi "there" \\ {x}')
        self.assertEqual(run('message', 'title', 'Hola')[0][:3], ('title', 'Steve', 'title'))
        # Mascota domesticada: el dueño es el UUID del jugador como int-array.
        self.assertIn('Owner:[I;0,0,590331,-818849628]', run('pet', 'cat')[0][-1])
        rocket = run('fireworks')[0][-1]
        self.assertEqual(rocket.count('{'), rocket.count('}'))
        # Muerte: protección antes de llevarlo; al vacío no.
        panel.stored_location = Mock(return_value=('overworld', [5, 12, 7]))
        commands = run('tp-death')
        self.assertEqual([c[3] for c in commands[:3]], ['minecraft:resistance', 'minecraft:fire_resistance', 'minecraft:slow_falling'])
        self.assertEqual(commands[-1][-3:], ('5.5', '12', '7.5'))
        panel.stored_location = Mock(return_value=('overworld', [5, -90, 7]))
        self.assertIn('void', run('tp-death'))
        panel.stored_location = Mock(return_value=None)
        self.assertIn('no bed', run('tp-respawn'))

    def test_world_verbs_are_validated(self):
        self.assertEqual(helper.parse(['world-create', 'My World', '-42'])[1], ['My World', '-42'])
        self.assertEqual(helper.parse(['world-import', 'Xemitest_world.mcworld', 'Xemi'])[1], ['Xemitest_world.mcworld', 'Xemi'])
        self.assertEqual(helper.parse(['world-export', 'main', 'bedrock'])[1], ['main', 'bedrock'])
        for argv in (['world-create', ''], ['world-create', 'x', 'seed'], ['world-activate', '../main'],
                     ['world-activate', 'Main'], ['world-export', 'main', 'png'], ['world-import', '../../etc/shadow.zip', 'x'],
                     ['world-import', 'evil.sh', 'x'], ['world-import', 'a/b.zip', 'x'], ['world-rename', 'main', 'a\nb'],
                     ['world-delete']):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                helper.parse(argv)

    def test_pack_verbs_are_validated_and_become_manage_commands(self):
        self.assertEqual(helper.parse(['pack-use', 'faithful-32x', 'optional', 'on', '26.3', '1.21.5', '26.3']),
                         ('pack-use', ['faithful-32x', 'optional', 'on', '26.3', '1.21.5'], False))
        self.assertEqual(helper.parse(['pack-use', 'faithful-32x', 'required', 'on'])[1], ['faithful-32x', 'required', 'on'])
        self.assertEqual(helper.parse(['packs']), ('packs', [], False))
        for argv in (['pack-use', 'bare-bones', 'optional', 'on'], ['pack-use', 'faithful-32x', 'optional', 'on', '1.8.9'],
                     ['pack-use', 'faithful-32x', 'always', 'on'], ['pack-use', 'faithful-32x', 'optional', 'yes'],
                     ['pack-use', 'faithful-32x', 'optional', 'off'], ['pack-use', 'faithful-32x'],
                     ['pack-use', '../x', 'optional', 'on'], ['pack-off', 'now'], ['packs', 'x']):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                helper.parse(argv)
        panel = Mock()
        helper.execute(panel, 'pack-use', ['faithful-32x', 'required', 'off', '1.21.5'])
        panel.manage.assert_called_with('pack', 'use', 'faithful-32x', '--bedrock', 'off', '--java', '1.21.5', '--required')
        helper.execute(panel, 'pack-use', ['faithful-32x', 'optional', 'on'])
        panel.manage.assert_called_with('pack', 'use', 'faithful-32x', '--bedrock', 'on', '--no-java')
        helper.execute(panel, 'pack-off', [])
        panel.manage.assert_called_with('pack', 'off')
        helper.execute(panel, 'pack-clean', [])
        panel.manage.assert_called_with('pack', 'clean')

    def test_pack_listing_includes_this_session_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'state').mkdir()
            (root / 'state/active.json').write_text(json.dumps({'minecraft': '26.3', 'lan_ip': '192.168.1.50', 'mclan_packs': {}}))
            (root / 'data/logs').mkdir(parents=True)
            (root / 'data/logs/latest.log').write_text(
                '[17:00:00 INFO]: Done (12.0s)!\n[17:00:01 INFO]: [MclanPacks] \u00a7aNo resource pack selected.\n'
                '[17:05:00 INFO]: [MclanPacks] Steve (1.21.5): Faithful 32x 1.21.5 SUCCESSFULLY_LOADED\n')
            listing = helper.Panel(root).packs()
        self.assertTrue(listing['installed'])
        self.assertEqual(listing['events'], ['[17:00:01 INFO]: [MclanPacks] No resource pack selected.',
                                             '[17:05:00 INFO]: [MclanPacks] Steve (1.21.5): Faithful 32x 1.21.5 SUCCESSFULLY_LOADED'])
        self.assertIsNone(listing['active'])
        faithful = next(p for p in listing['packs'] if p['id'] == 'faithful-32x')
        self.assertEqual(faithful['server_variant'], '26.3')
        self.assertEqual(listing['stored'], 0)

    def test_floodgate_uuid_from_xuid(self):
        self.assertEqual(helper.floodgate_uuid('2535412345678901'), '00000000-0000-0000-0009-01f1b0392c35')

    def test_rejected_attempts_from_real_and_java_log_lines(self):
        # Formato real de data/logs/latest.log (Paper 26.3), más dos líneas Java del mismo formato.
        log = ('[16:29:04] [localSession-7-2/INFO]: [Geyser-Spigot] Xeministrator has disconnected from the Java '
               'server because of §rYou are not whitelisted on this server!\n'
               '[16:29:04] [Server thread/INFO]: Disconnecting /192.168.2.100:0: You are not whitelisted on this server!\n'
               '[16:49:36] [localSession-9-2/INFO]: [Geyser-Spigot] Xeministrator has disconnected from the Java '
               'server because of §rYou are not whitelisted on this server!\n'
               '[17:00:00] [Server thread/INFO]: Disconnecting Steve (/192.168.2.20:51234): You are not white-listed on this server!\n'
               '[17:01:00] [Server thread/INFO]: Alex (/192.168.2.21:50000) lost connection: You are not whitelisted on this server!\n'
               # Un jugador admitido escribe en el chat el texto de un rechazo: no debe aparecer.
               '[17:02:00] [Async Chat Thread - #0/INFO]: <Steve> [Geyser-Spigot] Intruso has disconnected from the '
               'Java server because of §rYou are not whitelisted on this server!\n'
               '[17:03:00] [Async Chat Thread - #0/INFO]: <Steve> Mallory (/1.2.3.4:5) lost connection: You are not '
               'whitelisted on this server!\n')
        found = helper.rejected_attempts(log, set())
        self.assertEqual([(r['edition'], r['name'], r['last_seen']) for r in found],
                         [('bedrock', 'Xeministrator', '16:49:36'), ('java', 'Steve', '17:00:00'), ('java', 'Alex', '17:01:00')])
        self.assertEqual([r['name'] for r in helper.rejected_attempts(log, {'.xeministrator', 'steve'})], ['Alex'])

    def test_rejections_survive_container_recreation_via_paper_logs(self):
        import gzip
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory) / 'data/logs'
            logs.mkdir(parents=True)
            line = ('[16:29:04] [localSession-7-2/INFO]: [Geyser-Spigot] Xeministrator has disconnected from the Java '
                    'server because of §rYou are not whitelisted on this server!\n')
            with gzip.open(logs / '2026-10-18-1.log.gz', 'wt') as stream:
                stream.write(line)
            (logs / 'latest.log').write_text('[18:00:00] [Server thread/INFO]: Done (25.8s)!\n')
            panel = helper.Panel.__new__(helper.Panel)
            panel.runtime = Mock(data=Path(directory) / 'data')
            (logs / 'evil.log.gz').symlink_to(logs / '2026-10-18-1.log.gz')
            found = helper.rejected_attempts(panel.paper_logs(), set())
            self.assertEqual([(r['name'], r['last_seen']) for r in found], [('Xeministrator', '16:29:04')])

    def test_set_properties_stops_edits_and_restarts_only_if_running(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'data'
            data.mkdir()
            (data / 'server.properties').write_text('online-mode=true\ndifficulty=normal\n')
            panel = helper.Panel.__new__(helper.Panel)
            panel.runtime = Mock(data=data)
            panel.runtime.running.return_value = True
            with patch.object(helper.os, 'fchown'):
                result = panel.set_properties({'difficulty': 'hard'})
            self.assertTrue(result['ok'])
            self.assertIn('difficulty=hard', (data / 'server.properties').read_text())
            panel.runtime.stop.assert_called_once()
            panel.runtime.start.assert_called_once()
            panel.runtime.running.return_value = False
            panel.runtime.start.reset_mock()
            with patch.object(helper.os, 'fchown'):
                panel.set_properties({'difficulty': 'easy'})
            panel.runtime.start.assert_not_called()

    def test_open_for_always_reenables_whitelist(self):
        panel = helper.Panel.__new__(helper.Panel)
        panel.rcon = Mock(return_value='')
        panel.whitelist = Mock(return_value=[])
        failures = iter([RuntimeError('docker caído')])
        def flaky(since=None):
            error = next(failures, None)
            if error:
                raise error
            return ''
        panel.server_log = Mock(side_effect=flaky)
        with self.assertRaises(RuntimeError):
            panel.open_for('bedrock', 'Alguien', 1)
        calls = [c.args for c in panel.rcon.call_args_list]
        self.assertEqual(calls[0], ('whitelist', 'off'))
        self.assertIn(('whitelist', 'on'), calls)

    def test_open_for_admits_only_the_chosen_edition_and_kicks_others(self):
        panel = helper.Panel.__new__(helper.Panel)
        panel.rcon = Mock(return_value='')
        log = ('[17:00:00 INFO]: Alguien[/192.168.2.50:5000] logged in with entity id 6\n'
               '[17:00:01 INFO]: .Alguien[/192.168.2.100:0] logged in with entity id 7\n'
               '[17:00:02 INFO]: <Steve> .Fake[/1.1.1.1:1] logged in with entity id 9\n')
        panel.server_log = Mock(return_value=log)
        panel.whitelist = Mock(return_value=[{'name': '.Alguien', 'uuid': '00000000-0000-0000-0009-01f1b0392c35'}])
        result = panel.open_for('bedrock', 'Alguien', 1)
        self.assertTrue(result['ok'])
        calls = [c.args for c in panel.rcon.call_args_list]
        self.assertIn(('whitelist', 'add', '.Alguien'), calls)
        self.assertNotIn(('whitelist', 'add', 'Alguien'), calls)
        self.assertIn(('kick', 'Alguien', 'Whitelist reactivada'), calls)
        self.assertNotIn(('kick', '.Fake', 'Whitelist reactivada'), calls)
        self.assertLess(calls.index(('whitelist', 'on')), calls.index(('kick', 'Alguien', 'Whitelist reactivada')))

    def test_data_access_never_follows_links_or_special_files(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'data'
            (data / 'logs').mkdir(parents=True)
            secret = Path(directory) / 'secret'
            secret.write_text('root-only')
            (data / 'server.properties').symlink_to(secret)
            with self.assertRaises(OSError):
                helper.read_data(data, 'server.properties', 100)
            (data / 'server.properties').unlink()
            (data / 'server.properties').write_text('a=1\n')
            os_module = helper.os
            os_module.mkfifo(data / 'fifo')
            with self.assertRaises(OSError):
                helper.read_data(data, 'fifo', 100)
            (data / 'outside').symlink_to(Path(directory), target_is_directory=True)
            with self.assertRaises(OSError):
                helper.read_data(data, 'outside/secret', 100)
            self.assertEqual(helper.read_data(data, 'server.properties', 2, tail=True), b'1\n')
            # Escritura: sustituye un enlace por un archivo propio sin tocar su destino.
            (data / 'server.properties').unlink()
            (data / 'server.properties').symlink_to(secret)
            with patch.object(helper.os, 'fchown'):
                helper.write_data(data, 'server.properties', 'b=2\n')
            self.assertEqual(secret.read_text(), 'root-only')
            self.assertFalse((data / 'server.properties').is_symlink())
            self.assertEqual((data / 'server.properties').read_text(), 'b=2\n')

    def test_malformed_whitelist_is_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            panel = helper.Panel.__new__(helper.Panel)
            panel.runtime = Mock(data=Path(directory))
            self.assertEqual(panel.whitelist(), [])
            (Path(directory) / 'whitelist.json').write_text('{"name": "x"}')
            with self.assertRaises(helper.Error):
                panel.whitelist()


class PanelNetworkTests(unittest.TestCase):
    """Servidor real con TLS: redirección HTTP y aislamiento de clientes que no hablan."""

    def setUp(self):
        import shutil
        import ssl
        import subprocess
        if not shutil.which('openssl'):
            self.skipTest('openssl no disponible')
        self.temp = tempfile.TemporaryDirectory()
        cert, key = Path(self.temp.name) / 'c.pem', Path(self.temp.name) / 'k.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                        '-days', '1', '-subj', '/CN=test', '-addext', 'subjectAltName=IP:127.0.0.1',
                        '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        app = server.App({'bind': '127.0.0.1', 'port': 0, 'map_url': 'http://127.0.0.1:8100/',
                          'password': server.hash_password('contraseña-larga', iterations=1000)})
        self.httpd = server.PanelServer(('127.0.0.1', 0), server.Handler, app, context)
        app.config['port'] = self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.client = ssl.create_default_context(cafile=str(cert))

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.temp.cleanup()

    def test_plain_http_is_redirected_to_https(self):
        import socket
        with socket.create_connection(('127.0.0.1', self.port), timeout=5) as sock:
            sock.sendall(b'GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n')
            reply = sock.recv(4096).decode()
        self.assertTrue(reply.startswith('HTTP/1.1 301'))
        self.assertIn(f'Location: https://127.0.0.1:{self.port}/', reply)

    def test_connections_per_ip_are_capped(self):
        import socket
        idle = [socket.create_connection(('127.0.0.1', self.port), timeout=5) for _ in range(server.MAX_CONNECTIONS_PER_IP)]
        try:
            time.sleep(0.2)
            extra = socket.create_connection(('127.0.0.1', self.port), timeout=5)
            self.assertEqual(extra.recv(10), b'')  # cerrada por el servidor
            extra.close()
        finally:
            for sock in idle:
                sock.close()
        time.sleep(0.3)
        with urlopen(f'https://127.0.0.1:{self.port}/login', context=self.client, timeout=5) as response:
            self.assertEqual(response.status, 200)

    def test_silent_client_does_not_block_other_users(self):
        import socket
        silent = socket.create_connection(('127.0.0.1', self.port), timeout=5)
        try:
            started = time.monotonic()
            with urlopen(f'https://127.0.0.1:{self.port}/login', context=self.client, timeout=5) as response:
                self.assertEqual(response.status, 200)
            self.assertLess(time.monotonic() - started, 3)
        finally:
            silent.close()


class PanelAppTests(unittest.TestCase):
    def setUp(self):
        self.helper = Mock(side_effect=self.fake_helper)
        self.calls = []
        self.assets = tempfile.TemporaryDirectory()
        self.addCleanup(self.assets.cleanup)
        (Path(self.assets.name) / 'logo.png').write_bytes(b'\x89PNG\r\n\x1a\nfake')
        config = {'bind': '192.168.1.50', 'port': 8443, 'map_url': 'http://192.168.1.50:8100/',
                  'password': server.hash_password('contraseña-larga', iterations=1000), 'assets_dir': self.assets.name}
        self.app = server.App(config)
        self.app.helper = self.helper
        self.app.jobs.runner = self.helper

    def fake_helper(self, argv, timeout=60):
        self.calls.append(argv)
        if argv[0] == 'status':
            return status()
        return {'ok': True, 'output': 'hecho'}

    def request(self, method, path, body='', cookie=None, origin=None, ip='192.168.1.9', host='192.168.1.50:8443'):
        headers = {'Host': host}
        if cookie:
            headers['Cookie'] = cookie
        if origin:
            headers['Origin'] = origin
        return self.app.handle(method, path, headers, body.encode(), ip)

    def login(self):
        code, headers, _ = self.request('POST', '/login', 'password=contrase%C3%B1a-larga')
        self.assertEqual(code, 303)
        cookie = headers['Set-Cookie'].split(';')[0]
        for flag in ('Secure', 'HttpOnly', 'SameSite=Strict'):
            self.assertIn(flag, headers['Set-Cookie'])
        return cookie, self.app.sessions[cookie.split('=', 1)[1]]['csrf']

    def test_anonymous_requests_redirect_to_login(self):
        for method, path in (('GET', '/'), ('GET', '/players'), ('POST', '/action'), ('GET', '/map'), ('GET', '/console')):
            self.assertEqual(self.request(method, path)[:2], (303, {'Location': '/login'}))
        for method, path in (('GET', '/api/pulse'), ('GET', '/api/console'), ('POST', '/api/console')):
            self.assertEqual(self.request(method, path)[0], 401)
        self.helper.assert_not_called()

    def test_foreign_host_header_is_rejected(self):
        # DNS rebinding: una página externa que resuelve su dominio a la Pi no llega a las rutas.
        self.assertEqual(self.request('GET', '/login', host='evil.example')[0], 421)
        self.assertEqual(self.request('POST', '/login', 'password=contrase%C3%B1a-larga', host='evil.example:8443')[0], 421)
        self.assertEqual(self.request('GET', '/static/app.css', host='evil.example')[0], 421)
        self.assertEqual(self.request('GET', '/login', host='192.168.1.50')[0], 200)

    def test_static_files_and_textures_are_allowlisted(self):
        code, headers, body = self.request('GET', '/static/app.css')
        self.assertEqual((code, headers['Content-Type']), (200, 'text/css; charset=utf-8'))
        self.assertIn(b'Pixelify', body)
        self.assertEqual(self.request('GET', '/static/fonts/VT323-Regular.ttf')[1]['Content-Type'], 'font/ttf')
        self.assertEqual(self.request('GET', '/assets/logo.png')[:2], (200, {'Content-Type': 'image/png',
                                                                            'Cache-Control': 'public, max-age=3600'}))
        for path in ('/static/../helper.py', '/static/%2e%2e/helper.py', '/static/helper.py', '/assets/../panel.json',
                     '/static/fonts/../../server.py', '/assets/LOGO.PNG', '/static/app.css.map'):
            with self.subTest(path=path):
                self.assertIn(self.request('GET', path)[0], (303, 404))

    def test_legacy_spanish_routes_redirect(self):
        cookie, _ = self.login()
        for old, new in (('/whitelist', '/players'), ('/ajustes', '/world'), ('/logs', '/console'),
                         ('/auditoria', '/health'), ('/cuenta', '/account')):
            self.assertEqual(self.request('GET', old, cookie=cookie)[:2], (301, {'Location': new}))

    def test_every_page_renders_in_english_with_hotbar_and_assets(self):
        cookie, _ = self.login()
        self.app.helper = Mock(side_effect=lambda argv, timeout=60: status() if argv[0] == 'status' else
                               {'ok': True, 'output': '== Contenedor\nOK     En marcha\nAVISO  Whitelist\nFALLO  Algo',
                                'log': '[17:00:00 INFO]: <script>x</script>'})
        for path, marker in (('/', b'How to connect'), ('/players', b'Rejected attempts'), ('/world', b'Server settings'),
                             ('/console', b'paper@craft-pi'), ('/backups', b'Latest backups'), ('/health', b'Server audit'),
                             ('/account', b'Change password')):
            with self.subTest(path=path):
                code, _, body = self.request('GET', path, cookie=cookie)
                self.assertEqual(code, 200)
                self.assertIn(marker, body)
                self.assertIn(b'class="hotbar"', body)
                self.assertIn(b'/assets/logo.png', body)
                self.assertNotIn(b'<script>x', body)
                self.assertNotIn(b' style=', body)  # la CSP prohíbe estilos en línea
                self.assertEqual(body.count(b'<script'), 1)  # sólo /static/app.js
                self.assertRegex(body.decode(), r'/static/app\.css\?v=[0-9a-f]{12}"')
        health = self.request('GET', '/health', cookie=cookie)[2]
        self.assertIn(b'<span class="badge">FAIL</span>', health)

    def test_console_api_requires_csrf_header_and_validates_command(self):
        cookie, csrf = self.login()
        headers = {'Host': '192.168.1.50:8443', 'Cookie': cookie, 'Origin': 'https://192.168.1.50:8443'}
        body = json.dumps({'command': 'time set day'}).encode()
        self.assertEqual(self.app.handle('POST', '/api/console', headers, body, '192.168.1.9')[0], 403)
        code, _, payload = self.app.handle('POST', '/api/console', dict(headers, **{'X-CSRF-Token': csrf}), body, '192.168.1.9')
        self.assertEqual(code, 200)
        self.assertEqual(self.calls[-1], ['console', 'time set day'])
        evil = dict(headers, **{'X-CSRF-Token': csrf, 'Origin': 'https://evil.example'})
        self.assertEqual(self.app.handle('POST', '/api/console', evil, body, '192.168.1.9')[0], 403)
        self.assertEqual(self.app.handle('POST', '/api/console', dict(headers, **{'X-CSRF-Token': csrf}), b'{nope',
                                         '192.168.1.9')[0], 400)
        self.assertEqual(json.loads(self.request('GET', '/api/console?lines=999999', cookie=cookie)[2])['ok'], True)
        self.assertEqual(self.calls[-1], ['logs', '200'])

    def test_map_controls_and_spawn_link(self):
        cookie, csrf = self.login()
        body = self.request('GET', '/world', cookie=cookie)[2]
        self.assertIn(b'#world:-32:64:0:150', body)
        self.assertIn(b'Pause rendering', body)
        self.assertIn(b'Open live map at spawn', self.request('GET', '/', cookie=cookie)[2])
        self.assertEqual(self.request('GET', '/map', cookie=cookie)[1]['Location'],
                         'http://192.168.1.50:8100/#world:-32:64:0:150:0:0:0:0:perspective')
        self.request('POST', '/world', f'csrf={csrf}&do=render&value=off', cookie)
        self.assertEqual(self.calls[-1], ['bluemap-render', 'off'])
        self.app.jobs.runner = Mock(return_value={'ok': True, 'output': ''})
        location = self.request('POST', '/world', f'csrf={csrf}&do=plugin&value=off', cookie)[1]['Location']
        self.assertTrue(location.startswith('/job/'))
        for _ in range(50):
            if self.app.jobs.runner.called:
                break
            time.sleep(0.02)
        self.assertEqual(self.app.jobs.runner.call_args.args[0], ['plugin', 'bluemap', 'off'])
        self.assertEqual(self.request('POST', '/world', f'csrf={csrf}&do=plugin&value=rm', cookie)[0], 400)

    def test_login_requires_same_origin_and_concurrent_attempts_respect_limit(self):
        self.assertEqual(self.request('POST', '/login', 'password=x', origin='https://evil.example')[0], 403)
        # Lo que envía un navegador real desde el formulario del propio panel.
        self.assertEqual(self.request('POST', '/login', 'password=contrase%C3%B1a-larga',
                                      origin='https://192.168.1.50:8443')[0], 303)
        results = []
        def attempt():
            results.append(self.request('POST', '/login', 'password=mal', ip='192.168.1.77')[0])
        threads = [threading.Thread(target=attempt) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count(200), server.MAX_FAILURES)
        self.assertEqual(results.count(429), 20 - server.MAX_FAILURES)

    def test_wrong_passwords_are_throttled(self):
        for _ in range(server.MAX_FAILURES):
            self.assertEqual(self.request('POST', '/login', 'password=mal')[0], 200)
        self.assertEqual(self.request('POST', '/login', 'password=contrase%C3%B1a-larga')[0], 429)
        self.assertEqual(self.request('POST', '/login', 'password=contrase%C3%B1a-larga', ip='192.168.1.10')[0], 303)

    def test_posts_require_csrf_and_same_origin(self):
        cookie, csrf = self.login()
        self.assertEqual(self.request('POST', '/action', 'do=stop', cookie)[0], 403)
        self.assertEqual(self.request('POST', '/action', f'do=stop&csrf={csrf}', cookie, origin='https://evil.example')[0], 403)
        self.assertNotIn(['stop'], self.calls)
        code, headers, _ = self.request('POST', '/action', f'do=stop&csrf={csrf}', cookie, origin='https://192.168.1.50:8443')
        self.assertEqual(code, 303)
        self.assertTrue(headers['Location'].startswith('/job/'))

    def test_dashboard_escapes_server_provided_text(self):
        cookie, _ = self.login()
        code, _, body = self.request('GET', '/', cookie=cookie)
        self.assertEqual(code, 200)
        self.assertNotIn(b'<script>alert', body)
        self.assertIn(b'&lt;script&gt;', body)
        self.assertIn(b'192.168.1.50:25565', body)
        self.assertIn(b'Hola &lt;b&gt;', self.request('GET', '/world', cookie=cookie)[2])

    def test_only_one_long_job_at_a_time(self):
        cookie, csrf = self.login()
        release = threading.Event()
        def slow(argv, timeout=60):
            release.wait(5)
            return {'ok': True, 'output': 'fin'}
        self.app.jobs.runner = slow
        first = self.request('POST', '/action', f'do=backup&csrf={csrf}', cookie)
        second = self.request('POST', '/action', f'do=restart&csrf={csrf}', cookie)
        self.assertTrue(first[1]['Location'].startswith('/job/'))
        self.assertEqual(second[1]['Location'], '/')
        job = first[1]['Location'].rsplit('/', 1)[1]
        page = self.request('GET', first[1]['Location'], cookie=cookie)[2]
        self.assertIn(b'http-equiv="refresh"', page)
        self.assertIn(f'data-job="{job}"'.encode(), page)
        self.assertEqual(json.loads(self.request('GET', f'/api/job/{job}', cookie=cookie)[2])['state'], 'running')
        release.set()
        for _ in range(50):
            if not self.app.jobs.busy():
                break
            time.sleep(0.05)
        self.assertIn(b'Finished', self.request('GET', first[1]['Location'], cookie=cookie)[2])
        self.assertEqual(json.loads(self.request('GET', f'/api/job/{job}', cookie=cookie)[2])['state'], 'done')

    def test_settings_send_only_changed_values(self):
        cookie, csrf = self.login()
        body = (f'csrf={csrf}&do=settings&motd=Hola+%3Cb%3E&difficulty=hard&gamemode=survival&max-players=6&view-distance=6'
                '&simulation-distance=4&pvp=true&allow-flight=false&spawn-protection=16&online-mode=false')
        self.app.jobs.runner = Mock(return_value={'ok': True, 'output': ''})
        code, headers, _ = self.request('POST', '/world', body, cookie)
        self.assertEqual(code, 303)
        for _ in range(50):
            if self.app.jobs.runner.called:
                break
            time.sleep(0.02)
        self.assertEqual(self.app.jobs.runner.call_args.args[0], ['set-properties', 'difficulty=hard'])

    def test_whitelist_actions_map_to_helper_verbs(self):
        cookie, csrf = self.login()
        self.request('POST', '/players', f'csrf={csrf}&do=add&edition=bedrock&name=Otro', cookie)
        self.request('POST', '/players', f'csrf={csrf}&do=remove&name=.Alguien', cookie)
        self.request('POST', '/players', f'csrf={csrf}&do=switch&value=off', cookie)
        self.assertEqual(self.calls[-3:], [['whitelist-add', 'bedrock', 'Otro'], ['whitelist-remove', '.Alguien'],
                                           ['whitelist', 'off']])
        self.assertEqual(self.request('POST', '/players', f'csrf={csrf}&do=add&edition=root&name=x', cookie)[0], 400)

    def test_unknown_bedrock_gamertag_offers_temporary_opening(self):
        cookie, csrf = self.login()
        page = self.request('GET', '/players', cookie=cookie)[2]
        self.assertIn(b'Open 5 min and wait', page)
        self.app.helper = Mock(side_effect=lambda argv, timeout=60: status() if argv[0] == 'status' else {
            'ok': False, 'output': '[18:15:19 ERROR]: [floodgate] Got an error from requesting the xuid of a Bedrock '
                                   'player: Unable to find user in our cache. Please try specifying their Floodgate UUID instead'})
        self.request('POST', '/players', f'csrf={csrf}&do=add&edition=bedrock&name=Otro', cookie)
        page = self.request('GET', '/players', cookie=cookie)[2].decode()
        self.assertIn('Floodgate does not know Otro yet', page)
        self.assertNotIn('Got an error', page)

    def test_password_change_invalidates_other_sessions(self):
        first, _ = self.login()
        cookie, csrf = self.login()
        with tempfile.TemporaryDirectory() as directory:
            self.app.config_path = Path(directory) / 'panel.json'
            (Path(directory) / 'initial-password').write_text('vieja\n')
            body = f'csrf={csrf}&current=contrase%C3%B1a-larga&new=otra-clave-segura&repeat=otra-clave-segura'
            self.request('POST', '/account', body, cookie)
            self.assertTrue(server.check_password('otra-clave-segura', self.app.config['password']))
            self.assertFalse((Path(directory) / 'initial-password').exists())
            self.assertTrue(self.app.config_path.exists())
        self.assertEqual(self.request('GET', '/', cookie=first)[0], 303)
        self.assertEqual(self.request('GET', '/', cookie=cookie)[0], 200)

    def test_profile_page_and_actions(self):
        cookie, csrf = self.login()
        uuid = '00000000-0000-0000-0009-01f1b0392c35'
        listing = {'ok': True, 'players': [{'uuid': uuid, 'name': '.Alguien', 'edition': 'bedrock', 'online': True, 'op': False,
                                            'banned': False, 'whitelisted': True, 'last_played': None}]}
        profile = dict(listing['players'][0], ok=True, xuid=123, ban=None, op_level=None, first_played=None,
                       vitals={'health': 10.0, 'food': 8, 'gamemode': 'survival', 'abilities': {}},
                       location={'dimension': 'overworld', 'pos': [1.0, 64.0, 2.0], 'death': None, 'respawn': None},
                       inventory=[{'slot': 0, 'id': '<script>', 'count': 3}], ender_chest=[], equipment={},
                       stats={'custom': {'play_time': 2400}, 'mined': [{'id': 'stone', 'count': 5}]}, advancements=['story/root'])
        def fake(argv, timeout=60):
            self.calls.append(argv)
            return {'players': listing, 'player': profile, 'status': status()}.get(argv[0], {'ok': True, 'output': 'done'})
        self.app.helper = fake
        body = self.request('GET', '/players', cookie=cookie)[2]
        self.assertIn(f'href="/players/{uuid}"'.encode(), body)
        code, _, page = self.request('GET', f'/players/{uuid}', cookie=cookie)
        self.assertEqual(code, 200)
        for marker in (b'Vitals', b'Inventory', b'Statistics', b'Actions', b'Make operator', b'2 min', b'Story',
                       b'Give an item', b'Starter', b'Night vision', b'Last death', b'Thunderclap', b'Sharpness',
                       b'Message .Alguien', b'data-player-action="/api/players/'):
            self.assertIn(marker, page)
        self.assertNotIn(b'<script>', page)
        self.assertNotIn(b' disabled>', page.split(b'pa-admin-panel')[0].split(b'player-actions')[1])
        self.request('POST', f'/players/{uuid}', f'csrf={csrf}&do=gamemode&mode=creative', cookie)
        self.assertEqual(self.calls[-1], ['player-action', 'gamemode', '.Alguien', 'creative'])
        self.request('POST', f'/players/{uuid}', f'csrf={csrf}&do=op', cookie)
        self.assertEqual(self.calls[-1], ['player-action', 'op', '.Alguien'])
        other = '11111111-1111-1111-1111-111111111111'
        self.assertEqual(self.request('POST', f'/players/{other}', f'csrf={csrf}&do=op', cookie)[0], 400)
        self.assertEqual(self.request('POST', f'/players/{uuid}', f'csrf={csrf}&do=rm', cookie)[0], 400)
        self.assertEqual(self.request('POST', f'/players/{uuid}', 'do=op', cookie)[0], 403)
        # Objetos por nombre o id del catálogo; un objeto desconocido no llega al helper.
        self.app.items = {'diamond_sword': 'Diamond Sword', 'oak_log': 'Oak Log'}
        self.request('POST', f'/players/{uuid}', f'csrf={csrf}&do=give&item=Oak+Log&count=', cookie)
        self.assertEqual(self.calls[-1], ['player-action', 'give', '.Alguien', 'oak_log', '1'])
        calls = len(self.calls)
        self.request('POST', f'/players/{uuid}', f'csrf={csrf}&do=give&item=unobtainium&count=1', cookie)
        self.assertEqual([c[0] for c in self.calls[calls:]], ['players'])
        self.request('POST', f'/players/{uuid}', f'csrf={csrf}&do=tp-coords&dimension=overworld&x=1&y=&z=2', cookie)
        self.assertEqual(self.calls[-1], ['player-action', 'tp-coords', '.Alguien', 'overworld', '1', 'surface', '2'])
        # API JSON: misma traducción, token CSRF en cabecera y mismo Origin.
        headers = {'Host': '192.168.1.50:8443', 'Cookie': cookie, 'Origin': 'https://192.168.1.50:8443'}
        api = f'/api/players/{uuid}/action'
        body = json.dumps({'do': 'give', 'item': 'diamond_sword', 'count': 1}).encode()
        self.assertEqual(self.app.handle('POST', api, headers, body, '192.168.1.9')[0], 403)
        code, _, payload = self.app.handle('POST', api, dict(headers, **{'X-CSRF-Token': csrf}), body, '192.168.1.9')
        self.assertEqual((code, json.loads(payload)['message']), (200, 'Item given.'))
        self.assertEqual(self.calls[-1], ['player-action', 'give', '.Alguien', 'diamond_sword', '1'])
        for wrong, expected in ((b'[1]', 400), (json.dumps({'do': 'rm'}).encode(), 404), (json.dumps({'do': 'kit', 'kit': ['x']}).encode(), 400)):
            with self.subTest(wrong=wrong):
                self.assertEqual(self.app.handle('POST', api, dict(headers, **{'X-CSRF-Token': csrf}), wrong, '192.168.1.9')[0], expected)
        self.app.helper = lambda argv, timeout=60: listing if argv[0] == 'players' else {'ok': False, 'output': 'No player was found'}
        code, _, payload = self.app.handle('POST', api, dict(headers, **{'X-CSRF-Token': csrf}),
                                           json.dumps({'do': 'heal'}).encode(), '192.168.1.9')
        self.assertEqual(json.loads(payload), {'ok': False, 'error': 'No player was found'})
        items = json.loads(self.request('GET', '/api/items', cookie=cookie)[2])['items']
        self.assertEqual(items[0][:2], ['diamond_sword', 'Diamond Sword'])

    def test_inventory_and_slot_editor_pages(self):
        cookie, csrf = self.login()
        uuid = '00000000-0000-0000-0009-01f1b0392c35'
        player = {'uuid': uuid, 'name': '.Alguien', 'edition': 'bedrock', 'online': True, 'op': False, 'banned': False,
                  'whitelisted': True, 'last_played': None}
        sword = {'slot': 'hotbar.0', 'id': 'diamond_sword', 'count': 1, 'enchantments': [['sharpness', 7]],
                 'stored_enchantments': [], 'damage': 1200, 'max_damage': 1561, 'unbreakable': False,
                 'name': '<script>x</script>', 'potion': None}
        profile = dict(player, ok=True, xuid=1, ban=None, op_level=None, first_played=None, live=True, selected_slot=0,
                       vitals={'health': 20.0, 'food': 20, 'gamemode': 'survival', 'abilities': {}},
                       location={'dimension': 'overworld', 'pos': [1.0, 64.0, 2.0], 'death': None, 'respawn': None},
                       inventory=[sword, dict(sword, slot='hotbar.1', id='apple', count=12, enchantments=[], damage=0,
                                              max_damage=None, name=None)],
                       ender_chest=[], effects=[{'id': 'speed', 'level': 2, 'seconds': 75}],
                       stats={'custom': {}}, advancements=[])
        calls = []
        def fake(argv, timeout=60):
            calls.append(argv)
            return {'players': {'ok': True, 'players': [player]}, 'player': profile, 'status': status()}.get(argv[0], {'ok': True})
        self.app.helper = fake
        self.app.items = {'totem_of_undying': 'Totem of Undying'}
        page = self.request('GET', f'/players/{uuid}', cookie=cookie)[2]
        for marker in (b'mc-window', b'Ender chest', b'inv-slot enchanted selected', b'Sharpness VII', b'Durability: 361 / 1561',
                       b'dur-low', b'Speed II', b'1:15', b'?slot=hotbar.0#slot-editor', b'sprite-helmet', b'>Live<'):
            self.assertIn(marker, page)
        self.assertNotIn(b'<script>x', page)
        self.assertNotIn(b'id="slot-editor"', page)
        editor = self.request('GET', f'/players/{uuid}?slot=hotbar.0', cookie=cookie)[2]
        self.assertIn(b'id="slot-editor"', editor)
        for marker in (b'Hotbar slot 1', b'name="enchantment"', b'value="slot-repair"', b'value="slot-duplicate"',
                       b'Sharpness (max V)', b'conflicts with Sharpness', b'This item does not stack'):
            self.assertIn(marker, editor)
        section = editor.split(b'id="slot-editor"')[1].split(b'</section>')[0]
        self.assertNotIn(b'value="efficiency"', section)  # una espada no admite Eficiencia
        self.assertNotIn(b'255', section)
        apple = self.request('GET', f'/players/{uuid}?slot=hotbar.1', cookie=cookie)[2]
        for marker in (b'This item cannot be enchanted', b'max="64"', b'up to 64'):
            self.assertIn(marker, apple)
        self.assertNotIn(b'value="slot-repair"', apple)
        empty = self.request('GET', f'/players/{uuid}?slot=enderchest.5', cookie=cookie)[2]
        self.assertIn(b'Put an item here', empty)
        self.assertNotIn(b'id="slot-editor"', self.request('GET', f'/players/{uuid}?slot=../../x', cookie=cookie)[2])
        self.request('POST', f'/players/{uuid}', f'csrf={csrf}&do=slot-set&slot=enderchest.5&item=Totem+of+Undying&count=', cookie)
        self.assertEqual(calls[-1], ['player-action', 'slot-set', '.Alguien', 'enderchest.5', 'totem_of_undying', '1'])

    def test_offline_profile_only_enables_admin_actions(self):
        cookie, _ = self.login()
        uuid = '00000000-0000-0000-0009-01f1b0392c35'
        player = {'uuid': uuid, 'name': '.Alguien', 'edition': 'bedrock', 'online': False, 'op': False, 'banned': False,
                  'whitelisted': True, 'last_played': None}
        profile = dict(player, ok=True, xuid=123, ban=None, op_level=None, first_played=None,
                       vitals={'health': 10.0, 'food': 8, 'gamemode': 'survival', 'abilities': {}},
                       location={'dimension': 'overworld', 'pos': [1.0, 64.0, 2.0], 'death': None, 'respawn': None},
                       inventory=[], ender_chest=[], equipment={}, stats={'custom': {}}, advancements=[])
        self.app.helper = lambda argv, timeout=60: {'players': {'ok': True, 'players': [player]}, 'player': profile,
                                                    'status': status()}.get(argv[0], {'ok': True})
        page = self.request('GET', f'/players/{uuid}', cookie=cookie)[2]
        self.assertIn(b'is offline', page)
        self.assertIn(b'id="pa-admin" class="pa-radio" checked', page)
        items_panel = page.split(b'pa-items-panel')[1].split(b'pa-effects-panel')[0]
        self.assertIn(b'disabled', items_panel)
        admin = page.split(b'pa-admin-panel')[1]
        self.assertNotIn(b'<button class="mc-btn " disabled', admin.split(b'Game mode')[0])

    def test_world_library_page_and_actions(self):
        cookie, csrf = self.login()
        library = {'ok': True, 'active': 'main', 'limit': 50, 'imports': [{'name': 'Xemi.mcworld', 'size': 15 * 2**20, 'mtime': 1}],
                   'exports': [{'name': 'main-20261018t200000-000000z.zip', 'size': 2**20, 'mtime': 1}],
                   'worlds': [{'id': 'main', 'active': True, 'name': 'Main <b>', 'source': 'existing', 'created': '2026-10-18',
                               'last_active': None, 'size': 300 * 2**20, 'generated': True, 'seed': '', 'needs_relight': False},
                              {'id': 'fresh', 'active': False, 'name': 'Fresh', 'source': 'new', 'created': '2026-10-18',
                               'last_active': None, 'size': None, 'generated': False, 'seed': '42', 'needs_relight': False}]}
        def fake(argv, timeout=60):
            self.calls.append(argv)
            return {'worlds': library, 'status': status()}.get(argv[0], {'ok': True, 'output': 'done'})
        self.app.helper = fake
        page = self.request('GET', '/world', cookie=cookie)[2].decode()
        for marker in ('World library (2 / 50)', 'Main &lt;b&gt;', 'Not generated yet', 'Xemi.mcworld', '/exports/main-'):
            self.assertIn(marker, page)
        self.app.jobs.runner = Mock(return_value={'ok': True, 'output': ''})
        for body, argv in (('do=world-activate&id=fresh', ['world-activate', 'fresh']),
                           ('do=world-export&id=main&format=bedrock', ['world-export', 'main', 'bedrock']),
                           ('do=world-import&file=Xemi.mcworld&name=Xemi', ['world-import', 'Xemi.mcworld', 'Xemi']),
                           ('do=world-delete&id=fresh', ['world-delete', 'fresh'])):
            self.app.jobs.runner.reset_mock()
            location = self.request('POST', '/world', f'csrf={csrf}&{body}', cookie)[1]['Location']
            self.assertTrue(location.startswith('/job/'), body)
            for _ in range(50):
                if self.app.jobs.runner.called:
                    break
                time.sleep(0.02)
            self.assertEqual(self.app.jobs.runner.call_args.args[0], argv)
        self.request('POST', '/world', f'csrf={csrf}&do=world-create&name=New&seed=7', cookie)
        self.assertEqual(self.calls[-1], ['world-create', 'New', '7'])

    def test_resource_packs_section_and_actions(self):
        cookie, csrf = self.login()
        sys.path.insert(0, str(ROOT / 'scripts'))
        from mclan import packs
        with tempfile.TemporaryDirectory() as directory:
            listing = packs.Packs(Mock(root=Path(directory))).describe('26.3')
        faithful = next(p for p in listing['packs'] if p['id'] == 'faithful-32x')
        for variant in faithful['java']:
            variant['enabled'] = variant['label'] in ('26.3', '1.21.5')
        faithful.update(active=True, required=False)
        faithful['bedrock']['enabled'] = True
        reply = {'ok': True, **listing, 'installed': True, 'minecraft': '26.3', 'stored': 40 * 2**20,
                 'events': ['[17:05:00 INFO]: [MclanPacks] <script>x</script> 1.21.5 DECLINED']}
        def fake(argv, timeout=60):
            self.calls.append(argv)
            return {'packs': reply, 'status': status(), 'worlds': {'ok': False, 'output': 'n/a'}}.get(argv[0], {'ok': True, 'output': 'done'})
        self.app.helper = fake
        page = self.request('GET', '/world', cookie=cookie)[2].decode()
        for marker in ('Resource packs', 'QuestCraft (VR)', 'name="java-26.3" checked', 'name="java-1.21.5" checked',
                       'Other Java versions (15)', 'name="bedrock" value="on" checked', 'Java optional', 'Bare Bones',
                       'Install it yourself', 'https://faithfulpack.net/license', '&lt;script&gt;x', 'form="pack-faithful-32x"'):
            self.assertIn(marker, page)
        self.assertNotIn('<script>x', page)
        self.assertNotIn('name="java-1.21.4" checked', page)
        self.app.jobs.runner = Mock(return_value={'ok': True, 'output': ''})
        for body, argv in (('do=pack-use&id=faithful-32x&java-26.3=on&java-1.21.5=on&bedrock=on&mode=required',
                            ['pack-use', 'faithful-32x', 'required', 'on', '26.3', '1.21.5']),
                           ('do=pack-use&id=faithful-32x&java-1.21.5=on&java-..%2Fx=on&mode=sneaky',
                            ['pack-use', 'faithful-32x', 'optional', 'off', '1.21.5']),
                           ('do=pack-off', ['pack-off']), ('do=pack-clean', ['pack-clean'])):
            self.app.jobs.runner.reset_mock()
            location = self.request('POST', '/world', f'csrf={csrf}&{body}', cookie)[1]['Location']
            self.assertTrue(location.startswith('/job/'), body)
            for _ in range(50):
                if self.app.jobs.runner.called:
                    break
                time.sleep(0.02)
            self.assertEqual(self.app.jobs.runner.call_args.args[0], argv)
        self.app.jobs.runner.reset_mock()
        location = self.request('POST', '/world', f'csrf={csrf}&do=pack-use&id=faithful-32x', cookie)[1]['Location']
        self.assertEqual(location, '/world')
        self.assertFalse(self.app.jobs.runner.called)
        reply['installed'] = False
        self.assertIn('MclanPacks is not part of the active release', self.request('GET', '/world', cookie=cookie)[2].decode())

    def test_exports_are_streamed_and_names_are_restricted(self):
        cookie, _ = self.login()
        with tempfile.TemporaryDirectory() as directory:
            self.app.config['exports_dir'] = directory
            (Path(directory) / 'main-1.zip').write_bytes(b'PK' + b'x' * 3_000_000)
            (Path(directory) / 'secret.zip').symlink_to('/etc/hostname')
            code, headers, payload = self.request('GET', '/exports/main-1.zip', cookie=cookie)
            self.assertEqual((code, headers['Content-Disposition']), (200, 'attachment; filename="main-1.zip"'))
            self.assertIsInstance(payload, Path)
            self.assertEqual(self.request('GET', '/exports/secret.zip', cookie=cookie)[0], 404)
            for name in ('../panel.json', 'MAIN.zip', 'main-1.tar.gz', 'a/b.zip'):
                self.assertNotEqual(self.request('GET', f'/exports/{name}', cookie=cookie)[0], 200, name)
            self.assertEqual(self.request('GET', '/exports/main-1.zip')[0], 303)  # sin sesión
            httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            httpd.app = self.app
            self.app.config.update(bind='127.0.0.1', port=httpd.server_address[1])
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                request = Request(f'http://127.0.0.1:{httpd.server_address[1]}/exports/main-1.zip', headers={'Cookie': cookie})
                with urlopen(request) as response:
                    self.assertEqual(len(response.read()), 3_000_002)
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_world_upload_streams_to_imports_with_checks(self):
        import socket
        cookie, csrf = self.login()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.app.config['imports_dir'] = directory
            httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            httpd.app = self.app
            port = httpd.server_address[1]
            self.app.config.update(bind='127.0.0.1', port=port)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            def send(body, name='My World.zip', token=csrf, origin=None, session=cookie, length=None):
                headers = {'X-Filename': urllib.parse.quote(name), 'X-CSRF-Token': token, 'Content-Type': 'application/octet-stream'}
                if session:
                    headers['Cookie'] = session
                if origin:
                    headers['Origin'] = origin
                with socket.create_connection(('127.0.0.1', port), timeout=10) as sock:
                    head = f'POST /upload HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: {length if length is not None else len(body)}\r\n'
                    head += ''.join(f'{k}: {v}\r\n' for k, v in headers.items()) + '\r\n'
                    sock.sendall(head.encode() + body)
                    reply = b''
                    while chunk := sock.recv(65536):
                        reply += chunk
                status = int(reply.split(b' ', 2)[1])
                return status, json.loads(reply.split(b'\r\n\r\n', 1)[1] or b'{}')
            space = patch.object(server.shutil, 'disk_usage', return_value=Mock(free=100 * 2**30))
            space.start()
            self.addCleanup(space.stop)
            try:
                payload = b'PK\x03\x04' + b'w' * 3_000_000
                status, reply = send(payload)
                self.assertEqual((status, reply['name']), (200, 'My World.zip'))
                self.assertEqual((folder / 'My World.zip').read_bytes(), payload)
                self.assertEqual(send(payload)[1]['name'], 'My World-2.zip')
                small = b'PK\x03\x04 small'  # los rechazos responden sin leer el cuerpo
                self.assertEqual(send(small, session=None)[0], 401)
                self.assertEqual(send(small, token='nope')[0], 403)
                self.assertEqual(send(small, origin='https://evil.example')[0], 403)
                for name in ('../escape.zip', 'evil.sh', '.hidden.zip', 'a' * 120 + '.zip'):
                    self.assertEqual(send(small, name=name)[0], 400, name)
                self.assertEqual(send(b'<html>not a zip</html>', name='fake.zip')[0], 400)
                self.assertEqual(send(b'', length=server.MAX_UPLOAD + 1)[0], 413)
                with patch.object(server.shutil, 'disk_usage', return_value=Mock(free=1)):
                    self.assertEqual(send(small, name='big.zip')[0], 507)
                check = json.loads(self.request('GET', '/api/upload-check?name=evil.sh&size=10', cookie=cookie, host=f'127.0.0.1:{port}')[2])
                self.assertFalse(check['ok'])
                check = json.loads(self.request('GET', '/api/upload-check?name=ok.mcworld&size=10', cookie=cookie, host=f'127.0.0.1:{port}')[2])
                self.assertTrue(check['ok'])
                self.assertEqual(sorted(p.name for p in folder.iterdir()), ['My World-2.zip', 'My World.zip'])
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_background_jobs_notify_instead_of_redirecting(self):
        cookie, csrf = self.login()
        release = threading.Event()
        self.app.jobs.runner = lambda argv, timeout=60: (release.wait(5), {'ok': True, 'output': 'backup ok'})[1]
        headers = {'Host': '192.168.1.50:8443', 'Cookie': cookie, 'X-Requested-With': 'fetch'}
        code, kind, payload = self.app.handle('POST', '/action', headers, f'do=backup&csrf={csrf}'.encode(), '192.168.1.9')
        self.assertEqual(code, 202)
        job = json.loads(payload)['job']
        code, _, payload = self.app.handle('POST', '/action', headers, f'do=restart&csrf={csrf}'.encode(), '192.168.1.9')
        self.assertEqual((code, 'still running' in json.loads(payload)['error']), (409, True))
        jobs = json.loads(self.request('GET', '/api/jobs', cookie=cookie)[2])['jobs']
        self.assertEqual((jobs[0]['id'], jobs[0]['state'], jobs[0]['ok']), (job, 'running', None))
        release.set()
        for _ in range(50):
            if not self.app.jobs.busy():
                break
            time.sleep(0.05)
        entry = json.loads(self.request('GET', f'/api/job/{job}', cookie=cookie)[2])
        self.assertEqual((entry['state'], entry['ok'], entry['seen'], entry['result']['output']), ('done', True, False, 'backup ok'))
        api = {'Host': '192.168.1.50:8443', 'Cookie': cookie}
        self.assertEqual(self.app.handle('POST', f'/api/job/{job}/seen', api, b'', '192.168.1.9')[0], 403)
        self.assertEqual(self.app.handle('POST', f'/api/job/{job}/seen', dict(api, **{'X-CSRF-Token': csrf}), b'', '192.168.1.9')[0], 200)
        self.assertTrue(json.loads(self.request('GET', '/api/jobs', cookie=cookie)[2])['jobs'][0]['seen'])
        # Sin JavaScript sigue la redirección a la página de la tarea.
        self.assertTrue(self.request('POST', '/action', f'do=backup&csrf={csrf}', cookie)[1]['Location'].startswith('/job/'))

    def test_long_operations_are_marked_async_in_pages(self):
        cookie, _ = self.login()
        backups = self.request('GET', '/backups', cookie=cookie)[2].decode()
        self.assertRegex(backups, r'<form class="inline" method="post" action="/action" data-async>')
        self.assertIn('data-tray', backups)
        self.assertIn('data-jobs-dialog', backups)
        world = self.request('GET', '/world', cookie=cookie)[2].decode()
        self.assertIn('action="/world" data-async data-confirm="Save and restart', world)

    def test_logout_ends_session(self):
        cookie, csrf = self.login()
        self.request('POST', '/logout', f'csrf={csrf}', cookie)
        self.assertEqual(self.request('GET', '/', cookie=cookie)[0], 303)

    def test_http_handler_sets_security_headers_and_limits_body(self):
        httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        httpd.app = self.app
        self.app.config.update(bind='127.0.0.1', port=httpd.server_address[1])
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{httpd.server_address[1]}'
            with urlopen(f'{base}/login') as response:
                csp = response.headers['Content-Security-Policy']
                self.assertIn("default-src 'none'", csp)
                self.assertIn("script-src 'self';", csp)
                self.assertNotIn('unsafe-inline', csp)
                self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                # Con no-referrer los navegadores mandan Origin: null en formularios y el login fallaría.
                self.assertEqual(response.headers['Referrer-Policy'], 'same-origin')
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(f'{base}/login', data=b'x' * (server.MAX_BODY + 1)))
            self.assertEqual(caught.exception.code, 413)
            import socket
            for header in (b'Content-Length: -1', b'Transfer-Encoding: chunked', b'Content-Length: 1e3'):
                with socket.create_connection(httpd.server_address, timeout=5) as sock:
                    sock.sendall(b'POST /login HTTP/1.1\r\nHost: 127.0.0.1\r\n' + header + b'\r\n\r\n')
                    self.assertIn(b' 400 ', sock.recv(200), header)
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == '__main__':
    unittest.main()
