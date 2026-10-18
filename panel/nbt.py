"""Lector NBT (formato de Minecraft Java) de sólo lectura y con límites.

Los .dat de jugadores viven en data/, que controla el contenedor: el helper los lee como root, así que
se acota el tamaño descomprimido, la profundidad y la longitud de las listas, y cualquier anomalía es
un ValueError. No escribe nunca NBT.
"""
import gzip
import io
import struct

MAX_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 64
MAX_ITEMS = 200_000


class Reader:
    def __init__(self, data):
        self.data, self.position, self.items = data, 0, 0

    def take(self, size):
        if size < 0 or self.position + size > len(self.data):
            raise ValueError('NBT truncado')
        chunk = self.data[self.position:self.position + size]
        self.position += size
        return chunk

    def unpack(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self):
        return self.take(self.unpack('>H')).decode('utf-8', 'replace')

    def count(self):
        size = self.unpack('>i')
        self.items += max(size, 0)
        if size < 0 or self.items > MAX_ITEMS:
            raise ValueError('NBT con demasiados elementos')
        return size

    def payload(self, tag, depth):
        if depth > MAX_DEPTH:
            raise ValueError('NBT demasiado anidado')
        if tag == 1:
            return self.unpack('>b')
        if tag == 2:
            return self.unpack('>h')
        if tag == 3:
            return self.unpack('>i')
        if tag == 4:
            return self.unpack('>q')
        if tag == 5:
            return self.unpack('>f')
        if tag == 6:
            return self.unpack('>d')
        if tag == 7:
            return self.take(self.count())
        if tag == 8:
            return self.string()
        if tag == 9:
            inner, size = self.unpack('>b'), self.count()
            return [self.payload(inner, depth + 1) for _ in range(size)]
        if tag == 10:
            result = {}
            while (child := self.unpack('>b')) != 0:
                name = self.string()
                result[name] = self.payload(child, depth + 1)
            return result
        if tag == 11:
            size = self.count()
            return list(struct.unpack(f'>{size}i', self.take(4 * size)))
        if tag == 12:
            size = self.count()
            return list(struct.unpack(f'>{size}q', self.take(8 * size)))
        raise ValueError(f'Etiqueta NBT desconocida: {tag}')


def loads(raw):
    """NBT sin comprimir o en gzip → dict de la etiqueta raíz."""
    if raw[:2] == b'\x1f\x8b':
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('NBT demasiado grande')
    reader = Reader(raw)
    if reader.unpack('>b') != 10:
        raise ValueError('La raíz NBT no es un compuesto')
    reader.string()
    return reader.payload(10, 0)
