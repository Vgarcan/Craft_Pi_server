# Craft Pi Server — Minecraft LAN Server Installer

Instalador y administrador de **Paper + GeyserMC + Floodgate**, dentro de Docker, orientado a una **Raspberry Pi 5 de 4 GB**, Raspberry Pi OS Lite de **64 bits** y microSD de **128 GB**.

## Índice

1. [Alcance y estado de verificación](#1-alcance-y-estado-de-verificación)
2. [Requisitos](#2-requisitos)
3. [Descarga e instalación](#3-descarga-e-instalación)
4. [Arquitectura y archivos](#4-arquitectura-y-archivos)
5. [Cómo instala y selecciona versiones](#5-cómo-instala-y-selecciona-versiones)
6. [Conexión Java y Bedrock](#6-conexión-java-y-bedrock)
7. [Administración](#7-administración)
8. [Backups y restauración](#8-backups-y-restauración)
9. [Actualizaciones](#9-actualizaciones)
10. [Configuración y memoria](#10-configuración-y-memoria)
11. [Red y arranque automático](#11-red-y-arranque-automático)
12. [Diagnóstico](#12-diagnóstico)
13. [Pruebas y desarrollo](#13-pruebas-y-desarrollo)
14. [Fuentes y decisiones](#14-fuentes-y-decisiones)
15. [Panel web y mapa](#15-panel-web-y-mapa)

## 1. Alcance y estado de verificación

El código, las pruebas, la configuración Docker y las guías se entregan juntos. La versión 0.1.0 se preparó sin hardware; desde la **0.2.0** el proyecto se ha instalado y ensayado en una Raspberry Pi 5 de 4 GB. El estado por fases, con la distinción entre **implementado** y **verificado en hardware**, está en [ESTADO.md](docs/ESTADO.md). El detalle de cada comprobación está en [VALIDACION.md](docs/VALIDACION.md).

El instalador sólo muestra una verificación local correcta después de comprobar:

- Estado estable y `healthy` del contenedor.
- Respuesta **Java Server List Ping** mediante TCP y **RakNet Unconnected Pong** mediante UDP.
- Que Geyser confirma en ese arranque el **signaling NetherNet** integrado y que su puerto TCP publicado acepta conexiones.
- Que Geyser y Floodgate están habilitados, mediante un diagnóstico **offline** generado por la instancia de Geyser cargada.
- Que el protocolo Java real de Paper coincide con el soportado por esa build de Geyser.
- Que Geyser utiliza autenticación Floodgate y Java mantiene `online-mode=true`.
- Que ambos protocolos responden también a través de los puertos publicados en la IP del host.

Eso prueba servicios y conectividad **desde la propia Pi**. La aceptación final necesita entrar y jugar desde un equipo Java y un dispositivo Bedrock reales de tu LAN. Un ping no prueba el inicio de sesión ni el rendimiento.

Se contempla una instalación por host. No incluye panel web, mapa, mods de Forge/Fabric, soporte VR específico ni publicación en Internet. Esas ampliaciones requieren otro alcance.

## 2. Requisitos

| Elemento | Requisito o valor inicial |
| --- | --- |
| Equipo objetivo | Raspberry Pi 5, 4 GB, alimentación adecuada y refrigeración activa |
| Sistema | Raspberry Pi OS Lite de 64 bits basado en Debian Bookworm 12 o Trixie 13, con systemd |
| Arquitectura | Kernel `aarch64`/`arm64` y paquetes `arm64` |
| Almacenamiento | microSD de 128 GB recomendada, sistema de archivos Linux; 8 GiB libres en el proyecto y 4 GiB en el almacenamiento de Docker |
| Python del host | Python 3.11 o posterior; sin dependencias Python de terceros en producción |
| Permisos | `sudo` para instalar y administrar; no se añade al usuario al grupo `docker` |
| Red | LAN preferentemente Ethernet; salida a Internet para APT, imágenes, JARs, dependencias y autenticación |
| Clientes | Java de la versión seleccionada; Bedrock dentro del rango compatible con Geyser |

El espacio mínimo comprueba el arranque; no garantiza capacidad para un mundo grande o todas sus copias. Las copias necesitan espacio adicional comparable al tamaño de los datos sin comprimir. No se altera swap, particionado ni firmware.

Una microSD de 128 GB comercial no tiene por qué mostrar 128 GiB en Linux. El instalador comprueba espacio utilizable, no el nombre comercial de la tarjeta.

## 3. Descarga e instalación

Para preparar la microSD, habilitar SSH y realizar el primer acceso, sigue [la guía desde cero](docs/INSTALACION_DESDE_CERO.md). Los comandos de esta sección se ejecutan **en la Raspberry Pi**, por SSH o desde su terminal.

### Opción A: descargar con Git — recomendada

En una instalación nueva:

```bash
sudo apt-get update
sudo apt-get install -y git python3 ca-certificates iproute2
sudo git clone https://github.com/Vgarcan/Craft_Pi_server.git /opt/minecraft-lan-installer
cd /opt/minecraft-lan-installer
sudo chmod +x install.sh manage.sh tests/run.sh docker/entrypoint.sh
sudo ./install.sh --check
```

El directorio `/opt/minecraft-lan-installer` debe ser nuevo. Si ya contiene una instalación, utiliza sus comandos de administración y conserva un backup antes de cambiar código. Los mundos y las configuraciones generadas no se guardan en Git.

### Opción B: descargar ZIP sin Git

Desde la página del repositorio selecciona **Code → Download ZIP**, o descarga [el código de la rama main](https://github.com/Vgarcan/Craft_Pi_server/archive/refs/heads/main.zip).

También puedes descargarlo directamente en la Pi:

```bash
sudo apt-get update
sudo apt-get install -y curl unzip python3 ca-certificates iproute2
curl --fail --location --output /tmp/Craft_Pi_server-main.zip \
  https://github.com/Vgarcan/Craft_Pi_server/archive/refs/heads/main.zip
sudo unzip /tmp/Craft_Pi_server-main.zip -d /opt
sudo mv /opt/Craft_Pi_server-main /opt/minecraft-lan-installer
cd /opt/minecraft-lan-installer
sudo chmod +x install.sh manage.sh tests/run.sh docker/entrypoint.sh
sudo ./install.sh --check
```

Usa esta opción sólo si **ninguno** de esos dos directorios de destino existe. No descomprimas encima de una instalación: un ZIP de GitHub incluye el sufijo `-main` en su carpeta raíz.

El paquete original `Minecraft-LAN-Server-Installer.zip` tiene otra estructura: ya contiene `minecraft-lan-installer/` y no necesita ese cambio de nombre. La guía desde cero distingue ambos paquetes.

### Comprobar e instalar

`--check` no instala paquetes ni inicia contenedores. Comprueba el host, su IP y el acceso HTTPS a las fuentes. Si tienes varias interfaces o direcciones, indica la que deben usar los jugadores:

```bash
sudo ./install.sh --check --lan-ip 192.168.1.50
```

Lee la [EULA de Minecraft](https://www.minecraft.net/eula). Si la aceptas:

```bash
sudo ./install.sh --accept-eula --lan-ip 192.168.1.50
```

**Sustituye `192.168.1.50` por la IP real de la Pi.** Si sólo hay una dirección candidata, puedes omitir `--lan-ip`. La versión de Minecraft se determina al instalar; no se mantiene en `latest` al arrancar.

Paper exige un `User-Agent` identificable con contacto. El ejemplo utiliza `https://github.com/Vgarcan`; puedes sustituirlo por tu URL o email:

```bash
sudo ./install.sh --accept-eula --contact https://github.com/Vgarcan
```

Se solicita aceptación explícita de la EULA en la primera instalación. Ninguna prueba ni parámetro acepta contratos por ti.

Después de instalar, comprueba el estado y los registros:

```bash
sudo ./manage.sh status
sudo ./manage.sh logs --tail 100
sudo ./manage.sh verify
```

Conecta después un cliente Java y uno Bedrock con la IP y versiones indicadas. La comprobación automática local no sustituye esa prueba de juego.

No cierres la alimentación mientras descarga, genera el mundo o guarda datos. El tiempo máximo inicial es 900 segundos; puedes aumentarlo hasta 7200 con `--timeout`. Si falla, conserva los mensajes y usa la guía de diagnóstico.

## 4. Arquitectura y archivos

Un único contenedor ejecuta **Paper**. **Geyser-Spigot** y **Floodgate-Spigot** son plugins de esa misma JVM, no servidores Bedrock independientes.

| Tráfico o dato | Recorrido |
| --- | --- |
| Cliente Java | IP LAN de la Pi, TCP 25565 → Paper |
| Cliente Bedrock hasta 26.5x (RakNet) | IP LAN de la Pi, UDP 19132 → Geyser → Paper, autenticado por Floodgate |
| Cliente Bedrock 26.60+ (NetherNet) | Signaling HTTP en TCP 19132, juego WebRTC en UDP 19133 → Geyser → Paper |
| Administración | `manage.sh` → Docker Compose; RCON sólo dentro del contenedor |
| Mundos y jugadores | `data/` del host → `/data` del contenedor |
| Binarios fijados | `releases/<id>/` del host → `/opt/release` en sólo lectura |
| Logs de operaciones | `logs/operations.log`, con rotación |
| Logs del servidor | Docker con rotación y registros propios de Paper en `data/logs/` |

El contenedor sólo publica, en la IP LAN, los puertos del juego (TCP 25565, UDP 19132, TCP 19132 y UDP 19133) y la web del mapa (TCP 8100). El panel web es un servicio aparte del host en TCP 8443. **RCON 25575 no se publica en el host.** No se monta el socket Docker en el contenedor.

```text
minecraft-lan-installer/
  install.sh                       Entrada de instalación
  manage.sh                        Entrada de administración
  docker/
    Dockerfile                     JVM Temurin ARM64 + Python para sondas
    compose.template.yaml          Plantilla versionada
    entrypoint.sh                  Arranque y sustitución de los dos plugins gestionados
    probe.py                       Java Status, RakNet y RCON
  docs/ESTADO.md                   Estado por fases: implementado frente a verificado
  scripts/
    main.py                        Entrada Python común
    mclan/                         Host, versiones, runtime, backups, CLI y utilidades
  config/settings.example.json     Ejemplo de opciones
  docs/                            Guía inicial, recuperación y validación
  tests/                           Pruebas y fixtures de contrato de API
```

Durante la instalación se crean `compose.yaml`, `.env`, `config/settings.json`, `state/`, `releases/`, `data/`, `backups/` y `logs/`.

`install.sh` y `manage.sh` son entradas Bash pequeñas y comentadas. La lógica se comparte en módulos Python de biblioteca estándar: así se puede probar sin ejecutar APT ni Docker. **Python pertenece al host y a las sondas; Minecraft sigue ejecutándose en Java.**

No muevas el directorio mientras el servicio esté activo. Los bind mounts se resuelven contra la ruta estable del proyecto.

## 5. Cómo instala y selecciona versiones

La primera instalación sigue esta secuencia:

1. Comprueba OS, ARM64, RAM física/disponible, espacio e IP local. Si la elección de IP es ambigua, exige una dirección explícita.
2. Consulta las fuentes por HTTPS. Usa la página oficial de compatibilidad para seleccionar la versión Java que Geyser emula.
3. Exige un build `STABLE` de Paper para esa misma versión. Obtiene el Java mínimo desde la API de Paper. Actualmente contempla Java 21 y 25; otros valores producen un error explícito.
4. Fija versiones y builds concretas de Geyser y Floodgate. Descarga los JARs y comprueba sus SHA-256 publicados. Una publicación de Geyser durante la resolución cancela el intento para evitar mezclar estados.
5. Si Docker falta, prepara su repositorio oficial de Debian y sólo solicita paquetes necesarios. Conserva un Engine existente. Si faltan Compose o Buildx, busca sus plugins compatibles. No ejecuta `curl | sh`, un `apt upgrade` general ni elimina paquetes en conflicto.
6. Comprueba que el contexto Docker sea el socket local del sistema, activa el servicio Docker y verifica su arquitectura.
7. Descarga Temurin ARM64, fija su digest y construye la imagen local. El identificador de imagen incorpora el digest y los hashes de Dockerfile, entrada y sonda.
8. Lee `CONFIG_VERSION` del `.class` dentro del JAR de Geyser **sin ejecutarlo en el host**. El esquema contemplado es el **10**; uno distinto requiere revisión y detiene la instalación.
9. Crea configuración sólo si falta. Genera `compose.yaml`, comprueba `docker compose config --quiet`, arranca y realiza las verificaciones descritas arriba.

La evidencia documental anterior no sustituye el ensayo de arranque. Floodgate no publica una matriz universal que permita demostrar toda compatibilidad mediante números: se comprueba además su carga efectiva y la autenticación configurada en la instancia real. El acceso de jugadores se confirma con la prueba manual LAN.

`state/active.json` y `releases/<id>/versions.lock.json` conservan builds, URLs, hashes, versión de Minecraft, Java, digest, fuentes de compatibilidad y versión de esquema. Los snapshots conservan estos datos y los tres JARs.

**Idempotencia:** repetir `install.sh` sobre una instalación activa no consulta `latest`, no sustituye la configuración ni vuelve a crear el mundo. Comprueba y arranca la release ya fijada. Las actualizaciones necesitan `manage.sh update`.

`data/` con contenido y sin metadatos gestionados se rechaza. No existe importación implícita de servidores anteriores. Un intento inicial incompleto conserva su marca `state/installing.json` para poder repetirlo sin truncar configuraciones.

## 6. Conexión Java y Bedrock

**Java, Windows/macOS/Linux:** usa la versión que muestra el instalador. Multijugador → Añadir servidor → `IP_DE_LA_PI:25565`. Se mantiene la autenticación normal de Java: necesitas una cuenta y licencia Java válidas.

**Bedrock, dispositivos con la opción de añadir un servidor externo:** Jugar → Servidores → Añadir servidor → nombre libre, dirección `IP_DE_LA_PI`, puerto `19132`. La cuenta/autenticación Bedrock se mantiene; Floodgate permite entrar sin comprar además la edición Java.

Algunas consolas restringen la introducción de servidores personalizados. Por tanto, **multiplataforma no significa conexión directa garantizada desde cualquier Xbox, PlayStation o Switch**. Prueba inicialmente con Bedrock de Windows o un móvil/tableta que admita añadir servidores.

No se garantiza la detección automática en la pestaña LAN: Docker usa una red bridge. Introduce la dirección manualmente. Java y Bedrock necesitan estar en la misma red con comunicación entre clientes habilitada; una Wi-Fi de invitados puede aislarlos.

**NetherNet.** Bedrock deja RakNet a partir de la versión 26.60, anunciada por GeyserMC para el 27 de octubre de 2026. Desde la 0.2.0 Geyser funciona con `transport: both`: RakNet sigue en UDP 19132 para los clientes actuales, y NetherNet usa signaling HTTP en TCP 19132 y WebRTC en UDP 19133. Los jugadores siguen introduciendo la misma dirección y el puerto `19132`.

- Los valores se fijan en `compose.yaml` mediante las propiedades JVM `geyserTransport`, `geyserWebrtcPort` y `geyserAdvertiseAddresses`, que **prevalecen** sobre `plugins/Geyser-Spigot/config.yml`. La dirección anunciada es la IP LAN fijada; `rebind` la actualiza.
- La identidad del servidor NetherNet se guarda en `data/plugins/Geyser-Spigot/nethernet/` y forma parte de los backups. Si se pierde, cada jugador debe volver a confirmar el servidor.
- El signaling integrado usa HTTP sin TLS. Según GeyserMC, quien conecta por IP verá una confirmación de confianza la primera vez (*trust on first use*). No se instala certificado ni proxy en este alcance.
- **Pendiente de hardware:** el transporte NetherNet sólo podrá probarse con un cliente 26.60 real y con una build de Geyser que soporte esa versión. Ejecuta `manage.sh update --plan` cuando se publique.

Geyser necesita acceso a Internet al arrancar para obtener datos de autenticación de Microsoft/Mojang. Sin red, Paper arranca pero Geyser se desactiva y Bedrock no conecta.

## 7. Administración

Ejecuta desde la carpeta del proyecto, o utiliza la ruta absoluta al script. `sudo` es intencionado: acceso al grupo Docker equivale a privilegios elevados sobre el host.

| Comando | Resultado |
| --- | --- |
| `sudo ./manage.sh start` | Arranca y verifica; rechaza operaciones incompletas |
| `sudo ./manage.sh stop` | Solicita cierre con hasta 180 segundos de gracia; comprueba evidencia de guardado |
| `sudo ./manage.sh restart` | Cierre y arranque verificado |
| `sudo ./manage.sh status` | Estado Docker y release activa; no sustituye `verify` |
| `sudo ./manage.sh logs --tail 150` | Últimas líneas del servidor |
| `sudo ./manage.sh logs --follow` | Sigue registros; Ctrl+C termina la consulta, no el servidor |
| `sudo ./manage.sh verify --timeout 900` | Verificación de servicios y protocolos locales |
| `sudo ./manage.sh backup` | Backup completo con parada temporal |
| `sudo ./manage.sh restore backups/ARCHIVO.tar.gz` | Restauración confirmada y verificada |
| `sudo ./manage.sh update --plan` | Resuelve y muestra la propuesta de versiones |
| `sudo ./manage.sh update` | Actualiza builds compatibles, con confirmación y backup |
| `sudo ./manage.sh rebind 192.168.1.60` | Cambia la IP publicada tras confirmación; útil si cambia DHCP |
| `sudo ./manage.sh command "list"` | Envía un comando de consola tras confirmar `EJECUTAR` |

Los comandos de consola tienen permisos administrativos. No pongas contraseñas ni tokens en sus argumentos. `stop`, `restart` y `reload` no se envían por esa entrada: usa los comandos de administración correspondientes.

Sólo una operación que modifica estado puede ejecutarse a la vez. `status` y `logs` quedan disponibles mientras se trabaja. `verify` se bloquea también porque genera temporalmente un diagnóstico offline.

## 8. Backups y restauración

```bash
sudo ./manage.sh backup
sudo ./manage.sh restore backups/minecraft-FECHA-manual.tar.gz
```

`backup` detiene el servidor si estaba en marcha, espera cierre limpio, crea y lee de nuevo el archivo para comprobar **todos los hashes**, y vuelve a iniciar sólo si estaba funcionando. Si estaba parado, permanece parado.

Se exige evidencia de guardado en los logs de ese arranque: el mensaje vanilla `All dimensions are saved` o, en Paper 26.x con Moonrise, la secuencia `Saving worlds` → `Halted I/O scheduler for world` → `All RegionFile I/O tasks to complete`, en ese orden. Paper 26.3 ya no escribe el mensaje vanilla, y la 0.1.0 rechazaba todos los backups por ello. Se acepta salida JVM `0` o `143` con esa evidencia; un OOM, SIGKILL, otro código o falta de evidencia impide presentar una copia como consistente. Si un cambio de Paper modifica estos mensajes, habrá que revisar el comprobador.

El archivo contiene mundos, jugadores, configuración, EULA, datos y claves de plugins, metadatos, Compose, `.env` y los JARs de la release activa. No contiene la imagen Docker completa, todo el historial de releases, las herramientas del sistema ni los registros de operaciones externos a `data/`.

Al recuperar en otro host puedes indicar `restore RUTA --lan-ip NUEVA_IP_LOCAL`; la IP debe estar asignada a ese host.

`backups/` utiliza permisos restrictivos. **Las copias contienen la clave de Floodgate y la contraseña de RCON**; no las subas a un repositorio. Se añade un SHA-256 del archivo comprimido y un manifiesto interno con hashes y tamaños por archivo.

Restaurar pide escribir **`RESTAURAR`**. Primero valida y extrae a una carpeta temporal, rechazando rutas absolutas, `..`, enlaces, dispositivos, duplicados y contenido que no corresponda al manifiesto. Comprueba también los JARs y el formato de la release antes de sustituir el mundo.

Si el estado actual permite un backup consistente, crea una copia `pre-restore`. Si proviene de un cierre anómalo o una operación fallida, conserva su directorio íntegro como `state/pre-restore-...` para inspección, sin etiquetarlo como copia consistente. El mundo anterior no se borra antes de disponer de un estado preservado.

Una interrupción deja `state/operation.json`. Los arranques ordinarios se bloquean hasta completar una restauración. Si una actualización ya abrió el mundo, se restaura **el snapshot completo**, no sólo un JAR antiguo: los formatos de mundo no admiten un downgrade seguro genérico.

Guarda periódicamente el archivo y su `.sha256` fuera de esa microSD. Una copia en la misma tarjeta ayuda ante errores de operación; no resuelve la pérdida de la tarjeta. No hay purga automática ni backup programado en este alcance.

La guía [RECUPERACION.md](docs/RECUPERACION.md) describe fallos y restauración tras reinstalar el sistema.

## 9. Actualizaciones

```bash
sudo ./manage.sh update --plan
sudo ./manage.sh update
```

Por defecto conserva la versión Minecraft del mundo y sólo considera builds compatibles. Si Geyser ya exige otra versión Java, **se detiene**: no añade ViaVersion ni cambia la versión del mundo automáticamente.

Para revisar explícitamente una subida de Minecraft:

```bash
sudo ./manage.sh update --plan --minecraft-version auto
sudo ./manage.sh update --minecraft-version auto
```

La segunda orden pide escribir **`ACTUALIZAR`**. Descarga y construye antes de parar el servidor; después crea el snapshot `pre-update`, activa la nueva release y verifica si estaba en marcha. Si estaba parado, permanece parado y la verificación queda pendiente del siguiente arranque.

Si la verificación falla, detiene la nueva instancia y conserva la marca de operación y la ruta del snapshot. No afirma éxito ni intenta abrir un mundo convertido con una versión antigua. Usa `restore` con la copia indicada.

**Actualizar el código del instalador.** `manage.sh update` no descarga cambios de este repositorio; eso se hace con Git. El procedimiento ensayado en hardware para pasar de la 0.1.0 a la 0.2.0 es:

```bash
cd /opt/minecraft-lan-installer
sudo ./manage.sh backup
sudo git pull --ff-only
sudo ./manage.sh status          # avisa de archivos Docker/Compose nuevos
sudo ./manage.sh update --plan   # muestra builds y cambios del proyecto
sudo ./manage.sh update          # pide ACTUALIZAR
```

Tras el `git pull`, `start` y `restart` se niegan a arrancar con código Docker o plantilla Compose distintos de los de la instalación activa; `status`, `logs` y `stop` siguen disponibles. `update` detecta ese cambio aunque las builds de Paper y los plugins sean las mismas: reconstruye la imagen, para el servidor, crea el snapshot `pre-update`, sustituye `compose.yaml` y verifica.

`compose.yaml` sólo se sustituye si es idéntico a una plantilla publicada por el proyecto (lista `PREVIOUS_TEMPLATES` en `scripts/mclan/runtime.py`). Si lo editaste a mano, `update` se detiene sin tocarlo.

`restore` admite backups creados con una plantilla gestionada anterior o con otros archivos Docker. Restaura datos y builds fijadas, y activa la plantilla y la imagen del proyecto actual.

No actualiza Raspberry Pi OS ni Docker Engine por medio de `manage.sh`. Una release ya fijada reutiliza su digest de JVM. Para renovar sólo la base Java o revisar un esquema nuevo de Geyser se requiere una modificación explícita y validada del proyecto; no hay actualización de seguridad automática de imágenes fijadas.

## 10. Configuración y memoria

| Parámetro | Inicial | Motivo |
| --- | --- | --- |
| JVM `-Xms` | 512 MiB | Evita reservar el heap máximo desde el inicio |
| JVM `-Xmx` | 2048 MiB | Deja espacio para memoria nativa, Geyser y el OS |
| Límite total del contenedor | 3 GiB | Incluye JVM, memoria nativa, auxiliares y caché contabilizada. **Sólo se aplica con el cgroup de memoria activo**; ver más abajo |
| Swap del contenedor | Sin margen adicional sobre ese límite | Evita utilizar la SD como ampliación ilimitada de memoria |
| View distance | 6 chunks | Punto de partida moderado para la Pi |
| Simulation distance | 4 chunks | Reduce trabajo por tick |
| Max players | 6 | Límite inicial, **no garantía de rendimiento para seis jugadores** |

**Límite de memoria y Raspberry Pi OS.** El kernel de Raspberry Pi OS arranca con `cgroup_disable=memory`. Entonces Docker descarta `mem_limit` y `memswap_limit` (aviso `Your kernel does not support memory limit capabilities`), y el contenedor sólo queda limitado por el heap de Java y la RAM del host. `status` y el preflight lo advierten. Para activarlo, añade `cgroup_enable=memory` al final de la única línea de `/boot/firmware/cmdline.txt`, conserva una copia del archivo original y reinicia. Comprueba después que `/sys/fs/cgroup/cgroup.controllers` incluye `memory`. Un contenedor creado antes del cambio conserva `Memory=0` aunque se reinicie: recréalo una vez con `sudo ./manage.sh stop`, `sudo docker compose --project-name minecraft-lan rm -f minecraft` (desde el directorio del proyecto) y `sudo ./manage.sh start`. Los datos están en `data/` y no se pierden. Es un cambio del host: el instalador no lo aplica por su cuenta.

`sudo tools/audit.sh` revisa en modo lectura el arranque, el límite de memoria, el contenedor, los puertos, NetherNet, la whitelist, los recursos y los backups. Con `--verify` añade la verificación de protocolos. Sale con código 1 si hay algún FALLO.

Con escritorio gráfico (`graphical.target`) quedan menos de 2 GiB disponibles con el servidor en marcha. El perfil previsto es OS Lite.

La configuración es un punto de partida conservador, no un benchmark. Prueba primero con 2–4 jugadores y vigila CPU, temperatura, memoria y generación de chunks. No se han medido TPS ni rendimiento en este equipo. Los mapas o plugins adicionales pueden cambiar mucho el consumo.

Para cambiar dificultad, MOTD, distancias o jugadores:

```bash
sudo ./manage.sh stop
sudo nano data/server.properties
sudo ./manage.sh start
```

Conserva `online-mode=true`, `enforce-secure-profile=false`, `enable-status=true`, RCON interno y los puertos previstos: el validador comprueba estos valores. `enforce-secure-profile=false` permite el acceso cruzado sin exigir a Bedrock un perfil firmado de Java; no desactiva la autenticación online de Java.

El instalador crea la configuración Geyser sólo cuando falta y conserva la clave de Floodgate generada por el plugin. El diagnóstico efectivo, no sólo el contenido escrito, confirma el modo de autenticación.

Los dos JARs con nombres `Geyser-Spigot.jar` y `floodgate-spigot.jar` están reservados al instalador. El entrypoint los sincroniza desde la release fijada en cada arranque: copia a un archivo temporal y renombra, porque la copia queda en modo `0400` y no se podría sobrescribir. No añadas otra copia de estos plugins con otro nombre.

`compose.yaml` es una copia gestionada de `docker/compose.template.yaml`; el validador rechaza divergencias, sin sobrescribirlas. Para un cambio deliberado de memoria, realiza backup, para el servicio, modifica ambos archivos de forma idéntica y conserva esa versión del proyecto con sus backups. `.env` se genera a partir del estado: para cambiar IP usa `rebind`.

## 11. Red y arranque automático

Se publican TCP 25565, UDP 19132, TCP 19132, UDP 19133 y TCP 8100 (mapa) **en la IPv4 LAN seleccionada**; el panel escucha en TCP 8443 de esa misma IP. No se usa `0.0.0.0` en el host ni se publica IPv6. Dentro del contenedor, Geyser escucha en `0.0.0.0`, que pertenece a su red aislada.

El instalador no entra al router, no crea port forwarding, no usa UPnP y no modifica las reglas del firewall del host. **Docker sí gestiona sus reglas de red** para publicar puertos; esto puede interactuar con UFW/firewalld. Una publicación en una IP local no equivale por sí misma a una ACL por subred: equipos de redes enrutadas que ya puedan alcanzar esa IP podrían acceder. Consulta la documentación Docker de firewall si tu red requiere restricciones adicionales.

La IP queda fijada en el estado para evitar publicar accidentalmente en otra interfaz. Si cambia DHCP, usa `rebind` con la nueva dirección que realmente figure en `ip -4 addr`. Una reserva DHCP manual es opcional, no un paso del instalador. Tras un reinicio con otra IP, Docker puede no poder publicar los puertos hasta que se corrija.

Docker se habilita con systemd y el servicio usa `restart: unless-stopped`. Si estaba en marcha, arrancará con Docker después de reiniciar el host. **Una parada manual se respeta**, incluso después de reiniciar; para reactivarlo usa `manage.sh start`.

Un contenedor `unhealthy` no se reinicia sólo por ese estado: esa política actúa al terminar el proceso. No se oculta esa diferencia con un watchdog no solicitado. Un OOM hace salir a Java y permite aplicar la política de reinicio; los logs y la verificación detectan reinicios repetidos.

## 12. Diagnóstico

| Síntoma | Comprobación y acción |
| --- | --- |
| ARM64 rechazado | `uname -m` y `dpkg --print-architecture`; reinstala Lite **64 bits** si el usuario es `armhf` |
| OS no contemplado | Utiliza una imagen Bookworm/Trixie de Raspberry Pi OS Lite; no fuerces la detección |
| Varias IP candidatas | `ip -4 addr`; usa `--lan-ip` en la instalación o `rebind` después |
| Puerto ocupado | `sudo ss -ltnup`; comprueba qué proceso utiliza 25565/TCP, 19132/UDP, 19132/TCP o 19133/UDP |
| Bucle de reinicio con `cp: cannot create regular file '/data/plugins/Geyser-Spigot.jar': Permission denied` | Fallo de la 0.1.0. Actualiza a la 0.2.0 (`git pull` y `manage.sh update`). Mientras tanto: `stop`, borrar sólo esos dos JAR de `data/plugins/` y `start` |
| `start` pide ejecutar `update` | Hiciste `git pull` y el proyecto trae código Docker o Compose nuevo; sigue el procedimiento de la sección 9 |
| Aviso `NetherNet support is NOT enabled` | Instalación 0.1.0; tras actualizar no debe aparecer. `logs` debe mostrar `Built-in signaling started on` |
| Jugador rechazado con `You are not whitelisted` | `white-list=true` en `server.properties`. Añádelo con `manage.sh command "whitelist add NOMBRE"` (Java) o `"fwhitelist add NOMBRE"` (Bedrock vía Floodgate) |
| Docker existe pero no responde | `sudo systemctl status docker`; comprueba espacio y contexto local |
| Paquetes Docker en conflicto | El script no los elimina: revisa qué servicios dependen de ellos antes de resolverlo manualmente |
| Fallo HTTP, DNS o TLS | Revisa fecha/hora (`timedatectl`), DNS, acceso a las fuentes y proxy; no desactives TLS |
| Falta build estable compatible | Espera una pareja compatible o revisa el proyecto; no cambia de versión por su cuenta |
| Cambió la página o esquema Geyser | El parser falla de forma explícita; actualiza el soporte y sus pruebas, sin saltarte la comprobación |
| Mundo tarda en generar | Consulta logs; aumenta `--timeout` si sigue progresando y hay recursos |
| Java conecta, Bedrock no | Comprueba versión Bedrock, Geyser/Floodgate, UDP, aislamiento Wi-Fi y que el cliente admita añadir servidor |
| No aparece como LAN | Introduce IP y puerto manualmente |
| Sale OOM o reinicia | `sudo docker stats --no-stream`; reduce distancias/carga y revisa memoria disponible del host |
| Backup no confirma guardado | Revisa logs del cierre; no borres datos ni fuerces la etiqueta de copia consistente |
| Restauración bloqueada por operación incompleta | Usa el snapshot íntegro indicado en `state/operation.json`; consulta RECUPERACION.md |
| Cambió la IP | `sudo ./manage.sh rebind NUEVA_IP` |

Diagnóstico básico:

```bash
sudo ./manage.sh status
sudo ./manage.sh logs --tail 200
sudo docker stats --no-stream
sudo journalctl -u docker --since '30 minutes ago'
df -h
free -m
vcgencmd measure_temp
vcgencmd get_throttled
```

Para ver el error preciso de Compose, sin pegar su configuración completa en un sitio público:

```bash
sudo docker compose --project-name minecraft-lan --env-file .env -f compose.yaml config --quiet
```

Los registros de operaciones rotan en `logs/`; Docker limita sus logs a tres archivos de aproximadamente 10 MB. Paper también escribe dentro de `data/logs/`: revisa periódicamente el espacio, pues no se establece aquí una purga adicional de sus archivos.

## 13. Pruebas y desarrollo

```bash
./tests/run.sh
```

Incluye sintaxis Bash, compilación Python y pruebas `unittest`. No instala Docker ni ejecuta APT. Usa temporales, fixtures y dobles de Docker; las sondas se prueban además contra sockets TCP/UDP locales reales que simulan respuestas del protocolo.

Para exigir ShellCheck:

```bash
sudo apt-get install shellcheck
./tests/run.sh --strict
```

El instalador valida el Compose generado mediante el plugin Compose real del host. Las pruebas automatizadas comprueban preservación del mundo, checksums, rechazo de enlaces/traversal, restauración, bloqueos, compatibilidad y fallos de actualización. El informe de validación distingue lo ejecutado aquí de la prueba pendiente en ARM64.

Antes de actualizar fixtures o ampliar compatibilidad, conserva una copia de las respuestas oficiales, revisa los esquemas y ajusta pruebas. Un fixture no representa un servidor Minecraft funcionando.

## 14. Fuentes y decisiones

Fuentes revisadas el **18 de octubre de 2026**. El instalador vuelve a consultar las fuentes al resolver una instalación o actualización, y conserva la evidencia de esa resolución.

- [Docker Engine en Debian](https://docs.docker.com/engine/install/debian/), [Compose](https://docs.docker.com/compose/install/linux/) y [reinicio de contenedores](https://docs.docker.com/engine/containers/start-containers-automatically/).
- [Docker y filtrado de puertos](https://docs.docker.com/engine/network/packet-filtering-firewalls/).
- [API de descargas Paper](https://docs.papermc.io/misc/downloads-service/) y [requisitos Java](https://docs.papermc.io/misc/java-install/).
- [Preparación de GeyserMC para NetherNet](https://geysermc.org/blog/preparing-for-nethernet) y [configuración de Geyser](https://geysermc.org/wiki/geyser/setup/).
- [Compatibilidad Geyser](https://geysermc.org/wiki/geyser/supported-versions/), [configuración](https://geysermc.org/wiki/geyser/understanding-the-config/) y [Floodgate](https://geysermc.org/wiki/floodgate/).
- [Código fuente Geyser](https://github.com/GeyserMC/Geyser): configuración, constante de esquema, diagnóstico y comando `dump offline` contrastados con el commit de la build inspeccionada.
- [Raspberry Pi Imager](https://www.raspberrypi.com/software/) y [EULA Minecraft](https://www.minecraft.net/eula).

No se redistribuyen JARs, claves, mundos ni imágenes Docker en este paquete. Se descargan desde sus proveedores al instalar. Cada componente conserva sus propias condiciones y licencia. El fijado por digest de Temurin no convierte la reconstrucción en bit a bit reproducible: la capa APT que añade Python obtiene paquetes del repositorio durante la construcción. Se reutiliza la imagen ya construida cuando está disponible.

## 15. Panel web y mapa

### Mapa: BlueMap

Desde la 0.3.0, cada release incluye **BlueMap**, un plugin de Paper dentro del mismo proceso. No es un segundo servidor. Se resuelve en Hangar como la última versión *Release* que declara la versión de Minecraft fijada, y se fija con el SHA-256 publicado, igual que Geyser y Floodgate.

- Mapa 3D y jugadores en vivo en `http://IP_DE_LA_PI:8100/`, sólo en la LAN.
- `plugins/BlueMap/core.conf` se crea una sola vez con un perfil para la Pi: un hilo de render y métricas desactivadas. Después puedes editarlo; no se sobrescribe.
- `accept-download: true` acepta la EULA de Minecraft para descargar las texturas del cliente desde Mojang. Sólo se escribe si `eula.txt` ya registra tu aceptación.
- Los backups no incluyen `data/bluemap/web/maps`: las teselas se regeneran tras un restore.
- `verify` comprueba que BlueMap está habilitado en Paper. Que su web no responda es sólo un aviso: el mapa es accesorio y no debe tumbar un update.
- **Paper 26.x y la luz:** Moonrise no deja la luz en el formato que espera BlueMap, y sin ajuste sólo se renderiza la vista lejana: de cerca el mapa se ve negro. `docker/bluemap-setup.sh` activa `ignore-missing-light-data` una vez en cada mapa, marcado con `# craft-pi: light-fix`. Si lo reviertes a mano, se respeta. Corre dentro del contenedor, como su usuario, en cada arranque y en `verify` tras el primero.
- Si al abrir el mapa sólo ves negro, puede que el navegador conserve en la URL una posición sin terreno generado. Usa el enlace **Ver mapa en el spawn** del panel.

**Consumo.** Desde el panel (Estado → Módulos y consumo) o la terminal:

```bash
sudo ./manage.sh command "bluemap stop"   # pausa el render al momento; la web sigue
sudo ./manage.sh command "bluemap start"
sudo ./manage.sh plugin bluemap off       # retira el plugin y libera su memoria (reinicia)
sudo ./manage.sh plugin bluemap on
```

Desactivado, `.env` incluye `DISABLED_PLUGINS=bluemap` y el entrypoint no copia el JAR. En la Pi, el contenedor recién arrancado pasó de unos 1,34 GiB a 1,14 GiB.

Para limitar más el render (por ejemplo `min-inhabited-time` o `render-mask` con radio), edita `plugins/BlueMap/maps/*.conf` con el servidor parado. Consulta la [documentación de BlueMap](https://bluemap.bluecolored.de/wiki/configs/Maps.html).

### Panel web

```bash
sudo panel/install.sh                  # instalar o actualizar (repetir tras rebind)
sudo cat /etc/mclan-panel/initial-password
sudo panel/install.sh --reset-password # nueva contraseña desde la terminal
sudo panel/install.sh --uninstall
```

Abre `https://IP_DE_LA_PI:8443/` desde la LAN. Si escribes la dirección sin `https://`, el panel redirige solo. El certificado es autofirmado: el navegador avisará la primera vez. Compara su huella SHA-256 con la que imprime `install.sh` antes de aceptarlo.

| Página | Qué permite |
| --- | --- |
| Estado | Salud, jugadores conectados, memoria, temperatura, avisos, versiones y últimos backups. Botones de arrancar, parar, reiniciar y backup |
| Whitelist | Activar o desactivar; añadir jugadores Java, Bedrock (gamertag) o Bedrock por XUID; quitar; ver los **intentos rechazados** de las últimas 72 horas y permitirlos con un botón; **apertura temporal** para un jugador concreto |
| Ajustes | MOTD, dificultad, modo de juego, jugadores máximos, distancias, PvP, vuelo y protección del spawn. Guardar reinicia el servidor |
| Logs | Últimas 100, 200 o 500 líneas |
| Auditoría | Resultado de `tools/audit.sh` |
| Mapa | Enlace a BlueMap |
| Cuenta | Cambio de contraseña |

**Diseño y seguridad:**

- El servidor web corre como el usuario de sistema `mclan-panel`, sin privilegios, y no habla con Docker. Toda operación pasa por `panel/helper.py`, que es lo único que `mclan-panel` puede ejecutar con sudo (`/etc/sudoers.d/mclan-panel`).
- El helper sólo acepta verbos fijos y nombres que cumplen patrones cerrados; **nunca** envía texto libre a la consola. Los ajustes editables son una lista cerrada: autenticación, puertos y RCON no se tocan desde la web.
- Las operaciones usan `manage.sh` o su mismo bloqueo, y quedan en `logs/operations.log` como `Operación: panel …`. Sigue habiendo un único responsable del proceso de Paper.
- `install.sh` comprueba que el helper y todo el código que importa pertenecen a root y no admiten escritura de grupo u otros. Si alguien cambia esos permisos, la regla sudo deja de ser segura.
- Contraseña con PBKDF2-SHA256 (dos cálculos simultáneos como máximo). Cookie `__Host-` con `Secure`, `HttpOnly` y `SameSite=Strict`, token CSRF y comprobación de `Origin` en cada POST (también en el login), bloqueo tras 5 fallos en 10 minutos, CSP sin scripts y sin incrustación en otras páginas.
- Sólo atiende el `Host` con el que se publica (contra DNS rebinding). TLS por conexión en su propio hilo, `Content-Length` estricto y límite de 32 conexiones en total y 8 por IP.
- `data/` pertenece al usuario del contenedor: el helper no sigue enlaces en ningún componente de la ruta, sólo lee archivos regulares y con tope, y escribe `server.properties` mediante descriptores de directorio. El diagnóstico de Geyser se lee dentro del contenedor.
- Reiniciar el servicio del panel no interrumpe un backup o reinicio ya lanzado (`KillMode=process`).

**Jugadores Bedrock y XUID.** `fwhitelist add GAMERTAG` sólo funciona si la caché global de GeyserMC conoce al jugador. Si no, hay dos salidas:

- Indicar su XUID: el panel lo convierte al UUID de Floodgate.
- Usar la **apertura temporal**: la whitelist se desactiva hasta que entra ese jugador (máximo 15 minutos), se le añade y se reactiva siempre, también si falla algo. Mientras está abierta puede entrar cualquier cuenta válida de la LAN.

### Importar un mundo Bedrock (.mcworld)

No hay un comando automático: es una migración que conviene revisar cada vez. El procedimiento ensayado (detalle en [ESTADO.md](docs/ESTADO.md)) es:

1. Extraer el `.mcworld` (un zip) en una carpeta nueva y revisar que no traiga behavior ni resource packs.
2. Convertir con el CLI de [Chunker](https://github.com/HiveGamesOSS/Chunker), comprobando su SHA-256, a `JAVA_<versión del servidor>`. Hazlo en un contenedor sin red con la imagen Java de la release: `docker run --rm --network none --user 1000:1000 -v …:/in -v …:/out <java_base> java -jar chunker-cli.jar -i /in -o /out/mundo -f JAVA_26_3`. LevelDB necesita escribir en la carpeta de entrada: usa una copia.
3. Revisar los avisos `Missing entity mapping`: esas entidades se pierden.
4. Probar el mundo en un Paper aislado (sin red ni puertos) antes de tocar producción.
5. `stop`, `backup`, apartar `data/world`, copiar el mundo convertido con propietario `10000:10000` y `start`.
6. Fijar el spawn si Bedrock usaba «buscar superficie» (`setworldspawn X Y Z`), y regenerar el mapa: borra `data/bluemap/web/maps` con el servidor parado y lanza `bluemap force-update MAPA`.
