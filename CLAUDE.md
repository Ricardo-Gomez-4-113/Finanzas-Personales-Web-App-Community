# CLAUDE.md

Guía de trabajo para este repositorio: flujo, cambios recientes, cómo verificar y pendientes.

## Flujo de trabajo

1. Todo cambio se implementa y prueba primero en este repositorio (Community).
2. Si las suites pasan, se hace commit y push a `main`.
3. El mismo cambio se pasa después a la edición privada (SaaS), donde se vuelve a probar antes de su propio commit y push.

Convenciones:
- Commits en español con formato `tipo(area): descripción`, sin líneas `Co-Authored-By` ni atribución a asistentes.
- Cualquier cambio de CSS o JS en `static/` requiere `python3 scripts/cache_bust.py`.
- Las fórmulas de interés, conversión cambiaria y prorrateo viven en `finanzas_core.py`, nunca en `routers/` ni en el frontend, y cada cambio lleva su caso en `test_finanzas.py`.
- Las migraciones de `database.py` son inmutables: los cambios de esquema van como una versión nueva e idempotente.

## Estructura relevante

- `main.py`: servidor FastAPI, middleware de seguridad y rutas SPA.
- `database.py`: conexiones por tenant y migraciones perezosas. Esquema actual: versión 17 (`CURRENT_TENANT_SCHEMA_VERSION`).
- `finanzas_core.py`: cálculo financiero puro (conversiones, rendimientos, frecuencias, sub-periodos, reparto en centavos).
- `security.py`: bcrypt, JWT (HS256, clave obligatoria de al menos 32 caracteres), RBAC y control de tasa.
- `mercado.py`: tipos de cambio y precios, con caché y valores de respaldo.
- `routers/`: `auth`, `admin`, `movimientos`, `apartados`, `inversiones`, `recurrentes`, `respaldo`, `presupuesto`.
- `static/`: SPA con módulos ES6 y hojas de estilo en `static/css/`.
- Datos: `data/master.db` (usuarios y auditoría) y `data/tenants/user_{id}.db` (una base por usuario).

## Registro de cambios (revisión de backend, Fase 1)

Cada entrada indica el commit y qué incluye.

- **`2acc27b` test(api): usar fecha de hoy en prueba de cambio a quincenal.** La prueba usaba una fecha fija que quedó en el pasado. Ahora compara contra la fecha de hoy.
- **`6cf5017` fix(seguridad): fallar si la sesión no trae db_name.** `get_tenant_db` y tres puntos de `routers/respaldo.py` usaban `get("db_name", "user_1.db")`, que abría la base del administrador si faltaba el dato. Ahora usan `current_user["db_name"]` y fallan cerrado. Prueba nueva: `test_tenant_sin_db_name_falla_cerrado`.
- **`f185bdd` refactor(mercado): centralizar tipo de cambio MXN de respaldo.** La constante `TASA_MXN_RESPALDO` en `mercado.py` reemplaza seis literales repartidos en `routers/movimientos.py`, `routers/inversiones.py` y `export_service.py`.
- **`9785494` fix(presupuesto): validar distribución de subperiodos.** El campo `distribucion_subperiodos` de tarjetas aceptaba diccionarios arbitrarios; un monto no numérico se guardaba y rompía la lectura del presupuesto. El modelo `DistribucionSubperiodo` valida monto (0 a 1e12), porcentaje (0 a 100) y día (1 a 31) al crear y actualizar. Prueba: `test_distribucion_subperiodos_se_valida_al_guardar`.
- **`342f89a` fix(api): acotar listas de IDs y normalizar la moneda del Excel.** Tope de 1000 IDs en `eliminar-lote`, `importar-fijos` (`recurrentes_ids`) y `exportar-a-fijos` (`tarjetas_ids`); tope de 31 en `subdivision_config`. El nombre del archivo Excel usa solo MXN o USD, con la misma regla del generador. Prueba: `test_listas_de_ids_tienen_tope_y_nombre_de_excel_es_seguro`.
- **`f6ff627` fix(inversiones): convertir montos en EUR con su tipo de cambio.** Antes, un activo en EUR se valuaba a su valor nominal en la moneda base. Ahora `convertir` pasa por USD y recibe `tasa_usd_eur` (unidades de EUR por USD). Sin esa tasa, un monto en EUR lanza `ValueError` en lugar de valuarse en silencio. Afecta a `valorar_posiciones`, `calcular_resumen`, `export_service` y a las rutas de inversiones y resumen. `TASA_EUR_RESPALDO = 0.89` en `mercado.py`. Pruebas: se corrigió el aserto que fijaba el comportamiento anterior en `test_convertir_y_rendimiento_casos_borde` y se agregó `test_portafolio_en_eur_se_valua_con_su_tasa`.
- **`ac59b9c` fix(api): rechazar moneda_base no soportada.** `/api/resumen`, `/api/gastos-por-categoria`, `/api/inversiones` y `/api/rendimiento` validan `moneda_base` con `Query(pattern=...)`: MXN, USD o EUR, sin distinguir mayúsculas. Cualquier otro valor responde 422. Prueba: `test_moneda_base_solo_acepta_monedas_soportadas`.

