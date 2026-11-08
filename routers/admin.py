"""routers/admin.py - Administración de usuarios y métricas globales (solo admin)."""

from fastapi import APIRouter
from fastapi import HTTPException, status, Depends
from pydantic import BaseModel, Field
from typing import Literal, Optional, Dict, Any

import master_db
from routers.auth import PATRON_CORREO, ClaveNueva, UserUpdateRequest, cambia_identidad, exigir_contrasena_actual, exigir_nombre_libre
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
    password: ClaveNueva = Field(..., min_length=8, max_length=72, description="Contraseña de acceso (mínimo 8 caracteres)")
    role: Literal['admin', 'user'] = Field('user', description="Rol del usuario: 'admin' o 'user'")
    email: Optional[str] = Field(None, max_length=120, pattern=PATRON_CORREO, description="Correo electrónico para recuperación de contraseña")
    current_password: Optional[str] = Field(None, max_length=72, description="Contraseña del administrador: obligatoria para crear otro administrador")


def _es_principal(user: Dict[str, Any]) -> bool:
    # Solo por id: el nombre «admin» está reservado para ella, pero un nombre se puede cambiar y un id no
    return user["id"] == 1


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
def admin_crear_usuario(req: UserCreateRequest, admin: Dict[str, Any] = Depends(verify_admin)):
    """
    Crea un nuevo usuario en la base maestra y dispara el aprovisionamiento
    automático de su base de datos individual en data/tenants/user_{id}.db.
    Crear otro administrador exige la contraseña de quien lo crea: con un token robado no se fabrica un acceso propio.
    """
    username = req.username.strip()
    exigir_nombre_libre(username)
    if master_db.get_user_by_username(username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El nombre de usuario '{username}' ya está registrado."
        )
        
    if req.role == "admin":
        exigir_contrasena_actual(admin["id"], req.current_password)

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

    if _es_principal(user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede suspender la cuenta del Administrador principal."
        )
    if not req.is_active and user["role"] == "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede suspender a un administrador: quítale primero el rol."
        )

    master_db.set_user_status(user_id, 1 if req.is_active else 0)
    estado_str = "activado" if req.is_active else "suspendido"
    return {"mensaje": f"El usuario '{user['username']}' ha sido {estado_str}.", "is_active": req.is_active}

@router.patch("/admin/users/{user_id}", summary="Editar usuario (nombre, contraseña y rol)")
def admin_editar_usuario(user_id: int, req: UserUpdateRequest, admin: Dict[str, Any] = Depends(verify_admin)):
    """
    Permite al administrador modificar el nombre de usuario, correo, contraseña y/o rol de cualquier cuenta.
    Cambiar una contraseña o un rol exige la contraseña del administrador; la cuenta principal solo se edita a sí misma.
    Si se edita la propia cuenta del administrador en sesión, se genera y retorna un nuevo token JWT.
    """
    user = master_db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    if _es_principal(user) and user_id != admin["id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La cuenta del Administrador principal solo puede editarla ella misma."
        )

    # Si se intenta cambiar el rol del admin a 'user' para su propia cuenta
    if req.role and req.role != user["role"] and user["role"] == "admin":
        if user_id == admin["id"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No puedes remover tu propio rol de administrador."
            )

    exigir_nombre_libre(req.username, user_id)
    cambia_clave = bool(req.password and req.password.strip())
    cambia_rol = bool(req.role and req.role != user["role"])
    # Contraseña, rol, nombre o correo, de la propia cuenta o de otra: se confirma con la contraseña del administrador
    # (antes de bcrypt). El correo cuenta porque con él se recupera la cuenta.
    if cambia_clave or cambia_rol or cambia_identidad(req, user):
        exigir_contrasena_actual(admin["id"], req.current_password)
    pwd_hash = hash_password(req.password) if cambia_clave else None

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

class EliminarUsuarioRequest(BaseModel):
    current_password: Optional[str] = Field(None, max_length=72, description="Contraseña del administrador: obligatoria para eliminar")


@router.delete("/admin/users/{user_id}", summary="Eliminar usuario y su base de datos")
def admin_eliminar_usuario(user_id: int, req: Optional[EliminarUsuarioRequest] = None, admin: Dict[str, Any] = Depends(verify_admin)):
    """
    Elimina permanentemente a un usuario de master_db y borra físicamente su base de datos tenant.
    Exige la contraseña del administrador: un token de administrador robado no basta para borrar cuentas.
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

    if _es_principal(user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede eliminar la cuenta principal del Administrador."
        )
    if user["role"] == "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede eliminar a un administrador: quítale primero el rol."
        )

    exigir_contrasena_actual(admin["id"], req.current_password if req else None)

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
