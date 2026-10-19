# Estado del proyecto por fases

Registro de avance de Craft Pi Server. Cada fase separa dos niveles:

- **Implementado:** el código o la documentación existen y pasan las pruebas automatizadas.
- **Verificado en hardware:** se ha observado funcionando en la Raspberry Pi 5 de 4 GB, con la evidencia indicada.

Lo que necesita un jugador real, un dispositivo concreto o una fecha futura queda como **pendiente**, aunque esté implementado.

## Resumen

| Fase | Objetivo | Estado a 18-10-2026 |
| --- | --- | --- |
| 1 | Estabilizar la instalación actual | **Completada en hardware**, salvo dos cambios del host que dependen del operador |
| 2 | Validar el juego real con Java, Bedrock y QuestCraft | **Pendiente**: necesita clientes reales |
| 3 | Validar la administración | **Parcial**: ciclo de vida, backup, update y ensayo de restore verificados; faltan reinicio de la Pi y carga con jugadores |
| 4 | Administración web | **Panel propio implementado y verificado en hardware** (0.3.1, auditado); falta usarlo con jugadores reales |
| 5 | Mapa web | **BlueMap 5.29 integrado y verificado en hardware** (0.3.0); falta medir el render con un mundo explorado |

## Equipo de referencia

| Elemento | Valor observado |
| --- | --- |
| Placa | Raspberry Pi 5 Model B Rev 1.1, 4 GB (4049 MiB visibles) |
| Sistema | Debian 13 Trixie arm64, kernel 6.18.50+rpt-rpi-2712, **con escritorio** (`graphical.target`) |
| Almacenamiento | microSD, 117 GiB de sistema de archivos, 100 GiB libres |
| Docker | Engine 29.9.1, Compose 5.6.0 |
| Release | `3dc37ca886d1073d069a`: Minecraft 26.3, Paper 205, Geyser 2.12.0 b1265, Floodgate 2.2.5 b141, Java 25 |

## Fase 1 — Estabilización

### Fallos encontrados en la instalación 0.1.0

| # | Síntoma observado | Causa | Corrección (0.2.0) |
| --- | --- | --- | --- |
| 1 | Tras `stop` + `start`, el contenedor entra en bucle de reinicio: `cp: cannot create regular file '/data/plugins/Geyser-Spigot.jar': Permission denied` | El primer arranque copia los JAR del release (0444) con `umask 077`, así que quedan en 0400. En el siguiente arranque, `cp` no puede abrirlos para escribir | `entrypoint.sh` copia a un temporal y renombra con `mv -f` |
| 2 | `backup` y `update` fallan siempre: «No se ha confirmado el guardado de todas las dimensiones» | Paper 26.3 con Moonrise ya no escribe `All dimensions are saved` | Se acepta la secuencia `Saving worlds` → `Halted I/O scheduler for world` → `All RegionFile I/O tasks to complete`, en orden |
| 3 | Geyser avisa: «NetherNet support is NOT enabled… starting October 27th» | El instalador escribía `transport: raknet`. Bedrock 26.60 deja RakNet | `transport: both` mediante propiedades JVM, signaling en TCP 19132, WebRTC en UDP 19133 y la IP LAN como dirección anunciada |
| 4 | `docker stats` marca `0B / 0B`; Compose avisa de que el kernel no soporta límites de memoria | Raspberry Pi OS arranca con `cgroup_disable=memory`: el límite de 3 GiB no se aplica | Aviso en preflight y `status`, y procedimiento en el README. **El cambio del host queda pendiente del operador** |
| 5 | Tras un `git pull`, una instalación activa no podía adoptar código Docker nuevo | `update` sólo contemplaba builds nuevas, y `validate` exigía los archivos Docker originales | `update` detecta cambios del proyecto, reconstruye, hace backup y aplica la plantilla. `restore` acepta backups de plantillas anteriores |

Hallazgo de configuración, no de código: un jugador Bedrock fue rechazado con `You are not whitelisted on this server!` porque `white-list=true`. Hay que añadir a cada jugador (README, sección 12).

### Cambios conservados

No había cambios locales sin commit en el repositorio de `/opt`. `server.properties` conserva los valores del operador (`white-list=true`, `max-players=6`, `difficulty=normal`). `plugins/Geyser-Spigot/config.yml` no se ha modificado: las propiedades JVM prevalecen sobre su `transport: raknet`.

### Datos preservados antes de tocar nada

| Copia | Contenido | Verificación |
| --- | --- | --- |
| `/var/backups/craft-pi/pre-phase1-20261018T163957Z.tar.gz` | Tar en bruto de `data/`, `state/`, `config/`, `.env`, `compose.yaml`, `releases/` y `logs/`, con el servidor parado | SHA-256 en `.sha256` |
| `backups/minecraft-20261018T164557.974688Z-pre-update.tar.gz` | Snapshot gestionado antes de activar la 0.2.0 | Hashes por archivo verificados por `backup` |
| `backups/minecraft-20261018T164823.937591Z-manual.tar.gz` | Snapshot manual posterior | Ídem; usado en el ensayo de restore |

**Estas copias siguen en la misma microSD.** Hay que copiarlas fuera.

### Verificado en hardware (18-10-2026)

| Prueba | Resultado |
| --- | --- |
| `manage.sh update` de 0.1.0 a 0.2.0 con las mismas builds | Correcto: imagen `mclan-runtime:e0178d4a4303cad82e23`, backup `pre-update` verificado, plantilla sustituida, `healthy` |
| Geyser con NetherNet | Log: `Transport set from system property: both`, `Built-in signaling started on http://0.0.0.0:19132, with NetherNet on UDP port 19133`. El aviso NetherNet desaparece |
| Puertos publicados | Sólo en 192.168.2.120: 25565/tcp, 19132/udp, 19132/tcp y 19133/udp. 25575 (RCON) no aparece |
| Dos `restart` consecutivos (regresión del fallo 1) | Ambos verificados; `RestartCount=0` |
| `backup` con el servidor en marcha (regresión del fallo 2) | Parada limpia aceptada (exit 143 y secuencia Moonrise), copia verificada, rearranque verificado |
| Temperatura y throttling en reposo | 46–48 °C, `throttled=0x0` |
| Memoria en reposo, sin jugadores | Java ~1,0–1,25 GiB RSS; host con 1,4–1,9 GiB disponibles y 0,2–0,4 GiB de swap en uso (con escritorio) |

