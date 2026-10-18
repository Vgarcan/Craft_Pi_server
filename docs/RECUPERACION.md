# Recuperación y operaciones interrumpidas

## Índice

1. Información que conservar
2. Instalación inicial incompleta
3. Actualización o restauración incompleta
4. Cierre anómalo y falta de guardado
5. Pérdida de la microSD
6. Cambios de IP y espacio

## 1. Información que conservar

Conserva el proyecto de la misma versión, el backup `.tar.gz`, su `.sha256`, los logs de la operación y los metadatos locales. No publiques claves, contraseñas, diagnósticos completos ni mundos.

No ejecutes `docker compose down -v`, limpiezas generales de Docker o borrados de `data/` como paso de diagnóstico. Las copias y los bind mounts están fuera del contenedor, pero una limpieza manual del directorio sí puede destruirlos.

La administración se hace con `manage.sh`. Llamadas directas a Docker omiten parte de las comprobaciones y bloqueos del proyecto.

## 2. Instalación inicial incompleta

El instalador conserva datos/configuración ya creados. Si no existe `state/operation.json`, consulta logs y repite `install.sh` con las mismas opciones. Un estado activo no cambia de builds al repetirlo.

`state/installing.json` identifica un intento gestionado anterior. No lo crees para forzar la importación de un mundo ajeno: esa ruta no implementa importación ni conversión de servidores existentes.

Si cambiaste la EULA a `false`, se conserva esa decisión. Para volver a aceptarla revisa y edita su archivo explícitamente antes de iniciar; el script no reescribe una EULA existente.

## 3. Actualización o restauración incompleta

Comprueba:

```bash
sudo ./manage.sh status
sudo cat state/operation.json
sudo ./manage.sh logs --tail 200
```

La marca puede incluir la ruta del snapshot `pre-update`. Una actualización modifica archivos del mundo al arrancar; por eso no hay rollback automático sólo de binarios.

Restaura una copia anterior completa:

```bash
sudo ./manage.sh restore backups/ARCHIVO_ANTERIOR.tar.gz
```

Confirma con `RESTAURAR`. Se valida antes de sustituir datos. Las operaciones incompletas o un cierre no comprobado conservan el directorio anterior en `state/pre-restore-...` para revisión.

Durante el primer arranque de una actualización/restauración se aplica temporalmente `restart: "no"` mediante un override de Compose. La marca se mantiene hasta verificar el servidor. Sólo después se restablece `unless-stopped`. Si se interrumpe en esa fase, la nueva instancia no queda programada para volver a abrir el mundo automáticamente después de un reinicio del host. Puede seguir en marcha hasta que la detengas si el proceso administrativo se interrumpió mientras la JVM continuaba viva.

No borres `operation.json` para saltarte la recuperación: hacerlo elimina el bloqueo sin demostrar coherencia. Si un especialista decide una reparación manual distinta, debe revisar contenedor, datos, `.env`, `state/active.json`, JARs y protocolo, y documentar qué estado está aceptando.

Si todavía queda el proceso anterior vivo, no pueden ejecutarse dos operaciones de escritura a la vez: el bloqueo se libera al finalizar ese proceso. Consultar logs en otra sesión no impide administrar el servicio.

## 4. Cierre anómalo y falta de guardado

Después de un OOM, una salida forzada o un corte eléctrico, los archivos existentes no equivalen a un snapshot consistente. Una restauración confirmada puede conservarlos como datos de recuperación y volver a una copia anterior verificada.

Si `stop` no encuentra la evidencia de guardado esperada (mensaje vanilla o secuencia Moonrise; ver README, sección 8), no presenta el siguiente backup como consistente. Verifica si Paper cambió su formato de log o si realmente falló el guardado. El validador admite salida JVM 143 sólo cuando hay evidencia de guardado completo y no hubo OOM.

Tras un corte eléctrico, el procedimiento no puede garantizar la integridad física de la microSD ni reparar por sí mismo todos los formatos de región. Conserva los archivos originales antes de intentar reparaciones externas.

## 5. Pérdida de la microSD

Necesitas una copia guardada **fuera** de la tarjeta perdida y el proyecto en la misma versión o en una posterior. Desde la 0.2.0, `restore` acepta backups de plantillas gestionadas anteriores y construye la imagen del proyecto actual.

1. Reinstala el OS siguiendo la guía desde cero.
2. Transfiere el proyecto y el backup con su `.sha256`.
3. Instala y comprueba Docker/Compose con el instalador. En esta ruta sencilla se crea primero una instalación nueva de prueba; la restauración sustituirá después ese estado, con confirmación.
4. Para el servidor de prueba.
5. Copia el backup a una carpeta privada accesible al administrador y ejecuta `manage.sh restore RUTA`.
6. Si la IP guardada en el backup no está asignada a este host, utiliza desde el principio `manage.sh restore RUTA --lan-ip NUEVA_IP_LOCAL`. Si ya falló el intento por esa IP, conserva la marca y repite la restauración con esa opción.
7. Verifica y entra desde ambos clientes.

Los backups incluyen los JARs, pero no una exportación de la imagen Docker. Si la imagen fijada ya no está localmente, puede necesitarse Internet para reconstruirla desde el digest guardado y los archivos Docker originales. No se garantiza disponibilidad perpetua de repositorios externos.

Para una recuperación sin crear primero un mundo de prueba, un administrador puede preparar Docker/Compose/Buildx mediante el procedimiento oficial y usar `manage.sh restore` sobre una copia nueva de este proyecto. Se rechazan contenedores ya activos del mismo proyecto sin metadatos locales, para no modificar un servidor desconocido.

## 6. Cambios de IP y espacio

Una instalación activa sin operación pendiente admite:

```bash
sudo ./manage.sh rebind NUEVA_IP_LOCAL
```

La IP debe existir realmente en el host. Se pide `RECONFIGURAR` antes del cambio y no se modifica el router.

Si vas a restaurar un backup en otro host con otra IP, utiliza la opción `restore --lan-ip NUEVA_IP_LOCAL` documentada en la ayuda del comando. El cambio se valida antes de arrancar; se conservan las versiones y los datos de la copia.

No hay purga automática de backups, releases ni carpetas `pre-restore`. Tras verificar manualmente una recuperación y conservar una copia externa, puedes revisar qué intentos antiguos ya no necesitas. Nunca elimines la release referenciada en `state/active.json` mientras se esté usando.

Si el backup falla por espacio, el mundo no se sustituye. `backup` intenta recuperar el estado previo de marcha/parada; una restauración que se detuvo antes de completar el intercambio puede requerir un `start` explícito después de resolver el problema y comprobar que no hay marca pendiente.
