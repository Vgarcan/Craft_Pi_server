#!/usr/bin/env python3
"""Valida interpolación y restricciones con Compose real, sin arrancar contenedores."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def check(binary=None):
    executable = [str(binary)] if binary else ['docker', 'compose']
    environment = {k: v for k, v in os.environ.items()
                   if k not in ('LAN_IP', 'RELEASE_ID', 'RUNTIME_IMAGE') and not k.startswith('COMPOSE_')}
    with tempfile.TemporaryDirectory(prefix='mclan-compose-') as directory:
        root = Path(directory)
        (root / 'data').mkdir()
        (root / 'releases' / ('a'*20)).mkdir(parents=True)
        (root / '.env').write_text('LAN_IP=192.168.1.50\nRELEASE_ID=' + 'a'*20 + '\nRUNTIME_IMAGE=mclan-runtime:test\n')
        shutil.copyfile(ROOT / 'docker/compose.template.yaml', root / 'compose.yaml')
        prefix = [*executable, '--project-directory', str(root), '--env-file', str(root / '.env'),
                  '-f', str(root / 'compose.yaml')]
        def render(extra):
            result = subprocess.run([*prefix, *extra, 'config', '--format', 'json'],
                                    env=environment, capture_output=True, text=True, check=True, timeout=30)
            return json.loads(result.stdout)['services']['minecraft']
        service = render([])
        assert service['platform'] == 'linux/arm64'
        assert service['restart'] == 'unless-stopped'
        assert int(service['mem_limit']) == 3*1024**3
        assert int(service['memswap_limit']) == 3*1024**3
        assert service['environment']['JAVA_XMX'] == '2048M'
        assert service['user'] == '10000:10000'
        assert service['cap_drop'] == ['ALL']
        ports = {(p['target'], p['protocol'], str(p['published']), p['host_ip']) for p in service['ports']}
        assert ports == {(25565, 'tcp', '25565', '192.168.1.50'), (19132, 'udp', '19132', '192.168.1.50')}
        volumes = {v['target']: v for v in service['volumes']}
        assert set(volumes) == {'/data', '/opt/release'}
        assert volumes['/opt/release']['read_only'] is True
        assert service['healthcheck']['test'] == ['CMD', 'python3', '/opt/mclan/probe.py', 'health']
        override = root / 'transaction.override.yaml'
        override.write_text(json.dumps({'services': {'minecraft': {'restart': 'no'}}}))
        assert render(['-f', str(override)])['restart'] == 'no'
    print('Compose: plantilla normal y override transaccional válidos; ningún contenedor arrancado.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--standalone', type=Path, help='Ruta a un binario oficial docker-compose para validar sin Engine')
    args = parser.parse_args()
    check(args.standalone)
