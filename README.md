# 💰 Finanzas Cloud

Una plataforma web moderna, ligera, minimalista y autohospedada para llevar el control integral de finanzas personales, metas de ahorro y portafolios de inversión con **arquitectura Multi-Tenant (base de datos aislada por usuario)** y autenticación mediante **JSON Web Tokens (JWT)**.

Diseñada desde cero para ser rápida, privada y correr de forma ultra eficiente en servidores locales o dispositivos como una Raspberry Pi mediante Docker.

---

## 🚀 Tecnologías Utilizadas

* **Backend:** [FastAPI](https://fastapi.tiangolo.com/) (Python 3.11) con inyección dinámica de dependencias.
* **Seguridad & Auth:** [PyJWT](https://pyjwt.readthedocs.io/) (Firma de tokens HS256) y [bcrypt](https://pypi.org/project/bcrypt/) (Hashing seguro con salt automático).
* **Base de Datos Dual (Multi-Tenant):** [SQLite](https://www.sqlite.org/) con modo WAL (`PRAGMA journal_mode=WAL;`):
  * **Master DB:** `data/auth_master.db` (Control de usuarios, roles, estado y métricas de acceso).
  * **Tenant DBs:** `data/tenants/user_{id}.db` (Bases de datos independientes y 100% aisladas para cada usuario).
* **Frontend:** HTML5, CSS3 Moderno (Variables CSS & Modo Oscuro), JavaScript (Vanilla ES6+ SPA) y cliente interceptor `apiFetch`.
* **Visualización de Datos:** [Chart.js](https://www.chartjs.org/) (Gráficas de barras mensuales y dona de asignación de activos).
* **Servidor ASGI:** [Uvicorn](https://www.uvicorn.org/) (Producción sin live-reload; para desarrollo usa `--reload` localmente).
* **Despliegue & Contenedor:** Docker y Docker Compose.

---

## ✨ Características y Módulos

### 1. 🛡️ Arquitectura Multi-Tenant Dinámica (DB por Usuario)
* **Aislamiento Total de Datos (Zero-Leakage):** Cada usuario posee su propio archivo SQLite físico (`data/tenants/user_{id}.db`). No existe riesgo de filtración de datos cruzados entre usuarios.
* **Sin Duplicación de Código:** Una sola instancia de FastAPI y un solo Frontend SPA. El backend resuelve dinámicamente en cada petición la conexión correspondiente al tenant extrayendo el `db_name` del JWT verificado.
* **Aprovisionamiento Automático:** Al dar de alta un usuario desde el panel de administración, el sistema crea e inicializa automáticamente su base de datos individual con todas las tablas e índices.

### 2. 🔐 Autenticación JWT y Guardias de Sesión
* **Inicio de Sesión Seguro:** Endpoint `POST /auth/login` con validación de contraseña mediante bcrypt y verificación de cuenta activa (`is_active == 1`).
* **Auditoría de Actividad:** Cada inicio de sesión exitoso actualiza en `auth_master.db` la fecha/hora de `last_login` e incrementa el contador `login_count`.
* **Interceptor Automático (`apiFetch`):** Todas las peticiones del Frontend adjuntan la cabecera `Authorization: Bearer <token>`. Si el token expira o el usuario es suspendido, el sistema redirige automáticamente al login.
* **Purga Limpia de Sesión (Logout):** Al cerrar sesión o cambiar de cuenta, el cliente destruye las instancias de Chart.js, purga la memoria y limpia el DOM para garantizar que ningún dato quede visible en caché para el siguiente usuario.

### 3. 👥 Panel de Administración de Usuarios (Solo Admin)
* Pestaña exclusiva `🛡️ Admin` visible únicamente para cuentas con rol `admin`.
* **Tarjetas KPI Globales:**
  * 👥 Total de usuarios registrados.
  * 🟢 Usuarios activos hoy y en los últimos 7 días.
  * 🔴 Usuarios suspendidos o inactivos.
  * 💾 Almacenamiento total acumulado en disco por todas las bases de datos tenant.
* **Tabla de Gestión de Usuarios:**
  * Usuario, Rol, Estado (Activo / Suspendido).
  * Fecha de creación y **Último Acceso en tiempo relativo** (*"Hace 10 min"*, *"Hace 2 horas"*, *"Nunca"*).
  * Total de inicios de sesión y peso de su base de datos individual en KB/MB.
* **Controles Administrativos:**
  * Modal para **Crear Nuevos Usuarios** (con rol `user` o `admin`).
  * Botón para **Suspender / Reactivar** acceso de usuarios en tiempo real.
  * Botón para **Eliminar usuario** con borrado físico de su base de datos.
  * Protección contra auto-bloqueo y auto-eliminación de la cuenta del administrador en sesión.

### 4. 📊 Dashboard Central & Patrimonio Neto (Net Worth)
* **Métrica Consolidada:** Tu Patrimonio Neto es lo que tienes ahorrado e invertido, en tiempo real:
  * 🐖 **Apartados y Metas** (Ahorro guardado en sobres).
  * 📈 **Portafolio de Inversión** (Valor de mercado actual de acciones y ETFs).
  * El **Balance Disponible** (efectivo en caja) se muestra aparte y no forma parte del patrimonio.
* **Métricas de Flujo:** Ingresos totales, gastos acumulados y rendimiento global de inversiones.
* **Gráfica Mensual:** Comparativa visual de ingresos vs gastos mes a mes con Chart.js.

### 5. 💳 Movimientos y 🎯 Metas de Ahorro
* Registro rápido de ingresos y gastos con modales de edición in situ.
* **Periodos según tu ingreso fijo:** el corte lo marca tu ingreso fijo de mayor monto (quincenal, semanal o mensual). Cada periodo va del día de cobro al día anterior al siguiente. Sin ingreso fijo, se usan meses calendario.
* **Tarjetas del periodo:** ingreso (recibido y por recibir), gastos normales, fijos y MSI, remanente y saldo al cierre. Los aportes a metas se muestran aparte y no cuentan como gasto.
* **Tabla de periodos:** los periodos en curso y los siguientes, con el saldo que se arrastra de uno a otro.
* Sobres libres de ahorro y metas con barra de progreso porcentual.
* Transferencias protegidas contra depósitos que excedan el balance disponible.

### 6. 📈 Inversiones & Asset Allocation en Tiempo Real
* **Autocompletado Predictivo:** Búsqueda en vivo de tickers en Yahoo Finance (símbolo, nombre, bolsa).
* **Soporte Multidivisa Inteligente:** Tratamiento diferenciado de activos en `USD` y `MXN` con tipos de cambio oficiales.
* **Gráfica de Dona:** Visualización del peso porcentual de cada activo sobre el portafolio total.
* **Sincronización con 1 Clic:** Actualización en lote de cotizaciones y rendimientos de mercado.

### 7. 📑 Exportación a Excel y Respaldos Aislados
* **Excel con Gráfica Nativa de Pastel:** Descarga el reporte contable del tenant activo con Asset Allocation en formato `.xlsx`.
* **Copia de Seguridad por Usuario:** Botón directo para descargar el archivo `.db` propio del usuario en sesión o restaurarlo. La restauración valida cabecera, integridad y esquema, rechaza archivos dañados o ajenos sin tocar los datos actuales y guarda una copia `.bak` antes de reemplazar.

---

### 8. 🔁 Fijos, MSI y Cuentas con Rendimiento
* **Fijos y MSI:** nómina (quincenal, mensual o semanal), gastos fijos (renta, suscripciones) y compras a meses. Cada registro genera sus movimientos reales solo cuando llega su fecha; nunca se insertan cobros futuros.
* **Compras a meses:** el balance baja al vencer cada cuota; la deuda pendiente se muestra aparte y no resta del patrimonio neto.
* **Compras pausadas:** no tienen cobros programados, pero su deuda sigue contando en Deuda MSI hasta liquidarlas.
* **Cuentas con rendimiento:** saldos con tasa anual fija (interés diario compuesto). No modifican el balance disponible; aportar, retirar o cambiar la tasa capitaliza el saldo actual.
* **Categorías:** cada movimiento tiene categoría; el dashboard muestra el gasto del mes por categoría.
* **Saltar pagos:** un fijo puede saltarse una vez y una cuota MSI puede contabilizarse o aplazarse.

### 9. 🧮 Presupuesto por Pagos
* Tarjetas de presupuesto por categoría con reparto por quincenas o semanas (simétrico o personalizado).
* Los repartos se calculan en centavos: la suma de las tarjetas de un pago siempre coincide exactamente con su ingreso.
* Pagos del periodo: según la frecuencia del ingreso (semanal 4, quincenal 2, mensual 1), aparecen bloques de pago con su día de cobro y sus propias tarjetas. Cada pago muestra su ingreso, lo asignado y lo libre, y no puede asignar más que su ingreso.
* Cada pago tiene su propia distribución del ingreso: una barra con sus tarjetas y lo que queda libre.
* Compras a meses sin intereses dentro de un pago: una tarjeta MSI aporta su cuota mensual y, al exportar, se convierte en una compra a cuotas con el plazo indicado.
* Exportación del presupuesto a gastos fijos con fecha de inicio configurable. Es un reemplazo total: borra todos los fijos, también las compras a meses que no estén en el presupuesto, y crea los del presupuesto. Cada tarjeta de un pago es un fijo mensual en el día de su pago.

### 10. 🩺 Respaldo Automático y Monitoreo
* **Respaldo nocturno:** `scripts/respaldo_automatico.py` copia la base maestra y todos los tenants a `data/backups/`, verifica cada copia y conserva 14 días.
* **Respaldo previo a migrar:** antes de actualizar el esquema de un tenant se guarda una copia en `data/backups_premigracion/`.
* **`/health`:** estado de la base maestra y antigüedad del último respaldo; lo usa el `healthcheck` de Docker.

---

## 📂 Estructura del Proyecto

```text
Finanzas-Personales-Web-App-Community/
├── main.py              # App FastAPI: middleware, lifespan (arranque), SPA e include_router
├── routers/             # Endpoints por dominio
│   ├── auth.py          # Login, perfil, recuperación de contraseña
│   ├── admin.py         # Administración de usuarios y métricas (solo admin)
│   ├── movimientos.py   # Transacciones, categorías, resumen y gasto por categoría
│   ├── recurrentes.py   # Fijos y compras a meses (MSI)
│   ├── apartados.py     # Sobres y metas de ahorro
│   ├── inversiones.py   # Posiciones bursátiles, cuentas con rendimiento y tipos de cambio
│   ├── presupuesto.py   # Presupuesto por pagos del periodo, exportación a fijos
│   ├── respaldo.py      # Respaldo, restauración y exportación a Excel
│   └── salud.py         # Endpoint /health para Docker y monitoreo
├── finanzas_core.py     # Lógica financiera pura: conversión, resumen, recurrentes, MSI y rendimiento
├── mercado.py           # Cotizaciones de Yahoo Finance y tipos de cambio (en paralelo, con caché)
├── database.py          # Esquema por tenant, migraciones versionadas (v1–v12) y conexión por petición
├── master_db.py         # Base maestra (usuarios, OTP y estadísticas admin)
├── security.py          # bcrypt, JWT, dependencias verify_token/verify_admin y freno anti fuerza bruta
├── export_service.py    # Reporte Excel (.xlsx) con hojas de resumen, movimientos, inversiones, fijos y rendimientos
├── email_service.py     # Correos transaccionales (API o SMTP)
├── test_finanzas.py     # Pruebas de la lógica financiera (sin dependencias)
├── tests/               # Pruebas en Docker: API, aislamiento, migraciones, respaldo y navegador (Playwright)
├── scripts/             # respaldo_automatico, migrar_todos, cache_bust y verificar_reglas
├── docs/DESARROLLO.md   # Guía de desarrollo, pruebas y mantenimiento
├── requirements.txt     # Dependencias con versiones fijas (fastapi, uvicorn, pydantic, openpyxl, bcrypt, pyjwt)
├── Dockerfile           # Imagen Docker basada en python:3.11-slim (sin --reload)
├── docker-compose.yml   # Compose con ./data montado (puerto 8002 → 8001) y healthcheck
├── .env.example         # Variables de entorno (JWT y correo)
├── README.md            # Documentación del proyecto
├── LICENSE              # GNU General Public License v3.0 (GPLv3)
├── data/                # Almacenamiento local (ignorado en git)
│   ├── auth_master.db   # Base maestra de usuarios
│   ├── tenants/         # Una base SQLite por usuario (user_1.db, user_2.db...)
│   └── backups/         # Respaldos automáticos con rotación
└── static/
    ├── index.html       # SPA: login, pestañas y modales
    ├── css/             # tokens.css, componentes.css, layout.css, vistas.css
    └── js/              # ES modules: main (arranque), core (estado y helpers), auth, ui,
                         # dashboard, movimientos, recurrentes, apartados, inversiones, admin, respaldo
```

---

## 🛠️ Instalación y Despliegue con Docker

1. **Clonar el repositorio:**
   ```bash
   git clone https://github.com/Ricardo-Gomez-4-113/Finanzas-Personales-Web-App-Community.git
   cd Finanzas-Personales-Web-App-Community
   ```

2. **Configurar variables de entorno y clave secreta:**
   ```bash
   cp .env.example .env
   ```
   Genera una clave criptográfica segura para `JWT_SECRET_KEY` (de al menos 32 bytes / 64 caracteres hex):
   * **Con Docker (recomendado, sin requerir Python en el host):**
     ```bash
     docker run --rm python:3.11-slim python3 -c "import secrets; print(secrets.token_hex(32))"
     ```
   * **Con Python local:**
     ```bash
     python3 -c "import secrets; print(secrets.token_hex(32))"
     ```
   * **Con OpenSSL (Linux / macOS):**
     ```bash
     openssl rand -hex 32
     ```
   Pega la clave generada en tu archivo `.env`:
   ```env
   JWT_SECRET_KEY=tu_clave_hexadecimal_generada_aqui
   ```

3. **Construir e iniciar los contenedores:**
   ```bash
   docker compose up -d --build
   ```

4. **Abrir en el navegador:**
   Ingresa a `http://localhost:8002` (o el puerto que definas en `docker-compose.yml`).

   > Para desarrollo con recarga automática, ejecuta sin Docker:
   > ```bash
   > uvicorn main:app --reload --port 8001
   > ```
   > Tras cambiar código en Docker, vuelve a construir con `docker compose up -d --build`.

5. **Programar el respaldo nocturno (recomendado):** agrega al crontab del host (`crontab -e`):
   ```cron
   0 3 * * * docker exec finanzas-community python3 scripts/respaldo_automatico.py
   ```
   Mientras no exista un respaldo de las últimas 26 horas, `/health` reportará `"respaldo": "vencido"`.

### 🔑 Credenciales Iniciales por Defecto

Al arrancar por primera vez, el sistema crea automáticamente el usuario Administrador y migra los datos preexistentes:

* **Usuario:** `admin`
* **Contraseña:** la que definas en `DEFAULT_ADMIN_PASSWORD` dentro de `.env`. Si no la defines, se genera una clave temporal aleatoria y se imprime una sola vez en los logs (`docker logs finanzas-community`); cámbiala al iniciar sesión.
* **Rol:** `admin`

> [!TIP]
> Una vez dentro, puedes ingresar a la pestaña `🛡️ Admin` para crear nuevos usuarios o dar de alta cuentas adicionales con su respectivo correo electrónico.

## 📧 Configuración del Servicio de Correo (Opcional)

La versión comunitaria incluye soporte para el envío de códigos de recuperación de contraseña de 6 dígitos. Puedes conectar tu propio servicio de correo mediante API o servidor SMTP configurando el archivo `.env`:

1. **Copia la plantilla de configuración:**
   ```bash
   cp .env.example .env
   ```

2. **Define las credenciales de tu proveedor:**
   * **Opción A: Vía API de Correo**
     ```env
     RESEND_API_KEY=tu_api_key_aqui
     RESEND_FROM=Finanzas Cloud <notificaciones@finanzas.rgmlabs.org>
     ```
   * **Opción B: Vía Servidor SMTP**
     ```env
     SMTP_HOST=smtp.tu-servidor.com
     SMTP_PORT=587
     SMTP_USER=tu_usuario@tu-dominio.com
     SMTP_PASSWORD=tu_contrasena_o_token
     SMTP_FROM=Finanzas Cloud <tu_usuario@tu-dominio.com>
     ```

> [!NOTE]
> Si no configuras un proveedor de correo, la aplicación opera de forma autónoma en modo local. Las contraseñas pueden ser administradas directamente desde el panel de administración.

---

## 🌐 Resumen de Endpoints de la API

### Autenticación, Perfil y Recuperación
| Método | Endpoint | Seguridad | Descripción |
| :--- | :--- | :--- | :--- |
| `POST` | `/auth/login` | Público | Autenticación con credenciales, registro de auditoría y emisión de JWT |
| `GET` | `/auth/me` | `verify_token` | Retorna los datos del usuario en sesión activa |
| `PATCH` | `/auth/profile` | `verify_token` | Permite al usuario activo modificar su nombre de usuario o contraseña |
| `POST` | `/auth/forgot-password` | Público | Genera y envía código de 6 dígitos por email (máximo 2 intentos por usuario en 24h) |
| `POST` | `/auth/reset-password` | Público | Valida código y actualiza clave (máximo 2 intentos fallidos antes de cancelarlo) |
| `DELETE` | `/auth/delete-account` | `verify_token` | Elimina la cuenta propia y su base de datos |

### Administración (Exclusivo Admin)
| Método | Endpoint | Seguridad | Descripción |
| :--- | :--- | :--- | :--- |
| `GET` | `/admin/stats` | `verify_admin` | KPIs globales: total usuarios, activos hoy/7d, inactivos y peso en disco |
| `GET` | `/admin/users` | `verify_admin` | Lista todos los usuarios con fecha relativa de acceso y tamaño de BD |
| `POST` | `/admin/users` | `verify_admin` | Crea usuario y aprovisiona automáticamente su base de datos individual |
| `PATCH` | `/admin/users/{id}` | `verify_admin` | Modifica nombre de usuario, contraseña o rol (renueva JWT si es la propia cuenta) |
| `PATCH` | `/admin/users/{id}/status` | `verify_admin` | Activa o suspende el acceso de un usuario (protege al admin actual) |
| `DELETE` | `/admin/users/{id}` | `verify_admin` | Elimina usuario y purga su BD tenant (**protegido**: no se puede borrar administradores) |

### Endpoints Financieros Multi-Tenant (Aislados por Usuario)
| Método | Endpoint | Seguridad | Descripción |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/resumen` | `verify_token` | Resumen patrimonial del tenant en la divisa base, con los periodos del ingreso fijo (`periodos`) |
| `GET` | `/api/resumen-mensual` | `verify_token` | Agrupación de ingresos y gastos mensuales |
| `GET` | `/api/transacciones` | `verify_token` | Listado de movimientos con categoría y origen (fijo, MSI o apartado) |
| `POST` | `/api/transacciones` | `verify_token` | Registrar movimiento (la categoría debe existir) |
| `PUT` | `/api/transacciones/{id}` | `verify_token` | Editar movimiento (las cuotas MSI y los ahorros de apartado no se editan aquí) |
| `DELETE` | `/api/transacciones/{id}` | `verify_token` | Eliminar movimiento (mismas restricciones que la edición) |
| `POST` | `/api/transacciones/eliminar-lote` | `verify_token` | Eliminar varios movimientos a la vez |
| `GET` | `/api/categorias` | `verify_token` | Listar categorías de gasto e ingreso |
| `POST` | `/api/categorias` | `verify_token` | Crear categoría (nombre único por tipo) |
| `GET` | `/api/gastos-por-categoria` | `verify_token` | Gasto del mes por categoría (`?mes=YYYY-MM&moneda_base=`), sin movimientos de apartados |
| `GET` | `/api/recurrentes` | `verify_token` | Fijos y compras a meses con deuda pendiente y progreso |
| `POST` | `/api/recurrentes` | `verify_token` | Crear fijo o compra a meses (`meses` + `monto_total`) |
| `PATCH` | `/api/recurrentes/{id}` | `verify_token` | Editar descripción, monto (solo fijos), categoría o pausar/reanudar |
| `DELETE` | `/api/recurrentes/{id}` | `verify_token` | Eliminar registro; los movimientos ya generados se conservan |
| `POST` | `/api/recurrentes/{id}/saltar` | `verify_token` | Saltar un pago o aplazar una cuota MSI |
| `GET` | `/api/presupuesto` | `verify_token` | Presupuesto con pagos, sus tarjetas y los totales de todos los pagos |
| `PUT` | `/api/presupuesto/config` | `verify_token` | Configurar ingreso base, frecuencia y subdivisión |
| `POST` / `PATCH` / `DELETE` | `/api/presupuesto/tarjetas[/{id}]` | `verify_token` | Crear, editar o eliminar tarjetas de presupuesto |
| `PUT` | `/api/presupuesto/unidades/{orden}` | `verify_token` | Fijar el día de cobro de un pago (1 a 4) |
| `POST` | `/api/presupuesto/exportar-a-fijos` | `verify_token` | Convertir el presupuesto en gastos fijos |
| `GET` | `/api/rendimiento` | `verify_token` | Cuentas con rendimiento: valor actual, interés y ganancia mensual |
| `POST` | `/api/rendimiento` | `verify_token` | Crear cuenta con rendimiento |
| `POST` | `/api/rendimiento/{id}/movimiento` | `verify_token` | Aportar o retirar (capitaliza el saldo; no toca el balance) |
| `PUT` | `/api/rendimiento/{id}` | `verify_token` | Cambiar nombre o tasa (capitaliza con la tasa anterior) |
| `DELETE` | `/api/rendimiento/{id}` | `verify_token` | Eliminar cuenta con rendimiento |
| `GET` | `/api/apartados` | `verify_token` | Listar sobres y metas de ahorro |
| `POST` | `/api/apartados` | `verify_token` | Crear apartado o meta |
| `POST` | `/api/apartados/{id}/movimiento` | `verify_token` | Depositar o retirar fondos de un apartado |
| `DELETE` | `/api/apartados/{id}` | `verify_token` | Eliminar apartado (valida saldo en cero) |
| `GET` | `/api/inversiones` | `verify_token` | Portafolio con valuación y Asset Allocation |
| `POST` | `/api/inversiones` | `verify_token` | Dar de alta posición bursátil |
| `PUT` | `/api/inversiones/{id}` | `verify_token` | Modificar títulos o precio promedio |
| `DELETE` | `/api/inversiones/{id}` | `verify_token` | Eliminar posición bursátil |
| `POST` | `/api/inversiones/sincronizar` | `verify_token` | Sincronizar cotizaciones en vivo en lote |
| `GET` | `/api/inversiones/cotizar/{ticker}` | `verify_token` | Consultar precio de mercado en tiempo real |
| `GET` | `/api/inversiones/buscar` | `verify_token` | Autocompletado de tickers de Yahoo Finance |
| `GET` | `/api/tipo-cambio` | `verify_token` | Tipos de cambio vigentes con caché |
| `GET` | `/api/exportar-excel` | `verify_token` | Descarga de reporte Excel del tenant (incluye hojas de fijos y rendimientos) |
| `GET` | `/api/backup` | `verify_token` | Descarga de la base de datos SQLite del tenant |
| `POST` | `/api/restore` | `verify_token` | Restaurar la base del tenant desde un `.db` (máx. 50 MB, validado) |

### Monitoreo
| Método | Endpoint | Seguridad | Descripción |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Público | Estado de la base maestra y del último respaldo (503 si la base no responde). No expone datos de usuarios |

---

## 🧪 Entorno de Pruebas Local

Variables ficticias en `.env.test` para levantar la app sin Docker y explorarla:

```bash
pip install -r requirements.txt
set -a; . ./.env.test; set +a      # carga las variables de prueba
uvicorn main:app --reload --port 8001
```

* Aplicación: http://127.0.0.1:8001 (usuario `admin`, contraseña `admin123`)
* Documentación de la API: http://127.0.0.1:8001/docs
* Para empezar de cero, borra la carpeta `data/`.

> `.env.test` solo contiene valores de desarrollo. Nunca lo uses en producción; el archivo real de producción es `.env` (no se versiona).

## ✅ Pruebas

```bash
python3 test_finanzas.py   # lógica financiera: rápido, sin Docker ni dependencias
./tests/run_tests.sh       # todo en Docker: reglas, finanzas, migraciones, respaldo y API
./tests/run_frontend.sh    # navegador real (Playwright): login, pestañas, desbordamiento y foco en modales
```

Las pruebas en Docker corren en contenedores efímeros: no tocan tu instancia ni la carpeta `data/`. Cubren aislamiento entre usuarios, tokens JWT manipulados (incluido `alg: none`), permisos de admin, límite de intentos de login, restauración con archivos dañados y la cadena completa de migraciones.

### Contribuir
* Toda lógica de dinero, fechas o repartos va en `finanzas_core.py`, con su caso en `test_finanzas.py`.
* Después de cambiar CSS o JS ejecuta `python3 scripts/cache_bust.py` (actualiza los `?v=`); `./scripts/verificar_reglas.sh` revisa que no falte.
* La UI usa micro-SVG en lugar de emojis y los colores de texto de estado vienen de `--success-text`, `--danger-text` y `--warning-text`.
* Guía completa en [`docs/DESARROLLO.md`](docs/DESARROLLO.md).

---

## 📜 Licencia y Términos de Uso

Este proyecto se distribuye bajo la licencia **GNU General Public License v3.0 (GNU GPLv3)**. Consulta el archivo [`LICENSE`](./LICENSE) para el texto legal completo.

* **Libertad de Uso y Estudio (incluyendo fines comerciales e internos):** Puedes descargar, auditar, desplegar y utilizar esta plataforma con total libertad para cualquier propósito personal, familiar, comercial o corporativo interno.
* **Libertad de Modificación y Contribución:** Eres libre de modificar el código fuente, corregir errores o añadir nuevas funcionalidades, siempre y cuando cualquier redistribución o trabajo derivado se mantenga bajo los mismos términos libres y de código abierto de la licencia GPLv3.
* **Copyleft y Código Abierto:** Obligación irrestricta de distribuir cualquier software derivado o redistribuido bajo la misma licencia GNU GPLv3, entregando el código fuente completo accesible para toda la comunidad y prohibiendo cierres privativos.

> **Aviso Legal:** Finanzas Cloud es un proyecto desarrollado de forma independiente por RGM Labs. No está afiliado, respaldado ni asociado con ninguna otra entidad o marca registrada con el mismo nombre.

