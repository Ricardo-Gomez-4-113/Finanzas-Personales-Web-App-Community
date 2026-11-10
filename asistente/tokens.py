"""asistente/tokens.py - Token de corta vida para que el asistente llame a la API en nombre de una cuenta."""

import secrets
from datetime import timedelta

import master_db
import security
from asistente.permisos import USO_ASISTENTE, USO_ESCRITURA

class AsistenteDesactivado(Exception):
    """El administrador apagó el asistente de esta cuenta."""


SEGUNDOS_DE_ESCRITURA = 20   # solo se usa al ejecutar una propuesta confirmada
SEGUNDOS_DE_VIDA = 60   # cada llamada a la API lleva un token nuevo: uno filtrado dura un minuto como mucho


def emitir_token(user_id, segundos=None, escritura=False):
    """JWT de la cuenta con `uso = "asistente"` (lectura) o `"asistente_escritura"` (solo al confirmar una propuesta).
    Falla si la cuenta no existe o está desactivada."""
    segundos = segundos if segundos is not None else (SEGUNDOS_DE_ESCRITURA if escritura else SEGUNDOS_DE_VIDA)
    user = master_db.get_user_by_id(int(user_id))
    if not user or not user.get("is_active"):
        raise ValueError("cuenta inexistente o desactivada")
    if not user.get("asistente_activo", 1):
        raise AsistenteDesactivado()
    claims = security.claims_sesion(user)
    claims["uso"] = USO_ESCRITURA if escritura else USO_ASISTENTE
    if escritura:
        claims["jti"] = secrets.token_urlsafe(12)   # un token de escritura sirve para una sola petición
    return security.create_access_token(claims, timedelta(seconds=segundos))
