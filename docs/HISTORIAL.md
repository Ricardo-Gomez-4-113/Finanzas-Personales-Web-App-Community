# Historial

Registro de cambios, fases cerradas y auditorías ya completadas de Finanzas Community, movido desde `CLAUDE.md`
(que conserva solo las reglas vigentes, las decisiones del usuario y lo abierto). Los hashes son de Community.

## Registro de cambios (revisión de backend, Fase 1)

Cada entrada indica el commit y qué incluye.

- **`2acc27b` test(api): usar fecha de hoy en prueba de cambio a quincenal.** La prueba usaba una fecha fija que quedó en el pasado. Ahora compara contra la fecha de hoy.
- **`6cf5017` fix(seguridad): fallar si la sesión no trae db_name.** `get_tenant_db` y tres puntos de `routers/respaldo.py` usaban `get("db_name", "user_1.db")`, que abría la base del administrador si faltaba el dato. Ahora usan `current_user["db_name"]` y fallan cerrado. Prueba nueva: `test_tenant_sin_db_name_falla_cerrado`.
- **`f185bdd` refactor(mercado): centralizar tipo de cambio MXN de respaldo.** La constante `TASA_MXN_RESPALDO` en `mercado.py` reemplaza seis literales repartidos en `routers/movimientos.py`, `routers/inversiones.py` y `export_service.py`.
- **`9785494` fix(presupuesto): validar distribución de subperiodos.** El campo `distribucion_subperiodos` de tarjetas aceptaba diccionarios arbitrarios; un monto no numérico se guardaba y rompía la lectura del presupuesto. El modelo `DistribucionSubperiodo` valida monto (0 a 1e12), porcentaje (0 a 100) y día (1 a 31) al crear y actualizar. Prueba: `test_distribucion_subperiodos_se_valida_al_guardar`.
- **`342f89a` fix(api): acotar listas de IDs y normalizar la moneda del Excel.** Tope de 1000 IDs en `eliminar-lote`, `importar-fijos` (`recurrentes_ids`) y `exportar-a-fijos` (`tarjetas_ids`); tope de 31 en `subdivision_config`. El nombre del archivo Excel usa solo MXN o USD, con la misma regla del generador. Prueba: `test_listas_de_ids_tienen_tope_y_nombre_de_excel_es_seguro`.
- **`f6ff627` fix(inversiones): convertir montos en EUR con su tipo de cambio.** Antes, un activo en EUR se valuaba a su valor nominal en la moneda base. Ahora `convertir` pasa por USD y recibe `tasa_usd_eur` (unidades de EUR por USD). Sin esa tasa, un monto en EUR lanza `ValueError` en lugar de valuarse en silencio. Afecta a `valorar_posiciones`, `calcular_resumen`, `export_service` y a las rutas de inversiones y resumen. `TASA_EUR_RESPALDO = 0.89` en `mercado.py`. Pruebas: se corrigió el aserto que fijaba el comportamiento anterior en `test_convertir_y_rendimiento_casos_borde` y se agregó `test_portafolio_en_eur_se_valua_con_su_tasa`.
- **`ac59b9c` fix(api): rechazar moneda_base no soportada.** `/api/resumen`, `/api/gastos-por-categoria`, `/api/inversiones` y `/api/rendimiento` validan `moneda_base` con `Query(pattern=...)`: MXN, USD o EUR, sin distinguir mayúsculas. Cualquier otro valor responde 422. Prueba: `test_moneda_base_solo_acepta_monedas_soportadas`.

## Pendientes

