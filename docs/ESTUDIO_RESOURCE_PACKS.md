# Estudio: paquetes de recursos para Java y Bedrock

Estudio del 19-10-2026 y decisiones tomadas el mismo día. El resultado se implementó en la 0.10.0: ver la sección 8 y [ESTADO.md](ESTADO.md). Fuentes al final.

## 1. Conclusión corta

- **Viable y ligero.** Un paquete de recursos es trabajo del cliente: el servidor sólo guarda y entrega archivos.
- **Faithful 32x sí:**
  - Java: publica una variante para **cada versión** de Minecraft en Modrinth, también la **26.3** (13 MB) y la **1.21.5** (8 MB), que es la última versión que lanza QuestCraft.
  - Bedrock: tiene una **edición oficial** (`.mcpack` en GitHub, 19 MB).
  - Licencia: permite expresamente su uso como *server resource pack*, citando la autoría y enlazando la licencia.
- **Bare Bones no se aloja:**
  - Licencia: «All Rights Reserved». Su autor prohíbe resubirlo sin permiso, y servirlo desde la Pi es redistribuirlo.
  - Versión: la última publicada es para la **1.21.11**, no para la 26.x.
  - Se muestra como **enlace a su página oficial** para que cada jugador lo instale (decisión del operador).
- **Java y Bedrock necesitan archivos distintos.** Geyser no convierte paquetes Java. El panel los presenta como un único paquete con variantes.
- **Una sola variante Java no sirve para todos.** Se comprobó en la Pi que el paquete 26.3 declara sólo `min_format`/`max_format` (84–97) y no lleva `pack_format`. Un cliente 1.21.5 como QuestCraft exige `pack_format`, así que probablemente no puede ni leerlo. El paquete 1.21.5 declara `pack_format` 55 (47–55). Por eso cada cliente debe recibir la variante de su versión.

## 2. Cómo funciona en cada edición

| | Java (Paper) | Bedrock (Geyser) |
| --- | --- | --- |
| Formato | `.zip` de Java con el `pack_format` de su versión | `.mcpack` o `.zip` de Bedrock |
| Entrega | El cliente lo descarga de una **URL HTTP** que le envía el servidor | Geyser lo envía **dentro de la conexión** al entrar |
| Nativo | `server.properties` (`resource-pack`, `resource-pack-sha1`, `require-resource-pack`): **un único paquete para todos** | Carpeta `plugins/Geyser-Spigot/packs/`, leída **al arrancar** |
| Por API | `Player.addResourcePack(id, url, sha1, aviso, obligatorio)`: un paquete distinto por jugador y en caliente | `SessionLoadResourcePacksEvent`: un paquete por sesión, sin reiniciar |
| ¿Opcional u obligatorio? | Opcional (puede jugar sin él) u obligatorio (quien lo rechaza no entra) | En la práctica **obligatorio**: Geyser fuerza aceptar los paquetes mientras su paquete integrado esté activo (`force-resource-packs`, `enable-integrated-pack`) |
| Coste en la Pi | Servir un archivo de 8 a 13 MB a cada cliente nuevo; nada en juego | Geyser lo envía por la conexión de juego al entrar |

**URL de Java.** Tiene que ser HTTP accesible por los clientes. El panel usa HTTPS con certificado autofirmado, que el cliente Java rechazaría. Por eso los paquetes se sirven en **HTTP, sólo en la IP LAN, puerto 8101** (decisión del operador).

**Clientes concretos:**
- **Java 26.3:** recibe la variante 26.3.
- **QuestCraft (VR) y otros Java de otra versión (vía ViaVersion):** reciben la variante de su versión si está elegida. Si no hay ninguna, juegan sin paquete, sin avisos de incompatibilidad.
- **Bedrock:** recibe la edición Bedrock. Las consolas no permiten instalar paquetes a mano, así que la entrega por Geyser es la única vía para ellas.

## 3. Qué tipos de extensiones admite esta arquitectura

