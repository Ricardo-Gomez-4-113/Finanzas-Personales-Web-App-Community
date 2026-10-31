"""routers/admin.py - Administración de usuarios y métricas globales (solo admin)."""

from fastapi import APIRouter
from fastapi import HTTPException, status, Depends
from pydantic import BaseModel, Field
from typing import Literal, Optional, Dict, Any

import master_db
from routers.auth import UserUpdateRequest, exigir_contrasena_actual
from finanzas_core import ahora_local
from security import (
    claims_sesion,
    hash_password,
    create_access_token,
    verify_admin
)

router = APIRouter()

class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$", description="Nombre de usuario alfanumérico")
    password: str = Field(..., min_length=8, max_length=72, description="Contraseña de acceso (mínimo 8 caracteres)")
    role: Literal['admin', 'user'] = Field('user', description="Rol del usuario: 'admin' o 'user'")
    email: Optional[str] = Field(None, max_length=120, description="Correo electrónico para recuperación de contraseña")


class UserStatusRequest(BaseModel):
    is_active: bool = Field(..., description="Estado activo (True) o suspendido (False)")


# =============================================================================
# ENDPOINTS: ADMINISTRACIÓN DE USUARIOS (SOLO ADMIN)
# =============================================================================

@router.get("/admin/users", summary="Listar usuarios con métricas")
def admin_listar_usuarios(_admin: Dict[str, Any] = Depends(verify_admin)):
    """Retorna todos los usuarios registrados con su actividad y tamaño en disco de su BD."""
    return master_db.list_users_with_metrics()

@router.post("/admin/users", status_code=status.HTTP_201_CREATED, summary="Crear nuevo usuario")
def admin_crear_usuario(req: UserCreateRequest, _admin: Dict[str, Any] = Depends(verify_admin)):
    """
    Crea un nuevo usuario en la base maestra y dispara el aprovisionamiento
    automático de su base de datos individual en data/tenants/user_{id}.db.
    """
    username = req.username.strip()
    if master_db.get_user_by_username(username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El nombre de usuario '{username}' ya está registrado."
        )
        
    pwd_hash = hash_password(req.password)
    try:
        nuevo_usuario = master_db.create_user_entry(
            username=username,
            password_hash=pwd_hash,
            role=req.role,
            is_active=1,
            email=req.email
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    return {
        "mensaje": f"Usuario '{username}' creado exitosamente con su base de datos individual.",
        "user": {
            "id": nuevo_usuario["id"],
            "username": nuevo_usuario["username"],
            "role": nuevo_usuario["role"],
            "db_name": nuevo_usuario["db_name"],
            "email": nuevo_usuario.get("email")
        }
    }

@router.patch("/admin/users/{user_id}/status", summary="Cambiar estado de usuario")
def admin_cambiar_estado(user_id: int, req: UserStatusRequest, admin: Dict[str, Any] = Depends(verify_admin)):
    """
    Activa o desactiva la cuenta de un usuario.
    Regla de Seguridad: El administrador no puede desactivar su propia cuenta.
    """
    if user_id == admin["id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes desactivar tu propia cuenta de administrador."
        )
        
    user = master_db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    if user["id"] == 1 or user["username"].lower() == "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede suspender la cuenta del Administrador principal."
        )
        
    master_db.set_user_status(user_id, 1 if req.is_active else 0)
    estado_str = "activado" if req.is_active else "suspendido"
    return {"mensaje": f"El usuario '{user['username']}' ha sido {estado_str}.", "is_active": req.is_active}

@router.patch("/admin/users/{user_id}", summary="Editar usuario (nombre, contraseña y rol)")
def admin_editar_usuario(user_id: int, req: UserUpdateRequest, admin: Dict[str, Any] = Depends(verify_admin)):
    """
    Permite al administrador modificar el nombre de usuario, correo, contraseña y/o rol de cualquier cuenta.
    Si se edita la propia cuenta del administrador en sesión, se genera y retorna un nuevo token JWT.
    """
    user = master_db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    # Si se intenta cambiar el rol del admin a 'user' para su propia cuenta
    if req.role and req.role != user["role"] and user["role"] == "admin":
        if user_id == admin["id"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No puedes remover tu propio rol de administrador."
            )

    pwd_hash = hash_password(req.password) if (req.password and req.password.strip()) else None
    # Un admin que cambia su propia contraseña por este camino también debe confirmar la actual
    if pwd_hash and user_id == admin["id"]:
        exigir_contrasena_actual(user_id, req.current_password)

    try:
        user_actualizado = master_db.update_user(
            user_id=user_id,
            username=req.username,
            password_hash=pwd_hash,
            role=req.role,
            email=req.email
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    resp = {
        "mensaje": f"Usuario '{user_actualizado['username']}' actualizado exitosamente.",
        "user": {
            "id": user_actualizado["id"],
            "username": user_actualizado["username"],
            "role": user_actualizado["role"],
            "db_name": user_actualizado["db_name"],
            "email": user_actualizado.get("email")
        }
    }

    # Si se modificó la cuenta activa del admin, renovar su JWT
    if user_id == admin["id"]:
        nuevo_token = create_access_token(claims_sesion(master_db.get_user_by_id(user_actualizado["id"])))
        resp["new_token"] = nuevo_token

    return resp

@router.delete("/admin/users/{user_id}", summary="Eliminar usuario y su base de datos")
def admin_eliminar_usuario(user_id: int, admin: Dict[str, Any] = Depends(verify_admin)):
    """
    Elimina permanentemente a un usuario de master_db y borra físicamente su base de datos tenant.
    Regla de Seguridad: No se puede eliminar ningún usuario con rol 'admin' ni la cuenta principal 'admin'.
    """
    if user_id == admin["id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes eliminar tu propia cuenta de administrador."
        )
        
    user = master_db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    if user["id"] == 1 or user["username"].lower() == "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede eliminar la cuenta principal del Administrador."
        )
        
    eliminado = master_db.delete_user_and_tenant(user_id)
    if not eliminado:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error al eliminar los archivos del usuario.")
        
    return {"mensaje": f"Usuario '{user['username']}' y su base de datos eliminados con éxito."}

@router.get("/admin/stats", summary="Métricas y KPIs globales del sistema")
def admin_estadisticas_globales(_admin: Dict[str, Any] = Depends(verify_admin)):
    """Calcula las métricas de usuarios activos, logins acumulados y espacio total en disco."""
    stats = master_db.get_admin_stats(ahora_local())
    from email_service import esta_configurado_servicio_correo
    stats["email_service_configured"] = esta_configurado_servicio_correo()
    return stats
