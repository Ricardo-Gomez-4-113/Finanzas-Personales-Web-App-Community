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
| Ingresos / gastos del mes | Resumen, historial mensual | Solo transacciones **sin** apartado. Los depósitos a metas no son gasto. |
| Gastos normales del mes | Movimientos y fijos | Gastos del mes sin fijos ni cuotas MSI. |
| Compromiso mensual | Movimientos y fijos | Fijos activos mensualizados más cuotas MSI mensuales. |
| Libre al mes | Movimientos y fijos | Ingreso fijo mensual menos compromiso menos gastos normales del mes. |
| Libre hasta el cobro | Resumen (periodo) | Disponible menos cargos fijos y cuotas MSI entre mañana y el siguiente cobro. |
| Deuda MSI | Movimientos y fijos | Monto total pendiente de cada compra a meses. |

Reglas que las pruebas vigilan: el historial mensual y el Resumen usan la misma regla de movimientos de apartados, y el `monto_actual` de cada apartado debe coincidir con sus movimientos.

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