### Cambios del host aplicados con autorización del operador (18-10-2026)

| Cambio | Reversión | Estado |
| --- | --- | --- |
| `cgroup_enable=memory` añadido a `/boot/firmware/cmdline.txt` | Restaurar `/boot/firmware/cmdline.txt.pre-craftpi-20261018` | **Verificado tras reinicio**: `memory` en `cgroup.controllers`; contenedor recreado con `Memory=3221225472` y `MemorySwap=3221225472` |
| Arranque por defecto `multi-user.target` (sin escritorio) | `sudo systemctl set-default graphical.target` | **Verificado tras reinicio**: sin sesión gráfica |
| Whitelist: añadir al jugador Bedrock que intentó entrar | `manage.sh command "whitelist remove NOMBRE"` | **No aplicado.** Floodgate no encontró su XUID en la caché de GeyserMC. Abrir la whitelist temporalmente para añadirlo conectado quedó bloqueado por la política de permisos del asistente; lo decide el operador |

Auditoría previa al reinicio (`tools/audit.sh --verify`): dos FALLO esperados (cgroup de memoria y `Memory=0` en el contenedor); el resto OK.

### Reinicio de la Pi (18-10-2026, 18:25)

| Prueba | Resultado |
| --- | --- |
| Arranque automático tras `reboot` | **Verificado**: el servidor quedó `healthy` sin intervención. Java Status, RakNet y signaling TCP respondían en la IP LAN antes de usar `sudo` |
| Límite de memoria | El contenedor existente conservaba `Memory=0`, como se preveía. Tras `stop`, `compose rm` y `start` se aplica 3 GiB (`docker stats`: 981 MiB / 3 GiB) |
| `tools/audit.sh --verify` tras recrear | **Sin fallos**. Avisos: whitelist activa y vacía, y backups en la misma microSD |
| Recursos en reposo, sin jugadores | Java ~1,0 GiB; 1,57 GiB disponibles; swap 0; 52,7 °C; `throttled=0x0` |

La memoria disponible incluye la sesión de administración: VS Code Server por SSH, sus extensiones y el asistente ocupan del orden de 1 GiB. Con la sesión cerrada queda más margen. Mide la carga con jugadores sin esa sesión abierta, o descuéntala.

Tras el reinicio, `sudo` vuelve a pedir contraseña: es el comportamiento normal de la Pi, no un fallo.

### Pendiente de la fase 1

- Añadir jugadores a la whitelist (decisión del operador; ver README, sección 12).
- Reservar la IP de la Pi en el router (reserva DHCP).
- Copiar los backups fuera de la microSD.

## Fase 2 — Juego real (pendiente)

Nada de esta fase puede verificarse sin clientes. Anota versión, plataforma, resultado y fecha en esta tabla.

| Prueba | Estado | Evidencia a registrar |
| --- | --- | --- |
| Java 26.3 entra y juega | Pendiente | Versión del launcher, cuenta Java con licencia |
| Bedrock 26.30–26.52 entra y juega (RakNet) | **Verificado (18-10-2026)**: el jugador Bedrock se añadió con la apertura temporal del panel (su gamertag no estaba en la caché de GeyserMC), entró en el spawn y aparece en vivo en BlueMap. Identidad Floodgate `00000000-0000-0000-0009-…` | Plataforma, versión, `fwhitelist add` |
| Java y Bedrock en el mismo mundo a la vez | Pendiente | Se ven, interactúan y editan los mismos bloques |
| Inventarios separados Java/Bedrock | Esperado por diseño: Floodgate crea otra identidad (prefijo `.`) | Confirmar que no se mezclan |
| Persistencia tras `restart` | Pendiente | Bloque colocado, cofre e inventario conservados |
| Persistencia y arranque tras reiniciar la Pi | **Arranque verificado** (18-10-2026); persistencia de bloques e inventario pendiente de jugadores | `sudo reboot`, después `status` y `verify` sin intervención |
| Parada manual respetada tras reboot | Pendiente | Tras `stop` y `reboot`, el servidor sigue parado |
| Bedrock 26.60 por NetherNet | Pendiente de publicación (27-10-2026) y de una build de Geyser compatible | `update --plan`; confirmación *trust on first use* |
| QuestCraft | Pendiente. Ver [EVALUACION_WEB.md](EVALUACION_WEB.md), sección QuestCraft, para versiones compatibles | Versión de QuestCraft, versión Java que lanza, resultado |

## Fase 3 — Administración

| Operación | Estado |
| --- | --- |
| `start`, `stop`, `restart`, `status`, `logs`, `verify` | Verificado en hardware |
| `backup` | Verificado en hardware (fallo 2 corregido) |
| `update` de proyecto, mismas builds | Verificado en hardware |
| `update` con builds nuevas de Paper/Geyser | Implementado y probado con dobles; pendiente de que haya builds nuevas |
| `restore`, ensayo controlado | Verificado en copia aislada: archivo validado (222 archivos con hash), mundo arrancado en un contenedor sin red ni puertos (`Done` en 19,8 s, Java Status protocolo 777, RCON operativo) y parada limpia. El servidor de producción no se tocó |
| `restore` sobre la instalación activa | Pendiente. Ensáyalo cuando una pérdida de disponibilidad sea aceptable, con copia externa previa |
| `rebind` | Implementado; no ensayado en hardware |
| Memoria, CPU y temperatura con 2–4 jugadores | Pendiente: necesita la fase 2 |

Observación del ensayo de restore: sin red, Paper arranca, pero Geyser no obtiene datos de Microsoft/Mojang y se desactiva. Es lo esperado, y confirma que Bedrock necesita Internet en el arranque.

Cada snapshot ocupa unos 330 MB: incluye los JAR del release y las librerías de Paper. No hay rotación automática.

## Fases 4 y 5 — Panel web y mapa (0.3.0)

Decisión: en lugar de Crafty, que exigiría cederle el proceso de Paper (ver [EVALUACION_WEB.md](EVALUACION_WEB.md)), un **panel propio** sobre `manage.sh` y **BlueMap** como plugin. Uso: README, sección 15.

### Verificado en hardware (18-10-2026)

