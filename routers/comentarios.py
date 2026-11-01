"""routers/comentarios.py - Comentarios que los usuarios envían desde su menú.

Se guardan en la tabla `comentarios` de la base maestra (data/auth_master.db). Nadie los lee desde la app:
se consultan directamente en la base. El texto no se escribe en el registro de fallos.
"""

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import master_db
from security import verify_token

router = APIRouter()

MAX_COMENTARIOS_POR_HORA = 5


class ComentarioNuevo(BaseModel):
    tipo: Literal["sugerencia", "error", "otro"]
    mensaje: str = Field(..., min_length=1, max_length=1000)
    contexto: Optional[str] = Field(None, max_length=120, description="Pestaña o pantalla desde la que se envía")


@router.post("/api/comentarios", status_code=status.HTTP_201_CREATED, summary="Enviar un comentario")
def enviar_comentario(cuerpo: ComentarioNuevo, current_user: Dict[str, Any] = Depends(verify_token)):
    mensaje = cuerpo.mensaje.strip()
    if not mensaje:
        raise HTTPException(status_code=422, detail="El comentario no puede estar vacío.")
    if master_db.contar_comentarios_recientes(current_user["id"]) >= MAX_COMENTARIOS_POR_HORA:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Has enviado varios comentarios en la última hora. Intenta de nuevo más tarde.",
        )
    id_comentario = master_db.guardar_comentario(current_user["id"], cuerpo.tipo, mensaje, cuerpo.contexto)
    return {"id": id_comentario, "mensaje": "Gracias. Recibimos tu comentario."}
