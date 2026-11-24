# Correspondencia del protocolo BLE

REA identificó el APK de referencia como `com.husqvarna.automowerconnect` 2026.7.0, con SHA-256 `dbc6c755c4ea524abfa1c3ac25948a895933bc7f5b2ce0e2ac586108892960d1`. La inspección del manifiesto y las clases devolvió las evidencias `ev_95a99a5154c95933c549a9b103c4c3ffa00c5f8ffabd903a2cfa4203339555fd` y `ev_e6c4960c7434a87da56e9d4a6d56417986dc0ff674b1065bbf9cde851f0105c1`.

El codificador HCP usa identificadores de familia y operación de 16 bits en orden little endian. El decodificador diferencia respuestas, resultados y eventos. Véanse `ev_a272e5e1e99ea0f3e60b0dc3dc4c37157e76cf627daddcc941358d963ff8d02a`, `ev_a8b55ed61cbccfe4e89b1460a04993175b393645c837f88c580b3c4c4bf164f7` y `ev_0961234204dc1b9d4fad078821ea783c8a5d75403480542e2625f3e1f416986f`.

El APK contiene 166 archivos TIF de variantes de firmware. Un nombre de operación en esos archivos describe una posibilidad del protocolo. Su uso operativo también depende de la ruta de la app, del modelo, de los accesorios y de la respuesta del robot. Por ejemplo, `MowerCapabilities` configura límites y disponibilidad por modelo (`ev_3765da7b80a105739073277d6fc811659b28727ba9bfb0fe23172a1222095dfd`).

| Funciones | Identificador de familia HCP | Estado |
| --- | --- | --- |
| Batería y carga | 4106 | Lecturas implementadas |
| Actividad, estado, pausa e inicio | 4586 | Incluye inicio en área secundaria en modo manual |
| Próximo arranque, restricciones y anulaciones del horario | 4658 | Lecturas y acciones puntuales implementadas |
| Horario semanal | 4690 | Lectura, edición en transacción y verificación posterior |
| Identificación y versión de firmware | 4698 | Implementadas |
| Temporizador climático | 4460 | Ajustes con detección |
| Altura de corte y reducción automática | 4422 | Ajustes con detección |
| ECO y estación | 4692 | Ajustes de ECO y caseta con detección |
| Faros | 4722 | Ajustes con detección |
| Distancias de cable, marcha atrás y ángulos de salida | 4712, 4716 | Rangos por modelo o versión del nodo. Las operaciones de lectura confirman cada control |
| Puntos de partida y pasillos | 4706 | Habilitación, proporción, distancia, guía y corte en pasillos según modelo |
| Búsqueda de la estación | 4708 | Métodos, retardos y alcance con detección |
| Navegación asistida y obstáculos | 4732, 5356 | Ajustes con detección |
| Protección contra heladas | 5370, 5412 | Ajuste con detección de variantes |
| Estadísticas y desgaste de cuchillas | 4726 | Lecturas y reinicio del contador por perfil de firmware |
| Historial de avisos | 4730 | Lectura paginada |
| Corte localizado | 4710, 4658, 4586 | Inicio con detección de disponibilidad. Pendiente verificar variantes de firmware |
| Áreas de trabajo | 5604, 4658 | Lista, inicio de corte, cambio de nombre y habilitación según firmware |
| Perfiles de instalación | 5900, 5896, 5578 | Lista, identificación y cambio de nombre del perfil activo, e indicadores de mapas disponibles |
| Geometría del mapa | 5578, 5786 | Descarga directa del SVG en los firmwares que admiten `GetRaw`. Carga del mapa activo con comprobación de la revisión |
| Posición GPS | 5264 | Lectura del dispositivo y entidad de ubicación opcional |
| GeoFence | 4734 | Lectura de estado, activación, radio, sensibilidad y cambio de centro según firmware |
| Accesorios avanzados y actualización de firmware | Varias | Pendiente confirmar el alcance de las rutas BLE de la app |

Las entradas implementadas identifican código y evidencia estática. La verificación con un robot físico se registra por modelo y firmware cuando existan resultados.