| Prueba | Resultado |
| --- | --- |
| `update` de 0.2.0 a release con BlueMap 5.29 | Correcto: backup `pre-update` verificado, imagen y plantilla nuevas, `verify` con BlueMap habilitado y web en 8100 |
| BlueMap en la Pi | Descarga del cliente 26.3 desde `piston-data.mojang.com`, tres mapas cargados, render inicial terminado en menos de 1 minuto (mundo recién creado). Contenedor 1,34 GiB de 3 GiB, 50–52 °C |
| Instalación del panel | Servicio `mclan-panel` activo con 14,5 MB de RAM, solo en `192.168.2.120:8443` |
| Alcance de sudo | `mclan-panel` ejecuta `python3 -I panel/helper.py`. Se rechazan `/bin/true` y el helper sin `-I`. El helper rechaza un nombre con `;` |
| HTTPS de punta a punta | Login fallido rechazado; login correcto. Estado, Whitelist, Ajustes, Logs y Auditoría responden entre 0,1 y 3,4 s. Un POST sin CSRF recibe 403 |
| Intentos rechazados | El jugador Bedrock rechazado esta tarde aparece con su botón «Permitir», leído de los logs rotados de Paper |

### Pendiente

- Cambiar la contraseña inicial del panel (Cuenta) y borrar así `/etc/mclan-panel/initial-password`.
- Añadir a los jugadores desde la página Whitelist. Para el jugador Bedrock sin XUID en caché: XUID o apertura temporal.
- Probar «Aplicar ajustes», «Backup» y la apertura temporal desde la web con jugadores reales. Están probadas con dobles, no en hardware.
- Medir BlueMap con un mundo explorado (CPU, temperatura, disco de `data/bluemap`).

## Auditoría de código y seguridad (18-10-2026, 0.3.1)

Dos revisiones de solo lectura (panel y núcleo), seguidas de correcciones y pruebas. Todas las de abajo están implementadas, cubiertas por pruebas automatizadas (96 en total) y desplegadas en la Pi. «Hardware» indica que además se comprobó en la Pi.

| Gravedad | Hallazgo | Corrección | Hardware |
| --- | --- | --- | --- |
| Crítica | `verify` leía, reescribía y hacía `chown` como root de `plugins/Geyser-Spigot/dump.json`, controlable por el contenedor (enlace → `/etc/shadow`) | El diagnóstico se lee y se borra dentro del contenedor (`probe.py dump`) | `update` y `verify` correctos |
| Alta | Un cliente TCP mudo bloqueaba todo el panel (handshake TLS en el bucle de aceptación) | TLS por conexión en su hilo, con timeout | Probado con TLS real en pruebas |
| Alta | `Content-Length` negativo: cuerpo sin límite en memoria | Cabecera estricta; `Transfer-Encoding` rechazado | — |
| Alta | El helper (root) seguía enlaces en `data/` al leer o escribir `server.properties`, `whitelist.json`, logs y `level.dat` | Abridor sin enlaces en ningún componente, sólo archivos regulares, topes de tamaño; escritura con `dir_fd` y `fchown` | Estado y whitelist correctos |
| Alta | `.dockerignore` excluía `bluemap-setup.sh`: el build habría fallado | Añadido, con prueba que cruza `COPY` y `.dockerignore` | Imagen construida |
| Alta | `restore` sin estado gestionado borraba un `data/` copiado a mano | Se conserva en `state/pre-restore-*` | — |
| Media | Carrera en el límite de login; PBKDF2 ilimitado | Intento reservado antes del hash; semáforo de 2 | — |
| Media | Sin límite de conexiones | 32 en total, 8 por IP | — |
| Media | El chat podía fabricar «intentos rechazados» | Patrones anclados al prefijo de log de Paper | Rechazo real detectado |
| Media | Apertura temporal: mezclaba Java/Bedrock y no expulsaba a otros | Edición explícita; expulsión al cerrar | — (pendiente con jugadores) |
| Media | `update` con el servidor parado retiraba la marca sin verificar | Arranca, verifica y vuelve a parar | — |
| Media | Backups «verificados» sin `fsync` | `fsync` del archivo y del directorio; también en escrituras atómicas | Backup `pre-update` correcto |
| Media | `restore` no validaba la IP ni la imagen base del backup | IP privada asignada al host y digest de Temurin validados | — |
| Media | Recorridos de `data/` incluían todas las teselas del mapa | `os.walk` podando las exclusiones | — |
| Media | Un fallo de la web de BlueMap tumbaba un update | Aviso, no fallo | — |
| Baja | Evidencia NetherNet perdida al rotar `docker logs` | Respaldo en `latest.log`, leído en el contenedor | — |
| Baja | `minecraft:stop` esquivaba el filtro de consola | Se compara sin espacio de nombres | — |
| Baja | IP LAN pública o de VPN aceptada | Sólo privadas | — |
| Baja | `audit.sh` abortaba sin `backups/` o `whitelist.json` | Tolerante | — |
| Baja | Sin `Host` ni `Origin` en el login; cookie sin `__Host-`; sesiones sin purga | Corregido | `Host` ajeno → 421 |
| Info | `manage.sh`/`install.sh` sin `python3 -I` | Añadido | `update` correcto |

Además, en hardware:

- **BlueMap negro de cerca:** sólo se generaban teselas lejanas, porque Paper/Moonrise no deja la luz en el formato que espera BlueMap. Con `ignore-missing-light-data` se generaron 144 teselas de detalle por mapa. El navegador conservaba además una posición sin terreno (x −635, z −5927); el spawn está en (−32, 64, 0).
- **Panel «no se muestra»:** se abría sin `https://`. Ahora `http://…:8443` responde con 301 a HTTPS.
- **Controles de consumo:** pausar y reanudar el render, y desactivar y activar BlueMap, probados en la Pi. Desactivado: JAR retirado, web parada y juego verificado.

### Pendientes de la auditoría (no corregidos)

- `validate()` comprueba enlaces en `data/` y después lee `server.properties` como root: queda una ventana TOCTOU con el servidor en marcha. Mitigación futura: leerlo dentro del contenedor.
- El override `restart: "no"` de las transacciones cambia el hash de configuración de Compose y puede recrear el contenedor en el siguiente `start` (sospechado, sin reproducir).
- Snapshot: el mundo se lee dos veces (hash y tar) y el archivo varias. Optimizable con hash en streaming y validación en una pasada.
- Propuestas operativas: rotación y exportación de backups a USB, backup programado con aviso a jugadores, `restore --dry-run`, menos ruido del healthcheck RCON en el log.
- La imagen instala `python3` por APT sin fijar versión (no es reproducible bit a bit).
- La contraseña inicial del panel apareció en una captura compartida: **cámbiala**.

