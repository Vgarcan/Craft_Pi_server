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
| 4 | Administración web | **Evaluación**; no integrado. Ver [EVALUACION_WEB.md](EVALUACION_WEB.md) |
| 5 | Mapa web | **Evaluación**; no integrado. Ver [EVALUACION_WEB.md](EVALUACION_WEB.md) |

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
| Bedrock 26.30–26.52 entra y juega (RakNet) | Pendiente. Hubo una conexión real desde la LAN, rechazada por whitelist: la red y la autenticación Bedrock llegaron a Paper | Plataforma, versión, `fwhitelist add` |
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

## Fases 4 y 5

Ver [EVALUACION_WEB.md](EVALUACION_WEB.md). No se ha instalado ningún panel ni mapa.
