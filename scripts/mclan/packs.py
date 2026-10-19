"""Paquetes de recursos (texturas) para Java y Bedrock: catálogo cerrado, descargas fijadas y selección.

Los archivos viven en packs/, fuera de data/: son de root, no engordan los backups y el contenedor sólo
los ve en /opt/packs, de lectura. El addon MclanPacks lee packs/active.properties:
- sirve las variantes Java por HTTP en la IP LAN (puerto 8101);
- envía a cada jugador Java la variante de su versión, detectada con ViaVersion (QuestCraft en
  1.21.5 recibe la de 1.21.5 y un PC con la 26.3, la de 26.3);
- entrega la variante Bedrock por Geyser al conectar.
Cambiar la selección se aplica en caliente con «mclanpacks reload»: no reinicia el servidor.

Sólo se ofrecen paquetes cuya licencia permite servirlos. Los demás son enlaces a su página oficial.
"""
import hashlib
import io
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import zipfile

from .common import Error, LOG, atomic_write

PORT = 8101
LIMIT = 64 * 1024**2
SELECTION = 'active.properties'
LABEL = re.compile(r'[0-9]+(?:\.[0-9]+){1,2}')

# Faithful 32x: su licencia permite usarlo como paquete de un servidor citando a sus autores y
# enlazando la licencia (https://faithfulpack.net/license). Cada versión de Minecraft tiene su
# variante; las hashes son las que publica Modrinth (Java) y GitHub (Bedrock).
_MODRINTH = 'https://cdn.modrinth.com/data/w0TnApzs/versions/'
CATALOG = {
    'faithful-32x': {
        'name': 'Faithful 32x',
        'summary': 'Vanilla look at twice the resolution (32x32 textures).',
        'credit': 'Faithful Team',
        'homepage': 'https://faithfulpack.net/',
        'license': 'https://faithfulpack.net/license',
        # Texto del aviso de Java al ofrecer el paquete: también cumple con la atribución.
        'prompt': 'Faithful 32x by the Faithful Team. License: faithfulpack.net/license',
        'java': {
            '26.3': {'release': '26.3-september-2026', 'versions': ['26.3'], 'size': 13116019,
                     'url': _MODRINTH + 'lDYpMiqk/Faithful%2032x%20-%2026.3.zip',
                     'sha1': '0f20dfdff314da4da3bdf365f66718e8c344075b',
                     'sha512': 'e90ec63af7f239dbb99aa20c98b23d830e76898f65d3e31b7f0b53bb36db12dcd8c38d6930c743237c9c3db7bf708423fa1d1b2fd497dd6c281dbe216b779db9'},
            '26.2': {'release': '26.2-june-2026', 'versions': ['26.2'], 'size': 10450190,
                     'url': _MODRINTH + 'jtTqgLTy/Faithful%2032x%20-%2026.2.zip',
                     'sha1': '48dc52a6bbceab45fe385451372d7e1633eb3bf4',
                     'sha512': 'e6bbd9f881f6b1e44070f1f2b6c92c153b385594ccf0e6a57bc385e9cc1fafd3cf66e43af28c468edba57388255904c35bfc38b82c6aea04c2bab1a53843a919'},
            '26.1.2': {'release': '26.1-june-2026', 'versions': ['26.1', '26.1.1', '26.1.2'], 'size': 12013169,
                       'url': _MODRINTH + 'Fj5GKJnf/Faithful%2032x%20-%2026.1.zip',
                       'sha1': '9447e84ee7121c86304e467a0f8d5b095872e1e6',
                       'sha512': 'edc5ad465d302ecd0da3a64b2358b5b8fcbef673ca21ca560695b6716bf60805e041c643b7051f688322cdcbd03a538bed3a44f07f02298d18b88af3fa8f07cb'},
            '1.21.11': {'release': '1.21.11-june-2026', 'versions': ['1.21.11'], 'size': 10133843,
                        'url': _MODRINTH + 'WpkfQiw9/Faithful%2032x%20-%201.21.11.zip',
                        'sha1': 'f89622843bd3b1faa8ac18d9a6a948684ff2afc4',
                        'sha512': '6f1c2a5207cafb3f2754ec459f9321ab00db0f0c512d1b303bff1010bbd4f10ac976a0d0ebcbe32c606adb34bc41fd049a6ac54787f97032c4852c01602221d0'},
            '1.21.10': {'release': '1.21.10-june-2026', 'versions': ['1.21.9', '1.21.10'], 'size': 6029761,
                        'url': _MODRINTH + 'uepfQSIn/Faithful%2032x%20-%201.21.10.zip',
                        'sha1': '1e1cf22d77a9460cb0b725464215716abc1e0cdb',
                        'sha512': 'd834bf54e87dfd16036082efa014d9b7f7d9bed040531df92a323406153e2a51da9ea44ec4a63bdf24dfd7a4b4bcc822cd229acf5f2fbc8ca6fbe4705b6c2786'},
            '1.21.8': {'release': '1.21.8-june-2026', 'versions': ['1.21.6', '1.21.7', '1.21.8'], 'size': 12141048,
                       'url': _MODRINTH + 'jLMZKVAv/Faithful%2032x%20-%201.21.8.zip',
                       'sha1': '19317173340832fa6721e3d8a3a1370644d69dd1',
                       'sha512': '3350ddd2821e3192ffc6e108f37099bb69c46a471202e64522ccf07bbcc1ef47fedb75b3ad6c3d8d2c7f06d3363fbc11d999861c5302d51ed07655dacfd10763'},
            '1.21.5': {'release': '1.21.5-june-2026', 'versions': ['1.21.5'], 'size': 8387483,
                       'url': _MODRINTH + '1tjaScEA/Faithful%2032x%20-%201.21.5.zip',
                       'sha1': 'fbb00445c1d53b20447488bfbf008f026174e21b',
                       'sha512': 'c79a2edc37541ba56f28e181316bdbba1f93001fbfc22c7045ccf78992cf70cb16bf996d4d8b343f4cebe59f7153392fcf83bb0b3a57481a6497cbd23bcba629'},
            '1.21.4': {'release': '1.21.4-june-2026', 'versions': ['1.21.4'], 'size': 10486470,
                       'url': _MODRINTH + 'oHo9IUvX/Faithful%2032x%20-%201.21.4.zip',
                       'sha1': 'afd81fddc98287f7115ce017128811eafc56a5eb',
                       'sha512': 'ad8ff809c0a4c874ba0c738856c12becbb6aa23525cf210b14285362fa7777bb366dc74f6bc8f86d179827ed20d02a1bc2e748de074a58d60ea8cc0e5168ffaf'},
            '1.21.3': {'release': '1.21.3-june-2026', 'versions': ['1.21.2', '1.21.3'], 'size': 11138692,
                       'url': _MODRINTH + 'u18xQW6M/Faithful%2032x%20-%201.21.3.zip',
                       'sha1': '9fab88b50763b598b7260e7f9822f488ebedb99c',
                       'sha512': '7f9e23b03957a739aa1e3f4a59c32108666f21da2c8007ddc00d6ca775409cfbd75906859c640a2ad4dfa6d7ee8434f5dc276e8607c8a634a19f3f17a9730ec6'},
            '1.21.1': {'release': '1.21.1-june-2026', 'versions': ['1.21', '1.21.1'], 'size': 11030261,
                       'url': _MODRINTH + 'ruZOIEZE/Faithful%2032x%20-%201.21.1.zip',
                       'sha1': 'cf90d965473549d3d642b835071c6472d60bcf7b',
                       'sha512': '5ceaff9539f1440434a7255a5ede1118b00491f85db5c414fb4463872bd041a590e84275daa3bfaecd52772967f114d09c53dc97070e6d3959752f31d4a63885'},
            '1.20.6': {'release': '1.20.6-june-2026', 'versions': ['1.20.5', '1.20.6'], 'size': 11524123,
                       'url': _MODRINTH + 'ZYK1JOPV/Faithful%2032x%20-%201.20.6.zip',
                       'sha1': '75b905e454ef5fc97051b7613dad767f1c536f9d',
                       'sha512': '125e59952ef3b0c16a6f516b5964fdd60d6f05511b06ef8ced0d5b484b54096e951db853eb853fc1f804684941c07f2623e3047523d1b0f0a26e966a94327c58'},
            '1.20.4': {'release': '1.20.4-june-2026', 'versions': ['1.20.3', '1.20.4'], 'size': 11373737,
                       'url': _MODRINTH + 'ovorg9Y8/Faithful%2032x%20-%201.20.4.zip',
                       'sha1': 'fd19851ba62f75720ec943cac98ff8693fbb9bdd',
                       'sha512': '0a4b11c2d5eb066ff4dce21ae344aa3d9b2c2b606c46be55d0f2e94515d38798017448008bedef409c02ea6c8d504490720dc857c208f0b51ce3c68579a6c713'},
            '1.20.2': {'release': '1.20.2-june-2026', 'versions': ['1.20.2'], 'size': 11277975,
                       'url': _MODRINTH + 'R6wk8WZU/Faithful%2032x%20-%201.20.2.zip',
                       'sha1': '5d547b6226178b2fed879a0457d4803e0a65ce08',
                       'sha512': '10a7812e816c341ed69293f4db0aae030d7447aba927be6fe295caf8d1907914658ae97ee1afe1d134382a1c41d197674ab9e6c0b66bbc0b7faa58f9906644fd'},
            '1.20.1': {'release': '1.20.1-june-2026', 'versions': ['1.20', '1.20.1'], 'size': 11088963,
                       'url': _MODRINTH + 'VgTWEXF2/Faithful%2032x%20-%201.20.1.zip',
                       'sha1': '7084d82b48844f7c1b625e157463c4d7b87a82d3',
                       'sha512': '924fa489a4e5877541e28bd51ef1839fca887e7deab5857ca3d6c18dd831b684918556b28072516f29f22039374043e6de4eefa697bc3979d3c568d090fd5862'},
            '1.19.4': {'release': '1.19.4-june-2026', 'versions': ['1.19.4'], 'size': 13479048,
                       'url': _MODRINTH + 'eW50bj4j/Faithful%2032x%20-%201.19.4.zip',
                       'sha1': '234453601dc2fb9d74b7e8e973e7f271bf4669dc',
                       'sha512': '03de4b2155f803c0ca74d78ad5e6e74111969cff361765406c9f34a9a218d1be19187fb87a88722d331864c2081cece73856f68ce52b836a1c8bc25968cea8a1'},
            '1.19.3': {'release': '1.19.3-june-2026', 'versions': ['1.19.3'], 'size': 13295820,
                       'url': _MODRINTH + 'zhz3qdHS/Faithful%2032x%20-%201.19.3.zip',
                       'sha1': '75c95e0aef62c1e964039f25a9135fcebde564ce',
                       'sha512': 'f1b1bd22ea7bead80b6377f70c2e98ccdb0bf50eedce74af9d53e89ec430b7a5ba5603049bb2d0426ebcce21f678d8900d4017a2eec7ded88e92ad0d22c3ce8e'},
            '1.19.2': {'release': '1.19.2-june-2026', 'versions': ['1.19', '1.19.1', '1.19.2'], 'size': 13076492,
                       'url': _MODRINTH + 'pDX8zaU1/Faithful%2032x%20-%201.19.2.zip',
                       'sha1': 'a443a3515f099d0793bae8f522ef086b8e791e04',
                       'sha512': '924349ff774cc585291840bb67cf956a0f9641477d996350042ac0e32dd35b46804981519801a51475926ed629f52dc1b4ffe4716f60e262f37eb38a3b899031'},
        },
        'bedrock': {'release': 'september-2026-bedrock-hotfix', 'size': 19380937,
                    'url': 'https://github.com/Faithful-Resource-Pack/Faithful-32x-Bedrock/releases/download/'
                           'september-2026-bedrock-hotfix/Faithful.32x.September.2026.Hotfix.mcpack',
                    'sha256': '75fb2ff355a00d0d60a3deb4bbe42db9a5a65dd79ff9f03eb1b6edf9c57d1c26'},
    },
    # Licencia «All Rights Reserved»: su autor no permite resubirlo, así que el servidor no lo aloja.
    # Cada jugador lo instala desde su página oficial.
    'bare-bones': {
        'name': 'Bare Bones',
        'summary': 'Simplified textures in the style of the official Minecraft trailers.',
        'credit': 'RobotPants',
        'homepage': 'https://modrinth.com/resourcepack/bare-bones',
        'license': 'All Rights Reserved: install it yourself from its official page.',
        'link_only': True,
    },
}