- **Migraciones:** los `ALTER TABLE ADD COLUMN` están en `try/except sqlite3.OperationalError`. Son idempotentes (se verificó re-migrando desde versión 0), pero ese `except` también oculta errores de SQL reales. No se modifican migraciones pasadas.
- **Reparto de MSI en `routers/recurrentes.py`:** calcula la cuota con `round(total / meses, 2)`. La última cuota se ajusta en `finanzas_core.py` para que el total cuadre. Resuelto: la cuota base la calcula `cuota_msi` en `finanzas_core.py`, con su caso en `test_finanzas.py`.
- **`mensualizar` (hecho):** una frecuencia desconocida lanza `ValueError` en lugar de tratarse como mensual. La base lo impide con su `CHECK`, así que el cambio solo muestra errores de código. Prueba en `test_finanzas.py`.
- **Fase 2, frontend:** hecho: `apiFetch` cierra sesión solo con 401 o con 403 de cuenta desactivada o suspendida, y avisa con un toast cuando no hay conexión; `leerJson` en `core.js` muestra el motivo del servidor cuando una carga falla (dashboard, movimientos y apartados); colores literales sustituidos por tokens (`--overlay-scrim`, `--chart-label`); la marca del sidebar se ve completa en dos líneas; el placeholder del ticker ya no sale en mayúsculas; el ticker se codifica en la URL de cotización. Revisión visual con capturas de login, resumen, movimientos, inversiones y presupuesto, en claro, oscuro y móvil. Auditoría de `innerHTML` con inventario de todas las interpolaciones: los datos del usuario pasan por `escapeHTML` y el resto son valores fijos, numéricos o construidos en el propio módulo. Pruebas de Playwright para el fallo de red y para el 403 de permisos y de cuenta desactivada (22 pruebas en total). Pendiente: ninguno bloqueante; la auditoría es estática y no sustituye una revisión de seguridad externa.
- **Fase 3, visual:** aplicado: cabeceras del sidebar y de la barra superior a 60 px (la barra encogía por flexbox); desvanecido en la cinta de periodos en móvil; porcentajes del donut de inversiones solo en rebanadas de 10 % o más (el resto se lee en la leyenda). Descartado: ajustar el espacio del FAB, porque el contenido móvil ya tiene 88 px de padding inferior y el botón se oculta al bajar. Comprobado: `tabular-nums` en las cifras del hero. Cierre de la exploración manual: importes grandes en una línea en móvil; título de la barra en dos líneas en móvil (sin puntos suspensivos); selector de moneda sin «($)»; invitación a registrar el primer movimiento cuando no hay datos; área táctil de 44 px en botones de fila y pestañas en móvil (el tamaño visible no cambia). El foco de teclado ya es visible: el contorno dorado de 2 px. **Actualización (3 nov 2026): el botón flotante («+»/FAB) se eliminó con su código, su CSS y sus pruebas; en celular el formulario de movimiento se abre con el botón «Nuevo movimiento» del encabezado de la tabla, y el relleno inferior móvil que le dejaba espacio pasó de 88 a 24 px.**

## Plan restante

Fases 1 a 6 cerradas.

- **`importar-fijos` (quitado):** la ruta y su modelo se eliminaron. El flujo vigente es solo `exportar-a-fijos` (presupuesto hacia Fijos y Movimientos).
- **Conflicto entre pestañas (hecho):** cada movimiento tiene `version` (migración 18). El formulario envía la versión con la que se abrió; si no coincide, la API responde 409, el modal se cierra, se recarga la lista y se avisa. El PUT sin `version_esperada` conserva la última escritura para clientes antiguos. Cubierto por `test_dos_pestanas_editan_el_mismo_movimiento_y_la_segunda_recibe_409` y por la prueba de navegador `test_otra_pestana_cambio_el_movimiento_y_no_se_pisa`. La cláusula `AND version = ?` del UPDATE tiene su prueba, `test_escritura_concurrente_entre_la_lectura_y_el_guardado_da_409`: otra conexión sube la versión entre la lectura y el UPDATE, y la respuesta es 409. Quitar la cláusula hace fallar esa prueba.
- **Fase 6, sistema de log (hecho):**
  - Backend: `observabilidad.py` escribe `data/logs/app.log` (o `FINANZAS_LOG_DIR`) con una línea JSON por evento. Cada petición lleva `id_peticion` en la cabecera `X-Request-Id`. Se registran los 5xx (con traza si hubo excepción), los 401/403 y los fallos de SQLite. Rotación de 5 MB con 5 archivos.
  - Qué no se guarda: contraseñas, tokens, cuerpos de petición, importes ni la cadena de consulta. Solo ids y códigos de estado.
  - Navegador: `static/js/diagnostico.js` reporta errores de JavaScript, promesas rechazadas y respuestas 5xx de `apiFetch` (con el `id_peticion`) a `POST /api/diagnostico/cliente`. Sin autenticación a propósito, con límites de tamaño y máximo 20 envíos por sesión.
  - Revisión: `python3 scripts/resumen_logs.py [carpeta]` resume fallos por evento, método, ruta y estado, y muestra los últimos con su id. `./tests/run_tests.sh` deja el registro de todas sus suites en `.logs_pruebas/app.log`, que es ignorado por git.
  - Pruebas: `tests/test_observabilidad.py` (7 pruebas) y dos pruebas de navegador (`test_error_de_javascript_llega_al_registro`, `test_respuesta_500_de_la_api_se_reporta_con_su_id`).
  - Límites conocidos: el registro asume un solo proceso de uvicorn (como el Dockerfile). Las pruebas de 401 generan líneas a propósito, así que `.logs_pruebas` no es un registro limpio de producción.
  - Flujo habitual: Community primero, luego `portar-a-saas.sh --probar`.

