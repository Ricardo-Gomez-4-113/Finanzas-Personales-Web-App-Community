"""routers/auth.py - Autenticación, perfil y recuperación de contraseña."""

from fastapi import APIRouter
from fastapi import HTTPException, status, Depends, Request, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Literal, Optional, Dict, Any

import master_db
from security import (
    claims_sesion,
    hash_senuelo,
    huella_clave,
    registrar_fallo_login, 
    verificar_tasa_login, 
    limpiar_fallos_login,
    obtener_ip_cliente,
    hash_password,
    verify_password,
    create_access_token,
    verify_token
)
from finanzas_core import ahora_local

router = APIRouter()

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, description="Nombre de usuario")
    password: str = Field(..., min_length=1, description="Contraseña en texto plano")


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$", description="Nombre de usuario alfanumérico")
    password: str = Field(..., min_length=4, max_length=72, description="Contraseña de acceso")
    role: Literal['admin', 'user'] = Field('user', description="Rol del usuario: 'admin' o 'user'")
    email: Optional[str] = Field(None, max_length=120, description="Correo electrónico para recuperación de contraseña")


class UserStatusRequest(BaseModel):
    is_active: bool = Field(..., description="Estado activo (True) o suspendido (False)")


class UserUpdateRequest(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$", description="Nuevo nombre de usuario")
    password: Optional[str] = Field(None, min_length=4, max_length=72, description="Nueva contraseña (dejar en blanco para no cambiar)")
    role: Optional[Literal['admin', 'user']] = Field(None, description="Rol del usuario: 'admin' o 'user'")
    email: Optional[str] = Field(None, max_length=120, description="Correo electrónico del usuario")


class ForgotPasswordRequest(BaseModel):
    identifier: str = Field(..., min_length=2, max_length=120, description="Nombre de usuario o correo electrónico")


class ResetPasswordRequest(BaseModel):
    identifier: str = Field(..., min_length=2, max_length=120, description="Nombre de usuario o correo electrónico")
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$", description="Código numérico de 6 dígitos")
    new_password: str = Field(..., min_length=4, max_length=72, description="Nueva contraseña")


# =============================================================================
# ENDPOINTS: AUTENTICACIÓN (LOGIN & PERFIL)
# =============================================================================

@router.post("/auth/login", summary="Iniciar sesión")
def login(credentials: LoginRequest, request: Request):
    """
    Valida las credenciales del usuario:
    1. Comprueba usuario y contraseña con bcrypt en master.db.
    2. Aplica control de tasa contra ataques de fuerza bruta.
    3. Verifica que la cuenta esté activa (is_active == 1).
    4. Registra la auditoría: actualiza last_login e incrementa login_count.
    5. Emite el token JWT firmado con sub, username, role y db_name.
    """
    client_ip = obtener_ip_cliente(request)
    clave_tasa = f"{client_ip}:{credentials.username.strip().lower()}"
    verificar_tasa_login(clave_tasa)

    username = credentials.username.strip()
    user = master_db.get_user_by_username(username)
    
    # Se verifica siempre (contra un hash señuelo si no hay usuario) para igualar el tiempo de respuesta
    clave_valida = verify_password(credentials.password, user["password_hash"] if user else hash_senuelo())
    if not user or not clave_valida:
        registrar_fallo_login(clave_tasa)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos."
        )
        
    limpiar_fallos_login(clave_tasa)

    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta ha sido desactivada por el administrador."
        )
        
    # Actualizar métricas de auditoría
    timestamp_hoy = ahora_local().strftime("%Y-%m-%d %H:%M:%S")
    master_db.update_login_activity(user["id"], timestamp_hoy)
    
    # Generar token JWT
    token = create_access_token(claims_sesion(user))
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "db_name": user["db_name"],
            "email": user.get("email")
        }
    }

@router.get("/auth/me", summary="Datos del usuario activo")
def get_mi_perfil(current_user: Dict[str, Any] = Depends(verify_token)):
    """Retorna la información del usuario en sesión a partir de su JWT."""
    return {"user": current_user}