def entry(pack_id):
    if pack_id not in CATALOG:
        raise Error(f'Paquete desconocido: {pack_id}. Consulta manage.sh pack list.')
    return CATALOG[pack_id]


def server_variant(pack, minecraft):
    """Etiqueta de la variante Java hecha para la versión del servidor (None si no hay)."""
    return next((label for label, v in pack.get('java', {}).items() if minecraft in v['versions']), None)


def java_name(variant):
    return f"{variant['sha1']}.zip"


def bedrock_name(variant):
    return f"{variant['sha256']}.mcpack"


def verify_payload(payload, variant, kind):
    """Hashes publicados por la fuente y estructura mínima del paquete (el servidor no lo extrae)."""
    if len(payload) != variant['size']:
        raise Error(f'Tamaño inesperado del paquete {kind} ({len(payload)} bytes).')
    for algorithm in ('sha1', 'sha256', 'sha512'):
        if algorithm in variant and hashlib.new(algorithm, payload).hexdigest() != variant[algorithm]:
            raise Error(f'El {algorithm.upper()} del paquete {kind} no coincide con el publicado.')
    marker = 'pack.mcmeta' if kind == 'java' else 'manifest.json'
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if marker not in archive.namelist():
                raise Error(f'El paquete {kind} no contiene {marker} en su raíz.')
    except zipfile.BadZipFile as exc:
        raise Error(f'El paquete {kind} no es un ZIP válido.') from exc


