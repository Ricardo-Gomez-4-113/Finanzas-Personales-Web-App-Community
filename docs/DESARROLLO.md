# Desarrollo, pruebas y mantenimiento

Guía práctica para trabajar en el código. La arquitectura (backend FastAPI, SQLite por usuario, SPA con módulos ES6) se resume abajo; los detalles viven en cada módulo.

## Arquitectura en una página

| Pieza | Dónde | Responsabilidad |
|---|---|---|
| Servidor | `main.py` | FastAPI, cabeceras de seguridad, rutas de la SPA |
| Conexiones y esquema | `database.py` | Una base SQLite por usuario (`data/tenants/user_{id}.db`), migraciones perezosas |
| Usuarios | `master_db.py` | Base maestra `data/auth_master.db` (usuarios, auditoría) |
| Seguridad | `security.py` | bcrypt, JWT HS256, roles y límite de intentos |
| Lógica financiera | `finanzas_core.py` | Todo cálculo de dinero, fechas y repartos. Nada de fórmulas en `routers/` ni en el frontend |
| API | `routers/` | Un módulo por área; `salud.py` expone `/health` |
| Frontend | `static/` | `index.html` + `css/` + `js/` (módulos ES6 sin framework) |

## Pruebas

Todo corre en contenedores efímeros: no toca el contenedor en producción ni `data/`.

```bash
./tests/run_tests.sh       # reglas, lógica financiera, migraciones, respaldo, API (aislamiento, JWT, restore, /health)
./tests/run_frontend.sh    # navegador real (Playwright): login, pestañas, desbordamiento 1366x768 y móvil, foco en modales
python3 test_finanzas.py   # solo la lógica financiera, rápido y sin Docker
```

Todo cambio en `finanzas_core.py` lleva su caso en `test_finanzas.py`.

Pruebas de funcionamiento:
- `tests/escenario.py` crea un usuario de referencia con seis meses de historia (nómina, renta, Netflix, una compra a 12 MSI, dos metas), solo por la API. Sus cifras esperadas están calculadas a mano en el mismo archivo.
- Las pruebas del escenario corren con el reloj fijo: `FINANZAS_MODO_PRUEBA=1` y `FINANZAS_HOY=2026-10-20T10:00:00`. Sin `FINANZAS_MODO_PRUEBA`, la variable `FINANZAS_HOY` no tiene efecto, así que producción siempre usa la hora real.
- `./scripts/smoke_produccion.sh [puerto]` revisa los contenedores reales (salud, páginas, recursos con `?v=`, cabeceras de seguridad, 401 sin token, Community sin landing). Es solo de lectura: no crea usuarios ni toca datos. Úsalo después de cada despliegue.

Cómo funcionan las suites:
- Cada archivo corre **todas** sus pruebas aunque una falle, y al final muestra cuáles fallaron. El código de salida es 1 si hubo fallos.
- Para agregar una prueba basta con escribir una función `test_...`: el ejecutor la descubre sola (`tests/ejecutor.py`).
- Las pruebas de API necesitan fixtures (cliente, usuarios, tokens), así que se registran a mano en `tests/test_api_aislamiento.py`. Si olvidas registrar una, el ejecutor falla y lo indica.
- Las pruebas de navegador abren su propio contexto e inician sesión; no dependen de otras pruebas. Esperan condiciones concretas, no tiempos fijos.

## Reglas que se verifican solas

`./scripts/verificar_reglas.sh` (también lo corre `run_tests.sh`) falla si:

- hay emojis nativos en `static/` (se usan micro-SVG);
- un CSS/JS de `index.html` no tiene `?v=` o no existe;
- los `?v=` no coinciden con el contenido actual;
- en Community existe algún archivo de la landing.

## Cifras financieras: qué significa cada una

Todas salen de `finanzas_core.py`. Si una pantalla muestra una cifra distinta, es un error.

