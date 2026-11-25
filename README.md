# Automower BLE Plus

Integración de Home Assistant para cortacéspedes Husqvarna Automower que admiten el servicio Bluetooth LE de Automower Connect. Usa un adaptador Bluetooth de Home Assistant o un proxy ESPHome que admita conexiones activas.

## Instalación mediante HACS

1. Una vez publicado, añade `olerida/automower-ble-plus` en HACS como repositorio personalizado de tipo **Integración** y descarga **Automower BLE Plus**. Requiere Home Assistant 2026.11.0 o posterior.
2. Si el mismo cortacésped está configurado en **Husqvarna Automower BLE**, deshabilita esa entrada. Así una única integración gestiona la conexión con el cortacésped.
3. En **Ajustes → Dispositivos y servicios**, configura el cortacésped descubierto o añade **Automower BLE Plus** con su dirección Bluetooth. Activa el modo de emparejamiento del cortacésped e introduce su PIN en el formulario si el modelo lo requiere.

## Funciones

Incluye el estado del cortacésped, inicio, pausa, aparcamiento, reanudación del horario y órdenes temporizadas. Las entidades de batería, estadísticas, modo ECO, temporizador climático, protección contra heladas, altura de corte, faros, instalación y otros ajustes aparecen si el firmware admite la función. La detección comienza después de la primera conexión.

En firmwares compatibles también aparecen la activación y la intensidad del corte en espiral, y los sensores de alarma antirrobo que el cortacésped puede reportar. Para editar los ajustes antirrobo configura el PIN de operador al añadir la integración.

- `get_schedule` lee los intervalos semanales y sus límites. `replace_schedule` guarda el nuevo horario y lo vuelve a leer. Cada intervalo usa `start` (hora local), `duration_minutes`, `weekdays` (`mon` a `sun`) y, si procede, `mission_id`.
- **Avería activa** muestra los fallos habituales en español o inglés. El botón **Confirmar avería** se habilita cuando el cortacésped permite confirmar el aviso actual. `get_message_history` devuelve una página del historial con los códigos originales, la gravedad y el identificador del aviso cuando se conoce.
- Si el firmware admite avisos nuevos por Bluetooth, la entidad **Avisos del cortacésped** se puede usar en automatizaciones. Cada evento incluye el código y la gravedad. [Entidades de evento de Home Assistant](https://contributors.home-assistant.io/core/entities/event/)
- `mow_secondary_area` inicia el modo de área secundaria. Si omites `minutes`, el cortacésped trabaja hasta que baja la batería.
- `get_work_areas` devuelve los nombres e identificadores de las áreas en modelos compatibles. Puedes pasar `mission_id` a `mow_for` para cortar en esa área o a `update_work_area` para cambiar su nombre o estado.
- `get_work_area_mowing` consulta el patrón, la dirección, el corte del perímetro y la altura de una zona. En equipos compatibles también incluye la asistencia visual, la detección de obstáculos y el corte solo con luz diurna. `set_work_area_mowing` cambia uno de los ajustes que aparecen en `editable`. La altura usa la unidad que indica la lectura, porcentaje o nivel del 1 al 9.
- **Instalación activa** aparece cuando el cortacésped informa de varias instalaciones. `get_installations` devuelve los perfiles y el ID activo. Las acciones permiten activar, renombrar, copiar y, si el firmware lo admite, crear una instalación vacía. Para eliminar un perfil inactivo, indica su ID y su nombre exacto.
- `get_map` lee el SVG del mapa activo y su suma de comprobación en firmwares compatibles. `replace_map` carga un SVG con el formato de Automower. Pásale como `expected_checksum` el valor `checksum` que devolvió `get_map` para proteger la revisión que estás editando.
- `get_geofence` lee el estado de GeoFence. El interruptor, el radio, la sensibilidad y el botón para centrarla en la posición actual aparecen según las funciones y la versión del cortacésped. Los controles que requieren el PIN del cortacésped solo aparecen si lo configuraste en Home Assistant.
- **Emparejar estación de carga** crea una nueva señal de lazo cuando el cortacésped está en la estación y espera la confirmación del equipo. En firmwares que permiten diagnosticar el cable, `get_loop_signals` lee solo las señales de los lazos instalados e indica el sensor que tomó cada medida.
- En equipos compatibles con FOTA, Home Assistant muestra el firmware del cortacésped o de la estación y permite buscar e instalar la actualización preparada por el equipo. La orden se envía por Bluetooth. El equipo descarga su firmware mediante la conectividad que admite su modelo. [Guía de firmware de Husqvarna](https://www.husqvarna.com/uk/support/how-to-update-your-automower-firmware-ka-01099/)
- Cuando el robot proporciona una posición válida, la entidad de ubicación se puede habilitar en Home Assistant.

El Automower 420 es el modelo de referencia. Las pruebas automatizadas reproducen el emparejamiento y la detección de funciones con un dispositivo simulado. El [inventario de BLE](docs/ble.md) recoge la cobertura y las funciones pendientes de comprobar.

Esta integración comunitaria utiliza [AutoMower-BLE](https://github.com/alistair23/AutoMower-BLE) para el emparejamiento y el establecimiento del canal Bluetooth.