def read_selection(text):
    """Lee lo que escribió write_selection (claves simples, valores ASCII sin escapes)."""
    values = {}
    for line in text.splitlines():
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key] = value
    if values.get('schema') != '1':
        raise Error('packs/active.properties no tiene el formato esperado.')
    if not values.get('pack'):
        return None
    java = [values[f'java.{i}.label'] for i in range(int(values.get('java.count', '0')))]
    return {'pack': values['pack'], 'java': java, 'bedrock': 'bedrock.file' in values,
            'required': values.get('required') == 'true'}


def selection_text(pack_id, java, bedrock, required):
    """Formato java.util.Properties con valores ASCII imprimibles: Java y Python lo leen igual."""
    lines = ['# Gestionado por manage.sh pack: MclanPacks lo lee al arrancar y con mclanpacks reload.', 'schema=1']
    if pack_id is None:
        return '\n'.join(lines + ['pack=']) + '\n'
    pack = CATALOG[pack_id]
    lines += [f'pack={pack_id}', f"name={pack['name']}", f"prompt={pack['prompt']}",
              f"required={'true' if required else 'false'}", f'java.count={len(java)}']
    for index, label in enumerate(java):
        variant = pack['java'][label]
        lines += [f'java.{index}.label={label}', f"java.{index}.versions={','.join(variant['versions'])}",
                  f"java.{index}.sha1={variant['sha1']}", f'java.{index}.file=files/{java_name(variant)}']
    if bedrock:
        lines += [f"bedrock.release={pack['bedrock']['release']}", f"bedrock.file=files/{bedrock_name(pack['bedrock'])}"]
    text = '\n'.join(lines) + '\n'
    if any(not 32 <= ord(c) < 127 or c == '\\' for c in text.replace('\n', '')):
        raise Error('El catálogo contiene caracteres no admitidos en la selección.')
    return text


