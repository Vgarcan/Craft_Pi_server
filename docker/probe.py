#!/usr/bin/env python3
"""Sondas reales sin librerías externas: Java Status, RakNet y RCON local."""
import json
from pathlib import Path
import secrets
import socket
import struct
import sys
import time

MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")
MAX_PACKET = 2 * 1024 * 1024


def exact(sock, size):
    if not 0 <= size <= MAX_PACKET:
        raise ValueError("Tamaño de paquete inválido")
    result = bytearray()
    while len(result) < size:
        part = sock.recv(size - len(result))
        if not part:
            raise ValueError("Conexión cerrada antes del paquete completo")
        result.extend(part)
    return bytes(result)


def varint(value):
    value &= 0xffffffff
    out = bytearray()
    while True:
        byte = value & 0x7f
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def read_varint(sock):
    value = 0
    for shift in range(0, 35, 7):
        byte = exact(sock, 1)[0]
        value |= (byte & 0x7f) << shift
        if not byte & 0x80:
            return value
    raise ValueError("VarInt demasiado largo")


def java_status(host, port=25565):
    encoded = host.encode()
    handshake = b'\x00' + varint(-1) + varint(len(encoded)) + encoded + struct.pack('>H', port) + b'\x01'
    with socket.create_connection((host, port), timeout=4) as sock:
        sock.sendall(varint(len(handshake)) + handshake + b'\x01\x00')
        length = read_varint(sock)
        if not 2 <= length <= MAX_PACKET:
            raise ValueError("Respuesta Java inválida")
        # Leer un paquete completo evita aceptar bytes de otra respuesta.
        import io
        class Reader:
            def __init__(self, payload):
                self.stream = io.BytesIO(payload)
            def recv(self, size):
                return self.stream.read(size)
        reader = Reader(exact(sock, length))
        if read_varint(reader) != 0:
            raise ValueError("No es Java Status")
        result = json.loads(exact(reader, read_varint(reader)))
        if not isinstance(result.get("version", {}).get("protocol"), int):
            raise ValueError("Java Status sin protocolo")
        return result


def bedrock_status(host, port=19132):
    stamp = struct.pack('>q', time.time_ns() // 1_000_000)
    packet = b'\x01' + stamp + MAGIC + secrets.token_bytes(8)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(4)
        sock.connect((host, port))
        sock.send(packet)
        pong = sock.recv(65535)
    if len(pong) < 35 or pong[0] != 0x1c or pong[1:9] != stamp or pong[17:33] != MAGIC:
        raise ValueError("No se recibió un Unconnected Pong de RakNet válido")
    size = struct.unpack('>H', pong[33:35])[0]
    payload = pong[35:]
    if len(payload) != size or not payload.startswith(b'MCPE;'):
        raise ValueError("Pong Bedrock sin anuncio MCPE válido")
    fields = payload.decode().split(';')
    return {"edition": fields[0], "protocol": int(fields[2]), "version": fields[3]}


def rcon(command, properties=Path('/data/server.properties')):
    """La contraseña se lee dentro del contenedor; no viaja en argumentos ni logs."""
    settings = {}
    for line in properties.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            settings[key.strip()] = value.strip()
    with socket.create_connection(('127.0.0.1', int(settings['rcon.port'])), timeout=4) as sock:
        def send(identifier, kind, text):
            body = struct.pack('<ii', identifier, kind) + text.encode() + b'\0\0'
            sock.sendall(struct.pack('<i', len(body)) + body)
        def receive():
            size = struct.unpack('<i', exact(sock, 4))[0]
            if not 10 <= size <= MAX_PACKET:
                raise ValueError('Paquete RCON inválido')
            payload = exact(sock, size)
            identifier, kind = struct.unpack('<ii', payload[:8])
            return identifier, kind, payload[8:-2].decode(errors='replace')
        send(1, 3, settings['rcon.password'])
        for _ in range(3):
            identifier, kind, _ = receive()
            if identifier == -1:
                raise ValueError('Autenticación RCON rechazada')
            if identifier == 1 and kind == 2:
                break
        else:
            raise ValueError('Sin confirmación de autenticación RCON')
        send(2, 2, command)
        # Las respuestas largas (p. ej. «data get entity») llegan en varios paquetes de 4096 bytes.
        # Un paquete de tipo desconocido detrás del comando marca el final: el servidor lo contesta
        # («Unknown request») sólo después de enviar todos los trozos de la respuesta.
        send(3, 0, '')
        parts, total = [], 0
        while True:
            identifier, _, payload = receive()
            if identifier == 3:
                return ''.join(parts)
            if identifier != 2:
                raise ValueError('Respuesta RCON inesperada')
            total += len(payload)
            if total > MAX_PACKET:
                raise ValueError('Respuesta RCON demasiado grande')
            parts.append(payload)


def geyser_dump(path=Path('/data/plugins/Geyser-Spigot/dump.json'), timeout=25):
    """Diagnóstico offline de Geyser. Se lee DENTRO del contenedor, como su usuario: el host (root)
    nunca abre este archivo, que el propio servidor podría sustituir por un enlace."""
    path.unlink(missing_ok=True)
    try:
        # OFFLINE es obligatorio: nunca se suben diagnósticos a dump.geysermc.org.
        rcon('geyser dump offline')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.is_file():
                try:
                    return json.loads(path.read_text()[:MAX_PACKET * 4])
                except json.JSONDecodeError:
                    pass
            time.sleep(1)
        raise ValueError('Geyser no creó un diagnóstico offline interpretable')
    finally:
        path.unlink(missing_ok=True)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'health'
    if mode == 'rcon':
        print(rcon(' '.join(sys.argv[2:])))
    elif mode == 'dump':
        print(json.dumps(geyser_dump()))
    else:
        host = sys.argv[2] if len(sys.argv) > 2 else '127.0.0.1'
        java = java_status(host)
        bedrock = bedrock_status(host)
        # RCON debe estar listo antes del diagnóstico efectivo de plugins.
        rcon('list')
        print(json.dumps({"java": java, "bedrock": bedrock}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, IndexError) as exc:
        print(f'Sonda fallida: {exc}', file=sys.stderr)
        sys.exit(1)
