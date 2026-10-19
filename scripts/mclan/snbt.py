"""Lector acotado de SNBT, el texto que devuelve /data get (p. ej. «Steve has the following entity data: {...}»).

Devuelve las mismas estructuras que el lector NBT binario (mclan.nbt): dict, list, int, float y str, de
modo que el mismo código lee el .dat guardado o los datos en vivo. Los textos vienen del juego y, en
parte, del jugador (nombres de objetos): se limitan tamaño, profundidad y número de elementos.
"""
import re

MAX_TEXT = 4 * 1024 * 1024
MAX_DEPTH = 64
MAX_ITEMS = 200_000
BARE = re.compile(r'[A-Za-z0-9._+\-]+')
INTEGER = re.compile(r'([+-]?\d+)([bBsSlL]?)')
FLOAT = re.compile(r'([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([fFdD]?)')
ESCAPES = {'\\': '\\', '"': '"', "'": "'", 'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f', 's': ' '}


class SnbtError(ValueError):
    pass


class Reader:
    def __init__(self, text):
        if len(text) > MAX_TEXT:
            raise SnbtError('SNBT demasiado grande')
        self.text, self.pos, self.items = text, 0, 0

    def skip(self):
        while self.pos < len(self.text) and self.text[self.pos] in ' \t\r\n':
            self.pos += 1

    def peek(self):
        self.skip()
        if self.pos >= len(self.text):
            raise SnbtError('SNBT incompleto')
        return self.text[self.pos]

    def expect(self, char):
        if self.peek() != char:
            raise SnbtError(f'Se esperaba {char!r} en la posición {self.pos}')
        self.pos += 1

    def count(self):
        self.items += 1
        if self.items > MAX_ITEMS:
            raise SnbtError('SNBT con demasiados elementos')

    def value(self, depth=0):
        if depth > MAX_DEPTH:
            raise SnbtError('SNBT demasiado profundo')
        char = self.peek()
        if char == '{':
            return self.compound(depth)
        if char == '[':
            return self.list(depth)
        if char in '"\'':
            return self.string()
        return self.literal()

    def compound(self, depth):
        self.expect('{')
        result = {}
        if self.peek() == '}':
            self.pos += 1
            return result
        while True:
            key = self.string() if self.peek() in '"\'' else self.bare()
            self.expect(':')
            result[key] = self.value(depth + 1)
            self.count()
            if self.peek() == ',':
                self.pos += 1
                continue
            self.expect('}')
            return result

    def list(self, depth):
        self.expect('[')
        self.skip()
        typed = re.match(r'([BIL])\s*;', self.text[self.pos:self.pos + 4])
        if typed:
            self.pos += typed.end()
        result = []
        if self.peek() == ']':
            self.pos += 1
            return result
        while True:
            item = self.literal() if typed else self.value(depth + 1)
            if typed and not isinstance(item, int):
                raise SnbtError('Array tipado con un valor no entero')
            result.append(item)
            self.count()
            if self.peek() == ',':
                self.pos += 1
                continue
            self.expect(']')
            return result

    def string(self):
        quote = self.text[self.pos]
        self.pos += 1
        out = []
        while True:
            if self.pos >= len(self.text):
                raise SnbtError('Texto sin cerrar')
            char = self.text[self.pos]
            self.pos += 1
            if char == quote:
                return ''.join(out)
            if char == '\\':
                code = self.text[self.pos:self.pos + 1]
                self.pos += 1
                if code in ESCAPES:
                    out.append(ESCAPES[code])
                elif code in ('x', 'u', 'U'):
                    width = {'x': 2, 'u': 4, 'U': 8}[code]
                    digits = self.text[self.pos:self.pos + width]
                    if not re.fullmatch(r'[0-9a-fA-F]+', digits or 'g') or len(digits) != width:
                        raise SnbtError('Escape inválido')
                    out.append(chr(int(digits, 16)))
                    self.pos += width
                else:
                    raise SnbtError('Escape inválido')
            else:
                out.append(char)

    def bare(self):
        self.skip()
        match = BARE.match(self.text, self.pos)
        if not match:
            raise SnbtError(f'Valor inesperado en la posición {self.pos}')
        self.pos = match.end()
        return match.group()

    def literal(self):
        word = self.bare()
        if match := INTEGER.fullmatch(word):
            return int(match[1])
        if match := FLOAT.fullmatch(word):
            return float(match[1])
        if word in ('true', 'false'):
            return int(word == 'true')
        return word


def loads(text):
    reader = Reader(text)
    result = reader.value()
    reader.skip()
    if reader.pos != len(reader.text):
        raise SnbtError('Datos sobrantes tras el SNBT')
    return result


def entity_data(reply):
    """Datos de la respuesta de «data get entity NOMBRE» (o None si no es un compuesto)."""
    start = reply.find('{')
    if start < 0 or 'has the following entity data' not in reply[:start]:
        return None
    data = loads(reply[start:].strip())
    return data if isinstance(data, dict) else None