class Packs:
    def __init__(self, runtime):
        self.runtime, self.root = runtime, runtime.root
        self.dir = self.root / 'packs'
        self.files = self.dir / 'files'
        self.path = self.dir / SELECTION

    def ensure(self):
        """packs/ y packs/files/ de root y legibles por el contenedor (se montan en /opt/packs)."""
        for folder in (self.dir, self.files):
            if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
                raise Error(f'{folder.relative_to(self.root)} debe ser una carpeta real.')
            folder.mkdir(mode=0o755, exist_ok=True)
            folder.chmod(0o755)

    def selection(self):
        if not self.path.is_file() or self.path.is_symlink():
            return None
        return read_selection(self.path.read_text(encoding='utf-8'))

    def stored(self, name):
        path = self.files / name
        return path.is_file() and not path.is_symlink()

    def describe(self, minecraft):
        current = self.selection()
        packs = []
        for pack_id, pack in CATALOG.items():
            item = {k: pack[k] for k in ('name', 'summary', 'credit', 'homepage', 'license')}
            item.update(id=pack_id, link_only=bool(pack.get('link_only')),
                        active=bool(current and current['pack'] == pack_id))
            if not item['link_only']:
                chosen = current['java'] if item['active'] else []
                item['java'] = [{'label': label, 'versions': v['versions'], 'size': v['size'],
                                 'downloaded': self.stored(java_name(v)), 'enabled': label in chosen}
                                for label, v in pack['java'].items()]
                item['server_variant'] = server_variant(pack, minecraft)
                bedrock = pack.get('bedrock')
                item['bedrock'] = bedrock and {'release': bedrock['release'], 'size': bedrock['size'],
                                               'downloaded': self.stored(bedrock_name(bedrock)),
                                               'enabled': bool(item['active'] and current['bedrock'])}
                item['required'] = bool(item['active'] and current['required'])
            packs.append(item)
        return {'active': current, 'port': PORT, 'packs': packs}

    def download(self, client, variant, name, kind):
        target = self.files / name
        if self.stored(name):
            return target
        LOG.info('Descargando paquete %s %s (%s MB)...', kind, variant.get('release'), round(variant['size'] / 1024**2, 1))
        payload = client.fetch(variant['url'], limit=LIMIT)
        verify_payload(payload, variant, kind)
        atomic_write_bytes(target, payload)
        return target

    def use(self, pack_id, java, bedrock, required, client, minecraft):
        """Descarga lo que falte y escribe la selección. Devuelve las variantes elegidas."""
        pack = entry(pack_id)
        if pack.get('link_only'):
            raise Error(f"{pack['name']} no se puede alojar en el servidor ({pack['license']}) "
                        f"Página oficial: {pack['homepage']}")
        if java is None:
            default = server_variant(pack, minecraft)
            java = [default] if default else []
        java = list(dict.fromkeys(java))
        unknown = [label for label in java if label not in pack['java']]
        if unknown:
            raise Error(f"Variantes Java desconocidas: {', '.join(unknown)}. Disponibles: {', '.join(pack['java'])}.")
        if bedrock and not pack.get('bedrock'):
            raise Error(f"{pack['name']} no tiene variante Bedrock.")
        if not java and not bedrock:
            raise Error('Elige al menos una variante Java o la de Bedrock; para quitar el paquete usa manage.sh pack off.')
        self.ensure()
        for label in java:
            self.download(client, pack['java'][label], java_name(pack['java'][label]), 'java')
        if bedrock:
            self.download(client, pack['bedrock'], bedrock_name(pack['bedrock']), 'bedrock')
        atomic_write(self.path, selection_text(pack_id, java, bedrock, required), mode=0o644)
        LOG.info('Paquete activo: %s; Java %s; Bedrock %s; %s.', pack_id, ', '.join(java) or 'no',
                 'sí' if bedrock else 'no', 'obligatorio' if required else 'opcional')
        return {'pack': pack_id, 'java': java, 'bedrock': bedrock, 'required': required}

    def off(self):
        self.ensure()
        atomic_write(self.path, selection_text(None, [], False, False), mode=0o644)
        LOG.info('Paquete de recursos desactivado.')

    def clean(self):
        """Borra las descargas que no usa la selección actual. Devuelve los bytes liberados."""
        current = self.selection()
        keep = set()
        if current:
            pack = CATALOG.get(current['pack'], {})
            keep = {java_name(pack['java'][label]) for label in current['java'] if label in pack.get('java', {})}
            if current['bedrock'] and pack.get('bedrock'):
                keep.add(bedrock_name(pack['bedrock']))
        freed = 0
        for path in sorted(self.files.iterdir()) if self.files.is_dir() else []:
            if path.name not in keep and (path.is_file() or path.is_symlink()):
                freed += path.lstat().st_size
                path.unlink()
        return freed


