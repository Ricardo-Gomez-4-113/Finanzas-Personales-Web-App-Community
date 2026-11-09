# CLAUDE.md

Guía de trabajo para este repositorio: flujo, reglas, cómo verificar y qué sigue abierto. El historial de cambios y de auditorías está en `docs/HISTORIAL.md`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.

## Flujo de trabajo

1. Todo cambio se implementa y prueba primero en este repositorio (Community).
2. Si las suites pasan, se hace commit y push a `main`.
3. El mismo cambio se pasa después a la edición privada (SaaS) con `~/bin/portar-a-saas.sh <commit> --probar` (mezcla `index.html` sin tocar la landing y corre las suites del SaaS); luego su propio commit y push.

Convenciones:
- Commits en español con formato `tipo(area): descripción`, sin líneas `Co-Authored-By` ni atribución a asistentes.
- Cualquier cambio de CSS o JS en `static/` requiere `python3 scripts/cache_bust.py`.
- Las fórmulas de interés, conversión cambiaria y prorrateo viven en `finanzas_core.py`, nunca en `routers/` ni en el frontend, y cada cambio lleva su caso en `test_finanzas.py`.
- Las migraciones de `database.py` son inmutables: los cambios de esquema van como una versión nueva e idempotente.

## Estructura relevante

- `main.py`: servidor FastAPI, middleware de seguridad y rutas SPA.
- `database.py`: conexiones por tenant y migraciones perezosas. Esquema actual: versión 22 (`CURRENT_TENANT_SCHEMA_VERSION`).
- `dinero.py`: el dinero se guarda en centavos enteros. `a_centavos` convierte al escribir y `fabrica_filas` (el `row_factory` de los tenants) devuelve pesos al leer las columnas de dinero. Un total en SQL (`SUM`, `CASE`) se divide entre `100.0` en la consulta; `verificar_reglas.sh` lo vigila.
- `finanzas_core.py`: cálculo financiero puro (conversiones, rendimientos, frecuencias, sub-periodos, reparto en centavos).
- `security.py`: bcrypt, JWT (HS256, clave obligatoria de al menos 32 caracteres), RBAC y control de tasa.
- `mercado.py`: tipos de cambio y precios, con caché y valores de respaldo.
- `routers/`: `auth`, `admin`, `movimientos`, `apartados`, `inversiones`, `recurrentes`, `respaldo`, `presupuesto`.
- `asistente/`: asistente con LLM (Gemini). El motor no conoce el canal; las herramientas llaman a la API con un token de 60 s
  (`uso = "asistente"`, lectura) que `security.verify_token` solo deja pasar por `asistente/permisos.py`. El modelo solo *propone* escrituras
  (`asistente/propuestas.py`); ejecutarlas exige la confirmación de la persona y un segundo token de 20 s (`asistente_escritura`). Probar con
  `python3 -m asistente.cli --demo --preguntas` (necesita `requirements-asistente.txt` y `GEMINI_API_KEY`). Plan y lecciones:
  `~/app-plan/plan-asistente-llm.md`. Telegram: QR de un solo uso desde el menú de usuario (`routers/asistente_telegram.py`, `asistente/vinculos.py`;
  tablas `telegram_*` en la base maestra); el bot (`asistente/bot_telegram.py`, `Dockerfile.bot`, perfil `asistente` del compose) no toca la base: usa
  `/internal/asistente/*` con `ASISTENTE_SECRETO_INTERNO`. Topes diarios por cuenta (`ASISTENTE_MAX_MENSAJES_DIA`, `ASISTENTE_MAX_TOKENS_DIA`, `ASISTENTE_MAX_MEDIOS_DIA`) y eventos JSON del bot en `docker logs finanzas-bot`.
  Fotos y notas de voz (`asistente/medios.py`): máx. 5 MB y 90 s, el tipo sale de los primeros bytes (no de Telegram), se descargan solo con vínculo y cupo, nunca se guardan (el historial lleva `[foto]`/`[nota de voz]`) y con archivo la guardia de cifras sin consulta se relaja (las escrituras siguen exigiendo confirmación).
  Auditoría del asistente: `tests/auditoria/run.sh --seccion asistente` (ver la skill `auditoria-uso-real`). Todo cambio en `permisos.py`, `tokens.py` o la parte del asistente en `security.py` pasa por `revisor-seguridad`.
- `static/`: SPA con módulos ES6 y hojas de estilo en `static/css/`. `static/js/confirmar.js` es el modal de confirmación (`confirmar({...})` devuelve una promesa); no se usa `confirm()` nativo.
- Datos: `data/master.db` (usuarios y auditoría) y `data/tenants/user_{id}.db` (una base por usuario).

## Herramientas de Claude Code en `.claude/`

Carpeta local (excluida de git en `.git/info/exclude`); parte de su formato viene de ECC (MIT). Qué hay y cuándo se usa:

