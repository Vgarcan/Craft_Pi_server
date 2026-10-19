"""Addons propios: identidad por código fuente y compilación aislada (Docker simulado)."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from mclan import addons, versions  # noqa: E402
from mclan.common import Error  # noqa: E402

JDK = 'eclipse-temurin@sha256:' + '2' * 64


class AddonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'addons', self.root / 'addons')
        self.release = self.root / 'releases/r1'
        self.release.mkdir(parents=True)
        with zipfile.ZipFile(self.release / 'paper.jar', 'w') as jar:
            jar.writestr('META-INF/libraries/io/papermc/paper/paper-api/26.3/paper-api-26.3.jar', b'api')
            jar.writestr('META-INF/libraries/net/kyori/adventure-key/5/adventure-key-5.jar', b'key')
            jar.writestr('META-INF/versions/26.3/server-26.3.jar.patch', b'not a library')
        (self.release / 'floodgate-spigot.jar').write_bytes(b'floodgate')
        (self.release / 'Geyser-Spigot.jar').write_bytes(b'geyser')

    def tearDown(self):
        self.temp.cleanup()

    def test_identity_follows_the_source_code(self):
        entry = addons.describe(self.root)['mclan_skins']
        self.assertEqual(entry['version'], '1.2.1')
        self.assertEqual(entry, addons.describe(ROOT)['mclan_skins'])
        java = self.root / 'addons/mclan-skins/src/dev/mclan/skins/MclanSkins.java'
        java.write_text(java.read_text() + '\n')
        self.assertNotEqual(addons.describe(self.root)['mclan_skins']['source_sha256'], entry['source_sha256'])
        os.symlink('/etc/passwd', self.root / 'addons/mclan-skins/evil.yml')
        with self.assertRaises(Error):
            addons.describe(self.root)

    def test_addon_is_part_of_the_release_and_managed_jars(self):
        self.assertEqual(versions.JARS['mclan_skins'], 'MclanSkins.jar')
        self.assertIn('MclanSkins.jar', (ROOT / 'docker/entrypoint.sh').read_text())
        config = (ROOT / 'addons/mclan-skins/config.yml').read_text()
        self.assertIn('java-skin: default', config)

    def test_build_runs_offline_against_release_libraries(self):
        lock = {'mclan_skins': addons.describe(self.root)['mclan_skins'], 'jdk_base': JDK, 'java': 25}
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            mounts = {args[i + 1].split(':')[1]: args[i + 1].split(':')[0] for i, a in enumerate(args) if a == '-v'}
            classpath = sorted(os.listdir(mounts['/cp']))
            self.assertEqual(classpath, ['000-paper-api-26.3.jar', '001-adventure-key-5.jar', 'Geyser-Spigot.jar', 'floodgate-spigot.jar'])
            self.assertTrue((Path(mounts['/src']) / 'src/dev/mclan/skins/MclanSkins.java').exists())
            with zipfile.ZipFile(Path(mounts['/out']) / 'addon.jar', 'w') as jar:
                jar.writestr('plugin.yml', 'name: MclanSkins')

        with patch('mclan.addons.run', side_effect=fake_run):
            jar = addons.build(self.root, self.release, lock, 'mclan_skins')
        args = calls[0]
        self.assertEqual(args[:3], ['docker', 'run', '--rm'])
        self.assertEqual(args[args.index('--network') + 1], 'none')
        self.assertTrue(args[args.index('--name') + 1].startswith('mclan-addon-mclan-skins-'))
        self.assertIn(JDK, args)
        self.assertTrue(any(a.endswith(':/cp:ro') for a in args) and any(a.endswith(':/src:ro') for a in args))
        self.assertIn('javac --release 25', args[-1])
        self.assertEqual(jar, self.release / 'MclanSkins.jar')
        self.assertEqual(jar.stat().st_mode & 0o777, 0o444)
        self.assertFalse(list(self.release.glob('.build-*')))

    def test_build_refuses_changed_source_or_missing_plugin_yml(self):
        lock = {'mclan_skins': dict(addons.describe(self.root)['mclan_skins'], source_sha256='0' * 64), 'jdk_base': JDK,
                'java': 25}
        with patch('mclan.addons.run') as run, self.assertRaises(Error):
            addons.build(self.root, self.release, lock, 'mclan_skins')
        run.assert_not_called()
        lock['mclan_skins'] = addons.describe(self.root)['mclan_skins']

        def no_descriptor(args, **kwargs):
            out = next(a for a in args if a.endswith(':/out')).rsplit(':', 1)[0]
            with zipfile.ZipFile(Path(out) / 'addon.jar', 'w') as jar:
                jar.writestr('Other.class', b'')

        with patch('mclan.addons.run', side_effect=no_descriptor), self.assertRaises(Error):
            addons.build(self.root, self.release, lock, 'mclan_skins')
        self.assertFalse((self.release / 'MclanSkins.jar').exists())
        self.assertFalse(list(self.release.glob('.build-*')))

    def test_a_build_that_times_out_removes_its_container(self):
        import subprocess
        lock = {'mclan_skins': addons.describe(self.root)['mclan_skins'], 'jdk_base': JDK, 'java': 25}
        calls = []

        def slow(args, **kwargs):
            calls.append(args)
            if args[:2] == ['docker', 'run']:
                raise subprocess.TimeoutExpired(args, 600)

        with patch('mclan.addons.run', side_effect=slow), self.assertRaises(subprocess.TimeoutExpired):
            addons.build(self.root, self.release, lock, 'mclan_skins')
        name = calls[0][calls[0].index('--name') + 1]
        self.assertEqual(calls[1], ['docker', 'rm', '-f', name])
        self.assertFalse(list(self.release.glob('.build-*')))

    def test_odd_source_names_are_refused(self):
        (self.root / 'addons/mclan-skins/src/-evil.java').write_text('class X {}')
        lock = {'mclan_skins': addons.describe(self.root)['mclan_skins'], 'jdk_base': JDK, 'java': 25}
        with patch('mclan.addons.run') as run, self.assertRaises(Error):
            addons.build(self.root, self.release, lock, 'mclan_skins')
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
