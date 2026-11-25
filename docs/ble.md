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
| Emparejamiento de estación y diagnóstico de lazos | 4692, 4462 | Emparejamiento con el robot en la estación y confirmación por evento. Lectura de los lazos instalados en los firmwares compatibles |
| Faros | 4722 | Ajustes con detección |
| Distancias de cable, marcha atrás y ángulos de salida | 4712, 4716 | Rangos por modelo o versión del nodo. Las operaciones de lectura confirman cada control |
| Puntos de partida y pasillos | 4706 | Habilitación, proporción, distancia, asignación de guía, anchura del pasillo delimitador y de cada guía compatible, y corte en pasillos según modelo |
| Búsqueda de la estación | 4708 | Métodos, retardos y alcance con detección |
| Navegación asistida y obstáculos | 4732, 5356 | Ajustes con detección |
| Corte de bordes y ultrasonidos | 6690, 4728 | Activación cuando el cortacésped anuncia cada función |
| Detección FlexiFence | 5926, 6050 | Interruptor según disponibilidad del accesorio en el cortacésped |
| Protección contra heladas | 5370, 5412 | Ajuste con detección de variantes |
| Estadísticas y desgaste de cuchillas | 4726 | Lecturas y reinicio del contador por perfil de firmware |
| Avisos y averías | 4730, 4586 | Historial paginado con códigos y niveles de gravedad. Avería actual y confirmación si el cortacésped la permite |
| Notificaciones BLE | 4586, 4730 | Actualización del estado, actividad y averías al recibir notificaciones. Entidad de evento si el firmware admite la suscripción a nuevos avisos |
| Corte en espiral y corte localizado | 4710, 4658, 4586 | Activación e intensidad del corte en espiral. Inicio manual de corte localizado cuando el perfil declara esa orden |
| Áreas de trabajo | 5604, 4658 | Lista, creación vinculada al mapa, borrado de misión, inicio, cambio de nombre, progreso y habilitación según firmware |
| Ajustes de corte por área | 5582, 5604 | Patrón, dirección, variación, perímetro, altura y prioridad según área y firmware |
| Perfiles de instalación | 5900, 5896, 5578 | Selección, cambio de nombre, copia, creación y borrado según firmware, e indicadores de mapas disponibles |
| Geometría del mapa | 5578, 5786 | Descarga directa del SVG en los firmwares que admiten `GetRaw`. Carga del mapa activo con comprobación de la revisión |
| Posición GPS | 5264 | Lectura del dispositivo y entidad de ubicación opcional |
| GeoFence | 4734 | Lectura de estado, activación, radio, sensibilidad y cambio de centro según firmware |
| Protección antirrobo | 4736 | Ajustes con sesión de operador y sensores de alarma según modelo y firmware |
| Actualización de firmware | 5296, 6244, 4698, 6552 | Entidades del cortacésped y la estación con detección, búsqueda de actualizaciones e instalación de la versión descargada |
| Asistencia visual y luz diurna | 6170, 7276, 7338, 7414, 5472, 5604 | Detección de bordes, posicionamiento visual, perfiles de obstáculos, estado del accesorio y ajustes por área según disponibilidad |
| Diagnóstico EPOS y asistencia por cable | 6170, 5336, 5650, 6778, 5242, 4692 | Estado y resultado de instalación de la base, solución RTK, señal de corrección, distancia y enlace de la estación de referencia, acoplamiento por cable y ajuste de asistencia por cable según disponibilidad |

Las entradas implementadas identifican código y evidencia estática. La verificación con un robot físico se registra por modelo y firmware cuando existan resultados.

La app lee la versión del nodo principal por BLE (`GetVersionString`, familia 96, operación 0) y busca el perfil correspondiente. REA localizó el envío en `ev_4ed95968cd8768f561ae97ffb22a93ce031875f446081a5a47c39feb89ac0f0a`, el receptor en `ev_b3916f62dbc81c6cbcbb5c2734e14bdde2beb156f5c2e8bc6be000d84ff226e5` y la resolución por familia en `ev_128d9d5f2a362e6f6ac9c7cbb0260fe668bdcd67a330a7065efbe99de16ebea5`. El constructor DEX confirma el envío enlazado con familia 96 y operación 0. Regenera el catálogo de rangos y operaciones a partir del APK con `tools/extract_firmware_profiles.py`. Para la identidad G3 `5.0`, la app apunta al perfil 31.7, con 150–3000 mm de marcha atrás y 45–315° de salida.

