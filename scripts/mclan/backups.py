"""Snapshots detenidos y restauración por staging; nunca extraer tar sobre el mundo activo."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
import uuid

from .common import Error, LOG, atomic_write, read_json, run, sha256, write_json
from .versions import JARS, managed_jars


# Teselas del mapa: regenerables y grandes. BlueMap las vuelve a renderizar tras un restore.
EXCLUDED = ('data/bluemap/web/maps',)


def excluded(relative):
    return any(relative == prefix or relative.startswith(prefix + '/') for prefix in EXCLUDED)


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')


def snapshot(runtime, label='manual'):
    """El llamador decide si debe volver a arrancar; aquí se exige cierre limpio."""
    runtime.assert_clean_stop()
    active = runtime.active()
    root = runtime.root
    backup_dir = root / 'backups'
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    paths = [root / 'data', root / 'config/settings.json', root / '.env',
             root / 'compose.yaml', root / 'state/active.json', root / 'releases' / active['id']]
    files = []
    directories = []
    for base in paths:
        if not base.exists():
            raise Error(f'Falta un componente del backup: {base}')
        for path in [base, *(sorted(base.rglob('*')) if base.is_dir() else [])]:
            if excluded(str(path.relative_to(root))):
                continue
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise Error('Backup cancelado: enlaces o archivos especiales en los datos.')
            if path.is_dir():
                directories.append(path)
            else:
                files.append(path)
    needed = sum(p.stat().st_size for p in files)
    if shutil.disk_usage(backup_dir).free < needed + 512*1024**2:
        raise Error('Falta espacio para un backup completo y 512 MiB de margen. No se borran copias antiguas.')
    target = backup_dir / f'minecraft-{stamp()}-{label}.tar.gz'
    temporary = target.with_suffix('.partial')
    manifest = {"schema": 1, "active": active['id'], "created_at": stamp(),
                "files": {str(p.relative_to(root)): {"size": p.stat().st_size, "sha256": sha256(p)} for p in files}}
    try:
        with tarfile.open(temporary, 'w:gz', compresslevel=3) as archive:
            for path in [*directories, *files]:
                archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
            import io
            payload = json.dumps(manifest, sort_keys=True).encode()
            info = tarfile.TarInfo('snapshot.json')
            info.size, info.mode = len(payload), 0o600
            archive.addfile(info, io.BytesIO(payload))
        # Lectura completa y hash de cada archivo antes de dar la copia por terminada.
        with tempfile.TemporaryDirectory(prefix='validate-', dir=runtime.state) as folder:
            # validate_archive puede comprobar sin duplicar el mundo en disco.
            validate_archive(temporary, Path(folder), extract=False)
        temporary.chmod(0o600)
        temporary.replace(target)
        atomic_write(target.with_name(target.name + '.sha256'), f'{sha256(target)}  {target.name}\n')
    finally:
        temporary.unlink(missing_ok=True)
    LOG.info('Backup verificado: %s', target)
    return target


def allowed(name, release_id):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name or str(p) != name.rstrip('/'):
        return False
    if p.parts[0] == 'data':
        return True
    return (name in ('.env', 'compose.yaml', 'config', 'config/settings.json', 'state', 'state/active.json', 'releases')
            or name == f'releases/{release_id}'
            or (len(p.parts) == 3 and p.parts[:2] == ('releases', release_id)
                and p.name in (*JARS.values(), 'versions.lock.json')))


def validate_archive(path, destination, *, extract=True):
    """Rechaza traversal, enlaces, dispositivos, duplicados y hashes/tamaños distintos."""
    with tarfile.open(path, 'r:gz') as archive:
        members = archive.getmembers()
        if len(members) > 500000 or len({m.name for m in members}) != len(members):
            raise Error('Backup demasiado grande o con nombres duplicados.')
        infos = [m for m in members if m.name == 'snapshot.json']
        if len(infos) != 1 or not infos[0].isfile() or infos[0].size > 64*1024**2:
            raise Error('Falta un manifiesto de backup válido.')
        manifest = json.load(archive.extractfile(infos[0]))
        release = manifest['active']
        if manifest.get('schema') != 1 or not isinstance(release, str) or not __import__('re').fullmatch('[a-f0-9]{20}', release):
            raise Error('Versión o release del backup inválida.')
        expected = manifest['files']
        actual_files = {m.name for m in members if m.isfile() and m.name != 'snapshot.json'}
        if set(expected) != actual_files:
            raise Error('El contenido del backup no coincide con el manifiesto.')
        total = 0
        for member in members:
            if member.name == 'snapshot.json':
                continue
            if not (member.isfile() or member.isdir()) or not allowed(member.name, release):
                raise Error(f'Entrada no admitida en backup: {member.name}')
            if member.isfile():
                if member.size < 0 or member.size != expected[member.name]['size']:
                    raise Error('Tamaño de archivo incorrecto en backup.')
                total += member.size
        if extract and shutil.disk_usage(destination).free < total + 512*1024**2:
            raise Error('Falta espacio para extraer el backup y 512 MiB de margen.')
        for member in members:
            if member.name == 'snapshot.json':
                continue
            target = destination / member.name
            if member.isdir():
                if extract:
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            digest = hashlib.sha256()
            stream = archive.extractfile(member)
            if extract:
                target.parent.mkdir(parents=True, exist_ok=True)
                output = target.open('xb')
            else:
                output = None
            try:
                while payload := stream.read(1024*1024):
                    digest.update(payload)
                    if output:
                        output.write(payload)
            finally:
                stream.close()
                if output:
                    output.close()
                    target.chmod(0o600)
            if digest.hexdigest() != expected[member.name]['sha256']:
                raise Error(f'Hash incorrecto en backup: {member.name}')
        return manifest


def restore(runtime, path, client, timeout, *, ip_override=None):
    """Confirmación ya solicitada por CLI; deja una marca si falla o se interrumpe."""
    path = path.resolve(strict=True)
    companion = path.with_name(path.name + '.sha256')
    if companion.exists() and companion.read_text().split()[0] != sha256(path):
        raise Error('El SHA-256 del archivo de backup no coincide.')
    staging = Path(tempfile.mkdtemp(prefix='restore-', dir=runtime.state))
    previous = runtime.state / f'pre-restore-{uuid.uuid4().hex}'
    swapped = False
    try:
        manifest = validate_archive(path, staging)
        active = read_json(staging / 'state/active.json')
        if active['id'] != manifest['active']:
            raise Error('El identificador activo no coincide con el manifiesto del backup.')
        # Se admiten plantillas gestionadas anteriores: al restaurar se activa la actual,
        # igual que tras update. Una plantilla editada a mano no se reconoce.
        try:
            runtime.template_drift(staging / 'compose.yaml')
        except Error as exc:
            raise Error('El backup contiene un compose.yaml no gestionado; revísalo antes de restaurar.') from exc
        if (runtime.root / 'compose.yaml').exists():
            try:
                runtime.template_drift()
            except Error as exc:
                raise Error('compose.yaml actual fue modificado; revisa sus diferencias antes de restaurar.') from exc
        if (staging / '.env').read_text() != runtime.env_text(active):
            raise Error('La configuración del backup no coincide con su release.')
        if active['id'] != read_json(staging / 'releases' / active['id'] / 'versions.lock.json')['id']:
            raise Error('Release incoherente en backup.')
        if ip_override is not None:
            from .host import lan_ip
            active['lan_ip'] = lan_ip(ip_override)
        # Si los archivos Docker cambiaron desde el backup, stage() construye la imagen actual
        # sobre la misma release fijada: se restauran datos y builds, no código antiguo.
        for key, filename in managed_jars(active):
            if sha256(staging / 'releases' / active['id'] / filename) != active[key]['sha256']:
                raise Error('Un JAR del backup no coincide con el lock.')
        # Preparar imagen/JARs antes de sustituir datos evita perder disponibilidad por red.
        release = runtime.root / 'releases' / active['id']
        release.parent.mkdir(parents=True, exist_ok=True)
        if release.exists():
            for p in (staging / 'releases' / active['id']).iterdir():
                if p.name != 'versions.lock.json' and sha256(release / p.name) != sha256(p):
                    raise Error('La release existente difiere del backup; no se sobrescribe.')
        else:
            shutil.copytree(staging / 'releases' / active['id'], release)
        active = runtime.stage(active, client, active['lan_ip'])
        recovery = (runtime.state / 'operation.json').exists()
        if runtime.active_path.exists():
            try:
                runtime.stop()
                runtime.assert_clean_stop()
            except Error:
                if runtime.running():
                    raise Error('El contenedor sigue funcionando; no se restaurarán datos sobre él.')
                recovery = True
                LOG.warning('No se confirmó el cierre anterior; se conservará íntegro como datos de recuperación.')
        else:
            existing = run(['docker', 'ps', '--quiet', '--filter', 'label=com.docker.compose.project=minecraft-lan'],
                           capture=True).stdout.strip()
            if existing:
                raise Error('Hay contenedores del proyecto sin metadatos locales. Detenlos y recupera su configuración primero.')
        # Si existe una marca de fallo, los datos pueden proceder de una actualización parcial.
        # Se preserva el directorio íntegro aunque no se presente como snapshot consistente.
        if runtime.active_path.exists() and not recovery:
            safety = snapshot(runtime, 'pre-restore')
            print(f'Copia de seguridad del estado anterior: {safety}')
        write_json(runtime.state / 'operation.json', {"operation": "restore", "backup": str(path),
                                                       "previous_data": str(previous)})
        if runtime.data.exists():
            runtime.data.rename(previous)
        (staging / 'data').rename(runtime.data)
        swapped = True
        for p in [runtime.data, *runtime.data.rglob('*')]:
            os.chown(p, 10000, 10000)
            p.chmod(0o700 if p.is_dir() else 0o600)
        restored_settings = read_json(staging / 'config/settings.json')
        if ip_override is not None:
            restored_settings['lan_ip'] = active['lan_ip']
        write_json(runtime.root / 'config/settings.json', restored_settings)
        atomic_write(runtime.root / 'compose.yaml', (runtime.root / 'docker/compose.template.yaml').read_text())
        runtime.activate(active)
        runtime.validate()
        try:
            runtime.start(timeout, pending=True)
            runtime.finish_transaction()
        except BaseException:
            write_json(runtime.state / 'operation.json', {"operation": "restore-failed", "previous_data": str(previous)})
            runtime.stop()
            raise
        # El borrado está cubierto por RESTAURAR y por el snapshot pre-restore verificado.
        # En recuperación de un fallo se conserva además la carpeta anterior para inspección.
        if previous.exists() and not recovery:
            shutil.rmtree(previous)
        elif previous.exists():
            print(f'Datos del intento anterior conservados para revisión: {previous}')
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if not swapped and previous.exists() and not runtime.data.exists():
            previous.rename(runtime.data)
