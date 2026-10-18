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
        self.assertEqual(profile['inventory'], [{'slot': 0, 'id': 'torch', 'count': 64}])
        self.assertEqual(profile['stats']['mined'][0], {'id': 'dirt', 'count': 50})
        self.assertEqual(profile['stats']['custom'], {'play_time': 72000, 'deaths': 2})
        self.assertEqual(profile['advancements'], ['story/root'])
        self.assertEqual(profile['vitals']['abilities'], {'flying': True, 'mayfly': True})
        self.assertEqual(profile['last_played'], 5)

    def test_player_verbs_are_validated(self):
        self.assertEqual(helper.parse(['player-action', 'gamemode', '.Alguien', 'creative'])[1], ['gamemode', '.Alguien', 'creative'])
        self.assertEqual(helper.parse(['player-action', 'kick', 'Steve'])[1], ['kick', 'Steve'])
        for argv in (['player-action', 'gamemode', 'Steve'], ['player-action', 'kick', 'Steve', 'creative'],
                     ['player-action', 'rm', 'Steve'], ['player-action', 'op', 'Steve;stop'], ['player-action', 'op', '@a'],
                     ['player-action', 'gamemode', 'Steve', 'god'], ['player', '../../etc'], ['player', 'ABC'],
                     ['players', 'x']):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                helper.parse(argv)

    def test_player_actions_only_for_known_players(self):
        panel = helper.Panel.__new__(helper.Panel)
        panel.players = Mock(return_value={'players': [{'name': 'Steve'}]})
        panel.rcon = Mock(return_value='ok')
        panel.runtime = Mock(data=Path('/nonexistent'))
        self.assertFalse(panel.player_action('op', 'Mallory')['ok'])
        panel.rcon.assert_not_called()
        panel.player_action('gamemode', 'Steve', 'creative')
        self.assertEqual(panel.rcon.call_args.args, ('gamemode', 'creative', 'Steve'))
        panel.player_action('ban', 'Steve')
        self.assertEqual(panel.rcon.call_args.args[:2], ('ban', 'Steve'))

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
        for marker in (b'Vitals', b'Inventory', b'Statistics', b'Server policies', b'Make operator', b'2 min', b'Story'):
            self.assertIn(marker, page)
        self.assertNotIn(b'<script>', page)
        self.request('POST', f'/players/{uuid}', f'csrf={csrf}&do=gamemode&mode=creative', cookie)
        self.assertEqual(self.calls[-1], ['player-action', 'gamemode', '.Alguien', 'creative'])
        self.request('POST', f'/players/{uuid}', f'csrf={csrf}&do=op', cookie)
        self.assertEqual(self.calls[-1], ['player-action', 'op', '.Alguien'])
        other = '11111111-1111-1111-1111-111111111111'
        self.assertEqual(self.request('POST', f'/players/{other}', f'csrf={csrf}&do=op', cookie)[0], 400)
        self.assertEqual(self.request('POST', f'/players/{uuid}', f'csrf={csrf}&do=rm', cookie)[0], 400)
        self.assertEqual(self.request('POST', f'/players/{uuid}', 'do=op', cookie)[0], 403)

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