| Tipo | ¿Encaja? | Cómo |
| --- | --- | --- |
| Resource packs Java | **Sí** | MclanPacks: HTTP en la LAN y una variante por versión del cliente |
| Resource packs Bedrock | **Sí** | MclanPacks a través de la API de Geyser |
| Plugins de Paper | **Sí, ya existe** | Releases fijadas por SHA-256 (BlueMap, Chunky, Via…) y addons propios compilados (MclanSkins, MclanPacks). Un plugin nuevo debe pasar por ese sistema |
| Behavior packs y addons Bedrock | **No** | Necesitan un servidor Bedrock. Geyser traduce a un servidor Java y no ejecuta lógica Bedrock; lo parecido se hace con plugins o la API de Geyser (objetos y bloques propios) |
| Mods Java (Fabric, Forge, NeoForge) | **No** con esta arquitectura | Paper no carga mods. Habría que cambiar de servidor (Fabric con Geyser-Fabric, etc.), y los clientes Java necesitarían el mismo mod. Bedrock no puede usarlos. Ya se descartó antes |

Los paquetes de recursos no ejecutan código ni tocan el mundo: son el tipo de extensión más seguro.

## 4. Alternativas

| Opción | A favor | En contra | Veredicto |
| --- | --- | --- | --- |
| A. Nativo: `server.properties` (Java) + carpeta de Geyser (Bedrock) | Sin código propio | Un único paquete Java para todos (QuestCraft o el PC se queda sin uno legible); cada cambio reinicia el servidor; el `.mcpack` dentro de `data/` engorda cada backup en 19 MB | Descartada por la incompatibilidad de formatos |
| **B. Addon propio (MclanPacks) con las APIs de Paper, ViaVersion y Geyser** | Variante por versión de cliente; cambios en caliente sin reiniciar; archivos fuera de `data/`; se compila como MclanSkins, sin JAR de terceros | Código propio que mantener (unas 400 líneas) | **Elegida** |
| C. Plugin de terceros de gestión de paquetes | Funciones extra | Otro JAR de terceros que fijar y vigilar; no sabe de QuestCraft ni de Geyser | No |
| D. Sólo enlaces para que cada jugador lo instale | Cero carga y cero licencias que redistribuir | Consolas Bedrock no pueden; experiencia peor | Sólo para paquetes que no se pueden redistribuir (Bare Bones) |
| E. Convertir paquetes Java a Bedrock (Rainbow, Thunder) | Un solo origen | Conversión imperfecta y otra herramienta | Innecesario: Faithful ya publica su edición Bedrock |

## 5. Diseño (opción B)

- **Catálogo cerrado** (`scripts/mclan/packs.py`): Faithful 32x con 17 variantes Java (1.19.2–26.3) y la Bedrock. Cada una tiene URL oficial, tamaño y hashes publicados (SHA-1 y SHA-512 de Modrinth, SHA-256 de GitHub). Bare Bones está como enlace.
- **Datos** en `packs/`, de root y fuera de `data/`, así que no entran en los backups:
  - `packs/files/<sha1>.zip` y `packs/files/<sha256>.mcpack`;
  - `packs/active.properties`, la selección que lee el addon.
  - Se monta de sólo lectura en el contenedor (`/opt/packs`).
- **MclanPacks** (`addons/mclan-packs`), compilado en la Pi como MclanSkins:
  - Sirve **sólo** las variantes Java elegidas, por `http://IP_LAN:8101/<sha1>.zip`. Sólo admite GET y HEAD, sin listados ni otras rutas. Corta las peticiones lentas a los 30 s y admite como mucho 32 conexiones.
  - Al entrar un jugador Java, ViaVersion dice qué versión usa su cliente y recibe la variante de esa versión. Un cliente más nuevo que el servidor recibe la del servidor; uno sin variante juega sin paquete.
  - Registra la variante Bedrock en cada sesión de Geyser.
  - Con el modo obligatorio, expulsa a quien rechaza el paquete.
  - Escribe en el log de Paper cada carga, rechazo o fallo.
  - `mclanpacks reload` aplica la selección nueva en caliente; `mclanpacks status` resume el estado.
- **CLI** `manage.sh pack list|use|off|clean`: descarga y verifica, escribe la selección y recarga el addon. Después comprueba desde el host que cada variante se descarga de 8101 con su SHA-1.
- **Panel** (World → Resource packs):
  - elegir versiones Java, Bedrock y el modo opcional u obligatorio;
  - aplicar y desactivar;
  - autoría y licencia visibles;
  - actividad del addon (cargas, rechazos y errores);
  - limpiar las descargas sin uso.

