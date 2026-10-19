"""Paquetes de recursos: catálogo fijado, descargas verificadas, selección y comprobación HTTP (sin red)."""
import copy
import hashlib
import http.server
import io
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from mclan import addons, cli, packs  # noqa: E402
from mclan.common import Error  # noqa: E402
from mclan.net import valid_url  # noqa: E402


def archive(marker, extra=b''):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as handle:
        handle.writestr(marker, '{}')
        handle.writestr('assets/x.png', extra)
    return buffer.getvalue()


def pinned(payload, *algorithms):
    return {'size': len(payload), **{a: hashlib.new(a, payload).hexdigest() for a in algorithms}}


class FakeClient:
    def __init__(self, payloads):
        self.payloads, self.calls = payloads, []

    def fetch(self, url, limit):
        self.calls.append(url)
        return self.payloads[url]


class CatalogTests(unittest.TestCase):
    def test_every_download_is_pinned_and_from_an_allowed_host(self):
        for pack_id, pack in packs.CATALOG.items():
            self.assertTrue(re.fullmatch(r'[a-z0-9-]+', pack_id))
            if pack.get('link_only'):
                self.assertNotIn('java', pack)
                self.assertNotIn('bedrock', pack)
                continue
            seen = set()
            for label, variant in pack['java'].items():
                self.assertTrue(packs.LABEL.fullmatch(label))
                self.assertIn(label, variant['versions'])
                self.assertFalse(seen & set(variant['versions']), 'una versión no puede tener dos variantes')
                seen |= set(variant['versions'])
                self.assertTrue(re.fullmatch(r'[0-9a-f]{40}', variant['sha1']))
                self.assertTrue(re.fullmatch(r'[0-9a-f]{128}', variant['sha512']))
                self.assertTrue(0 < variant['size'] <= packs.LIMIT)
                valid_url(variant['url'])
            self.assertTrue(re.fullmatch(r'[0-9a-f]{64}', pack['bedrock']['sha256']))
            valid_url(pack['bedrock']['url'])
            # El aviso de Java da el crédito y el enlace a la licencia que exige Faithful.
            self.assertIn(pack['credit'], pack['prompt'])

    def test_faithful_covers_the_server_and_questcraft(self):
        faithful = packs.CATALOG['faithful-32x']
        self.assertEqual(packs.server_variant(faithful, '26.3'), '26.3')
        self.assertEqual(packs.server_variant(faithful, '1.21.5'), '1.21.5')  # QuestCraft 6.0.0
        self.assertEqual(packs.server_variant(faithful, '1.21.2'), '1.21.3')
        self.assertIsNone(packs.server_variant(faithful, '1.8.9'))

    def test_bare_bones_is_only_a_link_to_its_official_page(self):
        bare = packs.CATALOG['bare-bones']
        self.assertTrue(bare['link_only'])
        self.assertTrue(bare['homepage'].startswith('https://modrinth.com/'))

    def test_addon_is_built_with_via_and_geyser(self):
        self.assertEqual(addons.ADDONS['mclan_packs'], ('mclan-packs', 'MclanPacks.jar'))
        self.assertEqual(set(addons.CLASSPATH['mclan_packs']), {'ViaVersion.jar', 'Geyser-Spigot.jar'})
        self.assertEqual(addons.describe(ROOT)['mclan_packs']['version'], '1.0.0')


class SelectionTests(unittest.TestCase):
    def test_roundtrip_and_format_the_plugin_reads(self):
        text = packs.selection_text('faithful-32x', ['26.3', '1.21.5'], True, False)
        self.assertEqual(packs.read_selection(text),
                         {'pack': 'faithful-32x', 'java': ['26.3', '1.21.5'], 'bedrock': True, 'required': False})
        faithful = packs.CATALOG['faithful-32x']
        self.assertIn(f"java.1.file=files/{faithful['java']['1.21.5']['sha1']}.zip\n", text)
        self.assertIn(f"bedrock.file=files/{faithful['bedrock']['sha256']}.mcpack\n", text)
        self.assertTrue(all(32 <= ord(c) < 127 for c in text.replace('\n', '')))
        self.assertNotIn('\\', text)

    def test_off_and_unknown_schema(self):
        self.assertIsNone(packs.read_selection(packs.selection_text(None, [], False, False)))
        with self.assertRaises(Error):
            packs.read_selection('schema=2\npack=x\n')


class PacksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.java_new, self.java_old, self.bedrock = archive('pack.mcmeta', b'26'), archive('pack.mcmeta', b'21'), archive('manifest.json')
        self.catalog = {'test-pack': {
            'name': 'Test pack', 'summary': 's', 'credit': 'Someone', 'homepage': 'https://example.org/',
            'license': 'https://example.org/license', 'prompt': 'Test pack by Someone',
            'java': {'26.3': {'release': 'r1', 'versions': ['26.3'], 'url': 'https://cdn.modrinth.com/a.zip',
                              **pinned(self.java_new, 'sha1', 'sha512')},
                     '1.21.5': {'release': 'r1', 'versions': ['1.21.5'], 'url': 'https://cdn.modrinth.com/b.zip',
                                **pinned(self.java_old, 'sha1', 'sha512')}},
            'bedrock': {'release': 'r1', 'url': 'https://github.com/c.mcpack', **pinned(self.bedrock, 'sha256')}},
            'link': copy.deepcopy(packs.CATALOG['bare-bones'])}
        self.client = FakeClient({'https://cdn.modrinth.com/a.zip': self.java_new,
                                  'https://cdn.modrinth.com/b.zip': self.java_old,
                                  'https://github.com/c.mcpack': self.bedrock})
        patcher = patch.dict(packs.CATALOG, self.catalog, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.packs = packs.Packs(Mock(root=self.root))

    def tearDown(self):
        self.temp.cleanup()

    def test_default_is_the_server_variant_plus_bedrock(self):
        chosen = self.packs.use('test-pack', None, True, False, self.client, '26.3')
        self.assertEqual(chosen['java'], ['26.3'])
        self.assertEqual(self.packs.selection(), {'pack': 'test-pack', 'java': ['26.3'], 'bedrock': True, 'required': False})
        stored = self.root / 'packs/files' / f"{hashlib.sha1(self.java_new).hexdigest()}.zip"
        self.assertEqual(stored.read_bytes(), self.java_new)
        self.assertEqual(stored.stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.root / 'packs').stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.root / 'packs/active.properties').stat().st_mode & 0o777, 0o644)

    def test_several_versions_and_cached_downloads(self):
        self.packs.use('test-pack', ['26.3', '1.21.5', '26.3'], False, True, self.client, '26.3')
        self.assertEqual(self.packs.selection()['java'], ['26.3', '1.21.5'])
        self.assertTrue(self.packs.selection()['required'])
        calls = len(self.client.calls)
        self.packs.use('test-pack', ['1.21.5'], False, False, self.client, '26.3')
        self.assertEqual(len(self.client.calls), calls)  # ya descargada: no se vuelve a pedir
        listing = self.packs.describe('26.3')
        test = next(p for p in listing['packs'] if p['id'] == 'test-pack')
        self.assertEqual([(v['label'], v['downloaded'], v['enabled']) for v in test['java']],
                         [('26.3', True, False), ('1.21.5', True, True)])
        self.assertFalse(test['bedrock']['enabled'])
        self.assertTrue(next(p for p in listing['packs'] if p['id'] == 'link')['link_only'])

    def test_bad_hash_or_structure_never_reaches_the_folder(self):
        self.client.payloads['https://cdn.modrinth.com/a.zip'] = archive('pack.mcmeta', b'tampered!')
        with self.assertRaises(Error):
            self.packs.use('test-pack', ['26.3'], False, False, self.client, '26.3')
        self.assertEqual(list((self.root / 'packs/files').iterdir()), [])
        self.assertIsNone(self.packs.selection())
        wrong = archive('manifest.json', b'26')
        self.catalog['test-pack']['java']['26.3'].update(pinned(wrong, 'sha1', 'sha512'))
        self.client.payloads['https://cdn.modrinth.com/a.zip'] = wrong
        with patch.dict(packs.CATALOG, self.catalog), self.assertRaisesRegex(Error, 'pack.mcmeta'):
            self.packs.use('test-pack', ['26.3'], False, False, self.client, '26.3')

    def test_invalid_requests(self):
        with self.assertRaisesRegex(Error, 'desconocidas'):
            self.packs.use('test-pack', ['1.8.9'], True, False, self.client, '26.3')
        with self.assertRaisesRegex(Error, 'Página oficial'):
            self.packs.use('link', None, True, False, self.client, '26.3')
        with self.assertRaises(Error):
            self.packs.use('missing', None, True, False, self.client, '26.3')
        with self.assertRaisesRegex(Error, 'al menos'):
            self.packs.use('test-pack', [], False, False, self.client, '26.3')
        self.assertEqual(self.client.calls, [])

    def test_off_keeps_files_and_clean_removes_unused(self):
        self.packs.use('test-pack', ['26.3', '1.21.5'], True, False, self.client, '26.3')
        self.packs.use('test-pack', ['1.21.5'], False, False, self.client, '26.3')
        freed = self.packs.clean()
        self.assertEqual(freed, len(self.java_new) + len(self.bedrock))
        self.assertEqual([p.name for p in (self.root / 'packs/files').iterdir()],
                         [f'{hashlib.sha1(self.java_old).hexdigest()}.zip'])
        self.packs.off()
        self.assertIsNone(self.packs.selection())
        self.assertEqual(len(list((self.root / 'packs/files').iterdir())), 1)
        self.assertEqual(self.packs.clean(), len(self.java_old))

    def test_served_checks_what_java_clients_download(self):
        self.packs.use('test-pack', ['26.3'], False, False, self.client, '26.3')
        files = {f"/{hashlib.sha1(self.java_new).hexdigest()}.zip": self.java_new}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = files.get(self.path)
                self.send_response(200 if body is not None else 404)
                self.end_headers()
                self.wfile.write(body or b'')

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        with patch.object(packs, 'PORT', server.server_address[1]):
            self.assertEqual(packs.served('127.0.0.1', self.packs.selection()), [])
            files.clear()
            self.assertEqual(len(packs.served('127.0.0.1', self.packs.selection())), 1)
            server.shutdown()
            server.server_close()
            self.assertIn('no responde', packs.served('127.0.0.1', None)[0])


