"""Comprobaciones del host y aprovisionamiento selectivo de Docker mediante APT."""
import ipaddress
import os
from pathlib import Path
import platform
import re
import shutil
import socket

from .common import Error, LOG, atomic_write, run


def os_release(path=Path('/etc/os-release')):
    result = {}
    for line in path.read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            result[key] = value.strip().strip('"')
    return result


def lan_ip(requested='auto'):
    addresses = []
    if shutil.which('ip'):
        import json
        for interface in json.loads(run(['ip', '-j', '-4', 'addr', 'show', 'scope', 'global'], capture=True).stdout):
            if interface['ifname'].startswith(('docker', 'br-', 'veth', 'lo')):
                continue
            addresses += [a['local'] for a in interface.get('addr_info', []) if a['family'] == 'inet']
    if requested != 'auto':
        try:
            address = ipaddress.IPv4Address(requested)
        except ipaddress.AddressValueError as exc:
            raise Error('LAN_IP debe ser una IPv4 local.') from exc
        if (str(address) not in addresses or address.is_loopback or address.is_link_local
                or not address.is_private):
            raise Error('La IP indicada no está asignada a una interfaz LAN de este host.')
        return str(address)
    # Sólo direcciones privadas: una IP pública o de VPN publicaría el servidor fuera de la LAN.
    addresses = [a for a in addresses if ipaddress.IPv4Address(a).is_private]
    if len(addresses) != 1:
        raise Error(f'No se puede elegir una única IP LAN ({addresses}). Usa --lan-ip DIRECCION.')
    return addresses[0]


def preflight(root, requested='auto'):
    release = os_release()
    arch = run(['dpkg', '--print-architecture'], capture=True).stdout.strip()
    if platform.machine() not in ('aarch64', 'arm64') or arch != 'arm64':
        raise Error('Se requiere kernel ARM64 y sistema de usuario arm64 (OS Lite de 64 bits).')
    if release.get('ID') not in ('debian', 'raspbian') or release.get('VERSION_CODENAME') not in ('bookworm', 'trixie'):
        raise Error('Sólo Raspberry Pi OS/Debian 12 Bookworm o 13 Trixie ARM64 están contemplados.')
    if not Path('/run/systemd/system').exists():
        raise Error('Se requiere systemd en el host Raspberry Pi OS.')
    mem = dict((k, int(v.split()[0])) for k, v in
               (line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines()))
    if mem['MemTotal'] < 3500 * 1024:
        raise Error('No se detectan al menos 3500 MiB de RAM física; perfil previsto para una Pi de 4 GB.')
    if mem.get('MemAvailable', 0) < 2600 * 1024:
        LOG.warning('Hay menos de 2600 MiB disponibles. Comprueba otros servicios antes de jugar.')
    free = shutil.disk_usage(root).free
    docker_disk = Path('/var/lib/docker') if Path('/var/lib/docker').exists() else Path('/var/lib')
    if free < 8 * 1024**3 or shutil.disk_usage(docker_disk).free < 4 * 1024**3:
        raise Error('Se requieren 8 GiB libres para el proyecto y 4 GiB para Docker (pueden compartir disco).')
    ip = lan_ip(requested)
    LOG.info('Host: %s %s, %s, RAM %.0f MiB, disco libre %.1f GiB, LAN %s',
             release['ID'], release['VERSION_CODENAME'], arch, mem['MemTotal']/1024, free/1024**3, ip)
    if not memory_limits():
        LOG.warning('El kernel no habilita el cgroup de memoria: el límite de 3 GiB del contenedor no se aplicará. '
                    'Consulta la sección de memoria del README.')
    model = Path('/proc/device-tree/model')
    if model.exists():
        LOG.info('Modelo: %s', model.read_text().strip('\0'))
    return release, ip


def installed(package):
    result = run(['dpkg-query', '-W', '-f=${Status}', package], capture=True, check=False)
    return result.returncode == 0 and result.stdout.strip() == 'install ok installed'


def apt_install(packages):
    missing = [p for p in packages if not installed(p)]
    if missing:
        # APT no puede eliminar silenciosamente paquetes para resolver conflictos.
        run(['apt-get', 'install', '--no-remove', '-y', '--no-install-recommends', *missing])


