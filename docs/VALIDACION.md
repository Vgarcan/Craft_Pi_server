# Informe de validación — 18 de octubre de 2026

> **Actualización 0.2.0 (18-10-2026, tarde).** Las secciones 1 a 4 describen la validación sin hardware de la 0.1.0. La sección 6 recoge la primera validación en la Raspberry Pi 5 y la batería automatizada de la 0.2.0. El estado por fases está en [ESTADO.md](ESTADO.md).

## Índice

1. Resultado y límites
2. Comprobaciones ejecutadas
3. Contratos y versiones observadas
4. Cómo reproducir
5. Aceptación pendiente en la Raspberry Pi
6. Validación 0.2.0 en la Raspberry Pi 5

## 1. Resultado y límites

**El proyecto ha superado 42 pruebas automatizadas, ShellCheck y validación de configuración con Compose real. No se ha ejecutado un servidor Minecraft real ni se ha instalado el proyecto en una Raspberry Pi.**

El entorno de preparación es Linux x86_64 con Python 3.12.14. No dispone de un Docker Engine operativo para construir o ejecutar los contenedores. La validación de Compose usa su binario oficial independiente y no necesita un daemon.

No se han medido TPS, uso real de memoria con jugadores, tiempos de generación, temperatura o durabilidad de la microSD. El perfil de 2 GiB de heap y 3 GiB de límite es una decisión inicial que debe ensayarse en el hardware.

## 2. Comprobaciones ejecutadas

| Comprobación | Resultado | Qué demuestra |
| --- | --- | --- |
| Sintaxis de los cuatro scripts shell | Correcta con `bash -n` | Los scripts se pueden analizar sintácticamente |
| ShellCheck 0.11.0 | Sin incidencias | Revisión estática de los scripts shell |
| Compilación Python | Correcta | Los módulos se pueden compilar en el entorno de prueba |
| `unittest` | **42 pruebas, todas correctas** | Casos de éxito, rechazo y recuperación con temporales y dobles |
| Ruff 0.17.1, reglas F y E9 | Sin incidencias | Sin imports sin uso ni los errores Python revisados por esas reglas |
| Docker Compose oficial 5.6.0 | Plantilla y override válidos | Interpolación, puertos, mounts, usuario, memoria y políticas de reinicio normal/transaccional |
| SHA-256 del binario Compose usado | Verificado contra checksum de su publicación oficial | Integridad del validador descargado |
| Resolución contra APIs oficiales reales | Correcta en la fecha indicada | El parser consulta e interpreta los contratos actuales |
| JAR Geyser descargado | SHA-256 coincide con la API | Integridad del JAR inspeccionado, no ejecución del plugin |
| Constante Geyser dentro del `.class` | `CONFIG_VERSION = 10` | La inspección sin JVM obtiene el esquema actual del artefacto real |
| Manifest de Temurin 25 JRE Jammy | Publica imagen Linux ARM64 | Disponibilidad de arquitectura en el registro al consultar; no arranque |
| Rechazo local de `install.sh --check` | Rechaza x86_64 antes de instalar | No se fuerza una instalación no soportada en este entorno |

La configuración Compose se comprobó sin crear contenedores y sin ejecutar APT. El ZIP no incluye los binarios de las herramientas de comprobación.

Las pruebas automatizadas cubren:

- Selección de builds estables, coincidencia Java/Paper/Geyser, rechazo de documentación desconocida y publicaciones que cambian durante la resolución.
- Descargas con hash incorrecto, preservación de archivos existentes y rechazo de fuentes no autorizadas.
- Bloqueo de escrituras concurrentes y confirmaciones canceladas/no interactivas.
- Un **servidor TCP simulado sobre socket local real** que responde a Java Status.
- Un **servidor UDP simulado sobre socket local real** que responde a RakNet Unconnected Ping.
- Paquetes malformados, VarInt truncado y autenticación/lectura RCON con un doble de socket.
- Rechazo de Floodgate deshabilitado, protocolo equivocado, build equivocada y autenticación Java offline; rechazo de validación de identidad Bedrock desactivada.
- Conservación del mundo al repetir instalación y rechazo de un directorio de datos no gestionado.
- Backups, validación de manifiestos, restauración de un mundo ficticio, archivos corruptos, enlaces, rutas peligrosas y poco espacio.
- Fallo de actualización/restauración con conservación del snapshot, marca de operación y bloqueo de arranque normal.
- Recuperación tras cierre anómalo conservando los datos anteriores y cambio de IP durante restauración.
- Reutilización de Docker/Compose/Buildx existentes, selección sólo de paquetes ausentes y protección frente a variables Compose exportadas.
- Evidencia de guardado del arranque actual para aceptar un cierre JVM 143 y rechazo de OOM.
- Suspensión del reinicio automático durante una transacción y restablecimiento después de la verificación.

Los dobles simulan decisiones del host y Docker. **No equivalen a probar APT, systemd, Paper o el cierre real de una JVM en ARM64.**

## 3. Contratos y versiones observadas

La resolución real durante esta preparación devolvió:

| Componente | Observado |
| --- | --- |
| Minecraft Java compatible publicado por Geyser | 26.3 |
| Paper STABLE seleccionado | Build 205 para 26.3 |
| Java mínimo indicado por Paper | 25 |
| Geyser | 2.12.0, build 1265 |
| Floodgate | 2.2.5, build 141 |
| Bedrock publicado como compatible | 26.30–26.52 |
| Esquema de configuración de ese JAR Geyser | 10 |