| Qué | Dónde | Cuándo |
|---|---|---|
| Skill `verificar` | `.claude/skills/verificar/` | Antes de cada commit: reglas, cache-busting, suites en paralelo y la prueba que falla con el código anterior |
| Skill `auditoria-uso-real` | `.claude/skills/auditoria-uso-real/` | Correr o repetir episodios del arnés (`tests/auditoria/`), controles con `--fuente`, clasificar arnés frente a app |
| Skill `portar-saas` | `.claude/skills/portar-saas/` | Solo cuando el usuario pide pasar commits al SaaS |
| Agente `revisor-backend` | `.claude/agents/` | Revisar cambios en `routers/`, `finanzas_core.py`, `database.py`, `dinero.py` |
| Agente `revisor-seguridad` | `.claude/agents/` | Cambios en auth, admin, respaldo, `security.py`, `main.py` o `innerHTML` |
| Agente `cazador-fallos-silenciosos` | `.claude/agents/` | Errores tragados y valores de respaldo que pasan por dato real |
| Agente `analista-pruebas` | `.claude/agents/` | Si las pruebas de un cambio lo cubren y fallarían antes |
| Reglas por ruta | `.claude/rules/backend.md`, `frontend.md`, `pruebas.md` | Se cargan solas al tocar esos archivos; son las lecciones de la auditoría (H, E, O, L) |
| Hook antes de commit | `.claude/hooks/antes_de_commit.py` | Automático: bloquea `Co-Authored-By`, `?v=` desactualizados y `verificar_reglas.sh` en rojo; avisa si el mensaje no es `tipo(area): …` |

Los agentes se lanzan solo cuando el usuario los pide, con una excepción que el usuario dejó fija:

- **`revisor-seguridad` siempre en cambios de autenticación**, antes del commit: `routers/auth.py`, `security.py`,
  las funciones de credenciales de `master_db.py`, `static/js/auth.js` y los formularios de login, perfil, contraseña,
  recuperación o eliminación de cuenta en `static/index.html`. Sus hallazgos críticos o altos se arreglan antes del commit.

El plan y los hallazgos de la auditoría por uso real viven en
`~/app-plan/plan-auditoria-uso-real.md`.

## Cómo verificar

- Pruebas rápidas de finanzas: `python3 test_finanzas.py`. Requiere las dependencias de `requirements.txt` (`pydantic==2.14.1` en particular); en Python del sistema con PEP 668 conviene un entorno virtual.
- Suites de backend en Docker: `./tests/run_tests.sh` (finanzas, migraciones, mercado, acciones, aislamiento de API y escenario de API).
- Batería completa, con cobertura y navegador: `./tests/run_todo.sh` (unos 6 minutos).
- Cobertura de `routers/`: `./tests/run_cobertura.sh`. Mínimo 80 %; solo debe subir. Subirlo en el propio script al añadir pruebas.
- Frontend con Playwright: `./tests/run_frontend.sh`. El script instala Playwright con pip; si la red corta la instalación, se salta sin fallar (`SKIP`). Todas deben salir en `OK`.

## Criterio de hecho

Un cambio está terminado cuando:
- Las suites de backend y de navegador pasan en Community (`./tests/run_todo.sh` antes de publicar).
- Hay una prueba que falla con el código anterior: el cambio tiene su caso, no solo verde.
- Si toca frontend, `python3 scripts/cache_bust.py` está ejecutado y el cambio se ve bien en móvil y escritorio.
- Antes de publicar en el SaaS: `~/bin/portar-a-saas.sh <commit> --probar`, y solo después su propio commit y push.

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
- Confirmaciones: `confirmar()` de `static/js/confirmar.js`, nunca `confirm()` ni `alert()` nativos. Las pruebas de navegador responden con `_responder_confirmacion`.

## Decisiones del usuario que no se vuelven a proponer

- El patrimonio es metas + inversiones; no se suma el efectivo ni se resta la deuda.
- Las inversiones y cuentas con rendimiento son una calculadora: no generan movimientos de caja.
- No se puede tener un gasto fijo sin un ingreso fijo activo: el bloqueo es deliberado, no una advertencia.
- El historial y la gráfica del Resumen siguen la frecuencia de cobro del ingreso fijo principal: un periodo por mes de cobro, quincena o semana (meses calendario si no hay ingreso fijo). Se retiró `mes_actual` del resumen (ninguna pantalla lo usaba) y `/api/resumen-mensual` pasó a `/api/historial-periodos` (`historial_por_periodos`).
- No hay frecuencia anual: solo semanal, quincenal y mensual. Se quitó el código a medias que la contemplaba (`siguiente_fecha`, `mensualizar`, `_ingreso_principal`); la API y la base ya la rechazaban.

## Abierto

- **Prueba intermitente:** `test_botones_de_fila_y_pestanas_tienen_area_tactil_de_44px` falló una vez esperando filas;
  no se pudo reproducir (12 corridas seguidas en verde, tres a la vez con la máquina cargada). Si vuelve a fallar en la
  batería completa, guardar la captura y el log de ese momento antes de repetirla.
- **Visual, hojas de estilo (consolidación cerrada):** `scripts/css_muerto.py static/css [--aplicar]` quita las
  declaraciones que una regla posterior (mismo selector, o con `:root ` delante, y mismo `@media`) ya sobrescribe siempre;
  no toca los respaldos de una misma regla ni los selectores que pueden ser `<html>`. Quitó 465 declaraciones (197 KB a
  182 KB) con 0 diferencias en el volcado de `~/bin/regresion-visual` (206 118 elementos, 110 pantallas). Lo que queda no
  se fusiona: de los 144 selectores en más de una hoja, 134 son el par `X` / `:root X`, donde `tema.css` sube la
  especificidad a propósito (fundirlos cambia la cascada), solo 6 son el mismo selector exacto (206 bytes) y los 40
  repetidos dentro de una hoja suman 1 KB. No compensa el riesgo.
- **Respaldo nocturno automático:** quitado de Community; pendiente para producción con permisos correctos en `data/backups`.
- **Seguridad, límite conocido:** una IP puede probar 20 cuentas por ventana de 5 min; una botnet no queda frenada.
