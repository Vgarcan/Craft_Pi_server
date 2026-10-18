"""Biblioteca de mundos: operaciones reales sobre temporales, con Docker y Paper simulados."""
import gzip
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from mclan import worlds  # noqa: E402
from mclan.common import Error, read_json  # noqa: E402


def level_dat(version):
    """level.dat mínimo: {"": {Data: {DataVersion: version}}} en NBT con gzip."""
    payload = (b'\x0a\x00\x00' + b'\x0a\x00\x04Data' + b'\x03\x00\x0bDataVersion' + struct.pack('>i', version)
               + b'\x00' + b'\x00')
    return gzip.compress(payload)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        (self.data / 'world').mkdir(parents=True)
        (self.data / 'world/level.dat').write_bytes(level_dat(5023))
        (self.data / 'world/main.txt').write_text('main world')
        (self.data / 'bluemap/web/maps/world').mkdir(parents=True)
        (self.data / 'bluemap/web/maps/world/tile').write_text('main tile')
        (self.data / 'server.properties').write_text('online-mode=true\ndifficulty=normal\ngamemode=survival\nlevel-seed=\n')
        self.state = self.root / 'state'
        self.state.mkdir()
        (self.root / 'tools').mkdir()
        (self.root / 'tools/relight.py').write_text('print("relight")\n')
        self.runtime = Mock(root=self.root, data=self.data, state=self.state)
        self.runtime.running.return_value = True
        self.runtime.active.return_value = {'minecraft': '26.3', 'runtime_image': 'img', 'bluemap': {}}
        # Como el runtime real: verificar la transacción retira la marca de operación.
        self.runtime.finish_transaction.side_effect = lambda: (self.state / 'operation.json').unlink(missing_ok=True)
        self.library = worlds.Library(self.runtime)
        for target in ('mclan.worlds.os.chown', 'mclan.safefs.os.fchown'):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        space = patch('mclan.worlds.shutil.disk_usage', return_value=Mock(free=100 * 1024**3))
        space.start()
        self.addCleanup(space.stop)
        snapshot = patch('mclan.worlds.backups.snapshot', return_value=self.root / 'backups/pre-switch.tar.gz')
        self.snapshot = snapshot.start()
        self.addCleanup(snapshot.stop)

    def tearDown(self):
        self.temp.cleanup()

    def stored_world(self, wid, text, name='Other'):
        library = self.library.load()
        library['worlds'][wid] = {'name': name, 'created': 'x', 'source': 'import-java', 'settings': {'difficulty': 'hard'}}
        self.library.save(library)
        slot = self.root / 'worlds' / wid / 'world'
        slot.mkdir(parents=True)
        (slot / 'level.dat').write_bytes(level_dat(5023))
        (slot / 'main.txt').write_text(text)

    def test_default_library_and_creation_rules(self):
        self.assertEqual(self.library.describe()['active'], 'main')
        wid = self.library.create('Mi Mundo Nuevo!', '-12345')
        self.assertEqual(wid, 'mi-mundo-nuevo')
        self.assertEqual(self.library.create('Mi Mundo Nuevo!'), 'mi-mundo-nuevo-2')
        for bad in ('', 'x' * 41, 'a\nb'):
            with self.subTest(name=bad), self.assertRaises(Error):
                self.library.create(bad)
        for seed in ('abc', '1' * 20, '9223372036854775808'):
            with self.subTest(seed=seed), self.assertRaises(Error):
                self.library.create('Seeded', seed)
        library = self.library.load()
        for index in range(worlds.LIMIT - len(library['worlds'])):
            library['worlds'][f'w{index}'] = {'name': 'x'}
        self.library.save(library)
        with self.assertRaises(Error):
            self.library.create('One too many')
        with self.assertRaises(Error):
            self.library.slot('../etc')

    def test_activation_swaps_world_map_and_settings(self):
        self.stored_world('other', 'other world')
        (self.root / 'worlds/other/bluemap-maps').mkdir()
        (self.root / 'worlds/other/bluemap-maps/tile').write_text('other tile')
        self.library.activate('other', 10)
        self.assertEqual((self.data / 'world/main.txt').read_text(), 'other world')
        self.assertEqual((self.root / 'worlds/main/world/main.txt').read_text(), 'main world')
        self.assertEqual((self.data / 'bluemap/web/maps/tile').read_text(), 'other tile')
        self.assertEqual((self.root / 'worlds/main/bluemap-maps/world/tile').read_text(), 'main tile')
        properties = (self.data / 'server.properties').read_text()
        self.assertIn('difficulty=hard', properties)
        self.assertIn('online-mode=true', properties)
        self.assertEqual((self.data / worlds.MARKER).read_text(), 'other\n')
        library = read_json(self.root / 'worlds/library.json')
        self.assertEqual(library['active'], 'other')
        self.assertEqual(library['worlds']['main']['settings']['difficulty'], 'normal')
        self.runtime.start.assert_called_once_with(10, pending=True)
        self.runtime.finish_transaction.assert_called_once()
        self.snapshot.assert_called_once()
        # Volver: cada mundo recupera sus propios ajustes.
        self.library.activate('main', 10)
        self.assertEqual((self.data / 'world/main.txt').read_text(), 'main world')
        self.assertIn('difficulty=normal', (self.data / 'server.properties').read_text())

    def test_new_world_is_generated_with_its_seed(self):
        wid = self.library.create('Fresh', '424242')
        self.library.activate(wid, 10)
        self.assertFalse((self.data / 'world').exists())  # Paper lo generará al arrancar
        self.assertIn('level-seed=424242', (self.data / 'server.properties').read_text())
        self.runtime.exec_probe.assert_any_call('rcon', 'bluemap', 'force-update', 'world', check=False)

    def test_failed_start_rolls_everything_back(self):
        self.stored_world('other', 'other world')
        self.runtime.start.side_effect = [Error('unhealthy'), None]
        with self.assertRaises(Error):
            self.library.activate('other', 10)
        self.assertEqual((self.data / 'world/main.txt').read_text(), 'main world')
        self.assertEqual((self.root / 'worlds/other/world/main.txt').read_text(), 'other world')
        self.assertIn('difficulty=normal', (self.data / 'server.properties').read_text())
        self.assertEqual(read_json(self.root / 'worlds/library.json')['active'], 'main')
        self.assertFalse((self.state / 'operation.json').exists())
        self.assertEqual(self.runtime.start.call_args.args, (10,))  # el mundo anterior vuelve a arrancar

    def test_import_java_zip_with_wrapper_folder_and_rejections(self):
        source = self.root / 'upload.zip'
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('My World/level.dat', level_dat(5000))
            archive.writestr('My World/region/r.0.0.mca', b'\x00' * 10)
        wid = self.library.import_world(source, 'Imported')
        self.assertTrue((self.root / 'worlds' / wid / 'world/level.dat').is_file())
        self.assertEqual(self.library.load()['worlds'][wid]['source'], 'import-java')
        newer = self.root / 'newer.zip'
        with zipfile.ZipFile(newer, 'w') as archive:
            archive.writestr('level.dat', level_dat(9999))
        with self.assertRaises(Error):
            self.library.import_world(newer, 'From the future')
        evil = self.root / 'evil.zip'
        with zipfile.ZipFile(evil, 'w') as archive:
            archive.writestr('../outside/level.dat', level_dat(5000))
        with self.assertRaises(Error):
            self.library.import_world(evil, 'Traversal')
        link = self.root / 'link.zip'
        with zipfile.ZipFile(link, 'w') as archive:
            info = zipfile.ZipInfo('level.dat')
            info.external_attr = 0o120777 << 16
            archive.writestr(info, '/etc/shadow')
        with self.assertRaises(Error):
            self.library.import_world(link, 'Symlink')
        empty = self.root / 'empty.zip'
        with zipfile.ZipFile(empty, 'w') as archive:
            archive.writestr('readme.txt', 'no world here')
        with self.assertRaises(Error):
            self.library.import_world(empty, 'Not a world')
        self.assertFalse(list(self.state.glob('import-*')))

    def test_export_and_delete_keep_a_verified_copy(self):
        self.stored_world('other', 'other world')
        exported = self.library.export('other')
        with zipfile.ZipFile(exported) as archive:
            self.assertEqual(sorted(archive.namelist()), ['level.dat', 'main.txt'])
        self.assertTrue(exported.with_name(exported.name + '.sha256').exists())
        with self.assertRaises(Error):
            self.library.delete('main')
        archive = self.library.delete('other')
        self.assertTrue(archive.exists())
        self.assertFalse((self.root / 'worlds/other').exists())
        self.assertNotIn('other', self.library.load()['worlds'])

    def test_restore_of_another_worlds_backup_keeps_the_active_one(self):
        self.stored_world('other', 'library copy of other')
        staged = self.root / 'staged'
        (staged / 'world').mkdir(parents=True)
        (staged / worlds.MARKER).write_text('other\n')
        commit, undo = self.library.restore_handoff(staged)
        self.assertEqual((self.root / 'worlds/main/world/main.txt').read_text(), 'main world')
        aside = list(self.state.glob('pre-restore-world-other-*'))
        self.assertEqual((aside[0] / 'main.txt').read_text(), 'library copy of other')
        commit()
        self.assertEqual(self.library.load()['active'], 'other')
        # Si restore fallara antes del intercambio, todo vuelve a su sitio.
        library = self.library.load()
        library['active'] = 'main'
        self.library.save(library)
        (self.root / 'worlds/main/world').rename(self.data / 'world')
        aside[0].rename(self.root / 'worlds/other/world')
        commit, undo = self.library.restore_handoff(staged)
        undo()
        self.assertEqual((self.data / 'world/main.txt').read_text(), 'main world')
        self.assertEqual((self.root / 'worlds/other/world/main.txt').read_text(), 'library copy of other')

    def test_same_world_restore_changes_nothing(self):
        staged = self.root / 'staged'
        staged.mkdir()
        commit, undo = self.library.restore_handoff(staged)  # sin marca = main
        commit()
        self.assertTrue((self.data / 'world/main.txt').exists())
        self.assertEqual(self.library.load()['active'], 'main')


if __name__ == '__main__':
    unittest.main()
