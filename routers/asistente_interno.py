"""routers/asistente_interno.py - API interna que usa el bot de Telegram. NO es para personas ni para el navegador.

El bot corre en otro contenedor y no toca la base ni firma tokens: le pide a la app, con un secreto propio
(`ASISTENTE_SECRETO_INTERNO`, distinto de la clave JWT), que vincule un chat, le diga de qué cuenta es, lo desvincule o le
dé un token de un minuto de la cuenta de ESE chat. Así un bot comprometido no puede pedir tokens de cuentas sin chat
vinculado, de administradores ni de nadie que no haya escaneado el QR. Sin el secreto configurado estas rutas no existen (404).
"""

import hmac
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from asistente import vinculos
from asistente.tokens import emitir_token
from security import clave_bloqueada, obtener_ip_cliente, reservar_intento_login

router = APIRouter(prefix="/internal/asistente", include_in_schema=False)

LARGO_MINIMO_SECRETO = 32
MAX_FALLOS_POR_IP = 20
# El bot llega directo por la red de Docker; el tráfico que pasa por un proxy o túnel inversor trae alguna de estas cabeceras
CABECERAS_DE_PROXY = ("cf-connecting-ip", "x-forwarded-for", "x-real-ip", "forwarded", "cf-ray", "true-client-ip")


class ChatRequest(BaseModel):
    chat_id: int = Field(..., ge=-(2 ** 53), le=2 ** 53)


class VincularRequest(ChatRequest):
    codigo: str = Field(..., min_length=1, max_length=64)
    username: Optional[str] = Field(None, max_length=64)


class TokenRequest(ChatRequest):
    escritura: bool = False
    cuenta_id: Optional[int] = Field(None, ge=1, description="Cuenta que el bot espera: si el chat ya no es de ella, no hay token")


def exigir_secreto_interno(request: Request) -> None:
    secreto = os.environ.get("ASISTENTE_SECRETO_INTERNO", "").strip()
    if len(secreto) < LARGO_MINIMO_SECRETO or any(c in request.headers for c in CABECERAS_DE_PROXY):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")   # sin secreto, o llegando desde fuera por un proxy: no existe
    clave_ip = f"ip-interno:{obtener_ip_cliente(request)}"
    if clave_bloqueada(clave_ip, MAX_FALLOS_POR_IP):   # una IP que ya falló demasiado no sigue adivinando, ni siquiera con el secreto bueno
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Demasiados intentos.")
    recibido = request.headers.get("x-asistente-secreto", "")
    if not hmac.compare_digest(recibido.encode("utf-8"), secreto.encode("utf-8")):
        reservar_intento_login(clave_ip, limite=MAX_FALLOS_POR_IP)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autorizado.")


@router.post("/vincular", dependencies=[Depends(exigir_secreto_interno)])
def vincular(cuerpo: VincularRequest):
    estado, _, chat_anterior, cuenta = vinculos.consumir_codigo(cuerpo.codigo, cuerpo.chat_id, cuerpo.username)
    return {"estado": estado, "chat_anterior": chat_anterior, "cuenta": cuenta}


@router.post("/chat", dependencies=[Depends(exigir_secreto_interno)])
def cuenta_del_chat(cuerpo: ChatRequest):
    cuenta = vinculos.usuario_de_chat(cuerpo.chat_id)
    if not cuenta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat sin vínculo.")
    return cuenta


@router.post("/desvincular", dependencies=[Depends(exigir_secreto_interno)])
def desvincular(cuerpo: ChatRequest):
    return {"desvinculado": vinculos.desvincular_chat(cuerpo.chat_id) is not None}


@router.post("/token", dependencies=[Depends(exigir_secreto_interno)])
def token_del_chat(cuerpo: TokenRequest):
    cuenta = vinculos.usuario_de_chat(cuerpo.chat_id)
    if not cuenta or (cuerpo.cuenta_id is not None and cuerpo.cuenta_id != cuenta["id"]):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat sin vínculo.")
    return {"token": emitir_token(cuenta["id"], escritura=cuerpo.escritura)}