## Migración de un mundo Bedrock (18-10-2026)

Migración tratada aparte, sobre copias, como se pidió al inicio del proyecto.

| Paso | Resultado |
| --- | --- |
| Origen | `.mcworld` de Bedrock 1.26.50 (15 MB, sin behavior ni resource packs) |
| Conversión | Chunker 1.21.0 CLI (`chunker-cli-1.21.0.jar`, SHA-256 verificado contra GitHub). Formato `JAVA_26_3`, ejecutado en un contenedor Temurin **sin red, sin capacidades y como usuario sin privilegios**. Extracción del zip con rechazo de rutas, enlaces y tamaños anómalos. 15 s |
| Pérdidas declaradas por Chunker | Entidades sin correspondencia en Java 26.3: animales y monstruos (gallina, llama, peces, zombi, creeper, esqueleto, guardián, delfín…), objetos en el suelo, orbes de experiencia, barcas, flechas, tridentes y **vagonetas con cofre (con su contenido)**. Bloques y cofres normales se conservan |
| Prueba aislada | Paper 26.3 de prueba sin red ni puertos: migración de carpetas de Paper, `Done` en 60 s, sin errores de chunks; `forceload` de 256 chunks sin errores; parada limpia |
| Sustitución | Parada, backup gestionado verificado y mundo anterior apartado en `/var/backups/craft-pi/world-pre-xemitest-*`. Mundo convertido con UID 10000 y permisos 700/600 |
| Spawn | Bedrock usa `SpawnY 32767` («buscar superficie») y Chunker lo dejó en y = −1. Se fijó con `setworldspawn 11 68 0`, la posición del jugador original |
| Verificación | `start` correcto (Java, Bedrock, NetherNet, BlueMap) |
| Mapa | Teselas antiguas borradas y render forzado: 1414 teselas de detalle en el mundo normal, unos 10 minutos con un hilo, pico de 59 °C, sin throttling, 94 MB |

**Luz (corregido después):** el mapa se veía casi negro. Chunker escribe los arrays `SkyLight` **todos a cero** (0 % de bytes no nulos, frente al 78 % de un mundo de Paper) y no añade `starlight.light_version`. Esto también afecta al juego: oscuridad y monstruos de día.

- Corrección: `tools/relight.py` carga una vez cada chunk existente dentro del contenedor y Paper/Moonrise recalcula la luz. Ensayado primero en una copia aislada: 166 s, del 0 % al 75–81 % de luz de cielo.
- Aplicado en producción tras un backup verificado (`backups/…184750…-manual.tar.gz`, mundo tal como se importó): 3 min 17 s, pico de 63 °C. Después, `bluemap purge` y render completo.
- Efecto secundario medido: forceload exige vecinos completos. Los chunks completos pasan de 3216 a 3974, un anillo de 1–2 chunks en el borde explorado generado con la misma semilla. Además hay unos 10.000 chunks a medio generar, invisibles. Es equivalente a acercarse al borde jugando.
- `save-all flush` superó el timeout de 4 s de la sonda RCON. El script ahora reintenta los comandos lentos y tolera ese caso.

**Pendiente:** el inventario del jugador Bedrock (6 objetos y su posición) quedó con UUID `00000000-0000-0000-0000-000000000000`. Se asignará a su UUID de Floodgate cuando entre por primera vez, con el servidor parado.

Lección para BlueMap: tras cambiar el mundo hay que borrar `data/bluemap/web/maps` y forzar `bluemap force-update` en cada mapa; si no, BlueMap considera los mapas al día.

## Pregeneración y borde del mundo (18-10-2026)

Objetivo: juego y mapa fluidos en la Pi. Generar terreno mientras se juega causa tirones, y el mapa quedaba incompleto.

- **Chunky 1.5.3** como plugin gestionado: resuelto en Hangar y fijado por SHA-256 en la release (`update` con backup `pre-update` verificado). En reposo no consume CPU.
- **Zona:** cuadrado centrado en (1024, 0) con radio 1600, es decir, de x −576 a 2624 y de z −1600 a 1600. Cubre toda la zona explorada del mundo importado (regiones x −1…4, z −3…2).
- **Borde del mundo:** `worldborder center 1024 0` y `worldborder set 3200`. La Pi no genera terreno fuera de la zona pregenerada. Para ampliar, pregenera un radio mayor con Chunky y sube el borde.
- **BlueMap:** ajuste único `craft-pi: tuning-1` con dos hilos de render y web con 250 bloques de detalle y 4000 de vista lejana. El navegador guarda la configuración en cookies: si sigue con los valores anteriores, usa *Settings → Reset* en el menú de BlueMap.
- Duración y temperaturas: ver la tabla siguiente, que se completa al terminar.

## Panel 0.4.0: rediseño en inglés y consola (18-10-2026)

- **Interfaz:** estilo Minecraft (barra de objetos como navegación con teclas 1–9, parallax de cielo, nubes y colinas, paneles de inventario, botones de piedra, barra de experiencia, corazones, avisos tipo logro), toda en inglés. Navegación sin recarga con mejora progresiva.
- **Texturas:** 49 texturas extraídas en la Pi del cliente 26.3 (dentro del contenedor) y validadas en el host. No están en el repositorio.
- **Consola:** logs en vivo filtrados y comandos de operador validados (`console`), con historial y autocompletado.
- **CSP endurecida:** sin `unsafe-inline`.
- **Verificación:** vista previa local con Chromium sin pantalla (escritorio 1440 px y móvil 412 px), sin violaciones de CSP ni errores de JavaScript. 107 pruebas automatizadas.
- **Pregeneración terminada:** 40.401 chunks en 25 min 04 s, a 16–24 chunks/s, con un máximo de unos 70 °C y sin throttling.

## Biblioteca de mundos (implementada, pendiente de hardware)

- Hasta 50 mundos y uno activo en los puertos de siempre, como decidió el operador.
- **Núcleo:** `scripts/mclan/worlds.py` y `manage.sh world …`. **Panel:** World → World library.
- **Pruebas automatizadas:** cambio de mundo con intercambio real de mundo, mapa y ajustes; mundo nuevo con semilla; marcha atrás si no arranca; importación con rechazos de seguridad; exportación verificada; borrado con archivo previo; restore de un backup de otro mundo sin perder el activo; rutas y descargas del panel. 124 pruebas en total.

