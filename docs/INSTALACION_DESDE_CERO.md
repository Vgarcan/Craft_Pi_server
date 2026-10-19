# Instalación desde cero

## Índice

1. Preparar hardware y tarjeta
2. Instalar Raspberry Pi OS Lite de 64 bits
3. Primer acceso por SSH y ajustes del sistema
4. Transferir y preparar el proyecto
5. Comprobar e instalar el servidor
6. Instalar el panel web
7. Primeros jugadores
8. Probar desde ambos clientes
9. Verificar persistencia y reinicio
10. Hacer y probar un backup

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

## 3. Primer acceso por SSH y ajustes del sistema

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

### Activar el límite de memoria (recomendado, antes de instalar)

Raspberry Pi OS arranca con el cgroup de memoria desactivado: sin él, Docker no aplica el límite de 3 GiB del contenedor. Añade `cgroup_enable=memory` al final de la única línea de `cmdline.txt`, conservando una copia:

```bash
sudo cp /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.bak
grep -q cgroup_enable=memory /boot/firmware/cmdline.txt || \
  sudo sed -i '1 s/$/ cgroup_enable=memory/' /boot/firmware/cmdline.txt
```

Si instalaste la versión con escritorio y no lo necesitas, arrancar sin él deja casi 1 GiB más para el servidor:

```bash
sudo systemctl set-default multi-user.target    # para volver: graphical.target
```

Reinicia (`sudo reboot`), vuelve a entrar por SSH y comprueba que `grep -w memory /sys/fs/cgroup/cgroup.controllers` muestra `memory`. Para deshacer el primer cambio, restaura `cmdline.txt.bak`.

## 4. Transferir y preparar el proyecto

### Descargar directamente en la Pi con Git

La opción recomendada evita transferir archivos desde tu ordenador:

```bash
sudo apt-get install -y git
sudo git clone https://github.com/Vgarcan/Craft_Pi_server.git /opt/minecraft-lan-installer
cd /opt/minecraft-lan-installer
sudo chmod +x install.sh manage.sh tests/run.sh docker/entrypoint.sh panel/install.sh tools/audit.sh
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
sudo chmod +x install.sh manage.sh tests/run.sh docker/entrypoint.sh panel/install.sh tools/audit.sh
sha256sum -c --quiet MANIFEST.sha256 && echo 'Archivos íntegros'
```

`MANIFEST.sha256` contiene el SHA-256 de cada archivo: si alguno no coincide, vuelve a descargar el ZIP.

## 5. Comprobar e instalar el servidor

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

## 6. Instalar el panel web

```bash
sudo panel/install.sh
```

Crea el usuario sin privilegios `mclan-panel`, el certificado para la IP, la regla sudo limitada al helper y el servicio, y extrae las texturas y el catálogo de objetos del cliente de Minecraft que descarga BlueMap. Si BlueMap aún no lo ha descargado (es normal en los primeros minutos), el script espera hasta 10 minutos. Si aun así avisa de que faltan, el panel funciona igual: repite `sudo panel/install.sh` un rato después.

Anota la huella SHA-256 que imprime. Abre `https://IP_DE_LA_PI:8443/` desde otro equipo de tu red, comprueba que el navegador muestra esa misma huella y acepta el certificado autofirmado. La contraseña inicial está en:

```bash
sudo cat /etc/mclan-panel/data/initial-password
```

Cámbiala en **Account** nada más entrar.

## 7. Primeros jugadores

La whitelist está activa: nadie entra hasta que lo añadas. En el panel, **Players → Add a player**:

- Java: el nombre de la cuenta.
- Bedrock: el gamertag sin espacios. Si el panel avisa de que Floodgate no lo conoce, pide al jugador que intente entrar una vez y pulsa **Open 5 min and wait** en su intento rechazado; o añádelo por su XUID.

Después abre tu propio perfil y usa **Actions → Admin → Make operator**. Opcionalmente, pregenera el mundo y fija su borde como indica el README (sección 3.6) para que el juego y el mapa vayan fluidos.

Comprueba todo con la auditoría:

```bash
sudo tools/audit.sh --verify
```

Debe terminar con «Resultado: sin fallos».

## 8. Probar desde ambos clientes

Usa un Java de la **versión Minecraft indicada por el instalador**, y un Bedrock del rango soportado. No compares el número Java con el de Bedrock como si fueran la misma serie de versiones.

| Cliente | Dirección |
| --- | --- |
| Minecraft Java | `IP_DE_LA_PI:25565` |
| Minecraft Bedrock | Dirección `IP_DE_LA_PI`, puerto `19132` |

Entra desde ambos, comprueba que ven el mismo mundo y que pueden interactuar con el mismo bloque. Si Bedrock no permite añadir un servidor externo, prueba inicialmente con otro dispositivo compatible; las restricciones de consola no las elimina Docker.

La autenticación y el protocolo pueden necesitar conectividad exterior aun tratándose de un servidor alojado en LAN. No cambies `online-mode=false` para sortear un problema de autenticación.

## 9. Verificar persistencia y reinicio

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

## 10. Hacer y probar un backup

Antes de utilizar el mundo como definitivo:

1. Crea un bloque de referencia.
2. Ejecuta `sudo ./manage.sh backup` y conserva la ruta que imprime.
3. Entra, modifica ese bloque y sal.
4. Ejecuta `sudo ./manage.sh restore RUTA_DEL_BACKUP.tar.gz`.
5. Revisa la confirmación y escribe `RESTAURAR` si quieres sustituir el estado actual.
6. Entra y comprueba que volvió el estado del backup.

El procedimiento desconecta jugadores durante las paradas y restauraciones. Pruébalo en el mundo inicial, cuando esa interrupción y vuelta atrás sean aceptables.

Tras esta prueba tendrás evidencia de instalación, acceso cruzado, persistencia, reinicio y restauración **en tu hardware**. Apunta la fecha, versiones, clientes y cualquier fallo en una copia de `docs/VALIDACION.md`.
