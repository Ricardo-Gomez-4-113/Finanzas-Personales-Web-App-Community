"""routers/asistente_telegram.py - Vincular el asistente de Telegram con la cuenta, desde «Mi perfil».

La persona escribe su contraseña actual, recibe un QR (y un enlace) con un código de un solo uso que vence en 5 minutos,
lo escanea con el teléfono y pulsa «Iniciar» en Telegram: el bot vincula ese chat con ESTA cuenta. Estas rutas no están en la
lista del token del asistente: el asistente no puede vincular ni desvincular nada.
"""

import os
import re
from typing import Any, Dict

import segno
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from asistente import vinculos
from routers.auth import exigir_contrasena_actual
from security import reservar_cupo_cuenta, verify_token

router = APIRouter()

_USUARIO_DEL_BOT = re.compile(r"^[A-Za-z0-9_]{5,32}$")


class CodigoRequest(BaseModel):
    contrasena_actual: str = Field(..., min_length=1, max_length=72)


def _usuario_del_bot():
    nombre = (os.environ.get("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
    return nombre if _USUARIO_DEL_BOT.match(nombre) else None


@router.get("/api/asistente/telegram", summary="Estado del asistente de Telegram de la cuenta")
def estado_telegram(current_user: Dict[str, Any] = Depends(verify_token)):
    return {"disponible": _usuario_del_bot() is not None, **vinculos.estado(current_user["id"])}


@router.post("/api/asistente/telegram/codigo", summary="Generar el QR para vincular Telegram")
def generar_codigo(cuerpo: CodigoRequest, current_user: Dict[str, Any] = Depends(verify_token)):
    bot = _usuario_del_bot()
    if not bot:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="El asistente de Telegram no está disponible en este servidor.")
    exigir_contrasena_actual(current_user["id"], cuerpo.contrasena_actual)
    if not reservar_cupo_cuenta("telegram_codigo", current_user["id"]):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Pediste demasiados códigos. Intenta de nuevo en unos minutos.")
    codigo, segundos = vinculos.crear_codigo(current_user["id"])
    enlace = f"https://t.me/{bot}?start={codigo}"
    qr = segno.make(enlace, error="m").svg_data_uri(scale=6, border=2, dark="#0f1318", light="#ece7dc")
    return {"enlace": enlace, "qr": qr, "vence_en_segundos": segundos}


@router.delete("/api/asistente/telegram", summary="Desvincular el asistente de Telegram")
def desvincular_telegram(current_user: Dict[str, Any] = Depends(verify_token)):
    vinculos.desvincular_cuenta(current_user["id"])
    return {"mensaje": "Telegram desvinculado."}