### Verificado en hardware (18-10-2026, 0.6.0)

| Prueba | Resultado |
| --- | --- |
| Despliegue de 0.5.0 y 0.6.0 | Sin deriva de runtime (no hace falta `update`). Panel reinstalado con 2182 texturas; `imports/` del administrador; `exports/` legible por el grupo del panel |
| Auditoría funcional del panel | **21/21** con un panel temporal en 127.0.0.1:8444, como `mclan-panel`, con helper y sudo reales: login; las 7 páginas (0–1,5 s; Health 3,4 s porque ejecuta la auditoría); perfil de jugador; pulse; logs sin ruido; `list` por la consola; `stop` bloqueado; 403 sin CSRF y con Origin ajeno. Panel temporal retirado |
| Crear mundo con semilla | `audit-test`, semilla 424242 |
| Activar un mundo nuevo | **104 s**: backup `pre-switch-main` verificado, intercambio, generación y verificación de Java, Bedrock y NetherNet. `seed` = 424242; BlueMap renderizando el mundo nuevo |
| Volver al mundo principal | **77 s**. Xemitest intacto: 185 regiones, mapa conservado (12 597 archivos frente a 12 595), semilla y borde de 3200 recuperados, y su dificultad propia |
| Borrar un mundo | Archivado como zip con `.sha256` en `backups/worlds/` antes de borrar (1 s) |
| `tools/audit.sh --verify` | Sin fallos. Avisos: 755 MiB de swap en uso (sesión de VS Code y render de BlueMap) y backups en la misma microSD |

### Pendiente

- Importar desde `imports/` un mundo Java y otro Bedrock, y exportar a `.mcworld`, con archivos reales del usuario.
- Rotación de backups: cada cambio de mundo añade un backup `pre-switch` de unos 330 MB.

## Panel 0.6.1: subida web y avisos de tareas (19-10-2026)

**Implementado (con pruebas automatizadas, 127 en total):**

- **Subida desde el navegador** a `imports/` en streaming, con comprobación previa (`/api/upload-check`) de nombre, tamaño, espacio y bloqueo. La importación arranca al terminar la subida.
- **Avisos de tareas en segundo plano**, a petición del operador: las operaciones largas ya no navegan a una página de tarea.
  - Con JavaScript, el formulario se envía con `fetch` y el servidor responde 202 con el id de la tarea (409 si ya hay otra en marcha).
  - La bandeja de la cabecera consulta `GET /api/jobs` (cada 3 s con algo en marcha y cada 20 s si no). Avisa con un toast al terminar y lleva un contador de no leídas.
  - Al pulsar se abre un diálogo con el resultado (`GET /api/job/<id>`) y la tarea se marca como vista (`POST /api/job/<id>/seen`, con CSRF).
  - Sin JavaScript se mantiene la redirección a `/job/<id>`.
  - Los avisos salen abajo a la derecha, encima de la barra de objetos, y en móvil ocupan todo el ancho.
- **Verificado en la vista previa local** (Chromium vía CDP, escritorio y 390 px): sin navegación, aviso «started», bandeja ocupada, aviso «done!», contador a 1, diálogo con el resultado y contador a 0 al abrirlo.

**Skins de Bedrock (diagnóstico, sin cambio de código):**

- La API de GeyserMC tiene la skin correcta del jugador Bedrock (subida el 18-10-2026 a las 22:39).
- Floodgate muestra una skin Java por defecto hasta que la subida termina, lo que puede tardar minutos. La skin se aplica al volver a conectar.
- Pendiente: confirmar con el operador tras reconectar. Si persiste, activar el modo de depuración de Geyser.

### Pendiente

- Subida web de punta a punta en la Pi con un archivo real (implementada y probada con pruebas automatizadas; falta verificarla en hardware).

## 0.6.2: clientes Java de otras versiones (19-10-2026)

- **Problema:** QuestCraft (Java en Meta Quest) mostraba el servidor como incompatible («Paper 26.3» en rojo). El rechazo ocurre en el cliente tras el ping, así que el servidor no registra nada.
- **Cambio:** ViaVersion 5.12.1 y ViaBackwards 5.12.1 pasan a ser plugins gestionados, como BlueMap y Chunky. Se usa la última Release de Hangar que declara Paper 26.3, con SHA-256 fijado. El resolutor de Hangar pide ahora `channel=Release`, porque Via publica muchas snapshots que dejarían la Release fuera de la página. El servidor y el mundo siguen en 26.3.
- **Pruebas:** 128, entre ellas la elección de la Release frente a snapshots más nuevas.
- **Pendiente de hardware:** conexión real desde QuestCraft tras `manage.sh update`.

## 0.7.0: skins de Bedrock (19-10-2026)

- **Diagnóstico** (código de Geyser 2.12 y Floodgate 2.2.5, contrastado con los JAR instalados):
  - Floodgate aplica al jugador Bedrock la conversión Java de su skin (API de GeyserMC, textura plana de 64×64).
  - Desde Bedrock 1.21.130 el cliente toma todas las skins, también la propia, de la lista de jugadores. Cuando el perfil tiene una skin Java, Geyser manda esa y descarta la original. Por eso el bicho azul se veía «aplanado» incluso tras reiniciar.
  - Geyser no tiene ninguna opción para evitarlo. Desactivar `validate-bedrock-login` lo haría, pero rompe la autenticación, así que se descarta.
- **Cambio:** addon propio `addons/mclan-skins` (MclanSkins 1.0.0, Paper). Cancela el `SkinApplyEvent` de Floodgate, la vía oficial de su API:
  - Java ve la skin por defecto.
  - Geyser recurre a la skin Bedrock original para los clientes Bedrock.
  - Configurable con `java-skin: default|converted`.
- **Compilación:** `scripts/mclan/addons.py` lo compila en `stage()`, en un JDK Temurin fijado por digest (`jdk_base`) y sin red, contra las librerías de la release. El hash del código entra en la identidad de la release.
- **Límites:**
  - Las skins persona o de más de 128×128 no las conserva Geyser.
  - Los cambios de skin en mitad de la partida exigen reconectar. Para Java, la API sólo acepta datos firmados en el login; para Bedrock, Geyser no traduce `PlayerSkinPacket`.
