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