def setup_docker(release, client):
    if os.environ.get('DOCKER_HOST') or os.environ.get('DOCKER_CONTEXT'):
        raise Error('Desactiva DOCKER_HOST/DOCKER_CONTEXT: sólo se administra el Docker local del sistema.')
    if not shutil.which('docker'):
        conflicts = [p for p in ('docker.io', 'podman-docker', 'containerd', 'runc') if installed(p)]
        if conflicts:
            raise Error(f'Paquetes existentes en conflicto: {conflicts}. No se eliminarán automáticamente.')
        run(['apt-get', 'update'])
        apt_install(['ca-certificates'])
        key = Path('/etc/apt/keyrings/minecraft-lan-docker.asc')
        key.parent.mkdir(parents=True, exist_ok=True)
        keydata = client.fetch('https://download.docker.com/linux/debian/gpg').decode()
        if 'BEGIN PGP PUBLIC KEY BLOCK' not in keydata:
            raise Error('Clave Docker inesperada.')
        if key.exists() and key.read_text() != keydata:
            raise Error('La clave Docker cambió; revisar manualmente antes de continuar.')
        if not key.exists():
            atomic_write(key, keydata, 0o644)
        source = Path('/etc/apt/sources.list.d/minecraft-lan-docker.sources')
        content = ('Types: deb\nURIs: https://download.docker.com/linux/debian\n'
                   f"Suites: {release['VERSION_CODENAME']}\nComponents: stable\n"
                   f'Architectures: arm64\nSigned-By: {key}\n')
        # No crear un segundo Signed-By para un repositorio ya administrado por otro instalador.
        others = [p for p in Path('/etc/apt/sources.list.d').glob('*')
                  if p != source and p.is_file() and 'download.docker.com' in p.read_text(errors='replace')]
        main_source = Path('/etc/apt/sources.list')
        if main_source.exists() and 'download.docker.com' in main_source.read_text():
            others.append(main_source)
        if not others:
            if source.exists() and source.read_text() != content:
                raise Error('El repositorio dedicado ya existe con otro contenido.')
            if not source.exists():
                atomic_write(source, content, 0o644)
        run(['apt-get', 'update'])
        apt_install(['docker-ce', 'docker-ce-cli', 'containerd.io'])
    endpoint = run(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], capture=True).stdout.strip()
    if endpoint != 'unix:///var/run/docker.sock':
        raise Error('El contexto Docker no apunta al socket local del sistema.')
    run(['systemctl', 'enable', '--now', 'docker'])
    detected = run(['docker', 'info', '--format', '{{.OSType}}/{{.Architecture}}'], capture=True).stdout.strip()
    if detected not in ('linux/aarch64', 'linux/arm64'):
        raise Error(f'Engine inesperado: {detected}')
    if run(['docker', 'compose', 'version'], capture=True, check=False).returncode:
        run(['apt-get', 'update'])
        # Conservar una instalación docker.io de Debian si existe.
        choices = ('docker-compose-v2', 'docker-compose-plugin') if installed('docker.io') else ('docker-compose-plugin', 'docker-compose-v2')
        for package in choices:
            policy = run(['apt-cache', 'policy', package], capture=True).stdout
            if re.search(r'Candidate: (?!\(none\))\S+', policy):
                apt_install([package])
                break
        else:
            raise Error('No hay paquete Compose v2 disponible; instala el plugin para tu Engine y reintenta.')
    run(['docker', 'compose', 'version'])
    if run(['docker', 'buildx', 'version'], capture=True, check=False).returncode:
        run(['apt-get', 'update'])
        choices = ('docker-buildx', 'docker-buildx-plugin') if installed('docker.io') else ('docker-buildx-plugin', 'docker-buildx')
        for package in choices:
            policy = run(['apt-cache', 'policy', package], capture=True).stdout
            if re.search(r'Candidate: (?!\(none\))\S+', policy):
                apt_install([package])
                break
        else:
            raise Error('Falta Buildx y no hay un paquete compatible disponible. No se modifica el Engine existente.')
    run(['docker', 'buildx', 'version'])


def memory_limits(controllers=Path('/sys/fs/cgroup/cgroup.controllers')):
    """Raspberry Pi OS arranca con cgroup_disable=memory: Docker ignora entonces mem_limit."""
    try:
        return 'memory' in controllers.read_text().split()
    except OSError:
        return False


def free_ports(ip):
    for kind, port in ((socket.SOCK_STREAM, 25565), (socket.SOCK_DGRAM, 19132),
                       (socket.SOCK_STREAM, 19132), (socket.SOCK_DGRAM, 19133), (socket.SOCK_STREAM, 8100)):
        with socket.socket(socket.AF_INET, kind) as sock:
            try:
                sock.bind((ip, port))
            except OSError as exc:
                raise Error(f'Puerto ocupado o IP ausente: {ip}:{port}') from exc
