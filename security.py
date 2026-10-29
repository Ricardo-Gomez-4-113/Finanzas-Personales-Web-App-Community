"""
security.py - Capa de Seguridad, Criptografía y Middleware JWT para FastAPI

Este módulo implementa:
1. Hashing seguro de contraseñas con bcrypt (salt automático).
2. Generación y verificación de tokens JWT (algoritmo HS256).
3. Dependencias de seguridad para FastAPI:
   - verify_token: Valida el JWT y verifica que el usuario permanezca activo en master_db.
   - verify_admin: Valida que el rol del usuario sea estrictamente 'admin'.
   - get_tenant_db: Inyecta la conexión a la base de datos específica del tenant autenticado.
"""

import os
import time
import hashlib
import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Generator, List
import sqlite3
import bcrypt
import jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from database import get_tenant_conn
import master_db

# Cargar variables de entorno desde .env si existe en el directorio base
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    try:
        with open(_env_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
    except Exception:
        pass

# Clave secreta para firma de tokens JWT (ESTRICTAMENTE OBLIGATORIA)
JWT_PLACEHOLDERS = {
    "tu_clave_secreta_jwt_de_al_menos_32_caracteres_aleatorios",
    "tu_clave_hexadecimal_generada_aqui",
    "tu_clave_generada_aqui",
    "secret",
    "changeme",
    "admin",
    "",
}

SECRET_KEY = (os.environ.get("JWT_SECRET_KEY") or "").strip()
if not SECRET_KEY or SECRET_KEY.lower() in JWT_PLACEHOLDERS or len(SECRET_KEY) < 32:
    raise RuntimeError(
        "\n"
        "===============================================================================\n"
        " [ERROR CRÍTICO DE SEGURIDAD] JWT_SECRET_KEY ES ESTRICTAMENTE OBLIGATORIA\n"
        "===============================================================================\n"
        " La variable de entorno 'JWT_SECRET_KEY' es obligatoria y debe tener al menos\n"
        " 32 caracteres seguros en el archivo .env.\n\n"
        " No se permite iniciar la aplicación sin esta clave porque firma los tokens de\n"
        " sesión de todos los usuarios.\n\n"
        " Para generar una clave criptográfica segura, ejecuta en tu terminal:\n"
        "   docker run --rm python:3.11-slim python3 -c \"import secrets; print(secrets.token_hex(32))\"\n"
        "   # o en tu terminal local: openssl rand -hex 32\n\n"
        " Luego asígnala en tu archivo .env:\n"
        "   JWT_SECRET_KEY=<tu_clave_generada>\n"
        "===============================================================================\n"
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 8

# Esquema OAuth2 con auto_error=False para personalizar la respuesta 401
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

def hash_password(plain_password: str) -> str:
    """Genera un hash seguro utilizando bcrypt con salt automático."""
    pwd_bytes = plain_password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica si una contraseña en texto plano coincide con el hash almacenado."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except Exception:
        return False

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Genera un token JWT firmado.
    Incluye en los claims: sub (user_id en string), username, role, db_name y timestamp exp.
    """
    to_encode = data.copy()
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    now_utc = datetime.now(timezone.utc)
    if expires_delta:
        expire = now_utc + expires_delta
    else:
        expire = now_utc + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
        
    to_encode.update({
        "exp": expire,
        "iat": now_utc
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decodifica y valida la firma y vigencia de un token JWT."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except (jwt.PyJWTError, Exception):
        return None

def huella_clave(password_hash: str) -> str:
    """Huella corta del hash de contraseña. El token la lleva y deja de valer al cambiar la contraseña."""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]

def claims_sesion(user: Dict[str, Any]) -> Dict[str, Any]:
    """Claims del JWT de sesión. Incluye la huella de la contraseña para invalidar sesiones previas."""
    return {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "db_name": user["db_name"],
        "email": user.get("email"),
        "pwd": huella_clave(user["password_hash"]),
    }

HASH_SENUELO: Optional[str] = None

def hash_senuelo() -> str:
    """Hash de referencia para igualar el tiempo de login cuando el usuario no existe."""
    global HASH_SENUELO
    if HASH_SENUELO is None:
        HASH_SENUELO = hash_password("senuelo-sin-uso-real")
    return HASH_SENUELO

def verify_token(token: Optional[str] = Depends(oauth2_scheme)) -> Dict[str, Any]:
    """
    Dependencia de seguridad que autentica al usuario:
    1. Verifica la presencia y firma del token Bearer.
    2. Comprueba la existencia y estado activo (is_active == 1) en master_db.
    3. Retorna la información actualizada del usuario.
    """
    cred_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales de autenticación inválidas o expiradas.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    if not token:
        raise cred_exception
        
    payload = decode_access_token(token)
    if not payload:
        raise cred_exception
        
    user_id = payload.get("sub")
    if not user_id:
        raise cred_exception
        
    try:
        user_id_int = int(user_id)
    except ValueError:
        raise cred_exception
        
    # Verificar en master_db para revocar acceso inmediato si fue desactivado
    user = master_db.get_user_by_id(user_id_int)
    if not user:
        raise cred_exception
    # Un cambio de contraseña invalida los tokens emitidos antes
    if payload.get("pwd") != huella_clave(user["password_hash"]):
        raise cred_exception
        
    if not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta ha sido desactivada por el administrador."
        )

    sub_status = user.get("subscription_status", "active")
    if sub_status in ("suspended", "cancelled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu suscripción se encuentra suspendida o cancelada. Contacta al soporte."
        )
        
    return {
        "id": user["id"],
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "db_name": user["db_name"],
        "db_path": user.get("db_path", ""),
        "email": user.get("email"),
        "is_active": user["is_active"],
        "subscription_status": sub_status
    }

def verify_admin(current_user: Dict[str, Any] = Depends(verify_token)) -> Dict[str, Any]:
    """Dependencia que valida que el usuario autenticado posea estrictamente el rol 'admin'."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido exclusivamente a administradores."
        )
    return current_user

def get_tenant_db(current_user: Dict[str, Any] = Depends(verify_token)) -> Generator[sqlite3.Connection, None, None]:
    """
    Dependencia FastAPI que resuelve dinámicamente la conexión a la base de datos
    específica del tenant autenticado ('data/tenants/{db_name}') gestionada por el caché LRU.
    Garantiza rollback automático en caso de error y liberación adecuada.
    """
    db_name = current_user.get("db_name", "user_1.db")
    conn = get_tenant_conn(db_name)
    try:
        yield conn
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


# Control en memoria de intentos fallidos de autenticación (freno anti fuerza bruta)
_intentos_fallidos_login: Dict[str, List[float]] = {}

_MAX_CLAVES_LOGIN = 10000
_VENTANA_LOGIN_SEG = 300

def _purgar_intentos(ahora: float) -> None:
    """Quita claves vencidas y, si siguen siendo demasiadas, descarta las más antiguas."""
    for k in list(_intentos_fallidos_login):
        vivos = [t for t in _intentos_fallidos_login[k] if ahora - t < _VENTANA_LOGIN_SEG]
        if vivos:
            _intentos_fallidos_login[k] = vivos
        else:
            del _intentos_fallidos_login[k]
    if len(_intentos_fallidos_login) >= _MAX_CLAVES_LOGIN:
        for k in sorted(_intentos_fallidos_login, key=lambda c: _intentos_fallidos_login[c][-1])[: _MAX_CLAVES_LOGIN // 2]:
            del _intentos_fallidos_login[k]

def registrar_fallo_login(clave: str):
    ahora = time.time()
    if len(_intentos_fallidos_login) >= _MAX_CLAVES_LOGIN:
        _purgar_intentos(ahora)
    intentos = [t for t in _intentos_fallidos_login.get(clave, []) if ahora - t < _VENTANA_LOGIN_SEG]
    intentos.append(ahora)
    _intentos_fallidos_login[clave] = intentos

def verificar_tasa_login(clave: str):
    ahora = time.time()
    intentos = [t for t in _intentos_fallidos_login.get(clave, []) if ahora - t < _VENTANA_LOGIN_SEG]
    if intentos:
        _intentos_fallidos_login[clave] = intentos
    else:
        _intentos_fallidos_login.pop(clave, None)
    if len(intentos) >= 5:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos fallidos. Por seguridad, espera 5 minutos antes de volver a intentar."
        )

def limpiar_fallos_login(clave: str):
    _intentos_fallidos_login.pop(clave, None)


def es_ip_privada_o_loopback(ip_str: str) -> bool:
    """Verifica de forma segura si una dirección IP pertenece a rangos de loopback o red privada interna."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


# Proxies cuya cabecera CF-Connecting-IP se acepta: solo el loopback del host y el gateway
# del bridge de Docker (por donde llega el tráfico de cloudflared hacia el contenedor).
_PROXIES_CONFIABLES = {
    ip.strip() for ip in os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1,::1,172.17.0.1").split(",") if ip.strip()
}

def obtener_ip_cliente(request: Request) -> str:
    """
    Resuelve la dirección IP real del cliente detrás de Cloudflare y proxies reversos confiables.
    
    1. Si la conexión TCP directa (request.client.host) proviene de un proxy confiable
       (loopback 127.0.0.1/::1 o redes privadas/Docker), inspecciona la cabecera 'CF-Connecting-IP'
       inyectada y autenticada por el borde de Cloudflare.
    2. Si 'CF-Connecting-IP' contiene una IP válida, se adopta como IP verídica del visitante.
    3. Si no hay proxy confiable o no existe la cabecera, recurre a request.client.host o 'unknown'.
    """
    direct_ip = request.client.host if request.client else "unknown"
    if direct_ip in _PROXIES_CONFIABLES:
        cf_ip = request.headers.get("CF-Connecting-IP")
        if cf_ip:
            cf_ip_clean = cf_ip.strip()
            try:
                ipaddress.ip_address(cf_ip_clean)
                return cf_ip_clean
            except ValueError:
                pass
    return direct_ip