- **Pruebas:** 132, entre ellas identidad por código, compilación sin red con montajes de sólo lectura y rechazo de enlaces y de código cambiado durante la actualización.
- **Depuración temporal:** `debug-mode: true` en la configuración de Geyser para ver el tipo de skin al conectar. Se retira después.
- **Pendiente de hardware:** compilación real en la Pi y comprobación visual en Bedrock (propia y de otros jugadores Bedrock) y en Java.

### MclanSkins 1.1.0: skins grandes (19-10-2026)

- **Verificado en hardware con la 1.0.0:** el plugin cargaba y Java dejaba de recibir la conversión. Pero el jugador seguía viendo una skin por defecto.
- **Causa** (registro de depuración de Geyser): «Unable to load bedrock skin … The size of '.Xeministrator' skin is: 256x256». Geyser no guarda en caché las skins de más de 128×128 ni las persona, así que su respaldo era la skin por defecto.
- **Cambio:** al iniciar sesión (`SessionLoginEvent`, antes de construir la lista de jugadores), el addon guarda esas skins (hasta 512×512) y su geometría con `SkinProvider.storeBedrockSkin`/`storeBedrockGeometry` de Geyser, por reflexión. Si no existen, avisa y no falla.
- **Modo `converted`:** usa `SessionSkinApplyEvent` para que Bedrock vea la original.
- **Compilación:** el addon se compila también contra `Geyser-Spigot.jar`. Un cambio incompatible en Geyser hace fallar `update` antes de parar el servidor.

### MclanSkins 1.2.0: skins persona completas (19-10-2026)

- **Verificado en hardware con la 1.1.0:** el cliente seguía mostrando Sunny. El respaldo de Geyser para ese UUID sería Efe, así que Geyser sí enviaba la skin y era el cliente quien la rechazaba.
- **Diagnóstico de la 1.1.1:** «256x256, persona=true, arm=slim, patch=…animated_face_persona…, geometry=75117 B, animatedImages=1, personaPieces=11, pieceTints=3, geometryEngine=1.14.0».
  - Es una skin del creador de personajes.
  - Geyser no conserva ni las piezas, ni los tintes, ni la animación de la cara, y además la marca como no persona.
- **Cambio:**
  - Al iniciar sesión, el addon construye la `SerializedSkin` completa a partir del JWT firmado del cliente, con `jose4j`, incluido en Geyser.
  - Sustituye la `UpstreamSession` de cada sesión por `SkinUpstream`. Esta cambia la skin de cada entrada de `PlayerListPacket` cuyo XUID corresponde a un jugador Bedrock.
  - La caché de Geyser (1.1.0) y `SessionSkinApplyEvent` ya no se usan.
- **Verificado en hardware (19-10-2026, 00:09):** con un jugador Bedrock y uno Java conectados a la vez:
  - En Bedrock se ve la skin persona completa del jugador Bedrock, con su forma.
  - En Java ese jugador aparece con Efe, la skin por defecto que corresponde a su UUID.
  - El registro muestra «Complete skin of Xeministrator: 256x256, persona=true, pieces=11, animations=1» en cada conexión y ningún error.
- **Java de la misma versión** (26.3, cuenta verificada) entra con ViaVersion instalado. Falta probar QuestCraft con una versión distinta.
- **Depuración de Geyser retirada:** `debug-mode: false`, idéntico a la copia previa. Se aplica en el siguiente reinicio.
- **Pendiente:** los cambios de skin en mitad de la partida siguen exigiendo reconectar.

## Panel 0.8.0: acciones de jugador (19-10-2026)

- **Petición del operador:** más acciones en el perfil (dar objetos y más) y una interfaz clara. Los mensajes escritos se permiten con límites.
- **Implementado:**
  - Pestañas Items, Effects, Teleport, Fun, Message y Admin, con 25 acciones validadas por el helper. Las listas cerradas están en `panel/catalog.py`.
  - API JSON `POST /api/players/<uuid>/action` y `GET /api/items`.
  - Catálogo de objetos e iconos de efectos extraídos del cliente.
  - Sin conexión, sólo Admin.
- **Pruebas:**
  - 136 automatizadas: validación de cada acción y de sus valores inválidos, comandos generados, escape de mensajes, mascota con el UUID como int-array, protección en la última muerte, API con CSRF y Origin, catálogo, instalador y perfil sin conexión.
  - Vista previa local en Chromium a 1280 y 390 px: pestañas, buscador, envío con aviso, contador, límite de nivel de encantamiento, pestaña recordada y sin desbordes.
- **Verificado en hardware (19-10-2026, 02:00):**
  - Panel desplegado. Catálogo de 1658 objetos con su nombre en inglés y 40 iconos de efectos extraídos. Todos los efectos, kits y objetos populares tienen icono y existen.
  - **Sintaxis en Paper 26.3:** todos los comandos se analizan bien, probados con un jugador inexistente; un objeto inexistente da «Unknown item» y el panel lo trata como fallo.
  - **Con el jugador Bedrock conectado**, por el mismo camino que la web (`mclan-panel` → sudo → helper), todo correcto:
    - mensaje sobre la barra y título con comillas y emoji;
    - fuegos artificiales («Summoned new Firework Rocket» ×3);
    - trueno (sonido y chispas);
    - 1 diamante;
    - visión nocturna;
    - lobo domesticado, con `Owner` = UUID del jugador como int-array.
  - **Rechazados por el helper:** `diamond;op` y Silk Touch III.
- **Pendiente:** que el operador pruebe las pestañas desde su navegador.

## 0.8.1: auditoría de la instalación (19-10-2026)

Antes de publicar los cambios se auditó si una instalación nueva reproduce todo lo hecho a mano durante el proyecto.

- **Comprobado:**
  - Un clon limpio pasa las pruebas y el MANIFEST.
  - `/opt` no tiene cambios ni archivos fuera de Git.
  - `install.sh --check` desde el clon limpio propone la misma release activa (`9aa39d2938ccf8308e8c`, con ViaVersion, ViaBackwards y MclanSkins).
  - La configuración inicial coincide con la actual.