Para el área secundaria, la app valida una conexión Bluetooth, selecciona `MANUAL`, escribe la duración y envía `StartTrigger`. La duración `0` corresponde al modo manual hasta batería baja. REA confirmó las variantes sin límite, temporizada y por área en `ev_44df46e896eb5c23d7a045befff9dc6ab19994fd3f18f06e11d882a458166e00`, `ev_3dc2ecad36bca062908f4f2c62fdd951e40b025d4e966158bba0cc886157da85` y `ev_af8e361a634f46949d5c53ce1d224e164efe4602b6f5623875cb5d6f010cd98a`.

REA siguió el reinicio del contador de cuchillas hasta `MowerBluetoothDataSourceImpl` en `ev_395604821ec534bd4bdcc33a21f773fec59c26ae808e728d72846041db9bcb0b`. En los TIF de la familia 4726 aparecen los pares 7/8 y 13/14. La integración selecciona el par por firmware y muestra el botón después de leer su contador.

`SiteManager` resuelve la lista de instalaciones mediante `GetNumberOfSites` y `GetIdListEntry` (`ev_95e5f935eba43867cff0ead95dc1fb48605e8c1eb2841e11c0fdecca210cc6b0`). La integración confirma el ID activo antes de aplicar `Site.SetName` y comprueba el nombre y el ID después de la escritura. Los indicadores de `SiteMap` informan de la disponibilidad de los mapas.

La app cambia de instalación con `ChangeSite` (`ev_0bd2bdfeb2fcee9fc9896caafb252953f6a77bfee9f89d09b034c8974ba39c65`), crea una nueva con `CreateNewSite` (`ev_94297831d32ced1031fd8e58ee247eac7e82d43acb4eb23c983e43aaea5d8f39`), copia la actual con `CreateSiteFromCurrent` (`ev_2da615b8e7768898cee3d95fb9604b938f3ed68d3ed814771663e97589ce5b71`) y elimina por ID (`ev_e66b6e040c58edf92b05b42b5153586af27bcc6583f9d14244e42402d725c19c`). La integración verifica el ID activo después del cambio y espera el evento de borrado del ID solicitado. El perfil G3 conocido del 420 carece de `SiteManager`.

La app lee por BLE la suma de comprobación del mapa activo y recupera la geometría de su caché o del servidor (`ev_399d1f0af547570ccf17d7f7c44029f5b1c9c088bdde53c040b777b92f10f935`). Los TIF declaran los atributos 100 y 101 de `SiteMap` como SVG legible por `NativeAttributes.GetRaw`. Para subir la geometría, la app serializa polígonos y líneas en SVG (`ev_21eefa8d76472a52a8195a83b1b8dfe0a0ebb72a1ffd47685c281b40d880a179`), usa GZIP y CRC32 para transferirla (`ev_692ad27c006213d64d6f040235177f16d22567c27db0fd6e353fbb43775abd33`, `ev_44753a99b47edacf59c23507baf3cbb41a8ada36fccaf4d21d0cbb1a8d36eacb`) y publica el MD5 del SVG tras el último fragmento (`ev_9b8c838b03440c9f763aa613bdcc68c0d1ea77e81b08fd8d2034f6f36f399b99`, `ev_5d487bd75f9af263575d117aee0cfb8fec03d28aa69233f9757475f5e7236bee`).

REA localizó el cambio del centro de GeoFence (`ev_b0392b33703ebe84b62c72825bf368ac0e5e0da6158561ce61363280d17d749e`) y los dos procedimientos de activación, con PIN (`ev_2e3391b8870b25a8f77ee77d58f20f3934a4f5c1662805c288e16170a9b8773c`) y con el comando que admite la sesión BLE existente (`ev_ea93b78641d83d26f468c86e91d66b764427336137556c2cf7086d8bf346d4c4`). El catálogo de TIF proporciona los límites del radio cuando el firmware los declara. Las coordenadas del centro se devuelven como enteros del firmware.

Los controles de corte de bordes (`ev_57d5396fbb4053a2b2a6012b16a6c44ff85ae6210651ebbd2b596d91485f07cb`) y ultrasonidos (`ev_f88be4afd15e7f7ce92f0413811d73240df10bebe195e6b2b9c90377d09a00ed`) usan un valor booleano. Cada interruptor requiere una respuesta afirmativa a su operación de disponibilidad y una lectura válida del ajuste.