La app lee la versión del nodo principal por BLE (`GetVersionString`, familia 96, operación 0) y busca el perfil correspondiente. REA localizó el envío en `ev_4ed95968cd8768f561ae97ffb22a93ce031875f446081a5a47c39feb89ac0f0a`, el receptor en `ev_b3916f62dbc81c6cbcbb5c2734e14bdde2beb156f5c2e8bc6be000d84ff226e5` y la resolución por familia en `ev_128d9d5f2a362e6f6ac9c7cbb0260fe668bdcd67a330a7065efbe99de16ebea5`. El constructor DEX confirma el envío enlazado con familia 96 y operación 0. Regenera el catálogo de rangos y operaciones a partir del APK con `tools/extract_firmware_profiles.py`. Para la identidad G3 `5.0`, la app apunta al perfil 31.7, con 150–3000 mm de marcha atrás y 45–315° de salida.

Para el área secundaria, la app valida una conexión Bluetooth, selecciona `MANUAL`, escribe la duración y envía `StartTrigger`. La duración `0` corresponde al modo manual hasta batería baja. REA confirmó las variantes sin límite, temporizada y por área en `ev_44df46e896eb5c23d7a045befff9dc6ab19994fd3f18f06e11d882a458166e00`, `ev_3dc2ecad36bca062908f4f2c62fdd951e40b025d4e966158bba0cc886157da85` y `ev_af8e361a634f46949d5c53ce1d224e164efe4602b6f5623875cb5d6f010cd98a`.

REA siguió el reinicio del contador de cuchillas hasta `MowerBluetoothDataSourceImpl` en `ev_395604821ec534bd4bdcc33a21f773fec59c26ae808e728d72846041db9bcb0b`. En los TIF de la familia 4726 aparecen los pares 7/8 y 13/14. La integración selecciona el par por firmware y muestra el botón después de leer su contador.

`SiteManager` resuelve la lista de instalaciones mediante `GetNumberOfSites` y `GetIdListEntry` (`ev_95e5f935eba43867cff0ead95dc1fb48605e8c1eb2841e11c0fdecca210cc6b0`). La integración confirma el ID activo antes de aplicar `Site.SetName` y comprueba el nombre y el ID después de la escritura. Los indicadores de `SiteMap` informan de la disponibilidad de los mapas.

La app lee por BLE la suma de comprobación del mapa activo y recupera la geometría de su caché o del servidor (`ev_399d1f0af547570ccf17d7f7c44029f5b1c9c088bdde53c040b777b92f10f935`). Los TIF declaran los atributos 100 y 101 de `SiteMap` como SVG legible por `NativeAttributes.GetRaw`. Para subir la geometría, la app serializa polígonos y líneas en SVG (`ev_21eefa8d76472a52a8195a83b1b8dfe0a0ebb72a1ffd47685c281b40d880a179`), usa GZIP y CRC32 para transferirla (`ev_692ad27c006213d64d6f040235177f16d22567c27db0fd6e353fbb43775abd33`, `ev_44753a99b47edacf59c23507baf3cbb41a8ada36fccaf4d21d0cbb1a8d36eacb`) y publica el MD5 del SVG tras el último fragmento (`ev_9b8c838b03440c9f763aa613bdcc68c0d1ea77e81b08fd8d2034f6f36f399b99`, `ev_5d487bd75f9af263575d117aee0cfb8fec03d28aa69233f9757475f5e7236bee`).

REA localizó el cambio del centro de GeoFence (`ev_b0392b33703ebe84b62c72825bf368ac0e5e0da6158561ce61363280d17d749e`) y los dos procedimientos de activación, con PIN (`ev_2e3391b8870b25a8f77ee77d58f20f3934a4f5c1662805c288e16170a9b8773c`) y con el comando que admite la sesión BLE existente (`ev_ea93b78641d83d26f468c86e91d66b764427336137556c2cf7086d8bf346d4c4`). El catálogo de TIF proporciona los límites del radio cuando el firmware los declara. Las coordenadas del centro se devuelven como enteros del firmware.
