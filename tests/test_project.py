"""Pruebas sin APT, Docker, internet ni escritura en /etc: se usan temporales y dobles."""
import argparse
import copy
import importlib.util
import io
import json
from pathlib import Path
import socket
import struct
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from mclan import backups, cli, versions
from mclan.classfile import integer_constant
from mclan.common import Error, confirm, create_only, read_json, sha256, write_json
from mclan.net import Client, valid_url
from mclan import runtime as runtime_module
from mclan.runtime import Runtime, shutdown_saved, validate_diagnostic
from mclan.versions import managed_jars

spec = importlib.util.spec_from_file_location('probe', ROOT / 'docker/probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
FIXTURES = ROOT / 'tests/fixtures'


class FakeClient:
    def json(self, url):
        if 'hangar.papermc.io' in url:
            return read_json(FIXTURES / 'hangar_bluemap.json')
        if '/geyser/' in url:
            return read_json(FIXTURES / 'geyser_build.json')
        if '/floodgate/' in url:
            return read_json(FIXTURES / 'floodgate.json')
        if url.endswith('/builds'):
            return read_json(FIXTURES / 'paper_builds.json')
        return read_json(FIXTURES / 'paper_version.json')

    def fetch(self, url):
        return (FIXTURES / 'supported.html').read_bytes()


def sample_active(root):
    active = versions.resolve(FakeClient())
    active.update(lan_ip='192.168.1.25', runtime_image='mclan-runtime:sample',
                  java_base='eclipse-temurin@sha256:' + '1'*64,
                  geyser_config_version=10,
                  runtime_sources={n: sha256(ROOT / 'docker' / n) for n in runtime_module.RUNTIME_FILES})
    release = root / 'releases' / active['id']
    release.mkdir(parents=True)
    for key, name in managed_jars(active):
        (release / name).write_bytes(key.encode())
        active[key]['sha256'] = sha256(release / name)
    write_json(release / 'versions.lock.json', active)
    return active


class VersionsTests(unittest.TestCase):
    def test_live_contract_fixtures_resolve_stable_matching_version_and_java(self):
        lock = versions.resolve(FakeClient())
        self.assertEqual(lock['minecraft'], '26.3')
        self.assertEqual(lock['java'], 25)
        self.assertEqual(lock['paper']['build'], 205)
        self.assertEqual(lock['geyser']['build'], 1265)
        self.assertNotIn('latest', lock['geyser']['url'])
        self.assertIn('bluemap', lock)

    def test_bluemap_newest_release_declaring_exact_version(self):
        lock = versions.resolve(FakeClient())
        self.assertEqual(lock['bluemap']['version'], '5.29')
        self.assertEqual(lock['bluemap']['sha256'], '7bf475936c26107d476c63151c76d5b49e865eebeada12c9e7d92155de877b9f')
        valid_url(lock['bluemap']['url'])
        # El orden de la API no decide; tampoco se aceptan canales no Release ni versiones no declaradas.
        data = read_json(FIXTURES / 'hangar_bluemap.json')
        data['result'].reverse()
        data['result'][-1]['channel']['name'] = 'Snapshot'
        client = FakeClient()
        client.json = lambda url: data if 'hangar' in url else FakeClient().json(url)
        self.assertEqual(versions.bluemap(client, '26.3')['version'], '5.28')
        with self.assertRaises(Error): versions.bluemap(client, '27.1')

    def test_bluemap_is_part_of_release_identity(self):
        first = versions.resolve(FakeClient())
        data = read_json(FIXTURES / 'hangar_bluemap.json')
        data['result'] = data['result'][1:]
        client = FakeClient()
        client.json = lambda url: data if 'hangar' in url else FakeClient().json(url)
        self.assertNotEqual(versions.resolve(client)['id'], first['id'])

    def test_old_release_without_bluemap_keeps_three_jars(self):
        self.assertEqual([k for k, _ in managed_jars({'paper': 1, 'geyser': 1, 'floodgate': 1})],
                         ['paper', 'geyser', 'floodgate'])

    def test_mismatch_fails_closed(self):
        with self.assertRaises(Error):
            versions.resolve(FakeClient(), '1.21.11')

    def test_unsupported_html_fails_closed(self):
        with self.assertRaises(Error):
            versions.supported_versions('<p>New documentation format</p>')

    def test_no_stable_build_fails(self):
        with self.assertRaises(Error):
            versions.stable_build([{'id': 100, 'channel': 'EXPERIMENTAL'}])

    def test_highest_stable_build_not_api_order(self):
        self.assertEqual(versions.stable_build([{'id': 3, 'channel': 'STABLE'},
                                               {'id': 50, 'channel': 'EXPERIMENTAL'},
                                               {'id': 7, 'channel': 'STABLE'}])['id'], 7)

    def test_html_cosmetic_change_keeps_release_id(self):
        client = FakeClient()
        first = versions.resolve(client)
        client.fetch = lambda u: b'<aside>example</aside>' + (FIXTURES / 'supported.html').read_bytes()
        self.assertEqual(first['id'], versions.resolve(client)['id'])

    def test_publication_race_is_rejected(self):
        client = FakeClient()
        original = client.json
        calls = 0
        def changed(url):
            nonlocal calls
            result = original(url)
            if '/geyser/' in url:
                calls += 1
                if calls == 2:
                    result['build'] += 1
            return result
        client.json = changed
        with self.assertRaises(Error):
            versions.resolve(client)


class FileSafetyTests(unittest.TestCase):
    def test_existing_world_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'level.dat'
            path.write_bytes(b'original-world')
            self.assertFalse(create_only(path, 'replacement'))
            self.assertEqual(path.read_bytes(), b'original-world')

    def test_create_only_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'link'
            path.symlink_to('/does-not-exist')
            with self.assertRaises(Error):
                create_only(path, 'overwrite')

    def test_checksum_failure_does_not_publish_partial_download(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'plugin.jar'
            client = Client('https://example.com/contact')
            client.fetch = lambda *a, **k: b'bad-download'
            with self.assertRaises(Error):
                client.download({'sha256': 'a'*64, 'url': 'https://download.geysermc.org/plugin'}, destination)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix('.part').exists())

    def test_existing_jar_with_bad_hash_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'plugin.jar'
            destination.write_bytes(b'existing')
            with self.assertRaises(Error):
                Client('https://example.com').download({'sha256': 'a'*64}, destination)
            self.assertEqual(destination.read_bytes(), b'existing')

    def test_sources_are_restricted_to_https_official_hosts(self):
        for url in ['http://fill.papermc.io/a', 'https://evil.example/a', 'https://user:pass@fill.papermc.io/a']:
            with self.subTest(url=url), self.assertRaises(Error):
                valid_url(url)

    def test_confirmation_cancel_and_noninteractive_refuse(self):
        with patch('builtins.input', return_value='no'), self.assertRaises(Error):
            confirm('RESTAURAR', 'Test')
        with patch('builtins.input', side_effect=EOFError), self.assertRaises(Error):
            confirm('RESTAURAR', 'Test')

    def test_process_lock_excludes_second_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with cli.locked(root):
                with self.assertRaises(Error):
                    with cli.locked(root):
                        pass

    def test_arm64_preflight_does_not_install_on_other_arch(self):
        from mclan import host
        with patch.object(host, 'run', return_value=Mock(stdout='amd64\n')):
            with patch.object(host.platform, 'machine', return_value='x86_64'):
                with self.assertRaises(Error):
                    host.preflight(ROOT)

    def test_exported_environment_cannot_override_managed_compose(self):
        import mclan.common as common
        with patch.dict(common.os.environ, {'LAN_IP': '0.0.0.0', 'RUNTIME_IMAGE': 'other',
                                          'RELEASE_ID': 'other', 'COMPOSE_FILE': 'other.yml'}), \
             patch.object(common.subprocess, 'run', return_value=Mock(returncode=0)) as runner:
            common.run(['docker', 'compose', 'config'])
        environment = runner.call_args.kwargs['env']
        for name in ('LAN_IP', 'RUNTIME_IMAGE', 'RELEASE_ID', 'COMPOSE_FILE'):
            self.assertNotIn(name, environment)

    def test_jvm_constant_reader_and_rejection(self):
        # Classfile mínimo sintético: pool UTF8, Integer y campo ConstantValue.
        def utf(value):
            raw = value.encode()
            return b'\x01' + struct.pack('>H', len(raw)) + raw
        pool = utf('CONFIG_VERSION') + utf('I') + utf('ConstantValue') + b'\x03' + struct.pack('>i', 10)
        payload = (b'\xca\xfe\xba\xbe' + struct.pack('>HHH', 0, 65, 5) + pool
                   + struct.pack('>HHHHH', 0, 0, 0, 0, 1)
                   + struct.pack('>HHHHHIH', 0x19, 1, 2, 1, 3, 2, 4))
        self.assertEqual(integer_constant(payload, 'CONFIG_VERSION'), 10)
        with self.assertRaises(Error):
            integer_constant(b'bad', 'CONFIG_VERSION')


class HostAndLifecycleTests(unittest.TestCase):
    def test_existing_docker_compose_buildx_do_not_install_packages(self):
        from mclan import host
        def command(args, **kwargs):
            if args[:3] == ['docker', 'context', 'inspect']:
                return Mock(returncode=0, stdout='unix:///var/run/docker.sock\n')
            if args[:2] == ['docker', 'info']:
                return Mock(returncode=0, stdout='linux/aarch64\n')
            return Mock(returncode=0, stdout='OK')
        with patch.object(host.shutil, 'which', return_value='/usr/bin/docker'), \
             patch.object(host, 'run', side_effect=command) as calls, \
             patch.object(host, 'apt_install') as installer:
            host.setup_docker({'VERSION_CODENAME': 'trixie'}, Mock())
        installer.assert_not_called()
        self.assertFalse(any(call.args[0][0] == 'apt-get' for call in calls.call_args_list))

    def test_only_missing_apt_packages_are_requested(self):
        from mclan import host
        with patch.object(host, 'installed', side_effect=lambda p: p == 'existing'), \
             patch.object(host, 'run') as runner:
            host.apt_install(['existing', 'missing'])
        args = runner.call_args.args[0]
        self.assertIn('missing', args)
        self.assertNotIn('existing', args)
        self.assertIn('--no-remove', args)

    def test_graceful_sigterm_requires_current_boot_save_evidence(self):
        runtime = Runtime(ROOT)
        runtime.container = Mock(return_value={'Id': 'abc', 'State': {
            'Running': False, 'OOMKilled': False, 'ExitCode': 143, 'StartedAt': '2026-10-18T16:00:00Z'}})
        with patch('mclan.runtime.run', return_value=Mock(stdout='All dimensions are saved', stderr='')) as runner:
            runtime.assert_clean_stop()
        self.assertIn('2026-10-18T16:00:00Z', runner.call_args.args[0])
        with patch('mclan.runtime.run', return_value=Mock(stdout='Old or incomplete logs', stderr='')):
            with self.assertRaises(Error): runtime.assert_clean_stop()
        runtime.container.return_value['State']['OOMKilled'] = True
        with self.assertRaises(Error): runtime.assert_clean_stop()

    def test_moonrise_shutdown_sequence_counts_as_saved(self):
        # Secuencia observada en Paper 26.3 build 205 en la Raspberry Pi (18-10-2026).
        log = ('[16:39:41 INFO]: Stopping server\n[16:39:41 INFO]: Saving worlds\n'
               "[16:39:41 INFO]: [ChunkHolderManager] Halted I/O scheduler for world 'minecraft:overworld'\n"
               '[16:39:41 INFO]: All RegionFile I/O tasks to complete\n')
        self.assertTrue(shutdown_saved(log))
        self.assertTrue(shutdown_saved('All dimensions are saved'))
        self.assertFalse(shutdown_saved('[16:39:41 INFO]: Saving worlds\n'))
        # Orden invertido: el cierre de I/O no puede preceder al guardado.
        self.assertFalse(shutdown_saved('All RegionFile I/O tasks to complete\nSaving worlds\n'
                                        'Halted I/O scheduler for world'))
        runtime = Runtime(ROOT)
        runtime.container = Mock(return_value={'Id': 'abc', 'State': {
            'Running': False, 'OOMKilled': False, 'ExitCode': 143, 'StartedAt': '2026-10-18T16:00:00Z'}})
        with patch('mclan.runtime.run', return_value=Mock(stdout=log, stderr='')):
            runtime.assert_clean_stop()

    def test_memory_cgroup_detection(self):
        from mclan import host
        with tempfile.TemporaryDirectory() as directory:
            controllers = Path(directory) / 'cgroup.controllers'
            controllers.write_text('cpuset cpu io pids\n')
            self.assertFalse(host.memory_limits(controllers))
            controllers.write_text('cpuset cpu io memory pids\n')
            self.assertTrue(host.memory_limits(controllers))
            self.assertFalse(host.memory_limits(Path(directory) / 'missing'))

    def test_nethernet_verification_requires_signaling_log_and_tcp(self):
        runtime = Runtime(ROOT)
        info = {'Id': 'abc', 'State': {'StartedAt': '2026-10-18T16:00:00Z'}}
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen()
        port = server.getsockname()[1]
        real_connect = socket.create_connection
        try:
            with patch('mclan.runtime.run', return_value=Mock(stdout='[Geyser-Spigot] Built-in signaling started on 0.0.0.0:19132', stderr='')), \
                 patch('socket.create_connection', side_effect=lambda address, timeout: real_connect(('127.0.0.1', port), timeout)):
                runtime.verify_nethernet({'lan_ip': '127.0.0.1'}, info)
            with patch('mclan.runtime.run', return_value=Mock(stdout='NetherNet support is NOT enabled', stderr='')):
                with self.assertRaises(Error): runtime.verify_nethernet({'lan_ip': '127.0.0.1'}, info)
        finally:
            server.close()
        with patch('mclan.runtime.run', return_value=Mock(stdout='Built-in signaling started on', stderr='')), \
             patch('socket.create_connection', side_effect=OSError('refused')):
            with self.assertRaises(Error): runtime.verify_nethernet({'lan_ip': '127.0.0.1'}, info)

    def test_pending_start_keeps_marker_and_disables_auto_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(Path(directory))
            write_json(runtime.state / 'operation.json', {'operation': 'update'})
            runtime.validate = Mock(return_value={})
            runtime.compose = Mock()
            runtime.verify = Mock()
            runtime.start(10, pending=True)
            self.assertEqual(read_json(runtime.state / 'transaction.override.yaml')['services']['minecraft']['restart'], 'no')
            self.assertTrue((runtime.state / 'operation.json').exists())
            self.assertTrue(runtime.compose.call_args.kwargs['pending'])
            with self.assertRaises(Error): runtime.start(10)

    def test_finish_transaction_rearms_restart_only_after_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(Path(directory))
            write_json(runtime.state / 'operation.json', {'operation': 'update'})
            runtime.container = Mock(return_value={'Id': 'abc', 'State': {'Running': True}})
            with patch('mclan.runtime.run') as runner:
                runtime.finish_transaction()
            self.assertFalse((runtime.state / 'operation.json').exists())
            self.assertEqual(runner.call_args.args[0], ['docker', 'update', '--restart', 'unless-stopped', 'abc'])


class ProtocolTests(unittest.TestCase):
    def test_varint_and_truncated_input(self):
        class Reader:
            def __init__(self, data): self.data = io.BytesIO(data)
            def recv(self, size): return self.data.read(size)
        for value in (0, 127, 128, 774, 2**31-1):
            self.assertEqual(probe.read_varint(Reader(probe.varint(value))), value)
        with self.assertRaises(ValueError): probe.read_varint(Reader(b'\x80'))
        with self.assertRaises(ValueError): probe.read_varint(Reader(b'\x80'*6))

    def test_java_status_over_real_local_tcp_socket(self):
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen()
        port = server.getsockname()[1]
        errors = []
        def serve():
            try:
                conn, _ = server.accept()
                with conn:
                    conn.settimeout(2)
                    probe.exact(conn, probe.read_varint(conn))
                    self.assertEqual(probe.exact(conn, 2), b'\x01\x00')
                    raw = json.dumps({'version': {'name': '26.3', 'protocol': 999}}).encode()
                    body = b'\x00' + probe.varint(len(raw)) + raw
                    conn.sendall(probe.varint(len(body)) + body)
            except BaseException as exc: errors.append(exc)
            finally: server.close()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        result = probe.java_status('127.0.0.1', port)
        thread.join(3)
        self.assertFalse(errors)
        self.assertEqual(result['version']['protocol'], 999)

    def test_bedrock_ping_over_real_local_udp_socket(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(('127.0.0.1', 0))
        port = server.getsockname()[1]
        def serve():
            server.settimeout(3)
            try:
                request, address = server.recvfrom(1024)
                text = b'MCPE;Test;2193;26.52;0;6;123;LAN;Survival;1;19132;19133;'
                pong = b'\x1c' + request[1:9] + b'12345678' + probe.MAGIC + struct.pack('>H', len(text)) + text
                server.sendto(pong, address)
            finally: server.close()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.assertEqual(probe.bedrock_status('127.0.0.1', port)['version'], '26.52')
        thread.join(3)

    def test_malformed_pong_is_not_success(self):
        fake = Mock()
        fake.__enter__ = Mock(return_value=fake)
        fake.__exit__ = Mock(return_value=False)
        fake.recv.return_value = b'not-bedrock'
        with patch.object(probe.socket, 'socket', return_value=fake), self.assertRaises(ValueError):
            probe.bedrock_status('127.0.0.1')

    def test_rcon_authentication_and_command(self):
        fake = Mock()
        fake.__enter__ = Mock(return_value=fake)
        fake.__exit__ = Mock(return_value=False)
        def packet(identifier, kind, body):
            value = struct.pack('<ii', identifier, kind) + body.encode() + b'\0\0'
            return struct.pack('<i', len(value)) + value
        incoming = io.BytesIO(packet(1, 0, '') + packet(1, 2, '') + packet(2, 0, 'OK'))
        fake.recv.side_effect = incoming.read
        with tempfile.TemporaryDirectory() as directory:
            props = Path(directory) / 'server.properties'
            props.write_text('rcon.port=25575\nrcon.password=secret\n')
            with patch.object(probe.socket, 'create_connection', return_value=fake):
                self.assertEqual(probe.rcon('list', props), 'OK')


class DiagnosticTests(unittest.TestCase):
    def release(self):
        """Release sin BlueMap: el diagnóstico base sólo contiene Geyser y Floodgate."""
        return {k: v for k, v in versions.resolve(FakeClient()).items() if k != 'bluemap'}

    def diagnostic(self):
        return {'bootstrapInfo': {'onlineMode': True, 'plugins': [
            {'name': 'Geyser-Spigot', 'enabled': True}, {'name': 'floodgate', 'enabled': True}]},
            'versionInfo': {'mcInfo': {'javaVersions': ['26.3'], 'javaProtocol': 999}},
            'gitInfo': {'buildNumber': '1265'}, 'config': {'java': {'auth-type': 'FLOODGATE'},
                                                        'advanced': {'bedrock': {'validate-bedrock-login': True}}}}

    def test_enabled_plugins_and_exact_protocol_required(self):
        active = self.release()
        validate_diagnostic(self.diagnostic(), active, 999)
        invalid = self.diagnostic()
        invalid['bootstrapInfo']['plugins'][1]['enabled'] = False
        with self.assertRaises(Error): validate_diagnostic(invalid, active, 999)
        with self.assertRaises(Error): validate_diagnostic(self.diagnostic(), active, 998)

    def test_offline_java_auth_rejected(self):
        invalid = self.diagnostic()
        invalid['bootstrapInfo']['onlineMode'] = False
        with self.assertRaises(Error): validate_diagnostic(invalid, self.release(), 999)

    def test_wrong_auth_and_wrong_build_rejected(self):
        for change in ('auth', 'build'):
            diagnostic = self.diagnostic()
            if change == 'auth': diagnostic['config']['java']['auth-type'] = 'offline'
            else: diagnostic['gitInfo']['buildNumber'] = '1'
            with self.assertRaises(Error): validate_diagnostic(diagnostic, self.release(), 999)

    def test_bluemap_must_be_enabled_when_in_release(self):
        active = versions.resolve(FakeClient())
        diagnostic = self.diagnostic()
        with self.assertRaises(Error): validate_diagnostic(diagnostic, active, 999)
        validate_diagnostic(diagnostic, dict(active, disabled_plugins=['bluemap']), 999)
        diagnostic['bootstrapInfo']['plugins'].append({'name': 'BlueMap', 'enabled': True})
        validate_diagnostic(diagnostic, active, 999)
        old = {k: v for k, v in active.items() if k != 'bluemap'}
        validate_diagnostic(self.diagnostic(), old, 999)

    def test_disabled_bedrock_identity_validation_is_rejected(self):
        diagnostic = self.diagnostic()
        diagnostic['config']['advanced']['bedrock']['validate-bedrock-login'] = False
        with self.assertRaises(Error): validate_diagnostic(diagnostic, self.release(), 999)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = Runtime(self.root)
        self.runtime.state.mkdir()
        (self.root / 'docker').mkdir()
        for name in (*runtime_module.RUNTIME_FILES, 'compose.template.yaml'):
            (self.root / 'docker' / name).write_bytes((ROOT / 'docker' / name).read_bytes())
        self.active = sample_active(self.root)
        self.runtime.activate(self.active)
        (self.root / 'compose.yaml').write_bytes((ROOT / 'docker/compose.template.yaml').read_bytes())
        write_json(self.root / 'config/settings.json', {'contact': 'https://example.com', 'lan_ip': 'auto',
                                                       'minecraft_version': 'auto', 'startup_timeout': 900})
        self.runtime.data.mkdir()
        (self.runtime.data / 'level.dat').write_bytes(b'original-world')
        self.runtime.assert_clean_stop = Mock()
        self.runtime.running = Mock(return_value=False)
        self.runtime.stop = Mock()
        self.runtime.start = Mock()
        self.runtime.finish_transaction = Mock(side_effect=lambda: (self.runtime.state / 'operation.json').unlink(missing_ok=True))
        self.runtime.stage = Mock(side_effect=lambda lock, client, ip: lock)
        self.runtime.validate = Mock(return_value=self.active)

    def tearDown(self): self.temp.cleanup()

    def test_snapshot_roundtrip_restores_world_and_safety_backup(self):
        path = backups.snapshot(self.runtime)
        self.assertTrue(path.with_name(path.name + '.sha256').exists())
        (self.runtime.data / 'level.dat').write_bytes(b'changed-world')
        with patch('mclan.backups.os.chown'):
            backups.restore(self.runtime, path, Mock(), 10)
        self.assertEqual((self.runtime.data / 'level.dat').read_bytes(), b'original-world')
        self.assertEqual(len(list((self.root / 'backups').glob('*.tar.gz'))), 2)
        self.assertFalse((self.runtime.state / 'operation.json').exists())
        self.runtime.start.assert_called_once_with(10, pending=True)

    def test_corrupt_backup_never_touches_current_world(self):
        path = backups.snapshot(self.runtime)
        path.write_bytes(b'corrupted')
        with self.assertRaises(Error): backups.restore(self.runtime, path, Mock(), 10)
        self.assertEqual((self.runtime.data / 'level.dat').read_bytes(), b'original-world')
        self.runtime.stop.assert_not_called()

    def test_snapshot_refuses_symlinks_and_low_space(self):
        (self.runtime.data / 'link').symlink_to('/etc/passwd')
        with self.assertRaises(Error): backups.snapshot(self.runtime)
        (self.runtime.data / 'link').unlink()
        with patch('mclan.backups.shutil.disk_usage', return_value=Mock(free=1)):
            with self.assertRaises(Error): backups.snapshot(self.runtime)

    def test_archive_traversal_and_hardlink_are_rejected(self):
        for bad in ('../outside', '/tmp/outside', 'data/../../outside'):
            self.assertFalse(backups.allowed(bad, self.active['id']))
        path = self.root / 'bad.tar.gz'
        with tarfile.open(path, 'w:gz') as tar:
            manifest = json.dumps({'schema': 1, 'active': self.active['id'], 'files': {}}).encode()
            info = tarfile.TarInfo('snapshot.json'); info.size = len(manifest)
            tar.addfile(info, io.BytesIO(manifest))
            link = tarfile.TarInfo('data/link'); link.type = tarfile.LNKTYPE; link.linkname = '/etc/passwd'
            tar.addfile(link)
        with self.assertRaises(Error): backups.validate_archive(path, self.root / 'unused', extract=False)

    def test_update_failure_keeps_backup_and_blocks_next_start(self):
        args = argparse.Namespace(minecraft_version='auto', plan=False, timeout=10)
        newer = copy.deepcopy(self.active)
        newer['id'] = 'b'*20
        self.runtime.running.return_value = True
        self.runtime.start.side_effect = Error('Failed new server')
        with patch('mclan.cli.versions.resolve', return_value=newer), patch('mclan.cli.confirm'):
            with self.assertRaises(Error): cli.update(self.runtime, args, Mock())
        marker = read_json(self.runtime.state / 'operation.json')
        self.assertEqual(marker['operation'], 'update-failed')
        self.assertTrue(Path(marker['backup']).exists())
        real = Runtime(self.root)
        with self.assertRaises(Error): real.start(10)

    def test_backup_failure_restarts_previous_running_release(self):
        self.runtime.running.return_value = True
        proposal = dict(self.active, id='c'*20)
        args = argparse.Namespace(minecraft_version='auto', plan=False, timeout=10)
        with patch('mclan.cli.versions.resolve', return_value=proposal), patch('mclan.cli.confirm'), \
             patch('mclan.cli.backups.snapshot', side_effect=Error('disk full')):
            with self.assertRaises(Error): cli.update(self.runtime, args, Mock())
        self.runtime.start.assert_called_once_with(10, allow_drift=True)
        self.assertEqual(self.runtime.active()['id'], self.active['id'])

    def old_template(self):
        """Simula una plantilla gestionada anterior conocida por el proyecto."""
        old = (ROOT / 'docker/compose.template.yaml').read_text() + '# plantilla anterior\n'
        (self.root / 'compose.yaml').write_text(old)
        return patch.dict(runtime_module.PREVIOUS_TEMPLATES, {sha256(self.root / 'compose.yaml'): 'test'})

    def test_strict_validation_rejects_project_drift_but_update_detects_it(self):
        real = Runtime(self.root)
        with self.old_template():
            with self.assertRaises(Error): real.validate()
            self.assertTrue(real.template_drift())
        (self.root / 'compose.yaml').write_text('edited by hand\n')
        with self.assertRaises(Error): real.template_drift()
        (self.root / 'compose.yaml').write_bytes((ROOT / 'docker/compose.template.yaml').read_bytes())
        self.assertFalse(real.template_drift())
        drifted = dict(self.active, runtime_sources={n: '0'*64 for n in runtime_module.RUNTIME_FILES})
        self.assertTrue(real.runtime_drift(drifted))
        self.assertFalse(real.runtime_drift(self.active))

    def test_project_update_with_same_builds_rebuilds_backs_up_and_applies_template(self):
        args = argparse.Namespace(minecraft_version=None, plan=False, timeout=10)
        self.runtime.running.return_value = True
        with self.old_template(), patch('mclan.cli.versions.resolve', return_value=copy.deepcopy(self.active)), \
             patch('mclan.cli.confirm'):
            cli.update(self.runtime, args, Mock())
        self.runtime.stage.assert_called_once()
        self.assertEqual(len(list((self.root / 'backups').glob('*-pre-update.tar.gz'))), 1)
        self.assertEqual((self.root / 'compose.yaml').read_text(), (ROOT / 'docker/compose.template.yaml').read_text())
        self.runtime.start.assert_called_once_with(10, pending=True)
        self.assertFalse((self.runtime.state / 'operation.json').exists())

    def test_update_plan_reports_project_drift_without_changes(self):
        args = argparse.Namespace(minecraft_version=None, plan=True, timeout=10)
        with self.old_template(), patch('mclan.cli.versions.resolve', return_value=copy.deepcopy(self.active)):
            before = (self.root / 'compose.yaml').read_text()
            cli.update(self.runtime, args, Mock())
            self.assertEqual((self.root / 'compose.yaml').read_text(), before)
        self.runtime.stage.assert_not_called()
        self.runtime.stop.assert_not_called()

    def test_no_drift_and_same_builds_changes_nothing(self):
        args = argparse.Namespace(minecraft_version=None, plan=False, timeout=10)
        with patch('mclan.cli.versions.resolve', return_value=copy.deepcopy(self.active)), patch('mclan.cli.confirm') as asked:
            cli.update(self.runtime, args, Mock())
        asked.assert_not_called()
        self.runtime.stage.assert_not_called()

    def test_restore_backup_from_previous_template_activates_current_template(self):
        with self.old_template():
            path = backups.snapshot(self.runtime)
            (self.runtime.data / 'level.dat').write_bytes(b'changed-world')
            with patch('mclan.backups.os.chown'):
                backups.restore(self.runtime, path, Mock(), 10)
        self.assertEqual((self.runtime.data / 'level.dat').read_bytes(), b'original-world')
        self.assertEqual((self.root / 'compose.yaml').read_text(), (ROOT / 'docker/compose.template.yaml').read_text())

    def test_restore_rejects_backup_with_hand_edited_compose(self):
        (self.root / 'compose.yaml').write_text('edited by hand\n')
        path = backups.snapshot(self.runtime)
        (self.root / 'compose.yaml').write_bytes((ROOT / 'docker/compose.template.yaml').read_bytes())
        (self.runtime.data / 'level.dat').write_bytes(b'changed-world')
        with self.assertRaises(Error): backups.restore(self.runtime, path, Mock(), 10)
        self.assertEqual((self.runtime.data / 'level.dat').read_bytes(), b'changed-world')

    def test_snapshot_excludes_regenerable_map_tiles(self):
        tiles = self.runtime.data / 'bluemap/web/maps/world/tiles'
        tiles.mkdir(parents=True)
        (tiles / 'x.prbm.gz').write_bytes(b'tile')
        (self.runtime.data / 'bluemap/web/index.html').write_text('webapp')
        path = backups.snapshot(self.runtime)
        with tarfile.open(path) as archive:
            names = archive.getnames()
        self.assertIn('data/bluemap/web/index.html', names)
        self.assertFalse([n for n in names if n.startswith('data/bluemap/web/maps')])
        backups.validate_archive(path, self.root / 'unused', extract=False)

    def test_bluemap_setup_script_runs_as_container_user_logic(self):
        import subprocess
        folder = self.root / 'BlueMap'
        script = ROOT / 'docker/bluemap-setup.sh'
        first = subprocess.run(['sh', str(script), str(folder)], capture_output=True, text=True, check=True)
        self.assertEqual(first.stdout, '')
        core = folder / 'core.conf'
        self.assertIn('render-thread-count: 1', core.read_text())
        core.write_text('render-thread-count: 2\n')
        (folder / 'maps').mkdir()
        (folder / 'maps/world.conf').write_text('world: "world"\nignore-missing-light-data: false\n')
        second = subprocess.run(['sh', str(script), str(folder)], capture_output=True, text=True, check=True)
        self.assertEqual(second.stdout.strip(), 'changed')
        self.assertEqual(core.read_text(), 'render-thread-count: 2\n')
        self.assertIn('ignore-missing-light-data: true', (folder / 'maps/world.conf').read_text())
        # Si el operador lo revierte después, no se vuelve a tocar.
        conf = folder / 'maps/world.conf'
        conf.write_text(conf.read_text().replace('ignore-missing-light-data: true', 'ignore-missing-light-data: false'))
        third = subprocess.run(['sh', str(script), str(folder)], capture_output=True, text=True, check=True)
        self.assertEqual(third.stdout, '')
        self.assertIn('ignore-missing-light-data: false', conf.read_text())
        # Un mapa que es un enlace no se sigue.
        (folder / 'maps/evil.conf').symlink_to(self.root / 'outside.conf')
        (self.root / 'outside.conf').write_text('ignore-missing-light-data: false\n')
        subprocess.run(['sh', str(script), str(folder)], capture_output=True, text=True, check=True)
        self.assertEqual((self.root / 'outside.conf').read_text(), 'ignore-missing-light-data: false\n')

    def test_disabling_bluemap_changes_env_and_relaxes_checks(self):
        from mclan.runtime import plugin_active
        real = Runtime(self.root)
        env_before = (self.root / '.env').read_text()
        self.assertNotIn('DISABLED_PLUGINS', env_before)
        active = real.set_plugin('bluemap', False)
        self.assertEqual(active['disabled_plugins'], ['bluemap'])
        self.assertIn('DISABLED_PLUGINS=bluemap\n', (self.root / '.env').read_text())
        self.assertFalse(plugin_active(active, 'bluemap'))
        real.set_plugin('bluemap', True)
        self.assertEqual((self.root / '.env').read_text(), env_before)
        with self.assertRaises(Error): real.set_plugin('geyser', False)

    def test_manage_plugin_command_stops_toggles_and_restarts(self):
        self.runtime.running.return_value = True
        args = argparse.Namespace(command='plugin', name='bluemap', state='off', timeout=10)
        self.runtime.set_plugin = Mock()
        with patch('mclan.cli.run', return_value=Mock(stdout='unix:///var/run/docker.sock\n')), \
             patch('mclan.cli.settings', return_value={'contact': 'https://example.com'}):
            cli.manage(self.runtime, args)
        self.runtime.stop.assert_called_once()
        self.runtime.set_plugin.assert_called_once_with('bluemap', False)
        self.runtime.start.assert_called_once_with(10)

    def test_install_repeat_does_not_resolve_or_overwrite_world(self):
        args = argparse.Namespace(contact=None, lan_ip=None, minecraft_version=None,
                                  check=False, accept_eula=False, timeout=10)
        with patch('mclan.cli.host.preflight', return_value=({}, '192.168.1.25')), \
             patch('mclan.cli.host.setup_docker'), patch('mclan.cli.versions.resolve') as resolver:
            cli.install(self.runtime, args)
        resolver.assert_not_called()
        self.assertEqual((self.runtime.data / 'level.dat').read_bytes(), b'original-world')

    def test_unmanaged_data_is_refused(self):
        self.runtime.active_path.unlink()
        args = argparse.Namespace(contact=None, lan_ip=None, minecraft_version=None,
                                  check=False, accept_eula=True, timeout=10)
        with patch('mclan.cli.host.preflight', return_value=({}, '192.168.1.25')):
            with self.assertRaises(Error): cli.install(self.runtime, args)
        self.assertEqual((self.runtime.data / 'level.dat').read_bytes(), b'original-world')


    def test_restore_after_crash_preserves_unverified_previous_data(self):
        path = backups.snapshot(self.runtime)
        (self.runtime.data / 'level.dat').write_bytes(b'crashed-world')
        self.runtime.assert_clean_stop.side_effect = Error('unclean shutdown')
        with patch('mclan.backups.os.chown'):
            backups.restore(self.runtime, path, Mock(), 10)
        self.assertEqual((self.runtime.data / 'level.dat').read_bytes(), b'original-world')
        previous = list(self.runtime.state.glob('pre-restore-*'))
        self.assertEqual(len(previous), 1)
        self.assertEqual((previous[0] / 'level.dat').read_bytes(), b'crashed-world')

    def test_restore_can_rebind_for_new_host(self):
        path = backups.snapshot(self.runtime)
        with patch('mclan.backups.os.chown'), patch('mclan.host.lan_ip', return_value='192.168.1.60'):
            backups.restore(self.runtime, path, Mock(), 10, ip_override='192.168.1.60')
        self.assertEqual(self.runtime.active()['lan_ip'], '192.168.1.60')
        self.assertIn('LAN_IP=192.168.1.60', (self.root / '.env').read_text())

    def test_failed_restore_retains_marker_and_both_world_states(self):
        path = backups.snapshot(self.runtime)
        (self.runtime.data / 'level.dat').write_bytes(b'before-restore')
        self.runtime.start.side_effect = Error('health failed')
        with patch('mclan.backups.os.chown'):
            with self.assertRaises(Error): backups.restore(self.runtime, path, Mock(), 10)
        self.assertEqual(read_json(self.runtime.state / 'operation.json')['operation'], 'restore-failed')
        previous = list(self.runtime.state.glob('pre-restore-*'))
        self.assertEqual((previous[0] / 'level.dat').read_bytes(), b'before-restore')
        self.assertEqual((self.runtime.data / 'level.dat').read_bytes(), b'original-world')


if __name__ == '__main__':
    unittest.main()