La app escribe el patrón, la orientación y el ajuste de perímetro mediante `SetMissionPattern` (`ev_ca00b3b57f0bb24e32d86f74c7acd1339b2aa10fe881ce7e5d113cac1296ba5d`). El protocolo usa décimas de grado para los ángulos. La altura por área puede ser un nivel del 1 al 9 o un porcentaje (`ev_9392f199a6777106a194920df1794203d9e8b0f42139beb04d80516300e898ed`). La integración habilita su edición cuando identifica el formato en el perfil exacto del firmware y puede leer la altura del área.

REA localizó las órdenes BLE de instalación para el cortacésped (`ev_7192095d433119df061d28ef9d374817c98a00c869be1ab10577a3848eeadc33`) y para la estación (`ev_c18c2d79950de1684156894069a854d5f1840304dcc7ca1c8bb3efb3e72851fd`), y la comprobación de que esté aparcado o cargando en la app (`ev_23b232712c38d23bfcb0be7582df2b3ebd9f576edc9dc721dc60adc51e7af712`). La integración verifica que la versión elegida siga lista para instalar y que el cortacésped esté aparcado o cargando antes de enviar la orden. El ajuste de búsqueda automática de la app usa el atributo `Enabled` en las familias Fota y CSFota (`ev_1dabb550495df6b4706ad6dc8ecf87f1c732c76a56a8cc94afddfb2481702bc4`, `ev_678596f071ffa15ea0a27becb0543ae7c5790cd92f317cce5a144ef121db82f1`, `ev_6108243aa59c4feca3b86c5b8de7bc40c1480ff1fbe8e4ca4058dca3664105f7`).

El panel de la app llama «corte en espiral» a los ajustes de `SpotCutting` (`ev_46e6738081dcf287782384e11cb8fe3ca4a29353b2b95bbbe9781c8e27a49b98`, `ev_bece35ee572f766ee94f88a9ebe05da400d3c31bd361837f4708f0a363afab47`, `ev_0df7d226f6850dc837e3e1db461b61e2c75376aec8d1f1bdacd28017f8252559`). El perfil G3 que la app asigna al 420 declara la activación y sensibilidad. El botón manual requiere además `StartTrigger`, que ese perfil no declara.

REA confirmó los formatos de las escrituras antirrobo y los intervalos usados por la app (`ev_e44b63c08fee3d74bb161255991980922d2abe8cbb588165de791ab79fd94c7c`). La configuración distingue las alarmas de elevación, inclinación y movimiento con los indicadores por modelo (`ev_1783f2f3502c34150046f6bc8d08900d1a970bec31ac9c6753f45a7b538a4f2c`, `ev_0bf13ed7d130e9120113da4e0dfa2bb57eec4b4b32a623e740deccddd1343b5d`). Los sensores detectados consultan el estado por BLE durante el mantenimiento de la conexión.

El emparejamiento con la estación usa `ChargingStation.InitiateNewPairing` y espera un evento de resultado por BLE (`ev_91d13098314f0b91fb6ef04f09aad8ccdc24c3521c6d3c1e640b0371a9b153a2`, `ev_aba4a141e9fe9090a45c6b581942a37a6cb4324aaa4f70e39112d3128e13ce95`, `ev_16d469f2709047308633bcb9b6d9a9fed142b1775b09f9c31da45f25579bdd55`). El perfil identifica la variante de suscripción. La cabecera del evento contiene el tipo, la familia, el identificador y la longitud (`ev_b60f3404cf52918fe7fd994a41eb42575445f60d4d7a5516bd590f7a1653a530`). Los TIF definen los índices de posición para leer señales del cable. El perfil conocido del 420 declara el emparejamiento, pero carece de las lecturas `LoopSystem` que usa el diagnóstico.

La app consulta `MowerApp.IsErrorConfirmable` y envía `ConfirmError` (`ev_94d8513cc810e00823b149492e3fca4760888ee2d7a78fccc5e0e2d55f239e16`, `ev_a72472af0536626f9b7902baa9733a38c5fc983a86d0936ae9d5055f9513a3d1`). La integración vuelve a leer el código y el permiso del firmware antes de confirmar. El catálogo de avisos incluye los identificadores en los que coinciden todos los perfiles que declaran el código. Los casos ambiguos conservan el código numérico.

