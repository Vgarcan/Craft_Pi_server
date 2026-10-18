"""HTTPS con límites, reintentos y validación SHA-256 de descargas oficiales."""
import json
import re
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from http.client import IncompleteRead

from .common import Error, sha256

ALLOWED = {"fill.papermc.io", "fill-data.papermc.io", "download.geysermc.org",
           "geysermc.org", "download.docker.com", "hangar.papermc.io", "hangarcdn.papermc.io",
           # Chunker (conversor Bedrock ↔ Java): release de GitHub fijada por SHA-256.
           "github.com", "release-assets.githubusercontent.com"}


def valid_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise Error("La URL no pertenece a una fuente HTTPS autorizada.")


class Client:
    def __init__(self, contact):
        if not re.fullmatch(r"https://[^\s()]+|[^\s()@]+@[^\s()@]+\.[^\s()@]+", contact):
            raise Error("contact debe ser una URL HTTPS o email válido para Paper.")
        self.agent = f"Minecraft-LAN-Installer/1.0 ({contact})"

    def fetch(self, url, limit=4*1024*1024):
        valid_url(url)
        for attempt in range(3):
            try:
                with urlopen(Request(url, headers={"User-Agent": self.agent}), timeout=45) as response:
                    valid_url(response.url)
                    value = response.read(limit + 1)
                    if len(value) > limit:
                        raise Error("Respuesta remota demasiado grande.")
                    return value
            except (HTTPError, URLError, TimeoutError, ConnectionError, IncompleteRead) as exc:
                if isinstance(exc, HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                    raise Error(f"HTTP {exc.code}: {url}") from exc
                if attempt == 2:
                    raise Error(f"No se pudo acceder a {url}") from exc
                time.sleep(2**attempt)
        raise Error("Descarga incompleta.")

    def json(self, url):
        return json.loads(self.fetch(url))

    def download(self, artifact, destination):
        expected = artifact["sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise Error("SHA-256 remoto inválido.")
        if destination.exists():
            if sha256(destination) == expected:
                return
            raise Error(f"Hash incorrecto en archivo existente: {destination}")
        # El máximo limita tanto consumo de RAM como tamaño del JAR aceptado.
        payload = self.fetch(artifact["url"], limit=160*1024*1024)
        temporary = destination.with_suffix(".part")
        try:
            temporary.write_bytes(payload)
            if sha256(temporary) != expected:
                raise Error(f"SHA-256 no coincide: {destination.name}")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
