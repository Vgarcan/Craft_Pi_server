"""Panel web: validación del helper privilegiado y rutas HTTP sin Docker ni sudo."""
import http.server
import importlib.util
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
        config = {'bind': '192.168.1.50', 'port': 8443, 'map_url': 'http://192.168.1.50:8100/',
                  'password': server.hash_password('contraseña-larga', iterations=1000)}
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
        for method, path in (('GET', '/'), ('GET', '/whitelist'), ('POST', '/accion'), ('GET', '/mapa')):
            self.assertEqual(self.request(method, path)[:2], (303, {'Location': '/login'}))
        self.helper.assert_not_called()

    def test_foreign_host_header_is_rejected(self):
        # DNS rebinding: una página externa que resuelve su dominio a la Pi no llega a las rutas.
        self.assertEqual(self.request('GET', '/login', host='evil.example')[0], 421)
        self.assertEqual(self.request('POST', '/login', 'password=contrase%C3%B1a-larga', host='evil.example:8443')[0], 421)
        self.assertEqual(self.request('GET', '/login', host='192.168.1.50')[0], 200)

    def test_map_controls_and_spawn_link(self):
        cookie, csrf = self.login()
        body = self.request('GET', '/', cookie=cookie)[2]
        self.assertIn(b'Ver mapa en el spawn', body)
        self.assertIn(b'#world:-32:64:0:150', body)
        self.assertIn(b'Pausar render', body)
        self.assertEqual(self.request('GET', '/mapa', cookie=cookie)[1]['Location'],
                         'http://192.168.1.50:8100/#world:-32:64:0:150:0:0:0:0:perspective')
        self.request('POST', '/modulos', f'csrf={csrf}&do=render&value=off', cookie)
        self.assertEqual(self.calls[-1], ['bluemap-render', 'off'])
        self.app.jobs.runner = Mock(return_value={'ok': True, 'output': ''})
        location = self.request('POST', '/modulos', f'csrf={csrf}&do=plugin&value=off', cookie)[1]['Location']
        self.assertTrue(location.startswith('/tarea/'))
        for _ in range(50):
            if self.app.jobs.runner.called:
                break
            time.sleep(0.02)
        self.assertEqual(self.app.jobs.runner.call_args.args[0], ['plugin', 'bluemap', 'off'])
        self.assertEqual(self.request('POST', '/modulos', f'csrf={csrf}&do=plugin&value=rm', cookie)[0], 400)

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
        self.assertEqual(self.request('POST', '/accion', 'do=stop', cookie)[0], 403)
        self.assertEqual(self.request('POST', '/accion', f'do=stop&csrf={csrf}', cookie, origin='https://evil.example')[0], 403)
        self.assertNotIn(['stop'], self.calls)
        code, headers, _ = self.request('POST', '/accion', f'do=stop&csrf={csrf}', cookie, origin='https://192.168.1.50:8443')
        self.assertEqual(code, 303)
        self.assertTrue(headers['Location'].startswith('/tarea/'))

    def test_dashboard_escapes_server_provided_text(self):
        cookie, _ = self.login()
        code, _, body = self.request('GET', '/', cookie=cookie)
        self.assertEqual(code, 200)
        self.assertNotIn(b'<script>', body)
        self.assertIn(b'&lt;script&gt;', body)
        self.assertIn(b'192.168.1.50:25565', body)
        code, _, body = self.request('GET', '/ajustes', cookie=cookie)
        self.assertIn(b'Hola &lt;b&gt;', body)

    def test_only_one_long_job_at_a_time(self):
        cookie, csrf = self.login()
        release = threading.Event()
        def slow(argv, timeout=60):
            release.wait(5)
            return {'ok': True, 'output': 'fin'}
        self.app.jobs.runner = slow
        first = self.request('POST', '/accion', f'do=backup&csrf={csrf}', cookie)
        second = self.request('POST', '/accion', f'do=restart&csrf={csrf}', cookie)
        self.assertTrue(first[1]['Location'].startswith('/tarea/'))
        self.assertEqual(second[1]['Location'], '/')
        page = self.request('GET', first[1]['Location'], cookie=cookie)[2]
        self.assertIn(b'http-equiv="refresh"', page)
        release.set()
        for _ in range(50):
            if not self.app.jobs.busy():
                break
            time.sleep(0.05)
        self.assertIn(b'Terminado correctamente', self.request('GET', first[1]['Location'], cookie=cookie)[2])

    def test_settings_send_only_changed_values(self):
        cookie, csrf = self.login()
        body = (f'csrf={csrf}&motd=Hola+%3Cb%3E&difficulty=hard&gamemode=survival&max-players=6&view-distance=6'
                '&simulation-distance=4&pvp=true&allow-flight=false&spawn-protection=16&online-mode=false')
        self.app.jobs.runner = Mock(return_value={'ok': True, 'output': ''})
        code, headers, _ = self.request('POST', '/ajustes', body, cookie)
        self.assertEqual(code, 303)
        for _ in range(50):
            if self.app.jobs.runner.called:
                break
            time.sleep(0.02)
        self.assertEqual(self.app.jobs.runner.call_args.args[0], ['set-properties', 'difficulty=hard'])

    def test_whitelist_actions_map_to_helper_verbs(self):
        cookie, csrf = self.login()
        self.request('POST', '/whitelist', f'csrf={csrf}&do=add&edition=bedrock&name=Otro', cookie)
        self.request('POST', '/whitelist', f'csrf={csrf}&do=remove&name=.Alguien', cookie)
        self.request('POST', '/whitelist', f'csrf={csrf}&do=switch&value=off', cookie)
        self.assertEqual(self.calls[-3:], [['whitelist-add', 'bedrock', 'Otro'], ['whitelist-remove', '.Alguien'],
                                           ['whitelist', 'off']])
        self.assertEqual(self.request('POST', '/whitelist', f'csrf={csrf}&do=add&edition=root&name=x', cookie)[0], 400)

    def test_unknown_bedrock_gamertag_offers_temporary_opening(self):
        cookie, csrf = self.login()
        page = self.request('GET', '/whitelist', cookie=cookie)[2]
        self.assertIn(b'Abrir 5 min y esperar', page)
        self.app.helper = Mock(side_effect=lambda argv, timeout=60: status() if argv[0] == 'status' else {
            'ok': False, 'output': '[18:15:19 ERROR]: [floodgate] Got an error from requesting the xuid of a Bedrock '
                                   'player: Unable to find user in our cache. Please try specifying their Floodgate UUID instead'})
        self.request('POST', '/whitelist', f'csrf={csrf}&do=add&edition=bedrock&name=Otro', cookie)
        page = self.request('GET', '/whitelist', cookie=cookie)[2].decode()
        self.assertIn('Floodgate no conoce todavía a Otro', page)
        self.assertNotIn('Got an error', page)

    def test_password_change_invalidates_other_sessions(self):
        first, _ = self.login()
        cookie, csrf = self.login()
        with tempfile.TemporaryDirectory() as directory:
            self.app.config_path = Path(directory) / 'panel.json'
            (Path(directory) / 'initial-password').write_text('vieja\n')
            body = f'csrf={csrf}&current=contrase%C3%B1a-larga&new=otra-clave-segura&repeat=otra-clave-segura'
            self.request('POST', '/cuenta', body, cookie)
            self.assertTrue(server.check_password('otra-clave-segura', self.app.config['password']))
            self.assertFalse((Path(directory) / 'initial-password').exists())
            self.assertTrue(self.app.config_path.exists())
        self.assertEqual(self.request('GET', '/', cookie=first)[0], 303)
        self.assertEqual(self.request('GET', '/', cookie=cookie)[0], 200)

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
                self.assertIn("default-src 'none'", response.headers['Content-Security-Policy'])
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
