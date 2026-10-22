# 💰 Finanzas Cloud

Una plataforma web moderna, ligera, minimalista y autohospedada para llevar el control integral de finanzas personales, metas de ahorro y portafolios de inversión con **arquitectura Multi-Tenant (base de datos aislada por usuario)** y autenticación mediante **JSON Web Tokens (JWT)**.

Diseñada desde cero para ser rápida, privada y correr de forma ultra eficiente en servidores locales o dispositivos como una Raspberry Pi mediante Docker.

---

## 🚀 Tecnologías Utilizadas

* **Backend:** [FastAPI](https://fastapi.tiangolo.com/) (Python 3.11) con inyección dinámica de dependencias.
* **Seguridad & Auth:** [PyJWT](https://pyjwt.readthedocs.io/) (Firma de tokens HS256) y [bcrypt](https://pypi.org/project/bcrypt/) (Hashing seguro con salt automático).
* **Base de Datos Dual (Multi-Tenant):** [SQLite](https://www.sqlite.org/) con modo WAL (`PRAGMA journal_mode=WAL;`):
  * **Master DB:** `data/master.db` (Control de usuarios, roles, estado y métricas de acceso).
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
* **Auditoría de Actividad:** Cada inicio de sesión exitoso actualiza en `master.db` la fecha/hora de `last_login` e incrementa el contador `login_count`.
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
* **Métrica Consolidada:** Cálculo en tiempo real de tu Patrimonio Neto Total integrando los 3 pilares financieros:
  * 💵 **Balance Disponible** (Efectivo en caja).
  * 🐖 **Apartados y Metas** (Ahorro guardado en sobres).
  * 📈 **Portafolio de Inversión** (Valor de mercado actual de acciones y ETFs).
* **Métricas de Flujo:** Ingresos totales, gastos acumulados y rendimiento global de inversiones.
* **Gráfica Mensual:** Comparativa visual de ingresos vs gastos mes a mes con Chart.js.

### 5. 💳 Movimientos y 🎯 Metas de Ahorro
* Registro rápido de ingresos y gastos con modales de edición in situ.
* Sobres libres de ahorro y metas con barra de progreso porcentual.
* Transferencias protegidas contra depósitos que excedan el balance disponible.

### 6. 📈 Inversiones & Asset Allocation en Tiempo Real
* **Autocompletado Predictivo:** Búsqueda en vivo de tickers en Yahoo Finance (símbolo, nombre, bolsa).
* **Soporte Multidivisa Inteligente:** Tratamiento diferenciado de activos en `USD` y `MXN` con tipos de cambio oficiales.
* **Gráfica de Dona:** Visualización del peso porcentual de cada activo sobre el portafolio total.
* **Sincronización con 1 Clic:** Actualización en lote de cotizaciones y rendimientos de mercado.

### 7. 📑 Exportación a Excel y Respaldos Aislados
* **Excel con Gráfica Nativa de Pastel:** Descarga el reporte contable del tenant activo con Asset Allocation en formato `.xlsx`.
* **Copia de Seguridad por Usuario:** Botón directo para descargar el archivo `.db` propio del usuario en sesión.

---

### 8. 🔁 Fijos, MSI y Cuentas con Rendimiento
* **Fijos y MSI:** nómina (quincenal, mensual o semanal), gastos fijos (renta, suscripciones) y compras a meses. Cada registro genera sus movimientos reales solo cuando llega su fecha; nunca se insertan cobros futuros.
* **Compras a meses:** el balance baja al vencer cada cuota; el patrimonio neto resta la deuda pendiente.
* **Cuentas con rendimiento:** saldos con tasa anual fija (interés diario compuesto). No modifican el balance disponible; aportar, retirar o cambiar la tasa capitaliza el saldo actual.
* **Categorías:** cada movimiento tiene categoría; el dashboard muestra el gasto del mes por categoría.

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
│   └── respaldo.py      # Respaldo, restauración y exportación a Excel
├── finanzas_core.py     # Lógica financiera pura: conversión, resumen, recurrentes, MSI y rendimiento
├── mercado.py           # Cotizaciones de Yahoo Finance y tipos de cambio (en paralelo, con caché)
├── database.py          # Esquema por tenant, migraciones versionadas (v1–v4) y conexión por petición
├── master_db.py         # Base maestra (usuarios, OTP y estadísticas admin)
├── security.py          # bcrypt, JWT, dependencias verify_token/verify_admin y freno anti fuerza bruta
├── export_service.py    # Reporte Excel (.xlsx) con hojas de resumen, movimientos, inversiones, fijos y rendimientos
├── email_service.py     # Correos transaccionales (API o SMTP)
├── test_finanzas.py     # Pruebas de la lógica financiera (sin dependencias)
├── requirements.txt     # Dependencias (fastapi, uvicorn, pydantic, openpyxl, bcrypt, pyjwt)
├── Dockerfile           # Imagen Docker basada en python:3.11-slim (sin --reload)
├── docker-compose.yml   # Compose con ./data montado (puerto 8002 → 8001)
├── .env.example         # Variables de entorno (JWT y correo)
├── README.md            # Documentación del proyecto
├── LICENSE              # GNU General Public License v3.0 (GPLv3)
├── data/                # Almacenamiento local (ignorado en git)
│   ├── auth_master.db   # Base maestra de usuarios
│   └── tenants/         # Una base SQLite por usuario (user_1.db, user_2.db...)
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

### 🔑 Credenciales Iniciales por Defecto

Al arrancar por primera vez, el sistema crea automáticamente el usuario Administrador y migra los datos preexistentes:

* **Usuario:** `admin`
* **Contraseña:** `admin123`
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
| `GET` | `/api/resumen` | `verify_token` | Resumen patrimonial del tenant en la divisa base |
| `GET` | `/api/resumen-mensual` | `verify_token` | Agrupación de ingresos y gastos mensuales |
| `GET` | `/api/transacciones` | `verify_token` | Listado de movimientos con categoría y origen (fijo, MSI o apartado) |
| `POST` | `/api/transacciones` | `verify_token` | Registrar movimiento (la categoría debe existir) |
| `PUT` | `/api/transacciones/{id}` | `verify_token` | Editar movimiento (las cuotas MSI y los ahorros de apartado no se editan aquí) |
| `DELETE` | `/api/transacciones/{id}` | `verify_token` | Eliminar movimiento (mismas restricciones que la edición) |
| `GET` | `/api/categorias` | `verify_token` | Listar categorías de gasto e ingreso |
| `POST` | `/api/categorias` | `verify_token` | Crear categoría (nombre único por tipo) |
| `GET` | `/api/gastos-por-categoria` | `verify_token` | Gasto del mes por categoría (`?mes=YYYY-MM&moneda_base=`), sin movimientos de apartados |
| `GET` | `/api/recurrentes` | `verify_token` | Fijos y compras a meses con deuda pendiente y progreso |
| `POST` | `/api/recurrentes` | `verify_token` | Crear fijo o compra a meses (`meses` + `monto_total`) |
| `PATCH` | `/api/recurrentes/{id}` | `verify_token` | Editar descripción, monto (solo fijos), categoría o pausar/reanudar |
| `DELETE` | `/api/recurrentes/{id}` | `verify_token` | Eliminar registro; los movimientos ya generados se conservan |
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

## 🧪 Pruebas

```bash
python3 test_finanzas.py   # imprime OK si la lógica financiera cumple los casos base
```

Las pruebas usan solo la biblioteca estándar (sin pytest) y una base SQLite en memoria.

---

## 📜 Licencia y Términos de Uso

Este proyecto se distribuye bajo la licencia **GNU General Public License v3.0 (GNU GPLv3)**. Consulta el archivo [`LICENSE`](./LICENSE) para el texto legal completo.

* **Libertad de Uso y Estudio (incluyendo fines comerciales e internos):** Puedes descargar, auditar, desplegar y utilizar esta plataforma con total libertad para cualquier propósito personal, familiar, comercial o corporativo interno.
* **Libertad de Modificación y Contribución:** Eres libre de modificar el código fuente, corregir errores o añadir nuevas funcionalidades, siempre y cuando cualquier redistribución o trabajo derivado se mantenga bajo los mismos términos libres y de código abierto de la licencia GPLv3.
* **Copyleft y Código Abierto:** Obligación irrestricta de distribuir cualquier software derivado o redistribuido bajo la misma licencia GNU GPLv3, entregando el código fuente completo accesible para toda la comunidad y prohibiendo cierres privativos.

> **Aviso Legal:** Finanzas Cloud es un proyecto desarrollado de forma independiente por RGM Labs. No está afiliado, respaldado ni asociado con ninguna otra entidad o marca registrada con el mismo nombre.

