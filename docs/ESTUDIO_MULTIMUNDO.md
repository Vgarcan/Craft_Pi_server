# Estudio: varios mundos con puertos propios

Estudio del 18-10-2026, previo a cualquier cambio. **No se ha implementado nada**: el servidor actual sigue igual.

## 1. Qué se pide

- Crear mundos nuevos, importar mundos existentes (Java o Bedrock) y exportarlos.
- Que cada mundo tenga su dirección propia en un rango de puertos reservado, por ejemplo 9900–9999.
- Sin romper la instalación actual ni sus garantías:
  - un único responsable por mundo;
  - backups verificados;
  - acceso sólo por LAN;
  - versiones fijadas.

## 2. Límites que mandan

| Recurso | Dato | Consecuencia |
| --- | --- | --- |
| RAM | 4 GiB en total. El servidor actual usa de 1,0 a 2,4 GiB (heap de 2 GiB más memoria nativa). Hoy quedan 1,68 GiB libres con la sesión de administración abierta | **Pueden funcionar a la vez 1 mundo grande o 2 pequeños** (heap de 768 MiB a 1 GiB cada uno). 99 mundos simultáneos no son posibles |
| Disco | 100 GiB libres. Un mundo pregenerado de 3200 × 3200 ocupa cientos de MiB, más su mapa | Guardar decenas de mundos sí es viable |
| CPU | 4 núcleos. Pregenerar uno ocupa 25 min a 70 °C | Generación y render, de uno en uno |

Conclusión: el modelo realista es una **biblioteca de mundos**, con muchos mundos guardados y uno o dos encendidos, no 99 servidores en marcha.

## 3. Puertos

Cada mundo encendido necesita cuatro puertos:

- Java (TCP);
- Bedrock RakNet (UDP);
- signaling de NetherNet (TCP; Geyser no admite el mismo número que Java);
- WebRTC (UDP).

Además, el mapa, si se quiere uno por mundo.

**Propuesta: tres rangos con el mismo sufijo**, fáciles de recordar:

| Mundo N (01–99) | Java TCP | Bedrock UDP + TCP | WebRTC UDP | Mapa (opcional) |
| --- | --- | --- | --- | --- |
| Ejemplo: mundo 07 | 9907 | 19907 | 29907 | 8107 |
| Actual («main») | 25565 | 19132 | 19133 | 8100 |

- Los puertos sólo se publican **mientras el mundo está encendido** y sólo en la IP LAN, igual que ahora. No hace falta «dejar abiertos» 300 puertos: Docker crea un proceso `docker-proxy` por puerto publicado, y tenerlos todos siempre abiertos costaría memoria sin dar nada a cambio.
- Nada se abre en el router.
- Bedrock en móvil y PC admite puertos personalizados al añadir servidor. En consolas ya es limitado con el puerto por defecto.
- El mundo actual conserva sus puertos de siempre: nadie tiene que cambiar de dirección.

## 4. Arquitecturas posibles

| Opción | Cómo funciona | A favor | En contra | Veredicto |
| --- | --- | --- | --- | --- |
| **A. Un Paper con Multiverse** | Varios mundos dentro del mismo servidor; se cambia con un comando o un portal | La que menos RAM gasta; un solo puerto; BlueMap ve todos los mundos | **Multiverse-Core 5.8.1 no declara Paper 26.3** (sólo hasta 26.2). No da un puerto por mundo. Ajustes y whitelist compartidos; inventarios compartidos salvo Multiverse-Inventories | Hoy no es posible; reevaluar cuando declare 26.3 |
| **B. Un contenedor por mundo** | Cada mundo es una instancia propia (Paper, Geyser, Floodgate) con sus puertos, datos, backups y ajustes | Lo que se pide. Aislamiento total. Cada mundo sigue teniendo un único responsable. Reutiliza todo el código actual | Cada mundo encendido es un proceso Java completo; sólo caben 1 o 2 a la vez | **Recomendada** |
| C. Proxy Velocity y varios backends | Una sola dirección; los jugadores cambian con `/server` | Una sola dirección para todos | Proxy y cada backend consumen RAM a la vez; reenvío de identidad, Geyser en el proxy… demasiado para 4 GiB | Descartada en esta Pi |

## 5. Diseño propuesto (opción B)

**Instancias.** El mundo actual pasa a ser la instancia `main` con sus rutas de siempre (`data/`, `state/`, `backups/`), sin migración. Los nuevos mundos viven en `worlds/<id>/` con:

- su propio `data/`, `state/active.json` y backups;
- su proyecto Compose `minecraft-lan-<id>` y sus puertos.

Todos comparten las releases fijadas y la imagen.

**Operaciones** (CLI primero y después panel, sección «World library»):

| Operación | Detalle |
| --- | --- |
| Crear | Nombre, semilla opcional, dificultad y modo. Opcionalmente pregenerar un radio y fijar el borde, como en el mundo actual |
| Importar Java | `.zip` o carpeta de un mundo Java, con extracción segura (sin rutas, enlaces ni tamaños anómalos), prueba aislada y activación |
| Importar Bedrock | El procedimiento ya ensayado: Chunker en un contenedor sin red, recálculo de luz, prueba aislada y activación |
| Exportar | Backup verificado del mundo, o un `.zip` Java descargable. Opcionalmente un `.mcworld` con Chunker en sentido Java → Bedrock |
| Encender o apagar | Con presupuesto de memoria: no se enciende un mundo si no hay RAM para su heap; el panel lo explica |
| Borrar | Sólo con un backup verificado previo y confirmación escrita |

**Panel.** Selector de mundo en la cabecera. Todas las páginas (Dashboard, Players, Console…) actúan sobre el mundo elegido. En la biblioteca, cada mundo es una tarjeta con su estado, sus puertos y botones.

**Subidas desde la web.** Hoy el panel acepta cuerpos de 16 KiB por seguridad. Importar desde el navegador necesita un endpoint de subida en streaming con su propio límite (por ejemplo 2 GiB), espacio comprobado y escritura fuera de `data/`. Alternativa más simple para empezar: importar desde una carpeta o un USB de la Pi.

## 6. Plan por fases sin romper nada

| Fase | Cambio | Riesgo para el mundo actual |
| --- | --- | --- |
| M1 | `manage.sh` y el runtime aceptan `--world ID`, con `main` por defecto. Las pruebas actuales siguen pasando sin cambios | Ninguno si `main` mantiene rutas y nombre de proyecto: se comprueba con las mismas pruebas y la auditoría |
| M2 | Crear, encender, apagar y backup de mundos nuevos por CLI, con el plan de puertos | Ninguno: otros directorios y otros puertos |
| M3 | Importar y exportar (Java, Bedrock, backup) por CLI | Ninguno |
| M4 | Panel: selector y biblioteca de mundos | Bajo: la interfaz cambia, la lógica no |
| M5 | BlueMap por mundo (puertos 81NN) y presupuesto de memoria | Bajo |

Cada fase se ensaya en la Pi con copias, como hasta ahora, antes de pasar a la siguiente.

## 7. Decisiones pendientes del operador

1. ¿Cuántos mundos encendidos a la vez: 1 (el resto apagados) o 2 pequeños?
2. ¿Whitelist y operadores compartidos entre mundos o propios de cada uno?
3. ¿Mapa por mundo (más puertos y CPU) o sólo para el mundo principal?
4. ¿Importar desde el navegador o basta con una carpeta o USB en la Pi?
5. ¿Plan de puertos de la sección 3 (9900/19900/29900) o prefieres otro rango?