Los TIF declaran `MowerApp.SubscribeAllEvents` (4586:7) y `Messages.SubscribeEvents` (4730:4) en los firmwares que admiten cada suscripción. En el perfil G3 del 420 está declarada la primera. REA confirmó que el protocolo decodifica los eventos por familia e identificador (`ev_b60f3404cf52918fe7fd994a41eb42575445f60d4d7a5516bd590f7a1653a530`). La integración separa las notificaciones de las respuestas de comandos y vuelve a suscribirse después de reconectar.

La app relaciona la detección de bordes con `BoundaryEdgeDetection.Available` y `Enabled` (`ev_9a1dafe2f019183fe68903285f18d401441ff485ce414097fbe466daac286eb8`, `ev_3f929eab63a965490e3020c55907c90b55dac004f529f13cf7d075eaf96da321`). La opción de seguir funcionando con visión reducida corresponde a `ObstacleAvoidanceVision.KeepRunningWithReducedPerformance` (`ev_b4e003fd1caf1949a4d6be0d60ad4070045ddb009c04e68c06b086dd928027ea`, `ev_ea6255a169b6732b31f92cea53b1cebbc6720550e410e56320a8f0c3ee445d06`). El catálogo define el perfil global de dos opciones en los firmwares que declaran ambas. Los ajustes por área se consultan en `Missions` tras comprobar la capacidad correspondiente y una lectura válida del área.

La app crea una misión, le asigna un identificador de polígono y la habilita para programar (`ev_fdf3b186595cc2b79c9fd96ed55e0e4851e28b00400d912b387919437f33fee8`, `ev_97a8a6a5a86363436c6439fd6f8acfc001b31dfa53bf03eb563bec3a74680f93`, `ev_cf288a4f48793a65443fdecf99b13a24e69c50235c4d80135f216f1949af8239`). El lector SVG de la app clasifica los polígonos verdes como áreas de trabajo (`ev_28da0c59942928ead14ec47ce945e50f155666e10c6cae0d64956cee9905dfb9`). La integración comprueba la revisión del mapa antes de vincular el área y confirma su identidad tras escribirla. Para borrar, la app envía el ID con `Missions.DeleteMission` (`ev_d275964c1b8aede01045fc75be42554df5906f1a58c2fc10af8499e494ef571b`).

La app consulta por BLE el estado y resultado de la instalación de la base (`ev_63ed062b22c1b828fb7f9f6b8533373b32a033003b240af259bc4dd7ec5623e1`, `ev_1b0b2beaf2be6e0b542182f2d8830590f0d7b42f88d436b83bc0e4ce518ca3ad`). Para EPOS, toma la calidad de señal del servicio activo o de la estación de referencia y calcula la distancia a partir de las coordenadas locales en milímetros (`ev_2818badc7317a522ea5fa1bee73b78cb3f56f1ddebc1d14bfcc4dca8a79d70e6`, `ev_9d317f2907446dae773e254a2b89921335d227fe3573123d54d54cc92002b9fd`). El ajuste de asistencia por cable escribe `Epos.AugmentByWireEnabled` (`ev_2531ee1a4e885e25843db8f4d3055372ab7bb2df2654778bb47a18bdd65ff714`). La integración comprueba la disponibilidad de la función y verifica el ajuste mediante una lectura posterior.

La pantalla de anchura de pasillo usa los intervalos 1–9 para el delimitador y 0–9 para cada guía (`ev_6b301f9b87505ceb9e6e381f421014dab519902b5647fd6bcb2e35f7145257b0`). El perfil 31.7 del 420 declara `GetGuideCorridor` y `SetGuideCorridor` (4706:2 y 4706:3). La integración lee el número de guías y crea un control por cada guía cuya anchura pueda leer.

La sección de accesorios guarda el ajuste FlexiFence mediante `MobileLoop.Enabled` (`ev_980da380e3e068697a159431114b342149fcc332f6cfb6236dd77d6d19fee2ad`, `ev_daf03529ad8af72c3eba9cd87d424098569fd48f213f57e399bbdf73f4e8fb65`). El TIF del 420 declara la familia 5926. Hay otras variantes con la familia 6050. En ambos casos, la integración espera que `MobileLoop.Available` devuelva verdadero.