## Cómo verificar

- Pruebas rápidas de finanzas: `python3 test_finanzas.py`. Requiere las dependencias de `requirements.txt` (`pydantic==2.14.1` en particular); en Python del sistema con PEP 668 conviene un entorno virtual.
- Suites de backend en Docker: `./tests/run_tests.sh` (finanzas, migraciones, respaldo, mercado, acciones, aislamiento de API y escenario de API).
- Frontend con Playwright: `./tests/run_frontend.sh`. El script instala Playwright con pip; si la red corta la instalación, se salta sin fallar (`SKIP`). Repetir la ejecución hasta ver `OK: 19 pruebas`.

Estado al cierre de la Fase 1: suites de backend en verde (finanzas 108, migraciones 8, respaldo 2, mercado 4, acciones 2, aislamiento 33, escenario 18) y frontend 19 de 19.

## Pendientes

- **Migraciones:** los `ALTER TABLE ADD COLUMN` están en `try/except sqlite3.OperationalError`. Son idempotentes (se verificó re-migrando desde versión 0), pero ese `except` también oculta errores de SQL reales. No se modifican migraciones pasadas.
- **Reparto de MSI en `routers/recurrentes.py`:** calcula la cuota con `round(total / meses, 2)`. La última cuota se ajusta en `finanzas_core.py` para que el total cuadre. Mover esa regla a `finanzas_core.py` es una limpieza, no un bug.
- **`mensualizar`:** una frecuencia desconocida se trata como mensual. La API no lo permite porque la frecuencia es un `Literal`.
- **Fase 2, frontend:** pendiente. Revisión de manejo de errores de red y de sesión, estados vacíos, escape de HTML en datos del usuario y reglas visuales de la guía de estilo.

## Directivas de trabajo

- Localizar antes de leer: `grep -n "termino" ruta/archivo` para ubicar la función, ruta o selector, y luego leer solo ese bloque. No leer de una vez archivos de más de 150 líneas.
- No hacer escaneos globales (`find`, `diff -rq`, `ls -R`) salvo petición explícita.
- Estado de Git con `git status -s` o `git diff --stat`, sin `git diff` extensos.
- Guía completa de desarrollo: `docs/DESARROLLO.md`.

## Reglas de backend

- Aislamiento: toda consulta o mutación financiera opera sobre `data/tenants/user_{id}.db` del usuario autenticado.
- Migraciones: cada cambio de esquema es una versión nueva, secuencial e idempotente. No se modifican migraciones pasadas ni se borran bases de usuarios para forzar un esquema.
- Lógica financiera solo en `finanzas_core.py`, con su caso en `test_finanzas.py`.

## Reglas de UI y frontend

- Desbordes: los wrappers principales (`.app-main-wrapper`, `.app-main-content`) llevan `overflow-x: hidden` y `max-width: 100%`. No debe haber scroll horizontal accidental.
- Navegación: debe caber en 1366x768 o con escalado de 125% a 150%. La altura acumulada de navegación y sidebars no supera 440px, y las scrollbars se ocultan con `scrollbar-width: none`.
- Sin scrollbars anidados: tablas de pocas filas y modales no llevan `overflow-y: auto` con alturas fijas artificiales.
- Sistema visual "Banca privada": colores y tipografía viven en `static/css/tokens.css` (obsidiana `#0F1318`, champán `#C9B37E`, marfil `#ECE7DC`; claro en porcelana `#EEF0F3` con tinta `#161B22`). La interfaz usa Hanken Grotesk (`--font-ui`) y las cifras y titulares, Fraunces (`--font-num`). La capa de componentes está en `static/css/tema.css`, cargada al final, con reglas prefijadas `:root` para ganar sin `!important`.
- Sin colores literales en CSS ni JS: usar tokens. Las gráficas los leen con `colorVar`. Textos en tipo oración, sin mayúsculas forzadas ni etiquetas sobre cada título.
- Paleta: prohibidos los colores chillones, planos o saturados (amarillo mostaza, verde neón) y el negro puro `#000000`. Fondos obsidiana o slate, micro-bordes champán o cyan tenue y sombras difusas multicapa.
- Iconos: prohibidos los emojis del sistema en botones, títulos, tarjetas y menús. Solo micro-SVG con `stroke-width: 1.8`, `stroke: currentColor` y alineación vertical.
- Cifras: `font-variant-numeric: tabular-nums` en saldos, importes, porcentajes y celdas de tabla.
- Contraste: el texto de estado usa `var(--success-text)`, `var(--danger-text)` y `var(--warning-text)`. Los `--*-color` quedan para fondos, bordes e iconos.
- Cache-busting: tras cambiar CSS o JS en `static/`, ejecutar `python3 scripts/cache_bust.py`. No editar los `?v=` a mano.
- Tras cambios de UI, correr `./tests/run_frontend.sh`.