| Cifra | Dónde se ve | Cálculo |
|---|---|---|
| Disponible (balance) | Resumen, periodo de cobro | Suma de ingresos menos gastos de **todas** las transacciones (incluye depósitos y retiros de metas, que mueven dinero hacia y desde el ahorro). |
| Dinero en metas | Resumen, Metas | Suma de `monto_actual` de los apartados; coincide con depósitos menos retiros de sus movimientos. |
| Patrimonio | Resumen | Metas + valor de inversiones. |
| Ingresos / gastos del mes | Historial mensual | Solo transacciones **sin** apartado. Los depósitos a metas no son gasto. |
| Periodo | Resumen, Movimientos | Lo marca el ingreso fijo de mayor monto mensual: del día de cobro al día anterior al siguiente cobro. Sin ingreso fijo, meses calendario. `calcular_periodos` devuelve el periodo en curso y los siguientes. |
| Ingreso del periodo | Resumen, Movimientos | Ingresos con fecha dentro del periodo: recibidos más los fijos aún por recibir. |
| Gastos del periodo | Movimientos | Normales + fijos + cuotas MSI con fecha dentro del periodo, registrados o programados, con sus fechas reales (un gasto semanal cuenta 4 o 5 veces, no un promedio). Sin aportes a metas. |
| A metas | Movimientos | Depósitos menos retiros de metas del periodo, incluidos los aportes recurrentes programados. |
| Saldo al cierre | Movimientos | Saldo inicial del periodo más su remanente; es el saldo inicial del siguiente periodo. |
| Libre hasta el cobro | Resumen (periodo) | Disponible menos los cargos que faltan en el periodo más los ingresos que aún llegan en él: el saldo al cierre del periodo en curso. |
| Deuda MSI | Fijos y MSI | Monto pendiente de cada compra a meses, también si está pausada (la deuda sigue). |
| Rubro del movimiento | Resumen, Movimientos | Se guarda en la propia transacción (`transacciones.rubro`: fijos, msi, normales, ingreso o metas). No depende del enlace a `recurrentes`, que al borrar un fijo pasa a NULL. |
| Pagos del presupuesto | Presupuesto | La frecuencia activa 4 (semanal), 2 (quincenal) o 1 (mensual) pagos. Cada pago tiene su día de cobro y sus tarjetas (`presupuesto_tarjetas.unidad_orden`). Un pago con tarjetas exige fecha, y las fechas van en orden creciente. Las tarjetas siempre pertenecen a un pago: desde la versión 15 no hay tablero general, y las tarjetas sueltas pasaron al pago 1. Ninguno puede asignar más que su ingreso. |
| Exportación del presupuesto | Fijos y Movimientos | Reemplazo total: se borran todos los fijos, incluidas las compras a meses que no estén en el presupuesto (también los hechos a mano y los aportes a metas) y se crean los del presupuesto. Un fijo del presupuesto que sigue vigente se actualiza en su lugar, así renombrar una tarjeta no duplica el cargo del mes. Cada tarjeta de un pago es un fijo mensual en su día. |

Reglas que las pruebas vigilan: los periodos son contiguos y no se solapan, cada movimiento cae en un solo periodo, el inicio de un periodo mensual usa el día de cobro como ancla, la exportación quincenal cuadra con el presupuesto, el historial mensual y el Resumen usan la misma regla de movimientos de apartados; el `monto_actual` de cada apartado coincide con sus movimientos; el saldo al cierre de un periodo es el saldo inicial del siguiente, y lo libre hasta el cobro es el saldo al cierre del periodo en curso.

## Diseño de la interfaz

- `static/css/tokens.css`: paleta (oscuro obsidiana y champán, claro porcelana y tinta), tipografías (`--font-ui` Hanken Grotesk, `--font-num` Fraunces), radios y sombras. Todo color sale de aquí.
- `static/css/tema.css`: capa de componentes que se carga al final; sus reglas llevan prefijo `:root` para ganar a las anteriores sin `!important`.
- Las gráficas leen sus colores de los tokens (`colorVar`) y se redibujan al cambiar de tema.
- Textos en tipo oración; cifras en Fraunces con `tabular-nums`; íconos SVG con `stroke-width` 1.8.

## Cache-busting

No se editan los `?v=` a mano. Después de tocar CSS o JS:

```bash
python3 scripts/cache_bust.py
```

Pone en cada `?v=` un hash del contenido. `auth.js` y la landing no entran en el hash de la app, para que ambos repos calculen el mismo valor.

## Cambios de esquema

1. Nueva migración secuencial en `database.py` (nunca editar una anterior).
2. Subir `CURRENT_TENANT_SCHEMA_VERSION`.
3. Caso en `test_finanzas.py`; `tests/test_migraciones.py` comprueba la cadena completa desde v1.

Antes de migrar un tenant se guarda una copia en `data/backups_premigracion/` (las 3 últimas por usuario). Para migrar todos de una vez, por ejemplo tras un despliegue: `python3 scripts/migrar_todos.py`.

## Respaldos

- `scripts/respaldo_automatico.py` copia la base maestra y todos los tenants a `data/backups/AAAAmmdd_HHMMSS/`, verifica cada copia y borra las de más de 14 días.
- Se programa con cron, por ejemplo: `0 3 * * * docker exec <contenedor> python3 scripts/respaldo_automatico.py`.
- `/health` reporta `"respaldo": "vencido"` si el último tiene más de 26 horas, y responde 503 si la base maestra no responde. El `healthcheck` de `docker-compose.yml` usa ese endpoint.

## Sincronizar SaaS y Community (mantenedores)

La edición Community comparte el núcleo con una edición privada. Los cambios comunes se hacen en la privada y se propagan con `scripts/sync.sh`, que solo existe ahí:

```bash
./scripts/sync.sh            # simula y lista qué copiaría
./scripts/sync.sh --aplicar  # copia y corre verificar_reglas.sh en Community
```

Nunca se copian la landing, `main.py`, `auth.js`, `docker-compose.yml`, `Dockerfile`, README, LICENSE ni `.env`.