def atomic_write_bytes(path, payload):
    """Como atomic_write, para binarios; queda 0644 (el contenedor lo lee)."""
    temporary = path.with_name(f'.{path.name}.part')
    temporary.unlink(missing_ok=True)
    try:
        with open(temporary, 'xb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def served(lan_ip, current, timeout=30):
    """Comprueba desde el host lo que reciben los clientes Java: cada variante por su URL y su SHA-1."""
    problems = []
    base = f'http://{lan_ip}:{PORT}/'
    try:
        urlopen(base, timeout=5).close()
    except HTTPError:
        pass  # 404 en la raíz: el servidor de paquetes responde
    except (URLError, OSError) as exc:
        return [f'El servidor de paquetes no responde en {base} ({exc}).']
    if not current:
        return problems
    pack = CATALOG.get(current['pack'], {})
    for label in current['java']:
        variant = pack['java'][label]
        try:
            with urlopen(base + java_name(variant), timeout=timeout) as response:
                digest = hashlib.sha1(response.read(LIMIT + 1)).hexdigest()
        except (URLError, OSError) as exc:
            problems.append(f'Java {label}: no se descarga de {base}{java_name(variant)} ({exc}).')
            continue
        if digest != variant['sha1']:
            problems.append(f'Java {label}: el archivo servido no coincide con su SHA-1.')
    return problems