class CommandTests(unittest.TestCase):
    def test_parser(self):
        args = cli.parser().parse_args(['manage', 'pack', 'use', 'faithful-32x', '--java', '26.3', '--java', '1.21.5'])
        self.assertEqual((args.pack_action, args.id, args.java, args.bedrock, args.required), ('use', 'faithful-32x', ['26.3', '1.21.5'], 'on', False))
        self.assertTrue(cli.parser().parse_args(['manage', 'pack', 'use', 'x', '--no-java', '--required']).required)

    def test_running_server_must_confirm_and_serve(self):
        runtime = Mock()
        runtime.running.return_value = True
        runtime.exec_probe.return_value = Mock(stdout='Error: invalid pack selection (x)')
        args = cli.parser().parse_args(['manage', 'pack', 'off'])
        active = {'minecraft': '26.3', 'lan_ip': '192.168.1.50', 'mclan_packs': {}}
        with patch('mclan.packs.Packs') as fake, patch('mclan.packs.served', return_value=[]):
            fake.return_value.selection.return_value = None
            with self.assertRaisesRegex(Error, 'no confirmó'):
                cli.manage_pack(runtime, args, active, None)
            fake.return_value.off.assert_called_once()
            runtime.exec_probe.return_value = Mock(stdout='Reloaded. No resource pack selected. Java players updated: 0.')
            cli.manage_pack(runtime, args, active, None)
        runtime.exec_probe.assert_called_with('rcon', 'mclanpacks', 'reload')

    def test_release_without_the_addon_asks_for_update(self):
        args = cli.parser().parse_args(['manage', 'pack', 'use', 'faithful-32x'])
        with self.assertRaisesRegex(Error, 'update'):
            cli.manage_pack(Mock(root=Path('/nonexistent')), args, {'minecraft': '26.3', 'lan_ip': '192.168.1.50'}, None)


if __name__ == '__main__':
    unittest.main()