## 6. Fases

| Fase | Contenido | Estado |
| --- | --- | --- |
| 1 | Núcleo, CLI y addon | Hecha y verificada en la Pi (0.10.0) |
| 2 | Sección del panel | Hecha (0.10.0) |
| 3 | Un paquete por mundo, aplicado al activar el mundo en la biblioteca | Pendiente |
| 4 | Subir paquetes propios desde el panel, con validación del ZIP y de su licencia | Pendiente |

## 7. Decisiones del operador (19-10-2026)

1. **Java opcional** por defecto, y que se puedan **elegir versiones** que actúen al elegirse, pensando en la compatibilidad con VR (QuestCraft). Resultado: variantes por versión de cliente, aplicadas en caliente.
2. **Bare Bones**: enlace a su página oficial.
3. **Puerto 8101** (HTTP, sólo LAN) para servir los paquetes Java.

## 8. Verificado en la Pi y pendiente

**Verificado en la Pi** (Raspberry Pi 5, 19-10-2026):
- `manage.sh update` añade MclanPacks y el puerto 8101 con backup previo y verificación.
- `pack use faithful-32x --java 26.3 --java 1.21.5`:
  - descarga las tres variantes con sus hashes;
  - el addon las aplica sin reiniciar;
  - el host las descarga de 8101 con el SHA-1 correcto (unos 7 s en total).
- HTTP:
  - `application/zip` con el tamaño exacto;
  - 404 en la raíz, en otras rutas, en los intentos de salir de la carpeta (`..`) y en el `.mcpack`;
  - 405 con POST;
  - un cliente que no envía nada se corta a los 32 s.
- Cambios en caliente (sólo VR, desactivar, volver), selección corrupta (el addon no ofrece nada y la CLI falla) y Bare Bones rechazado con su enlace.
- Auditoría: puerto 8101 sólo en la IP LAN, MclanPacks habilitado y paquete servido.

**Pendiente con clientes reales:**
- Java 26.3: que aparezca el aviso con el crédito, que acepte y se vean las texturas 32x; rechazarlo en modo opcional y en obligatorio.
- QuestCraft 1.21.5 (vía ViaBackwards): que reciba la variante 1.21.5 y la cargue.
- Bedrock: que reciba Faithful al conectar y se vean las texturas.
- Rendimiento: tiempo de descarga en wifi y memoria con varios jugadores.

## Fuentes

- [Geyser: resource packs](https://geysermc.org/wiki/geyser/packs/) (carpeta `packs`, sin conversión Java → Bedrock, sin behavior packs, API de paquetes por sesión).
- Código de Geyser 2.12, `GeyserConfig.java`: `force-resource-packs` y `enable-integrated-pack`, que fuerza el anterior.
- API comprobada con `javap` sobre los JAR de la release: Paper 26.3 (`Player.addResourcePack`, `PlayerResourcePackStatusEvent`), ViaVersion 5.12.1 (`getPlayerProtocolVersion`, `getIncludedVersions`), Geyser 2.12 (`SessionLoadResourcePacksEvent`, `PackCodec.path`). El servidor HTTP es `jdk.httpserver` de Temurin 25.
- [Paper: resource packs](https://docs.papermc.io/adventure/resource-pack/) (varios paquetes desde 1.20.3 por API).
- [Mojang MC-266920](https://bugs-legacy.mojang.com/browse/MC-266920) (comportamiento con `require-resource-pack`).
- [Licencia de Faithful](https://faithfulpack.net/license); [Faithful 32x en Modrinth](https://modrinth.com/resourcepack/faithful-32x) (API `v2/project/faithful-32x/version`: archivos, tamaños y hashes); [Faithful 32x Bedrock en GitHub](https://github.com/Faithful-Resource-Pack/Faithful-32x-Bedrock/releases) (el `digest` SHA-256 de la API coincide con el calculado).
- [Bare Bones en Modrinth](https://modrinth.com/resourcepack/bare-bones) (All Rights Reserved; última versión para la 1.21.11).
