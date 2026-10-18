"""Lectura mínima de ConstantValue de un .class; no ejecuta código descargado."""
import io
import struct
from .common import Error


def integer_constant(payload, wanted):
    """Lee un public static final int (formato JVM) sin necesitar Java en el host."""
    stream = io.BytesIO(payload)
    def read(size):
        value = stream.read(size)
        if len(value) != size:
            raise Error('Classfile truncado.')
        return value
    def u2():
        return struct.unpack('>H', read(2))[0]
    if read(4) != b'\xca\xfe\xba\xbe':
        raise Error('Classfile inválido.')
    read(4)  # minor + major
    count = u2()
    pool = [None] * count
    index = 1
    while index < count:
        tag = read(1)[0]
        if tag == 1:
            pool[index] = read(u2()).decode('utf-8', errors='replace')
        elif tag == 3:
            pool[index] = struct.unpack('>i', read(4))[0]
        elif tag in (4, 9, 10, 11, 12, 17, 18):
            read(4)
        elif tag in (5, 6):
            read(8)
            index += 1
        elif tag in (7, 8, 16, 19, 20):
            read(2)
        elif tag == 15:
            read(3)
        else:
            raise Error(f'Tag classfile no soportado: {tag}')
        index += 1
    read(6)  # access, this_class, super_class
    read(u2() * 2)  # interfaces
    for _ in range(u2()):
        u2()  # field access flags
        name, descriptor = pool[u2()], pool[u2()]
        for _ in range(u2()):
            attribute = pool[u2()]
            size = struct.unpack('>I', read(4))[0]
            value = read(size)
            if name == wanted and descriptor == 'I' and attribute == 'ConstantValue' and size == 2:
                result = pool[struct.unpack('>H', value)[0]]
                if type(result) is int:
                    return result
    raise Error(f'No se pudo leer la constante JVM {wanted}.')
