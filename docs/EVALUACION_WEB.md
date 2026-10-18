# Evaluación: administración web, mapa web y QuestCraft

Revisión documental del 18-10-2026, previa a cualquier integración. **No se ha instalado nada de lo que se describe aquí.** Las afirmaciones proceden de las fuentes enlazadas. Lo marcado como *sin verificar* debe comprobarse en la Pi antes de decidir.

## 1. Restricción de diseño

Debe haber **un único responsable del proceso de Paper**. Hoy ese responsable es `manage.sh`, que controla un contenedor con Paper, Geyser y Floodgate. Ningún panel ni herramienta puede arrancar, parar o modificar ese mismo mundo en paralelo.

## 2. Fase 4 — Crafty Controller 4

### Cómo funciona

- Crafty **lanza Java por sí mismo** (`subprocess.Popen`), escribe comandos por stdin y detiene el servidor con su comando de parada. No se engancha a procesos ni a contenedores externos, y no usa RCON para controlar el servidor ([server.py](https://gitlab.com/crafty-controller/crafty-4/-/raw/master/app/classes/shared/server.py)).
- Consecuencia: **Crafty no puede administrar el contenedor actual.** Para usarlo, Paper tendría que ejecutarse dentro del contenedor de Crafty.
- Imagen oficial `registry.gitlab.com/crafty-controller/crafty-4` (también `arcadiatechnology/crafty-4`), con arm64. La versión 4.11.0 se publicó el 16-09-2026 y ocupa unos 654 MB comprimidos. Su Dockerfile instala OpenJDK 8, 11, 17, 21 y 25 ([Dockerfile v4.11.0](https://gitlab.com/crafty-controller/crafty-4/-/raw/v4.11.0/Dockerfile), [instalación Docker](https://docs.craftycontrol.com/pages/getting-started/installation/docker/)).
- La importación de un servidor se hace desde un zip. Extrae a `/crafty/servers/<uuid>`, **borra el zip de origen**, cambia permisos (grupo root, setgid) y genera un comando de arranque con `java` sin ruta ([import_helper.py](https://gitlab.com/crafty-controller/crafty-4/-/raw/master/app/classes/shared/import_helper.py)). *Sin verificar:* si ese `java` es Java 21 en Ubuntu 24.04, Paper 26.3 no arrancaría hasta indicar la ruta de Java 25.
- Panel HTTPS en 8443 con certificado autofirmado y usuarios con roles, TOTP y passkeys. Las credenciales iniciales quedan en `app/config/default-creds.txt` ([acceso](https://docs.craftycontrol.com/pages/getting-started/access/)). Se puede limitar a la LAN publicando `IP_LAN:8443:8443`.
- Ofrece backups zip con retención, tareas programadas y actualización del JAR por URL. *Sin verificar:* que compruebe hashes ([backups](https://docs.craftycontrol.com/pages/user-guide/backup-manager/), [tareas](https://docs.craftycontrol.com/pages/user-guide/task-scheduler/)).
- Compatibilidad declarada: Debian hasta la 13 y Raspberry Pi OS hasta la 12 ([compatibilidad](https://docs.craftycontrol.com/pages/getting-started/compatibility/)). No hay cifras publicadas de consumo en ARM.

### Opciones

| Opción | Responsable de Paper | Ventajas | Inconvenientes |
| --- | --- | --- | --- |
| **A. Migrar a Crafty** | Crafty | Panel completo, backups y tareas ya hechos | Se pierden las releases fijadas con SHA-256, la verificación Java/Bedrock/NetherNet, el cierre comprobado y el aislamiento (panel y Paper en el mismo contenedor). `manage.sh` dejaría de gestionar el ciclo de vida. Hay que vigilar la RAM con 4 GB |
| **B. Panel ligero propio** | `manage.sh` | Conserva todas las garantías actuales. El panel sólo invoca `status`, `logs`, `start`, `stop`, `backup` y `command` | Requiere desarrollo: autenticación, HTTPS en la LAN y auditoría de comandos |
| **C. Ensayo de Crafty en una copia** | `manage.sh` en producción; Crafty sólo sobre una copia | Da datos reales (RAM, Java 25, Geyser, permisos) antes de elegir entre A y B | Necesita parar la producción durante el ensayo o usar otros puertos, y nunca el mismo directorio |

**Recomendación:** C primero, y después B salvo que el ensayo muestre que Crafty cabe holgadamente en 4 GB **y** se acepte que Crafty sustituya por completo el ciclo de vida de `manage.sh`. Lo que nunca debe hacerse es combinar A y B sobre el mismo mundo.

### Comprobaciones del ensayo C

1. `docker manifest inspect` de la etiqueta 4.11.0 en arm64.
2. `java -version` por defecto y ruta real de Java 25 en el contenedor.
3. UID/GID efectivos y permisos tras importar una **copia** del zip, porque el original se borra.
4. Que `server.properties`, la configuración de Geyser/Floodgate y la clave de Floodgate se conservan.
5. RAM de Crafty en reposo junto a Paper.
6. Geyser con 19132/tcp y 19133/udp publicados.
7. Qué hace la detección de caídas de Crafty si el contenedor se para desde fuera.

## 3. Fase 5 — BlueMap

- **Compatibilidad:** BlueMap 5.29 (10-10-2026) para Paper soporta 26.1.1–26.3 y requiere Java 25. Encaja con la release actual ([releases](https://github.com/BlueMap-Minecraft/BlueMap/releases), [Hangar](https://hangar.papermc.io/Blue/BlueMap/versions)).
- **Instalación:** es un plugin de Paper dentro del mismo proceso, así que no crea un segundo responsable del mundo. La configuración va en `plugins/BlueMap/`.
- **Decisión del operador:** necesita `accept-download: true`, que implica aceptar el EULA de Mojang para descargar el cliente y obtener texturas ([Core](https://bluemap.bluecolored.de/wiki/configs/Core.html)).
- **Web:** servidor integrado en TCP 8100 con la opción `ip` para fijar la interfaz ([Webserver](https://bluemap.bluecolored.de/wiki/configs/Webserver.html)). En este proyecto habría que publicarlo como `IP_LAN:8100:8100` en la plantilla Compose.
- **Recursos:**
  - `render-thread-count: 1` para 4 núcleos o menos. El render va fuera del hilo principal ([configuración](https://bluemap.bluecolored.de/wiki/getting-started/Configuration.html)).
  - Funciona con 512 MB, y usa como caché el heap libre ([RAM](https://bluemap.bluecolored.de/community/RAM.html)).
  - Para limitar CPU y escrituras en la microSD: `player-render-limit`, `render-mask` con radio, `min-inhabited-time`, `enable-hires: false` y almacenamiento FILE con GZIP ([mapas](https://bluemap.bluecolored.de/wiki/configs/Maps.html), [almacenamiento](https://bluemap.bluecolored.de/wiki/customization/Storages.html)).
  - No hay cifras oficiales de disco: hay que medirlas.
- **Jugadores en vivo:** incluidos (`live-player-markers`), con opciones de privacidad (`hide-vanished`, `hide-sneaking`, modos de juego ocultos) ([Plugin](https://bluemap.bluecolored.de/wiki/configs/Plugin.html)). Los jugadores Bedrock aparecen con la cabeza de Steve salvo con un addon de terceros. *Sin verificar* su compatibilidad.
- **Integración propuesta, pendiente:**
  1. Fijar el JAR de BlueMap con SHA-256 como parte de la release, igual que Geyser.
  2. Publicar 8100 sólo en la IP LAN.
  3. Incluir su configuración en los backups, pero valorar excluir el render, que es regenerable y grande.
  4. Medir en la Pi el render inicial con radio limitado (CPU, temperatura, RAM y disco) antes de ampliarlo.

## 4. QuestCraft

- **Versiones:** QuestCraft 6.0.0 (31-05-2025) es la última versión oficial. Lanza Minecraft Java 1.19.2–1.21.5 y **no soporta 26.x** ([releases](https://api.github.com/repos/QuestCraftPlusPlus/QuestCraft/releases), [supportedVersions.json](https://raw.githubusercontent.com/QuestCraftPlusPlus/Pojlib/QuestCraft-6.0.0/supportedVersions.json)). Existen forks no oficiales con 26.2 experimental; no se recomiendan.
- **Autenticación:** usa cuenta Microsoft con Java Edition, así que debería funcionar con `online-mode=true`. *Inferido del código de Pojlib, sin verificar.*
- **Cómo podría entrar:** el método habitual es instalar **ViaVersion + ViaBackwards** en Paper. ViaBackwards 5.12.0 o posterior soporta servidores 26.3; la versión actual es la 5.12.1 ([ViaBackwards](https://github.com/ViaVersion/ViaBackwards/releases), [ViaVersion](https://github.com/ViaVersion/ViaVersion/releases)). Un cliente 1.21.5 entraría traducido, con posibles diferencias de bloques y objetos nuevos.
- **Decisión pendiente:** el proyecto evita deliberadamente añadir ViaVersion de forma automática. Añadirlo amplía la superficie de compatibilidad (Via + Geyser + Floodgate) y debe tratarse como un cambio explícito: JAR fijado con hash, pruebas Java y Bedrock repetidas y prueba real desde las Quest. *Sin verificar* la combinación QuestCraft/Vivecraft + Via + Geyser.

## 5. Bedrock 26.60 y NetherNet

- **Calendario:** GeyserMC anuncia Bedrock 26.60 para el 27-10-2026. Esa versión elimina RakNet ([blog](https://geysermc.org/blog/preparing-for-nethernet/)).
- **Transporte:** la 0.2.0 ya tiene NetherNet activo (README, sección 6).
- **Protocolo:** a 18-10-2026 ninguna build de Geyser soporta el protocolo 26.60 (compatibles: 26.30–26.52). Cuando aparezca, `manage.sh update --plan` la propondrá. Hasta entonces, los clientes Bedrock que se actualicen a 26.60 no podrán entrar.
- **Aviso de confianza:** con signaling HTTP y conexión por IP, cada jugador verá la confirmación *trust on first use* en su primera conexión. HTTPS sólo lo evita con un dominio y su certificado ([HTTPS para NetherNet](https://geysermc.org/wiki/geyser/nethernet-https/)). La identidad del servidor se guarda en `data/plugins/Geyser-Spigot/nethernet/`, que es persistente y entra en los backups.