## Plan de auditoría de interfaz (completado)

Estado de los seis puntos (cada uno con su prueba, publicado en Community y en el SaaS):
1. **Sidebar colapsado:** no se reprodujo compresión del icono en ningún ancho ni estado. Sí se corrigió el desplazamiento del icono con el cursor encima (`d35807d`). Si el icono que se ve mal es otro, falta una captura.
2. **Modo privacidad:** clase única `.dato-sensible` con `montoHTML()`. Las gráficas y los tooltips no siempre cubren todo: los `title` y las opciones de selector siguen en texto plano.
3. **Euro como moneda base:** selectores, gastos por categoría (antes 500 con base EUR) y exportación a Excel.
4. **Botón Cotizar:** altura del campo (26 px).
5. **Formularios y relleno automático:** `color-scheme` por tema y regla `-webkit-autofill`. El relleno real del navegador no se puede disparar en automatización: se verificó la regla, no la pintura.
6. **Comentarios:** tabla `comentarios` en la base maestra, `POST /api/comentarios`, 5 por hora, y `scripts/comentarios.py` para consultarlos.

Prueba intermitente: `test_botones_de_fila_y_pestanas_tienen_area_tactil_de_44px` falló una vez esperando filas de movimientos y pasó en la siguiente corrida. No está corregida la causa.

## Auditoría de mejoras (puntos 1 a 11, noviembre de 2026)

Los mismos cambios existen en el SaaS con sus propios hashes. Hashes de Community:

| # | Qué | Estado |
|---|---|---|
| 1 | Fecha imposible dejaba `/api/resumen` en 500 (`e9d1a80`). La migración v19 repara las ya guardadas (`33dffac`). | Hecho |
| 2 | Editar un gasto se saltaba la validación de saldo (`e9d1a80`). | Hecho |
| 3 | El presupuesto reescribía cualquier ingreso que dijera «nómina» (`e9d1a80`): solo toca el enlazado a su tarjeta o `NOMINA_PRESUPUESTO`. | Hecho |
| 4 | Moneda base EUR/USD en todas las pantallas (`f9f22c6`): los montos se guardan en MXN y el servidor da `factores_desde_mxn` en `/api/tipo-cambio`; el frontend solo multiplica. | Hecho |
| 5 | Patrimonio neto con efectivo y deuda. | **Descartado**: el patrimonio es metas + inversiones. |
| 6 | Ligar inversiones y cuentas con rendimiento al efectivo. | **Descartado**: son una calculadora, fuera del núcleo. |
| 7 | Dos motores de periodos (`1a16d3e`): la validación de «presupuesto del periodo superado» usa el mismo corte que el tablero. | Hecho |
| 8 | La ventana de cargos y los movimientos de apartados. | Cerrado sin cambios: es coherente con un modelo de efectivo. Lo fija `test_deposito_a_meta_consume_la_capacidad_del_periodo_igual_que_el_saldo` (`796e39f`). |
| 9 | Tipo de cambio de respaldo (`4f4752c`, `4e092c9`): cada moneda conserva su último valor bueno; `/api/tipo-cambio` informa `en_respaldo`; la etiqueta marca «(aprox.)». El frontend ya no trae el 18.40. | Hecho |
| 10 | Vista previa de la exportación a Fijos (`0cc3bac`) y modal de confirmación propio (`b20cc23`). | Hecho |
| 11 | `cfg.get` sobre una fila de SQLite en la exportación (`796e39f`). | Hecho |
| — | La cinta de periodos muestra «Libre del periodo» (ingresos menos gastos y metas de ese periodo), sin arrastrar el efectivo (`ea1a1bb`). «Libre hasta el cobro» del tablero sí cuenta el efectivo. | Hecho |

