# Automower BLE Plus

Integración de Home Assistant para cortacéspedes Husqvarna Automower que admiten el servicio Bluetooth LE de Automower Connect. Usa un adaptador Bluetooth de Home Assistant o un proxy ESPHome que admita conexiones activas.

## Instalación desde Marketplace

1. En Home Assistant 2026.11.0 o posterior, abre **Ajustes → Marketplace → Añadir desde enlace**. Añade `olerida/automower-ble-plus` como integración e instala **Automower BLE Plus**. [Abrir en Marketplace](https://my.home-assistant.io/redirect/hacs_repository/?owner=olerida&repository=automower-ble-plus&category=integration)
2. Si el mismo cortacésped está configurado en **Husqvarna Automower BLE**, deshabilita esa entrada. Así una única integración gestiona la conexión con el cortacésped.
3. En **Ajustes → Dispositivos y servicios**, configura el cortacésped descubierto o añade **Automower BLE Plus** con su dirección Bluetooth. Activa el modo de emparejamiento del cortacésped e introduce su PIN en el formulario si el modelo lo requiere.

## Funciones

Incluye el estado del cortacésped, inicio, pausa, aparcamiento, reanudación del horario y órdenes temporizadas. Las entidades de batería, estadísticas, modo ECO, temporizador climático, protección contra heladas, altura de corte, faros, instalación y otros ajustes aparecen si el firmware admite la función. La detección comienza después de la primera conexión.

En firmwares compatibles también aparecen la activación y la intensidad del corte en espiral, y los sensores de alarma antirrobo que el cortacésped puede reportar. Para editar los ajustes antirrobo configura el PIN de operador al añadir la integración.

- `get_schedule` lee los intervalos semanales y sus límites. `replace_schedule` guarda el nuevo horario y lo vuelve a leer. Cada intervalo usa `start` (hora local), `duration_minutes`, `weekdays` (`mon` a `sun`) y, si procede, `mission_id`.
- **Hora local del cortacésped** muestra el reloj que usa el horario de corte. **Sincronizar reloj del cortacésped** actualiza la hora por BLE con la zona del equipo si está configurada. En los demás casos usa la de Home Assistant. Si activas la sincronización automática y el robot tiene una zona horaria configurada, la integración corrige desviaciones de más de un minuto y cambios de horario.
- **Avería activa** muestra los fallos habituales en español o inglés. El botón **Confirmar avería** se habilita cuando el cortacésped permite confirmar el aviso actual. `get_message_history` devuelve una página del historial con los códigos originales, la gravedad y el identificador del aviso cuando se conoce.
- Si el firmware admite avisos nuevos por Bluetooth, la entidad **Avisos del cortacésped** se puede usar en automatizaciones. Cada evento incluye el código y la gravedad. [Entidades de evento de Home Assistant](https://contributors.home-assistant.io/core/entities/event/)
- `mow_secondary_area` inicia el modo de área secundaria. Si omites `minutes`, el cortacésped trabaja hasta que baja la batería.
- `get_work_areas` devuelve los nombres e identificadores de las áreas en modelos compatibles. Puedes pasar `mission_id` a `mow_for` para cortar en esa área o a `update_work_area` para cambiar su nombre o estado.
- `get_work_area_mowing` consulta el patrón, la dirección, el corte del perímetro y la altura de una zona. En equipos compatibles también incluye la asistencia visual, la detección de obstáculos y el corte solo con luz diurna. `set_work_area_mowing` cambia uno de los ajustes que aparecen en `editable`. La altura usa la unidad que indica la lectura, porcentaje o nivel del 1 al 9.
- `get_work_area_status` consulta el progreso del área actual o indicada. `reset_work_area_progress` reinicia el progreso guardado. En firmwares compatibles, `set_work_area_mowing` permite cambiar también la prioridad.
- `create_work_area` vincula una misión de corte a un polígono de trabajo existente. Usa el `id` del polígono y la suma de comprobación devueltos por `get_map`. Para eliminar una misión con `delete_work_area`, retira primero sus intervalos programados y proporciona su ID y nombre exacto.
- **Instalación activa** aparece cuando el cortacésped informa de varias instalaciones. `get_installations` devuelve los perfiles y el ID activo. Las acciones permiten activar, renombrar, copiar y, si el firmware lo admite, crear una instalación vacía. Para eliminar un perfil inactivo, indica su ID y su nombre exacto.
- `get_map` lee el SVG del mapa activo y su suma de comprobación en firmwares compatibles. `replace_map` carga un SVG con el formato de Automower. Pásale como `expected_checksum` el valor `checksum` que devolvió `get_map` para proteger la revisión que estás editando.
- `get_geofence` lee el estado de GeoFence. El interruptor, el radio, la sensibilidad y el botón para centrarla en la posición actual aparecen según las funciones y la versión del cortacésped. Los controles que requieren el PIN del cortacésped solo aparecen si lo configuraste en Home Assistant.
- **Emparejar estación de carga** crea una nueva señal de lazo cuando el cortacésped está en la estación y espera la confirmación del equipo. En firmwares que permiten diagnosticar el cable, `get_loop_signals` lee solo las señales de los lazos instalados e indica el sensor que tomó cada medida.
- En instalaciones con cable, puedes ajustar la anchura del pasillo del delimitador y de cada guía compatible. **Detección de FlexiFence** aparece en los cortacéspedes que admiten el accesorio. [Anchura de pasillo](https://www.husqvarna.com/uk/support/articles/how-to-adjust-the-corridor-width-in-a-boundary-wire-automower-installation-ka-01717/) · [FlexiFence](https://www.husqvarna.com/uk/support/articles/install-and-use-automower-flexi-fence-in-boundary-wire-installations-ka-70418/)
- **Probar punto de partida** y **Probar retorno** prueban los recorridos por cable que admite el robot. Para la salida, coloca el cortacésped en la base. Para el retorno, ponlo fuera de la base, a unos tres metros del cable y orientado hacia él. En el 420, pulsa **STOP** en el robot, prepara la prueba desde Home Assistant y pulsa **START** en el robot para ejecutarla. Puedes detener la prueba y guardar la última distancia medida como punto de partida.
- En modelos con EPOS, los sensores informan de la solución RTK, la calidad de la señal de corrección y el estado de instalación de la base según el firmware. **Asistencia EPOS por cable** aparece cuando el cortacésped admite complementar el posicionamiento por satélite con un cable físico.
- En equipos compatibles con FOTA, Home Assistant muestra el firmware del cortacésped o de la estación y permite buscar e instalar la actualización preparada por el equipo. La orden se envía por Bluetooth. El equipo descarga su firmware mediante la conectividad que admite su modelo. [Guía de firmware de Husqvarna](https://www.husqvarna.com/uk/support/how-to-update-your-automower-firmware-ka-01099/)
- Cuando el robot proporciona una posición válida, la entidad de ubicación se puede habilitar en Home Assistant.

El Automower 420 es el modelo de referencia. Las pruebas automatizadas reproducen el emparejamiento y la detección de funciones con un dispositivo simulado. El [inventario de BLE](docs/ble.md) recoge la cobertura y las funciones pendientes de comprobar.

Esta integración comunitaria utiliza [AutoMower-BLE](https://github.com/alistair23/AutoMower-BLE) para el emparejamiento y el establecimiento del canal Bluetooth.

El paquete de distribución se genera con `python3 tools/build_hacs_release.py`. Al subir una etiqueta de versión que coincida con el manifiesto, GitHub Actions publica el archivo que descargan Marketplace y HACS después de superar las pruebas y Hassfest. [Documentación de Marketplace](https://www.home-assistant.io/integrations/marketplace/)
