"""asistente/tokens.py - Token de corta vida para que el asistente llame a la API en nombre de una cuenta."""

from datetime import timedelta

import master_db
import security
from asistente.permisos import USO_ASISTENTE

SEGUNDOS_DE_VIDA = 60   # cada llamada a la API lleva un token nuevo: uno filtrado dura un minuto como mucho


def emitir_token(user_id, segundos=SEGUNDOS_DE_VIDA):
    """JWT de la cuenta con `uso = "asistente"`. Falla si la cuenta no existe o está desactivada."""
    user = master_db.get_user_by_id(int(user_id))
    if not user or not user.get("is_active"):
        raise ValueError("cuenta inexistente o desactivada")
    claims = security.claims_sesion(user)
    claims["uso"] = USO_ASISTENTE
    return security.create_access_token(claims, timedelta(seconds=segundos))