- **Corregido:**
  - `panel/install.sh` espera a que BlueMap descargue el cliente (texturas y catálogo; `MCLAN_ASSETS_WAIT`, 600 s) y explica cómo repetir.
  - **Bug:** `manage.sh update` perdía `disabled_plugins`, y un BlueMap desactivado volvía tras actualizar.
  - MclanSkins se puede desactivar (`manage.sh plugin mclan_skins off`). Desactivado, si no compila no bloquea `update`; activo, `update` falla antes de parar el servidor y dice cómo seguir.
  - La verificación de instalación y actualización exige cada plugin de la release habilitado en Paper.
  - `tools/audit.sh` comprueba cada plugin, el enganche de MclanSkins, Geyser sin depuración y las texturas y el catálogo del panel.
- **Documentación:**
  - README reorganizado con un camino de instalación completo (ajustes de memoria antes de instalar, servidor, panel, primer jugador, pregeneración opcional y auditoría), capturas con datos de demostración y referencias corregidas.
  - La guía desde cero añade memoria, panel y primeros jugadores.
  - El MANIFEST queda documentado para descargas ZIP.
- **Verificado en hardware:** `update` con la verificación nueva («Verificación local correcta»), `panel/install.sh` y `tools/audit.sh` sin fallos.
- **Pendiente de hardware:** la espera de texturas sólo se ensayará en una instalación nueva, porque aquí el cliente ya estaba descargado.

## 0.8.2: segunda auditoría (19-10-2026)

Tres revisiones independientes, de sólo lectura (documentación frente a código, instalación desde cero y seguridad de los cambios recientes), más pruebas prácticas en la Pi.

- **Comprobado en la Pi:**
  - El ajuste de `cmdline.txt` del README es idempotente y deja una sola línea.
  - El ZIP de GitHub conserva los permisos de ejecución y pasa el MANIFEST.
  - `plugin mclan_skins off` retira el JAR, `on` lo devuelve y la auditoría queda sin fallos.
  - La espera de texturas de `panel/install.sh`, simulada con un Docker falso, es correcta en los cinco casos.
- **Seguridad (corregido):**
  - `panel/install.sh` corría como root dentro de `/etc/mclan-panel`, que era del usuario del panel, y seguía enlaces. Un panel comprometido podía preparar un enlace y conseguir root en la siguiente reinstalación.
    - Ahora la carpeta es de root, el panel sólo escribe en `data/`, y root escribe ahí con `panel/install_config.py` (descriptores de directorio, `O_NOFOLLOW`, `O_EXCL`).
    - Las texturas quedan de root.
    - Migración automática de la organización anterior.
  - `tools/audit.sh` lee `data/` con `mclan.safefs` (sin enlaces y con tope) y usa `python3 -I`.
  - MclanSkins limita lo que reenvía (512×512, 8 animaciones, 64 piezas, 16 tintes, 1 MiB de geometría, tamaños de imagen coherentes).
  - Los mensajes rechazan caracteres invisibles de formato (Cf).
  - Una acción sobre un nombre compartido por dos UUID se rechaza.
  - Entradas con NUL o surrogates no tumban el panel.
  - Coordenadas imposibles en los `.dat` no rompen el perfil ni el helper.
  - `addons/` debe ser de root para instalar el panel.
- **Instalación (corregido):**
  - **Whitelist:** una instalación nueva no la activaba, aunque la documentación decía que sí. Ahora `white-list=true` y `enforce-whitelist=true`, y la auditoría avisa si está apagada.
  - **Addons desactivados:** un addon desactivado no se compila ni necesita el JDK. Si MclanSkins no compila en la primera instalación: `install.sh --disable-plugin mclan_skins`. Al reactivar un addon que no está en la release, `update` lo compila.
  - **Compilación:** el contenedor tiene nombre y se elimina si se pasa de tiempo; usa `--release` con la Java de la release; los nombres de archivo deben empezar por letra o dígito.
  - **Auditoría:** ya no da FALLO con BlueMap desactivado.
  - **`MCLAN_ASSETS_WAIT`:** con un cero delante ya no rompe la instalación.
  - **Certificado:** se regenera si clave y certificado no casan, y el temporal de texturas se borra siempre.
  - **`restore`:** completa una release a medias en lugar de fallar.
- **Documentación (corregido):**
  - Whitelist.
  - Qué acciones funcionan sin conexión.
  - Formato de respuesta de cada ruta de la API y rutas que faltaban.
  - «Y vacía» sólo en el Overworld.
  - Cómo pasar `MCLAN_ASSETS_WAIT` con sudo.
  - `compose.yaml` editado bloquea todas las operaciones.
  - Apertura temporal de 1 a 15 minutos y subidas que necesitan JavaScript.
  - Ruta nueva de la contraseña inicial (`/etc/mclan-panel/data/initial-password`).
  - Se retira el paquete ZIP original de la guía.
- **Pruebas:** 145.
- **Verificado en hardware (19-10-2026, 13:00):**
  - `update` compila MclanSkins 1.2.1 y pasa la verificación, que ahora exige los siete plugins.
  - `panel/install.sh` migra la organización anterior: `/etc/mclan-panel` queda `root:mclan-panel 0750`, la clave `root:mclan-panel 0640`, las texturas de root y `data/panel.json` del panel. Se conservan la huella del certificado y la contraseña.
  - El panel escribe en `data/` pero no en la raíz, lee la clave y sirve login y texturas.
  - Ataque simulado: un enlace `data/panel.json → /etc/hostname` hace que `panel/install.sh` se niegue («no es un archivo normal») y `/etc/hostname` queda intacto. Después se restauró la configuración.
  - `tools/audit.sh --verify`: sin fallos.

## 0.9.0: inventario como en el juego y editor de casillas (19-10-2026)

- **Petición del operador:** que el inventario se vea como en el juego y poder editar los objetos («en la web es modo dios»).
- **Implementado:**
  - **Inventario:** ventana al estilo del juego, con armadura y siluetas, figura, mano secundaria, 27 casillas, barra rápida con la seleccionada, cofre de ender y efectos activos. Tooltip del juego, brillo de encantamiento y barra de durabilidad (`catalog.MAX_DURABILITY`).
  - **Datos en vivo:** una sola consulta `data get entity` leída con un lector SNBT acotado (`scripts/mclan/snbt.py`).
  - **RCON:** `probe.py` reúne las respuestas de más de 4096 bytes con un paquete marcador.
  - **Editor de casillas:**
    - acciones `slot-enchant` (cualquier encantamiento, 1–255), `slot-unenchant`, `slot-repair`, `slot-unbreakable`, `slot-rename`, `slot-count`, `slot-clear`, `slot-set` y `slot-duplicate`, más `effect-remove`;
    - todo validado con listas cerradas de casillas;
    - los nombres siguen las mismas reglas que los mensajes.
  - **Compatibilidad:** prueba nueva que exige f-strings compatibles con Python 3.11 (Bookworm).