Lógica media resuelta (`7bf8b5d`, `6a3405b`):
- `convertir` lanza `ValueError` sin tasa USD/MXN válida; `convertir_de_mensual` rechaza frecuencias desconocidas; los `except` amplios de la distribución de tarjetas se acotaron.
- `sugerir_icono_color` compara palabras completas, sin acentos y con plural.
- Depositar a una meta usa `balance_disponible_a_fecha` (hoy): un ingreso programado a futuro no cuenta.
- Los mensajes de error del backend rotulan sus importes como MXN.
- La tarjeta del presupuesto se vincula a su meta por id (`presupuesto_tarjetas.apartado_id`, migración v20; la migración conserva los vínculos que coincidían por nombre). El cargo previo se reconoce también por `recurrente_id`, así que renombrar una tarjeta no duplica el cargo del mes.

Exportar el presupuesto a Fijos:
- `POST /api/presupuesto/exportar-a-fijos/vista-previa` corre la exportación completa y la revierte (`rollback`); devuelve el mismo detalle que la real. Cada fila trae `accion` (creado, actualizado, eliminado), `origen` (presupuesto, manual, meta, msi) y `nota` (cuotas de una compra a meses).
- El modal lista primero lo que se eliminará. Cancelar o Escape no tocan nada.

Decisiones del usuario que no se vuelven a proponer:
- El patrimonio es metas + inversiones; no se suma el efectivo ni se resta la deuda.
- Las inversiones y cuentas con rendimiento son una calculadora: no generan movimientos de caja.
- No se puede tener un gasto fijo sin un ingreso fijo activo: el bloqueo es deliberado, no una advertencia.
- El historial y la gráfica del Resumen siguen la frecuencia de cobro del ingreso fijo principal: un periodo por mes de cobro, quincena o semana (meses calendario si no hay ingreso fijo). Se retiró `mes_actual` del resumen (ninguna pantalla lo usaba) y `/api/resumen-mensual` pasó a `/api/historial-periodos` (`historial_por_periodos`).
- No hay frecuencia anual: solo semanal, quincenal y mensual. Se quitó el código a medias que la contemplaba (`siguiente_fecha`, `mensualizar`, `_ingreso_principal`); la API y la base ya la rechazaban.

Tanda visual (`2857cfd`):
- La gráfica mensual rotula «oct 2026» con `etiquetaMes` (la misma del historial) y rellena en cero los meses sin movimientos.
- Los dólares se rotulan `US$` en `fmt`; el peso y el euro no cambian.
- Los `title` con importes (donut de patrimonio, marcas del periodo, barras del presupuesto) usan `conTituloPrivado`: guardan `data-titulo` y `data-titulo-monto` y siguen el modo privacidad, también al repintarse con `innerHTML` (un `MutationObserver` en `core.js`).
- Estilos en línea movidos a clases; el bloque de tarjeta dividida de movimientos y fijos es `bloqueTarjetaPartes` (`static/js/tarjeta_partes.js`) y usa `color-mix` con tokens. Los velos de modales usan `--overlay-scrim`.

