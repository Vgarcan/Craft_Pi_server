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
                     ['whitelist-open-for', 'Steve', '60'], ['whitelist-open-for', 'a b', '5']):
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

    def test_floodgate_uuid_from_xuid(self):
        self.assertEqual(helper.floodgate_uuid('2535412345678901'), '00000000-0000-0000-0009-01f1b0392c35')

    def test_rejected_attempts_from_real_and_java_log_lines(self):
        log = ('[16:29:04 INFO]: [Geyser-Spigot] Xeministrator has disconnected from the Java server because of '
               '§rYou are not whitelisted on this server!\n'
               '[16:29:04 INFO]: Disconnecting /192.168.2.100:0: You are not whitelisted on this server!\n'
               '[16:49:36 INFO]: [Geyser-Spigot] Xeministrator has disconnected from the Java server because of '
               '§rYou are not whitelisted on this server!\n'
               '[17:00:00 INFO]: Disconnecting Steve (/192.168.2.20:51234): You are not white-listed on this server!\n'
               '[17:01:00 INFO]: Alex (/192.168.2.21:50000) lost connection: You are not whitelisted on this server!\n')
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
            with patch.object(helper.os, 'chown'):
                result = panel.set_properties({'difficulty': 'hard'})
            self.assertTrue(result['ok'])
            self.assertIn('difficulty=hard', (data / 'server.properties').read_text())
            panel.runtime.stop.assert_called_once()
            panel.runtime.start.assert_called_once()
            panel.runtime.running.return_value = False
            panel.runtime.start.reset_mock()
            with patch.object(helper.os, 'chown'):
                panel.set_properties({'difficulty': 'easy'})
            panel.runtime.start.assert_not_called()

    def test_open_for_always_reenables_whitelist(self):
        panel = helper.Panel.__new__(helper.Panel)
        panel.rcon = Mock(return_value='')
        panel.server_log = Mock(side_effect=RuntimeError('docker caído'))
        with self.assertRaises(RuntimeError):
            panel.open_for('Alguien', 1)
        self.assertEqual(panel.rcon.call_args_list[0].args, ('whitelist', 'off'))
        self.assertEqual(panel.rcon.call_args_list[-1].args, ('whitelist', 'on'))
        panel.rcon.reset_mock()
        panel.server_log = Mock(return_value='[17:00:00 INFO]: .Alguien[/192.168.2.100:0] logged in with entity id 7')
        result = panel.open_for('Alguien', 1)
        self.assertTrue(result['ok'])
        self.assertIn(('whitelist', 'add', '.Alguien'), [c.args for c in panel.rcon.call_args_list])
        self.assertEqual(panel.rcon.call_args_list[-1].args, ('whitelist', 'on'))


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

    def request(self, method, path, body='', cookie=None, origin=None, ip='192.168.1.9'):
        headers = {}
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
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{httpd.server_address[1]}'
            with urlopen(f'{base}/login') as response:
                self.assertIn("default-src 'none'", response.headers['Content-Security-Policy'])
                self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(f'{base}/login', data=b'x' * (server.MAX_BODY + 1)))
            self.assertEqual(caught.exception.code, 413)
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == '__main__':
    unittest.main()
