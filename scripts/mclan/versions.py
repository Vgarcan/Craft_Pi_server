"""Selección cerrada: versión Java publicada por Geyser + build STABLE de Paper."""
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import re
from datetime import datetime, timezone
from . import addons
from .common import Error

PAPER = "https://fill.papermc.io/v3/projects/paper"
GEYSER = "https://download.geysermc.org/v2/projects"
SUPPORTED = "https://geysermc.org/wiki/geyser/supported-versions/"
HANGAR = "https://hangar.papermc.io/api/v1/projects"
# Plugins resueltos en Hangar: clave del lock → proyecto. BlueMap (mapa web), Chunky
# (pregeneración de terreno; en reposo no consume CPU) y ViaVersion + ViaBackwards (dejan
# entrar a clientes Java de otras versiones, p. ej. QuestCraft en Meta Quest; el servidor
# y el mundo siguen en la versión que fija Geyser).
HANGAR_PLUGINS = {"bluemap": "Blue/BlueMap", "chunky": "pop4959/Chunky",
                  "viaversion": "ViaVersion/ViaVersion", "viabackwards": "ViaVersion/ViaBackwards"}
# Clave del lock → nombre del JAR en releases/<id>/ (y en data/plugins para los plugins).
# Una release antigua puede no incluir todos: usa managed_jars(lock).
JARS = {"paper": "paper.jar", "geyser": "Geyser-Spigot.jar", "floodgate": "floodgate-spigot.jar",
        "bluemap": "BlueMap.jar", "chunky": "Chunky.jar", "viaversion": "ViaVersion.jar",
        "viabackwards": "ViaBackwards.jar", **{key: jar for key, (_, jar) in addons.ADDONS.items()}}
# Nombres para mostrar de los plugins opcionales (resúmenes de update).
EXTRA_PLUGINS = (("bluemap", "BlueMap"), ("chunky", "Chunky"), ("viaversion", "ViaVersion"),
                 ("viabackwards", "ViaBackwards"), ("mclan_skins", "MclanSkins"))


def managed_jars(lock):
    return [(key, name) for key, name in JARS.items() if key in lock]


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def supported_versions(html):
    parser = Text()
    parser.feed(html)
    text = re.sub(r"\s+", " ", unescape(" ".join(parser.parts)))
    match = re.search(r"Geyser currently supports Minecraft Bedrock (.+?) and Minecraft Java ([0-9.]+)\s*\.", text)
    if not match:
        raise Error("No se puede interpretar la compatibilidad oficial de Geyser. "
                    "No se adivinan versiones: revisa la página y actualiza el parser.")
    return {"bedrock": match[1].strip(), "java": match[2].rstrip(".")}


def stable_build(builds):
    stable = [build for build in builds if build.get("channel") == "STABLE"]
    if not stable:
        raise Error("No hay build STABLE de Paper para la versión compatible.")
    return max(stable, key=lambda build: int(build["id"]))


def plugin(client, project):
    metadata = client.json(f"{GEYSER}/{project}/versions/latest/builds/latest")
    version, build = metadata["version"], int(metadata["build"])
    if not re.fullmatch(r"[0-9.]+(?:-SNAPSHOT)?", version):
        raise Error("Versión de plugin inesperada.")
    download = metadata["downloads"]["spigot"]
    return {"version": version, "build": build, "sha256": download["sha256"],
            "url": f"{GEYSER}/{project}/versions/{version}/builds/{build}/downloads/spigot"}


def bluemap(client, minecraft):
    return hangar(client, 'bluemap', minecraft)


def hangar(client, key, minecraft):
    """Última versión Release del plugin para Paper que declara soporte de esa versión exacta."""
    project = HANGAR_PLUGINS[key]
    # Filtrar por canal en la API: proyectos con muchas snapshots (Via) dejarían la última
    # Release fuera de la página. El filtro local se mantiene por si la API lo ignora.
    versions = client.json(f"{HANGAR}/{project}/versions?limit=25&offset=0&platform=PAPER&channel=Release")["result"]
    compatible = [v for v in versions if v["channel"]["name"] == "Release"
                  and minecraft in v["platformDependencies"].get("PAPER", [])]
    if not compatible:
        raise Error(f"No hay versión Release de {project} para Paper {minecraft}. "
                    "Espera a que se publique; no se instala una versión no declarada compatible.")
    chosen = max(compatible, key=lambda v: v["createdAt"])
    download = chosen["downloads"]["PAPER"]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", chosen["name"]) or not download.get("downloadUrl"):
        raise Error(f"Versión o descarga inesperada de {project}.")
    return {"version": chosen["name"], "sha256": download["fileInfo"]["sha256Hash"],
            "url": download["downloadUrl"]}


def resolve(client, requested="auto"):
    geyser = plugin(client, "geyser")
    html = client.fetch(SUPPORTED).decode("utf-8")
    supported = supported_versions(html)
    version = supported["java"] if requested == "auto" else requested
    if version != supported["java"]:
        raise Error(f"Geyser publicado requiere Java {supported['java']}; pedido {version}. "
                    "El servidor no se fuerza a otra versión ni se actualizan mundos implícitamente.")
    build = stable_build(client.json(f"{PAPER}/versions/{version}/builds"))
    metadata = client.json(f"{PAPER}/versions/{version}")
    java = int(metadata["version"]["java"]["version"]["minimum"])
    if java not in (21, 25):
        raise Error(f"Java {java} aún no está contemplado; revisa Dockerfile y pruebas.")
    floodgate = plugin(client, "floodgate")
    # Evita mezclar una publicación de Geyser ocurrida durante la resolución.
    if plugin(client, "geyser") != geyser:
        raise Error("Geyser cambió durante la resolución. Vuelve a intentarlo.")
    artifact = build["downloads"]["server:default"]
    lock = {"schema": 1, "minecraft": version, "java": java,
            "paper": {"build": int(build["id"]), "sha256": artifact["checksums"]["sha256"],
                      "url": artifact["url"]}, "geyser": geyser, "floodgate": floodgate,
            **{key: hangar(client, key, version) for key in HANGAR_PLUGINS},
            **addons.describe(),
            "compatibility": {**supported, "source": SUPPORTED,
                              "source_sha256": hashlib.sha256(html.encode()).hexdigest()}}
    # Cambios de presentación en la web no deben producir una release nueva.
    identity = {k: v for k, v in lock.items() if k != 'compatibility'}
    lock["id"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    lock["resolved_at"] = datetime.now(timezone.utc).isoformat()
    return lock
