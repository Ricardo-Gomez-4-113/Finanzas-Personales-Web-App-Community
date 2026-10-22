"""
main.py - Servidor Principal de la Aplicación de Finanzas Personales Multi-Tenant

Este módulo implementa:
1. Arquitectura Multi-Tenant Dinámica con aislamiento físico de base de datos por usuario (SQLite).
2. Autenticación y Autorización basada en JSON Web Tokens (JWT).
3. Panel de Administración de Usuarios con control de acceso por roles (RBAC) y métricas KPI.
4. Endpoints financieros transparentes (gastos, apartados, inversiones, rendimientos y cotizaciones).
5. Exportación de reportes a Excel (.xlsx) y respaldos físicos de base de datos por tenant.
"""

from fastapi import FastAPI, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import os
import sqlite3
from contextlib import asynccontextmanager

from database import (
    ensure_tenant_directories
)
import master_db
from security import (
    hash_password
)
from routers import auth, admin, movimientos, apartados, inversiones, respaldo, recurrentes, presupuesto

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Rutina de arranque de la plataforma:
    1. Asegura los directorios 'data/' y 'data/tenants/'.
    2. Inicializa la base de datos maestra ('master.db').
    3. Si es la primera ejecución, crea el Administrador por defecto ('admin') y
       migra automáticamente el archivo 'finanzas.db' existente a 'data/tenants/user_1.db'.
    """
    ensure_tenant_directories()
    # Clave inicial de respaldo si no hay usuarios en master.db
    admin_default_pass = os.environ.get("DEFAULT_ADMIN_PASSWORD", "admin123")
    admin_default_hash = hash_password(admin_default_pass)
    migrado = master_db.seed_default_admin_and_migrate(admin_default_hash)
    if migrado:
        print(f"[STARTUP] Base de datos maestra inicializada. Administrador creado: {migrado['username']} (db: {migrado['db_name']})")
    else:
        print("[STARTUP] Base de datos maestra lista y verificada.")

    from email_service import esta_configurado_servicio_correo
    if esta_configurado_servicio_correo():
        print("[EMAIL] Servicio de correo transaccional (API / SMTP) configurado y activo.")
    else:
        print("[EMAIL ADVERTENCIA] El servicio de envío de correos NO está configurado en .env. La recuperación de contraseñas por correo permanecerá inactiva hasta definir RESEND_API_KEY o credenciales SMTP.")
    yield

# Control de exposición de documentación técnica (Swagger UI / ReDoc / OpenAPI)
# Por seguridad en producción, queda completamente deshabilitado (404) a menos que ENABLE_DOCS=true
ENABLE_DOCS = os.environ.get("ENABLE_DOCS", "false").strip().lower() in ("true", "1", "yes")

# Inicialización de la aplicación FastAPI
app = FastAPI(
    lifespan=lifespan,
    title="Finanzas Personales API Multi-Tenant",
    description="Plataforma SaaS Multi-Tenant para control financiero personal e inversiones con bases de datos aisladas.",
    version="2.0",
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None
)

# Montar recursos estáticos (HTML, CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(movimientos.router)
app.include_router(apartados.router)
app.include_router(inversiones.router)
app.include_router(respaldo.router)
app.include_router(recurrentes.router)
app.include_router(presupuesto.router)

@app.exception_handler(sqlite3.OperationalError)
async def sqlite_operational_error_handler(request: Request, exc: sqlite3.OperationalError):
    """Manejo controlado de contención y bloqueos temporales en SQLite (evita 500 no capturado)."""
    err_str = str(exc).lower()
    if "locked" in err_str or "busy" in err_str:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "La base de datos se encuentra momentáneamente ocupada procesando otra transacción concurrente. Por favor, reintenta en un momento."
            },
            headers={"Retry-After": "1"}
        )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Error interno de base de datos."}
    )

@app.middleware("http")
async def add_security_and_cache_headers(request: Request, call_next):
    """Agrega cabeceras de seguridad HTTP (HSTS, CSP, nosniff, etc.) y políticas anti-caché."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    path = request.url.path
    if path.startswith("/api/") or path == "/api" or path.startswith("/auth/") or path == "/auth":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif path.startswith("/static/") or path in ("/", "/login", "/app"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

@app.get("/", summary="Página principal SPA")
@app.get("/login", summary="Página de acceso")
@app.get("/app", summary="Aplicación principal")
def leer_raiz():
    """Sirve la interfaz web estática desde static/index.html."""
    return FileResponse("static/index.html")
