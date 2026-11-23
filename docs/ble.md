# Correspondencia del protocolo BLE

REA identificó el APK de referencia como `com.husqvarna.automowerconnect` 2026.7.0, con SHA-256 `dbc6c755c4ea524abfa1c3ac25948a895933bc7f5b2ce0e2ac586108892960d1`. La inspección del manifiesto y las clases devolvió las evidencias `ev_95a99a5154c95933c549a9b103c4c3ffa00c5f8ffabd903a2cfa4203339555fd` y `ev_e6c4960c7434a87da56e9d4a6d56417986dc0ff674b1065bbf9cde851f0105c1`.

El codificador HCP usa identificadores de familia y operación de 16 bits en orden little endian. El decodificador diferencia respuestas, resultados y eventos. Véanse `ev_a272e5e1e99ea0f3e60b0dc3dc4c37157e76cf627daddcc941358d963ff8d02a`, `ev_a8b55ed61cbccfe4e89b1460a04993175b393645c837f88c580b3c4c4bf164f7` y `ev_0961234204dc1b9d4fad078821ea783c8a5d75403480542e2625f3e1f416986f`.

El APK contiene 166 archivos TIF de variantes de firmware. Un nombre de operación en esos archivos describe una posibilidad del protocolo. Su uso operativo también depende de la ruta de la app, del modelo, de los accesorios y de la respuesta del robot. Por ejemplo, `MowerCapabilities` configura límites y disponibilidad por modelo (`ev_3765da7b80a105739073277d6fc811659b28727ba9bfb0fe23172a1222095dfd`).

| Funciones | Identificador de familia HCP | Estado |
| --- | --- | --- |
| Batería y carga | 4106 | Lecturas implementadas |
| Actividad, estado, pausa e inicio | 4586 | Implementadas |
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
| Estadísticas y desgaste de cuchillas | 4726 | Lecturas implementadas |
| Historial de avisos | 4730 | Lectura paginada |
| Corte localizado | 4710, 4658, 4586 | Inicio con detección de disponibilidad. Pendiente verificar variantes de firmware |
| Áreas de trabajo | 5604, 4658 | Lista, inicio de corte, cambio de nombre y habilitación según firmware. Geometría pendiente |
| Posición GPS | 5264 | Lectura del dispositivo y entidad de ubicación opcional |
| Accesorios avanzados, GeoFence, perfiles y actualización de firmware | Varias | Pendiente confirmar el alcance de las rutas BLE de la app |

Las entradas implementadas identifican código y evidencia estática. La verificación con un robot físico se registra por modelo y firmware cuando existan resultados.

La app lee la versión del nodo principal por BLE (`GetVersionString`, familia 96, operación 0) y busca el perfil correspondiente. REA localizó el envío en `ev_4ed95968cd8768f561ae97ffb22a93ce031875f446081a5a47c39feb89ac0f0a`, el receptor en `ev_b3916f62dbc81c6cbcbb5c2734e14bdde2beb156f5c2e8bc6be000d84ff226e5` y la resolución por familia en `ev_128d9d5f2a362e6f6ac9c7cbb0260fe668bdcd67a330a7065efbe99de16ebea5`. El constructor DEX confirma el envío enlazado con familia 96 y operación 0. El catálogo de la integración contiene los rangos numéricos y permite regenerarlos a partir del APK con `tools/extract_installation_profiles.py`. Para la identidad G3 `5.0`, la app apunta al perfil 31.7, con 150–3000 mm de marcha atrás y 45–315° de salida.