@router.patch("/auth/profile", summary="Editar perfil del usuario activo")
def editar_mi_perfil(req: UserUpdateRequest, current_user: Dict[str, Any] = Depends(verify_token)):
    """
    Permite al usuario autenticado modificar su propio nombre de usuario, correo y/o contraseña.
    """
    user_id = current_user["id"]
    pwd_hash = hash_password(req.password) if (req.password and req.password.strip()) else None

    try:
        user_actualizado = master_db.update_user(
            user_id=user_id,
            username=req.username,
            password_hash=pwd_hash,
            role=None,  # No permitir auto-escalar rol
            email=req.email
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    nuevo_token = create_access_token(claims_sesion(master_db.get_user_by_id(user_id)))

    return {
        "mensaje": "Perfil actualizado exitosamente.",
        "user": {
            "id": user_actualizado["id"],
            "username": user_actualizado["username"],
            "role": user_actualizado["role"],
            "db_name": user_actualizado["db_name"],
            "email": user_actualizado.get("email")
        },
        "new_token": nuevo_token
    }

@router.delete("/auth/delete-account", summary="Eliminar permanentemente la cuenta del usuario activo")
def eliminar_mi_cuenta(current_user: Dict[str, Any] = Depends(verify_token)):
    """
    Permite a los usuarios estándar eliminar permanentemente su propia cuenta y su base de datos tenant.
    Regla de Seguridad: Los administradores NO pueden eliminar su propia cuenta a través de este endpoint.
    """
    user_id = current_user["id"]

    # Bloquear estrictamente si es administrador o cuenta principal
    if (
        current_user.get("role") == "admin"
        or user_id == 1
        or current_user.get("username", "").lower() == "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Los administradores del sistema no pueden eliminar su propia cuenta."
        )

    eliminado = master_db.delete_user_and_tenant(user_id)
    if not eliminado:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo procesar la eliminación de tu cuenta."
        )

    return {"mensaje": "Tu cuenta y toda tu información financiera han sido eliminadas permanentemente."}

# =============================================================================
# ENDPOINTS: RECUPERACIÓN DE CONTRASEÑA POR EMAIL
# =============================================================================

@router.get("/api/public/email-status", summary="Consultar estado de configuración del correo")
@router.get("/auth/email-status", summary="Consultar estado de configuración del correo (alias)")
def get_email_status():
    """Retorna si el servicio de correo transaccional (API o SMTP) está configurado en el servidor."""
    from email_service import esta_configurado_servicio_correo
    return {"configured": esta_configurado_servicio_correo()}

@router.post("/auth/forgot-password", summary="Solicitar código de restablecimiento de contraseña")
def forgot_password(req: ForgotPasswordRequest, background_tasks: BackgroundTasks, request: Request):
    """
    Solicita un código de recuperación de contraseña de 6 dígitos:
    1. Verifica si el servicio de correo está configurado.
    2. Aplica rate limiting para prevenir spam.
    3. Busca al usuario por username o email.
    4. Si tiene email registrado, genera código y lo envía en segundo plano por correo/SMTP.
    5. Responde con un mensaje neutro para evitar enumeración de usuarios.
    """
    from email_service import esta_configurado_servicio_correo, enviar_correo_codigo_recuperacion
    if not esta_configurado_servicio_correo():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El servicio de envío de correos no está configurado en este servidor. Por favor, solicita asistencia directamente al administrador del sistema para restablecer tu contraseña."
        )

    client_ip = obtener_ip_cliente(request)
    clave_tasa = f"forgot:{client_ip}:{req.identifier.strip().lower()}"
    verificar_tasa_login(clave_tasa)
    # Cada solicitud cuenta igual, exista o no la cuenta: no hay oráculo ni bloqueo por tercero
    registrar_fallo_login(clave_tasa)

    user = master_db.get_user_by_identifier(req.identifier)
    if user and user.get("email") and user.get("is_active"):
        codigo = master_db.create_password_reset_code(user["id"])
        background_tasks.add_task(
            enviar_correo_codigo_recuperacion,
            user["email"],
            user["username"],
            codigo
        )

    return {
        "mensaje": "Si el usuario o correo electrónico existe y tiene una dirección registrada, hemos enviado un código de recuperación de 6 dígitos (válido por 15 minutos)."
    }

@router.post("/auth/reset-password", summary="Restablecer contraseña con código de 6 dígitos")
def reset_password(req: ResetPasswordRequest, request: Request):
    """
    Restablece la contraseña utilizando el código de 6 dígitos recibido por correo.
    Aplica una política de seguridad estricta: máximo 2 intentos fallidos antes de invalidar el código.
    """
    client_ip = obtener_ip_cliente(request)
    clave_tasa = f"reset:{client_ip}:{req.identifier.strip().lower()}"
    verificar_tasa_login(clave_tasa)

    user, error_msg = master_db.verify_reset_code(req.identifier, req.code)
    if not user:
        registrar_fallo_login(clave_tasa)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg or master_db.MENSAJE_CODIGO_INVALIDO
        )

    # El hash costoso solo se calcula cuando el código ya es válido
    master_db.update_user_password(user["id"], hash_password(req.new_password))
    limpiar_fallos_login(clave_tasa)
    return {
        "mensaje": f"¡Contraseña actualizada exitosamente para '{user['username']}'! Ya puedes iniciar sesión con tu nueva contraseña."
    }
