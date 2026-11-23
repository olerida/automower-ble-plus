# Automower BLE Plus

Integración de Home Assistant para cortacéspedes Husqvarna Automower que admiten el servicio Bluetooth LE de Automower Connect. Usa un adaptador Bluetooth de Home Assistant o un proxy ESPHome que admita conexiones activas.

## Instalación mediante HACS

1. Una vez publicado, añade `olerida/automower-ble-plus` en HACS como repositorio personalizado de tipo **Integración** y descarga **Automower BLE Plus**. Requiere Home Assistant 2026.11.0 o posterior.
2. Si el mismo cortacésped está configurado en **Husqvarna Automower BLE**, deshabilita esa entrada. Así una única integración gestiona la conexión con el cortacésped.
3. En **Ajustes → Dispositivos y servicios**, configura el cortacésped descubierto o añade **Automower BLE Plus** con su dirección Bluetooth. Activa el modo de emparejamiento del cortacésped e introduce su PIN en el formulario si el modelo lo requiere.

## Funciones

Incluye el estado del cortacésped, inicio, pausa, aparcamiento, reanudación del horario y órdenes temporizadas. Las entidades de batería, estadísticas, modo ECO, temporizador climático, protección contra heladas, altura de corte, faros, instalación y otros ajustes aparecen si el firmware admite la función. La detección comienza después de la primera conexión.

- `get_schedule` lee los intervalos semanales y sus límites. `replace_schedule` guarda el nuevo horario y lo vuelve a leer. Cada intervalo usa `start` (hora local), `duration_minutes`, `weekdays` (`mon` a `sun`) y, si procede, `mission_id`.
- `get_message_history` devuelve una página de avisos del cortacésped.
- `mow_secondary_area` inicia el modo de área secundaria. Si omites `minutes`, el cortacésped trabaja hasta que baja la batería.
- `get_work_areas` devuelve los nombres e identificadores de las áreas en modelos compatibles. Puedes pasar `mission_id` a `mow_for` para cortar en esa área o a `update_work_area` para cambiar su nombre o estado.
- `get_installations` devuelve los perfiles y el ID activo en modelos compatibles. `rename_installation` cambia el nombre del perfil activo tras comprobar el ID.
- `get_map` lee el SVG del mapa activo y su suma de comprobación en firmwares compatibles. `replace_map` carga un SVG con el formato de Automower. Pásale como `expected_checksum` el valor `checksum` que devolvió `get_map` para proteger la revisión que estás editando.
- Cuando el robot proporciona una posición válida, la entidad de ubicación se puede habilitar en Home Assistant.

El Automower 420 es el modelo de referencia. Las pruebas automatizadas reproducen el emparejamiento y la detección de funciones con un dispositivo simulado. El [inventario de BLE](docs/ble.md) recoge la cobertura y las funciones pendientes de comprobar.

Esta integración comunitaria utiliza [AutoMower-BLE](https://github.com/alistair23/AutoMower-BLE) para el emparejamiento y el establecimiento del canal Bluetooth.
