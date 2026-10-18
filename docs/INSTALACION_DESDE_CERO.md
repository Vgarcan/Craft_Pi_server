# Instalación desde cero

## Índice

1. Preparar hardware y tarjeta
2. Instalar Raspberry Pi OS Lite de 64 bits
3. Primer acceso por SSH
4. Transferir y preparar el proyecto
5. Comprobar e instalar
6. Probar desde ambos clientes
7. Verificar persistencia y reinicio
8. Hacer y probar un backup

## 1. Preparar hardware y tarjeta

Necesitas la Pi 5 de 4 GB, microSD de 128 GB, lector de tarjetas, alimentación apropiada para Pi 5, refrigeración activa y conexión LAN. Ethernet facilita la instalación; Wi-Fi también puede servir si los clientes se pueden comunicar entre sí.

**Grabar la imagen del sistema borra la tarjeta seleccionada.** Copia antes cualquier archivo importante y verifica en el grabador que has seleccionado esa microSD. Este proyecto no formatea discos ni incluye un comando para hacerlo.

## 2. Instalar Raspberry Pi OS Lite de 64 bits

En tu ordenador abre [Raspberry Pi Imager](https://www.raspberrypi.com/software/):

1. Selecciona Raspberry Pi 5 como equipo.
2. Elige **Raspberry Pi OS Lite (64-bit)**, basado en Debian Bookworm o Trixie. No selecciones Lite de 32 bits ni el escritorio si quieres seguir exactamente este perfil.
3. Selecciona la microSD correcta.
4. Configura un hostname, por ejemplo `minecraft-pi`, un nombre de usuario y una contraseña propios. No presupongas que existe el usuario `pi`.
5. Habilita SSH, preferentemente mediante clave pública si ya utilizas claves. Configura Wi-Fi y país sólo si necesitas Wi-Fi.
6. Revisa los ajustes y confirma la grabación cuando hayas comprobado la tarjeta.

Introduce la tarjeta en la Pi apagada, conecta Ethernet y alimentación, y espera el primer arranque.

## 3. Primer acceso por SSH

Desde PowerShell, una terminal Linux o macOS, sustituye `TU_USUARIO`:

```bash
ssh TU_USUARIO@minecraft-pi.local
```

Si `.local` no resuelve, utiliza la IP que muestre tu router o una pantalla conectada a la Pi. No necesitas cambiar la configuración del router para este proyecto.

En la Pi:

```bash
uname -m
dpkg --print-architecture
cat /etc/os-release
free -m
df -h
ip -4 addr
```

Debes ver kernel `aarch64` y paquetes `arm64`. El OS debe corresponder a Bookworm o Trixie. Comprueba que la fecha y hora sean correctas; TLS puede fallar si el reloj está desajustado.

Instala las herramientas básicas si faltan:

```bash
sudo apt-get update
sudo apt-get install -y python3 unzip ca-certificates iproute2
```

En esta guía no se instala Java globalmente: lo contiene la imagen Docker. Tampoco se ejecuta una actualización general del sistema como parte del instalador. El mantenimiento del OS es una decisión separada del operador.

## 4. Transferir y preparar el proyecto

### Descargar directamente en la Pi con Git

La opción recomendada evita transferir archivos desde tu ordenador:

```bash
sudo apt-get install -y git
sudo git clone https://github.com/Vgarcan/Craft_Pi_server.git /opt/minecraft-lan-installer
cd /opt/minecraft-lan-installer
sudo chmod +x install.sh manage.sh tests/run.sh docker/entrypoint.sh
```

Es una instalación nueva: `/opt/minecraft-lan-installer` no debe existir. Si ya existe, revisa su estado y conserva un backup en lugar de clonarlo o descomprimirlo encima.

### Descargar un ZIP de GitHub

En el repositorio pulsa **Code → Download ZIP**, o utiliza el [enlace directo de la rama main](https://github.com/Vgarcan/Craft_Pi_server/archive/refs/heads/main.zip). GitHub lo descarga como `Craft_Pi_server-main.zip` y su carpeta raíz se llama `Craft_Pi_server-main/`.

Desde la carpeta de descargas de Windows, transfiérelo con PowerShell:

```powershell
scp .\Craft_Pi_server-main.zip TU_USUARIO@minecraft-pi.local:~/
```

En Linux/macOS:

```bash
scp Craft_Pi_server-main.zip TU_USUARIO@minecraft-pi.local:~/
```

En la Pi, sólo si ambos destinos son nuevos:

```bash
sudo unzip ~/Craft_Pi_server-main.zip -d /opt
sudo mv /opt/Craft_Pi_server-main /opt/minecraft-lan-installer
cd /opt/minecraft-lan-installer
sudo chmod +x install.sh manage.sh tests/run.sh docker/entrypoint.sh
```

### Si ya descargaste el paquete original del proyecto

El archivo `Minecraft-LAN-Server-Installer.zip` entregado inicialmente contiene la carpeta `minecraft-lan-installer/`, sin `-main`. Para ese archivo concreto:

```bash
sudo unzip ~/Minecraft-LAN-Server-Installer.zip -d /opt
cd /opt/minecraft-lan-installer
sudo chmod +x install.sh manage.sh tests/run.sh docker/entrypoint.sh
```

No mezcles los nombres de ambos ZIP. El código y la carpeta final son los mismos; cambia el nombre de la carpeta que genera cada descarga. Si eliges otra ubicación, utiliza siempre esa misma ruta en los comandos siguientes.

## 5. Comprobar e instalar

Primero:

```bash
sudo ./install.sh --check
```

Si hay varias direcciones, decide la interfaz LAN que usan los jugadores y repite con su IP real:

```bash
sudo ./install.sh --check --lan-ip 192.168.1.50
```

Comprueba el resultado y lee la [EULA](https://www.minecraft.net/eula). Sólo si la aceptas:

```bash
sudo ./install.sh --accept-eula --lan-ip 192.168.1.50
```

La IP es un ejemplo. La opción `--lan-ip` se puede omitir cuando sólo hay una candidata. `--contact` permite sustituir la URL de contacto que figura en la configuración de ejemplo.

El instalador puede tardar al descargar imágenes, preparar el entorno y generar el primer mundo. Si falla, no asumas que terminó. Consulta:

```bash
sudo ./manage.sh status
sudo ./manage.sh logs --tail 200
```

Si se trata de un fallo transitorio inicial, repite el instalador; no elimines `data/` para «empezar de nuevo». Si existe `state/operation.json`, sigue la guía de recuperación.

## 6. Probar desde ambos clientes

Usa un Java de la **versión Minecraft indicada por el instalador**, y un Bedrock del rango soportado. No compares el número Java con el de Bedrock como si fueran la misma serie de versiones.

| Cliente | Dirección |
| --- | --- |
| Minecraft Java | `IP_DE_LA_PI:25565` |
| Minecraft Bedrock | Dirección `IP_DE_LA_PI`, puerto `19132` |

Entra desde ambos, comprueba que ven el mismo mundo y que pueden interactuar con el mismo bloque. Si Bedrock no permite añadir un servidor externo, prueba inicialmente con otro dispositivo compatible; las restricciones de consola no las elimina Docker.

La autenticación y el protocolo pueden necesitar conectividad exterior aun tratándose de un servidor alojado en LAN. No cambies `online-mode=false` para sortear un problema de autenticación.

## 7. Verificar persistencia y reinicio

Haz esta prueba en el mundo de prueba inicial:

1. Coloca un bloque reconocible y guarda un objeto en un cofre.
2. Sal de los clientes.
3. Ejecuta `sudo ./manage.sh stop` y comprueba que confirma el cierre sin error.
4. Ejecuta `sudo ./manage.sh start`.
5. Vuelve a entrar y comprueba bloque, cofre e inventario.
6. Con el servidor funcionando, ejecuta `sudo reboot`.
7. Reconecta por SSH y comprueba `sudo ./manage.sh status` y `sudo ./manage.sh verify`.
8. Vuelve a entrar desde ambos clientes.

Si paras el servidor manualmente antes de reiniciar el host, `unless-stopped` respeta esa parada. Esa prueba debe terminar con el servicio parado hasta un `start` explícito.

## 8. Hacer y probar un backup

Antes de utilizar el mundo como definitivo:

1. Crea un bloque de referencia.
2. Ejecuta `sudo ./manage.sh backup` y conserva la ruta que imprime.
3. Entra, modifica ese bloque y sal.
4. Ejecuta `sudo ./manage.sh restore RUTA_DEL_BACKUP.tar.gz`.
5. Revisa la confirmación y escribe `RESTAURAR` si quieres sustituir el estado actual.
6. Entra y comprueba que volvió el estado del backup.

El procedimiento desconecta jugadores durante las paradas y restauraciones. Pruébalo en el mundo inicial, cuando esa interrupción y vuelta atrás sean aceptables.

Tras esta prueba tendrás evidencia de instalación, acceso cruzado, persistencia, reinicio y restauración **en tu hardware**. Apunta la fecha, versiones, clientes y cualquier fallo en una copia de `docs/VALIDACION.md`.