**Esta tabla es evidencia fechada, no una promesa de que esas sean las versiones futuras.** El instalador resolverá y fijará versiones cuando se ejecute en la Pi. Se conserva una muestra de las respuestas en `tests/fixtures/`; esos fixtures no se usan como fallback de producción.

El código de Geyser de la build observada se contrastó con el commit `02dbfaccb8be68c47c176c96c24bdbddb472b375` para revisar configuración, diagnóstico offline y comando `dump`. No se incluye su código fuente ni sus JARs en este proyecto.

## 4. Cómo reproducir

Pruebas sin dependencias de producción adicionales:

```bash
./tests/run.sh
```

Con ShellCheck instalado:

```bash
./tests/run.sh --strict
```

Cuando hay plugin Compose disponible, el ejecutor de pruebas invoca automáticamente la validación de configuración. También puedes hacerlo directamente, sin levantar servicios:

```bash
python3 tests/check_compose.py
```

Si un administrador ya dispone de un binario oficial independiente de Compose:

```bash
python3 tests/check_compose.py --standalone /RUTA/AL/BINARIO/docker-compose
```

La comprobación utiliza un directorio temporal y no altera `compose.yaml`, `.env` ni los mundos reales.

Ruff es una comprobación adicional de desarrollo, no un requisito de ejecución:

```bash
ruff check --select F,E9 scripts docker/probe.py tests
```

## 5. Aceptación pendiente en la Raspberry Pi

Registra los resultados al ejecutar la guía desde cero:

| Prueba en hardware | Estado a 18-10-2026 | Evidencia a anotar |
| --- | --- | --- |
| OS Lite 64 bits, modelo, RAM y espacio | **Hecho, con desviación**: Trixie arm64 **con escritorio**, Pi 5 4 GB, 103 GiB libres | Salida del preflight y modelo exacto |
| Instalación selectiva Docker/Compose/Buildx | Hecho (Engine 29.9.1, Compose 5.6.0) | Paquetes antes/después y versiones |
| Construcción y arranque ARM64 | **Verificado** | Imagen, release, logs y estado `healthy` |
| Diagnóstico efectivo de Geyser/Floodgate | **Verificado** en cada `verify` | Plugins habilitados y coincidencia de protocolo/build |
| Conexión Java desde otro equipo | Pendiente | Versión, inicio de sesión y juego |
| Conexión Bedrock desde otro dispositivo | Parcial: la conexión llegó a Paper y la whitelist la rechazó | Plataforma, versión, cuenta y juego |
| Dos clientes en el mismo mundo | Pendiente | Interacción común observada |
| Persistencia tras stop/start | Pendiente (el ciclo funciona; falta comprobar bloques e inventario) | Bloque, cofre e inventario conservados |
| Arranque tras reboot del host | **Verificado** (18-10-2026): `healthy` y puertos respondiendo sin intervención | Servicio activo y conectividad posterior |
| Respeto de parada manual tras reboot | Pendiente | Permanece parado hasta `start` |
| Backup y restore de prueba | **Backup verificado**; restore ensayado en copia aislada | Copia verificada y retorno al estado previo |
| Carga inicial de 2–4 jugadores | Pendiente | Memoria, CPU, temperatura y rendimiento percibido |
| Paquete de recursos en Java 26.3 | Pendiente (servido y verificado por SHA-1 desde el host) | Aviso con crédito, aceptar, texturas 32x; rechazo en modo opcional y obligatorio |
| Paquete de recursos en QuestCraft 1.21.5 | Pendiente | Que reciba la variante 1.21.5 (`mclanpacks status`) y la cargue |
| Paquete de recursos en Bedrock | Pendiente | Que Faithful llegue al conectar y se vean las texturas |

Sólo después de estas pruebas puede afirmarse que la instalación concreta funciona en tu Raspberry Pi y con tus clientes.

## 6. Validación 0.2.0 en la Raspberry Pi 5

### Batería automatizada

Ejecutada en la propia Pi (Python 3.13.5, arm64):

| Comprobación | Resultado |
| --- | --- |
| `./tests/run.sh --strict` | **51 pruebas correctas**; ShellCheck 0.10.0 sin incidencias; Compose real válido |
| `python3 -m pyflakes scripts docker/probe.py tests` | Sin incidencias (sustituye a Ruff F/E9, no disponible en la Pi) |

Pruebas nuevas en la 0.2.0:

- Secuencia de cierre de Paper/Moonrise aceptada en orden, rechazada incompleta o desordenada, y compatibilidad con el mensaje vanilla.
- Detección del cgroup de memoria.
- Verificación NetherNet: exige la línea de signaling en el log del arranque actual y un TCP que acepte conexiones.
- Validación estricta frente a deriva del proyecto; detección de plantillas anteriores y rechazo de las editadas a mano.
- `update` con las mismas builds y proyecto nuevo: reconstruye, crea `pre-update` y aplica la plantilla.
- `update --plan` sin efectos, y ausencia de cambios cuando no hay nada nuevo.
- `restore` de un backup con plantilla anterior; rechazo de un backup con `compose.yaml` editado a mano, sin tocar el mundo.
- Compose publica 19132/tcp y 19133/udp en la IP LAN, anuncia `IP:19133` y no publica RCON.

### Hardware

Detalle, fallos encontrados y copias preservadas: [ESTADO.md](ESTADO.md), fases 1 y 3.

Ninguna prueba automatizada ejecuta el `entrypoint.sh` real, porque exige UID 10000. Su corrección se verificó en hardware con dos `restart` consecutivos y con un `backup` que rearranca el servidor.