`!important` y sombras (resuelto):
- De 44 declaraciones `!important` quedan 15, cada una con su motivo escrito junto a la regla: el bloque de reducir movimiento, `.hidden`, el tamaño del canvas de Chart.js (que fija estilos en línea), el `display` que el JS pone en línea a la app, y los colores de estado (`.pnl-*`, `.networth-sin`, `.card-deficit`), que deben ganar a los colores que cada contexto fija. El resto se resolvió con una regla `:root` aparte para esas declaraciones, y bajando la especificidad de la regla de campos de `tema.css` con `:where()`.
- Las 57 sombras y tintes neutros con `rgba` literal usan `rgb(var(--sombra-rgb) / alfa)` y `rgb(var(--luz-rgb) / alfa)` (definidos en `tokens.css`); el valor calculado es idéntico.
- Prueba: se volcaron los estilos calculados de 200 640 elementos en 110 pantallas (login, siete pestañas, tres modales; claro y oscuro; cinco anchos de 390 a 1500 px) antes y después. Sin diferencias reales; las únicas, de menos de 3 px en títulos, aparecen también entre dos corridas de la versión anterior (carga de tipografía). El volcado no cubre hover, foco ni estados que dependen de datos (déficit).
- `scripts/verificar_reglas.sh` falla si reaparece un `rgba` neutro literal fuera de `tokens.css` o si hay más de 15 declaraciones `!important` (la landing, solo del SaaS, queda fuera).

Visual abierto: consolidar las 5 hojas (180 KB) y los `rgba` de colores de marca de la landing (SaaS); los estilos en línea que quedan son anchos dinámicos por dato. En `tema.css` queda un `#000` en una máscara de degradado, donde solo cuenta la transparencia.

Abierto:
- Monedas del portafolio (cerrado): solo USD, MXN y EUR. Una cotización en otra moneda se rechaza al registrar o cotizar (422), la sincronización la deja sin actualizar y `convertir` no valúa a valor nominal.
- Dinero en centavos (cerrado, migración v21): columnas `INTEGER` en `transacciones.monto`, `apartados.monto_actual/meta_monto`, `recurrentes.monto/monto_total`, `cuentas_rendimiento.capital`, `presupuesto_config.ingreso_base`, `presupuesto_tarjetas.monto` y `cuentas.saldo_inicial`. Cantidades y precios de la bolsa siguen en `REAL`. Una escritura sin `a_centavos` con decimales falla al leerse (`TypeError`); una con monto entero queda 100 veces mal, así que toda escritura de dinero pasa por `a_centavos`. Se comprobó comparando todos los GET y las escrituras sobre una misma base antes y después de migrar: cero diferencias.
- La importación de fijos al presupuesto solo reutiliza una tarjeta con el mismo nombre (sin distinguir mayúsculas); antes la emparejaba por subcadena.
- La cuota MSI se calcula también en `recurrentes.js` para la vista previa; el valor guardado sale de `cuota_msi`.
- Pruebas de navegador intermitentes: el servidor de pruebas salía a Yahoo y dependían de la latencia; ahora arranca con cotizaciones fijas (`LANZADOR` en `test_frontend.py`). `SOLO=texto ./tests/run_frontend.sh` repite pruebas sueltas. Si `test_botones_de_fila_y_pestanas_tienen_area_tactil_de_44px` vuelve a fallar, investigar aparte: no se probó que su causa fuera la red.

## Seguridad (revisión del login C1 a C6 y pruebas de borde)

