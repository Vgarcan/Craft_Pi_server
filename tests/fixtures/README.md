# Fixtures de contrato

Respuestas de lectura pública observadas el 18 de octubre de 2026. Se utilizan exclusivamente en pruebas sin red; el instalador no usa estas respuestas como fallback ni como versiones predeterminadas.

| Archivo | Fuente |
| --- | --- |
| `geyser_build.json` | `https://download.geysermc.org/v2/projects/geyser/versions/latest/builds/latest` |
| `floodgate.json` | `https://download.geysermc.org/v2/projects/floodgate/versions/latest/builds/latest` |
| `paper_version.json` | `https://fill.papermc.io/v3/projects/paper/versions/26.3` |
| `paper_builds.json` | `https://fill.papermc.io/v3/projects/paper/versions/26.3/builds`, muestra de dos builds |
| `hangar_bluemap.json` | `https://hangar.papermc.io/api/v1/projects/Blue/BlueMap/versions?limit=10&offset=0&platform=PAPER`, campos usados |
| `supported.html` | HTML mínimo para probar el texto de compatibilidad de `https://geysermc.org/wiki/geyser/supported-versions/` |

Los servidores ficticios de los tests usan un protocolo Java de ejemplo y no ejecutan Minecraft.