- **Pruebas:**
  - 151 automatizadas.
  - Vista previa en Chromium con datos inventados, a 1280 y 390 px: maqueta, tooltip, editor en ventana, cierre y sin desbordes.
- **Verificado en hardware (19-10-2026, 14:30):**
  - **Modificadores:** en 26.3 el tipo de un modificador en línea va en `type`, no en `function` («No key type»). Corregido, todos los del editor se analizan bien, y uno inventado se rechaza.
  - **RCON por trozos:** una respuesta de 6075 bytes llega entera (antes se cortaba en 4096), probada con un almacén temporal ya borrado.
  - **Despliegue:** `update` con la sonda nueva, `panel/install.sh` con las 5 siluetas de casilla (2228 texturas) y `tools/audit.sh` sin fallos.
- **Pendiente de hardware:** el editor con un jugador conectado.

## 0.9.1: el editor sigue las reglas del juego (19-10-2026)

- **Petición del operador:** que el editor compruebe la lógica del juego y no permita cosas absurdas (nivel 9999999999).
- **Cambio:** se retira el «modo dios» de nivel 255. El helper lee en vivo el objeto de la casilla y rechaza con un mensaje claro:
  - un encantamiento que el objeto no admite (`catalog.allowed_enchantments`, por tipo de objeto; los libros admiten todos);
  - un nivel por encima del máximo normal;
  - un encantamiento incompatible con uno ya presente (`catalog.EXCLUSIVE`);
  - una cantidad mayor que el tope del objeto (`catalog.max_stack`: 1, 16 o 64), también al poner un objeto;
  - reparar o hacer irrompible algo sin durabilidad;
  - quitar encantamientos a algo que no tiene.
- **Editor:** sólo ofrece lo válido. Muestra los incompatibles desactivados con su motivo y los niveles hasta el máximo de cada encantamiento; si el objeto no se puede encantar o no se apila, lo dice.
- **Pruebas:** 151, entre ellas espada con Sharpness frente a Smite, Efficiency en espada, encantar tierra, dos espadas en una casilla, perlas por encima de 16 y reparar tierra.

## 0.10.0: paquetes de recursos para Java y Bedrock (19-10-2026)

- **Petición del operador:** ofrecer y gestionar mejoras visuales para Java y Bedrock de forma sencilla, compatible, segura y sostenible, empezando por Faithful. Decisiones: Java opcional; poder elegir versiones que se apliquen al elegirlas, pensando en VR (QuestCraft); Bare Bones sólo como enlace a su página oficial; puerto 8101. Estudio y alternativas en [ESTUDIO_RESOURCE_PACKS.md](ESTUDIO_RESOURCE_PACKS.md).
- **Hallazgo que cambió el diseño:** el paquete Faithful 26.3 sólo declara `min_format`/`max_format` y no lleva `pack_format`, que un cliente 1.21.5 como QuestCraft exige. Con el único paquete de `server.properties`, QuestCraft o el PC se quedaba sin uno legible. Por eso se descartó la vía nativa.
- **Implementado:**
  - **Catálogo cerrado** (`scripts/mclan/packs.py`): Faithful 32x con 17 variantes Java (1.19.2–26.3) y la edición Bedrock, con URL oficial, tamaño y hashes publicados. Bare Bones, como enlace.
  - **Addon MclanPacks 1.0.0** (`addons/mclan-packs`), compilado en la Pi como MclanSkins:
    - HTTP de sólo lectura en el puerto 8101, publicado sólo en la IP LAN;
    - una variante por versión de cliente, según ViaVersion;
    - Bedrock por la API de Geyser;
    - modo opcional u obligatorio;
    - `mclanpacks reload` y `mclanpacks status`, y registro de cargas y rechazos en el log.
  - **Archivos** en `packs/`, de root, fuera de `data/` y de los backups, montados de sólo lectura en `/opt/packs`.
  - **CLI** `manage.sh pack list|use|off|clean`. Tras cada cambio comprueba desde el host cada variante servida y su SHA-1.
  - **Panel:** World → Resource packs, con versiones, Bedrock, modo, aplicar, desactivar, actividad, autoría y licencia, y limpieza de descargas. Verbos cerrados del helper (`packs`, `pack-use`, `pack-off`, `pack-clean`).
  - **Instalación y auditoría:** plantilla Compose con el puerto y el montaje (la anterior queda como gestionada para `update`), preflight de 8101, `verify` y `tools/audit.sh` con la sección «Paquetes de recursos».
- **Pruebas:**
  - Automatizadas: catálogo, descargas manipuladas, selección, comprobación HTTP, CLI, helper y panel.
  - Vista previa en Chromium con datos inventados a 1280 y 390 px, sin desbordes.
  - Compilación del addon contra los JAR exactos de la release, sin avisos.
- **Verificado en hardware (19-10-2026, 17:20):**
  - `update` añade MclanPacks y el puerto 8101 con backup previo y verificación. Además pasa de Paper 205 a 206, publicado ese día.
  - `pack use faithful-32x --java 26.3 --java 1.21.5`: tres descargas verificadas, aplicadas sin reiniciar y comprobadas desde 8101 por SHA-1, en unos 7 s.
  - HTTP:
    - `application/zip` y tamaño exacto;
    - 404 en otras rutas, en los intentos de salir de la carpeta y en el `.mcpack`;
    - 405 con POST;
    - un cliente mudo se corta a los 32 s.
  - Cambios en caliente, selección corrupta (sin paquete y error en la CLI), Bare Bones rechazado con su enlace, y auditoría sin fallos.
- **Pendiente con clientes reales:**
  - Java 26.3: aviso con el crédito, aceptar y ver las texturas; rechazar en los modos opcional y obligatorio.
  - QuestCraft 1.21.5: que reciba y cargue su variante.
  - Bedrock: que reciba Faithful al conectar.
  - Rendimiento: tiempo de descarga en wifi y memoria con varios jugadores.
- **Fases siguientes:** un paquete por mundo (aplicado al activarlo en la biblioteca) y subida de paquetes propios con validación.