Revisión enfocada del control de fuerza bruta, validada en un espacio de red sin salida (`unshare -rn`) con usuarios dummy.
- **C1, carrera en el control de tasa (corregido):** antes, 40 peticiones simultáneas evaluaban 40 contraseñas. Ahora `reservar_intento_login` comprueba y anota en un solo paso, bajo candado, antes de verificar: 5 evaluadas. Prueba: `tests/test_seguridad_login.py`.
- **C2, evicción de contadores (corregido):** un flujo de claves nuevas liberaba una cuenta bloqueada. Ahora la purga descarta primero las claves sin bloqueo, y si la tabla sigue llena rechaza claves nuevas con 429. Prueba y mutación: quitar la preferencia hace fallar la prueba de flujo.
- **C3, password spraying (corregido, con límite):** una IP probaba 60 cuentas con una contraseña débil sin un solo 429 y comprometía 3. Ahora hay un tope de 20 intentos fallidos por IP entre todas las cuentas y ventana de 5 min (`reservar_intento_login_ip`). Los aciertos se devuelven (`devolver_intento_login_ip`), para que una IP compartida no se bloquee por inicios de sesión correctos. Limitación: una IP sigue pudiendo probar 20 cuentas por ventana, y una botnet con muchas IPs no queda frenada por esto.
- **C4, adivinar el código de recuperación desde varias IPs (corregido):** el tope era por IP e identificador. 30 IPs hacían 150 verificaciones contra una cuenta en una ventana, y el nombre y el correo sumaban dos topes. Ahora cada cuenta tiene su propio cupo de 5 verificaciones y 5 solicitudes de código por ventana de 5 min, sin importar la IP ni el identificador (`reservar_cupo_cuenta`). Al agotarse, la respuesta es la misma que la de un código incorrecto o una solicitud normal, para no revelar la cuenta. Estimación: con el cupo por cuenta, adivinar el código toma en promedio unos 300 días por cuenta.
- **Prueba corregida en la misma ronda:** `test_registrar_ingreso_y_modal_accesible` usaba la fecha real del contenedor; el servidor de pruebas tiene el reloj fijo al 20 de octubre y el movimiento caía fuera del periodo. Ahora usa `escenario.HOY`.
- **C5, campos sin límite en el login (corregido):** un usuario de 5 MB se aceptaba y el contador guardaba una clave de 5 millones de caracteres, con 10 000 claves posibles. Ahora el login tiene los mismos límites del alta (usuario 50, contraseña 72). Además, el 422 ya no repite el valor enviado (manejador global en `main.py`): antes, un campo de 5 MB volvía en la respuesta. Una contraseña de 5 MB ya no era problema: bcrypt la rechaza y responde 401 en 0,12 s.
- **Tope del cuerpo de cada petición (corregido, residual de C5):** uvicorn no limitaba el cuerpo, así que un POST de varios MB se leía completo antes de la validación. `limite_cuerpo.py` responde 413 con 256 KB por petición. `/api/restore` tiene su propio tope de 50 MB, que es el del respaldo más la cabecera multipart. Se revisa por Content-Length (sin leer el cuerpo) y contando los bytes recibidos (chunked). El 413 lo arma el propio middleware, porque FastAPI convierte el corte en un 400 o 422 de parseo. Pruebas: `tests/test_limite_cuerpo.py`.
- **C6, oráculo de tiempo en la solicitud de código (corregido):** con correo configurado, la ruta escribía el código antes de responder y la respuesta tardaba más para una cuenta real. Medido con muestras reales: diferencia de unos 2 ms en medianas, con distribuciones solapadas. Es una señal débil, pero el fix es barato: la escritura y el envío van en una tarea de fondo. Después de la corrección, la diferencia es de −0,8 ms (ruido). Prueba con uvicorn real y retardo inyectado de 0,3 s en la escritura.
- Costo medido de un intento (bcrypt): unos 0,8 s en esta máquina; llenar 10 000 claves tarda del orden de minutos con concurrencia.
- **C7, `/health` público exponía estado interno (corregido):** el endpoint público revelaba `"respaldo":"vencido"` y la antigüedad del último respaldo. Ahora `/health` solo devuelve `{"status":"ok"}` (503 si la base maestra no responde, para el healthcheck de Docker). El detalle, solo para administradores, dice si la base maestra responde.
- **Pruebas de borde contra el dominio (sin volumen):** cabeceras y TLS correctos; rutas de desarrollo en 404; límite de login activo desde internet; `CF-Connecting-IP` y `X-Forwarded-For` falsas no cambian el contador.
- **Respaldo nocturno automático (quitado de Community):** el script `respaldo_automatico.py`, su suite, el chequeo del respaldo en `/api/salud/detalle` y el cron se eliminaron. Estaba vencido porque el cron no podía abrir su log (`data/backups` es de root). Queda pendiente para el servidor de producción, con permisos correctos en `data/backups`. Los respaldos de usuarios (descarga y restauración) y las copias previas a migrar no cambian.
