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

import master_db
from asistente import avisos, cupo, pase, vinculos
from asistente.tokens import AsistenteDesactivado, emitir_token
from security import clave_bloqueada, obtener_ip_cliente, reservar_intento_login

router = APIRouter(prefix="/internal/asistente", include_in_schema=False)

LARGO_MINIMO_SECRETO = 32
MAX_FALLOS_POR_IP = 20
MAX_TOKENS_POR_LLAMADA = 100_000   # un turno real gasta unos miles; así un servicio comprometido no agota de golpe el día de una cuenta
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


class CupoRequest(ChatRequest):
    cuenta_id: int = Field(..., ge=1)
    con_medio: bool = False


class UsoRequest(ChatRequest):
    cuenta_id: int = Field(..., ge=1)
    tokens: int = Field(..., ge=0, le=MAX_TOKENS_POR_LLAMADA)


class ResultadoAvisoRequest(ChatRequest):
    aviso_id: int = Field(..., ge=1, le=2 ** 31)
    entregado: bool


class PaseRequest(BaseModel):
    pase: str = Field(..., min_length=1, max_length=200)


class PaseCupoRequest(PaseRequest):
    con_medio: bool = False


class PaseUsoRequest(PaseRequest):
    tokens: int = Field(..., ge=0, le=MAX_TOKENS_POR_LLAMADA)


def _exigir_secreto(request: Request, variable: str) -> None:
    secreto = os.environ.get(variable, "").strip()
    if len(secreto) < LARGO_MINIMO_SECRETO or any(c in request.headers for c in CABECERAS_DE_PROXY):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")   # sin secreto, o llegando desde fuera por un proxy: no existe
    clave_ip = f"ip-interno:{variable}:{obtener_ip_cliente(request)}"
    if clave_bloqueada(clave_ip, MAX_FALLOS_POR_IP):   # una IP que ya falló demasiado no sigue adivinando, ni siquiera con el secreto bueno
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Demasiados intentos.")
    recibido = request.headers.get("x-asistente-secreto", "")
    if not hmac.compare_digest(recibido.encode("utf-8"), secreto.encode("utf-8")):
        reservar_intento_login(clave_ip, limite=MAX_FALLOS_POR_IP)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autorizado.")


def exigir_secreto_interno(request: Request) -> None:
    """Secreto del bot de Telegram: abre las rutas de chats (/chat, /vincular, /token, /cupo...). NO abre las de /web/*."""
    _exigir_secreto(request, "ASISTENTE_SECRETO_INTERNO")


def exigir_secreto_web(request: Request) -> None:
    """Secreto del servicio web: abre SOLO /web/*. Es otro valor que el de Telegram: un servicio comprometido no puede usar las rutas del otro."""
    _exigir_secreto(request, "ASISTENTE_WEB_SECRETO")


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


@router.post("/avisos/pendientes", dependencies=[Depends(exigir_secreto_interno)])
def avisos_pendientes():
    """Avisos de la app por entregar en Telegram, ya reclamados para el bot. No pasan por el modelo ni gastan cupo."""
    return {"avisos": avisos.reclamar()}


@router.post("/avisos/resultado", dependencies=[Depends(exigir_secreto_interno)])
def aviso_resultado(cuerpo: ResultadoAvisoRequest):
    return {"registrado": avisos.resultado(cuerpo.chat_id, cuerpo.aviso_id, cuerpo.entregado)}


@router.post("/token", dependencies=[Depends(exigir_secreto_interno)])
def token_del_chat(cuerpo: TokenRequest):
    cuenta = vinculos.usuario_de_chat(cuerpo.chat_id)
    if not cuenta or (cuerpo.cuenta_id is not None and cuerpo.cuenta_id != cuenta["id"]):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat sin vínculo.")
    try:
        return {"token": emitir_token(cuenta["id"], escritura=cuerpo.escritura)}
    except AsistenteDesactivado:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El asistente está desactivado para esta cuenta.")


@router.post("/cupo", dependencies=[Depends(exigir_secreto_interno)])
def reservar_cupo(cuerpo: CupoRequest):
    """Cuenta un mensaje contra el tope diario de la cuenta de ESE chat. `limite` es null si cabe, o «dia» / «medios»."""
    cuenta = vinculos.usuario_de_chat(cuerpo.chat_id)
    if not cuenta or cuenta["id"] != cuerpo.cuenta_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat sin vínculo.")
    if not cuenta.get("asistente", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El asistente está desactivado para esta cuenta.")
    return {"limite": cupo.por_defecto.reservar(cuenta["id"], cuerpo.con_medio)}


@router.post("/uso", dependencies=[Depends(exigir_secreto_interno)])
def sumar_uso(cuerpo: UsoRequest):
    cuenta = vinculos.usuario_de_chat(cuerpo.chat_id)
    if not cuenta or cuenta["id"] != cuerpo.cuenta_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat sin vínculo.")
    cupo.por_defecto.sumar_tokens(cuenta["id"], cuerpo.tokens)
    return {"ok": True}


# --- Canal web: el servicio `finanzas-asistente` actúa con el pase que la app le dio al reenviar un mensaje de la sesión de ESA cuenta ---
def _cuenta_del_pase(p: str) -> dict:
    user_id = pase.verificar(p)
    user = master_db.get_user_by_id(user_id) if user_id else None
    if not user or not user.get("is_active") or user.get("subscription_status") in ("suspended", "cancelled"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pase inválido.")
    if not user.get("asistente_activo", 1):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El asistente está desactivado para esta cuenta.")
    return user


@router.post("/web/token", dependencies=[Depends(exigir_secreto_web)])
def token_web(cuerpo: PaseRequest):
    """Token de LECTURA de un minuto. El de escritura nunca sale de aquí: la app lo emite al confirmar y se lo entrega al servicio."""
    return {"token": emitir_token(_cuenta_del_pase(cuerpo.pase)["id"])}


@router.post("/web/cupo", dependencies=[Depends(exigir_secreto_web)])
def cupo_web(cuerpo: PaseCupoRequest):
    return {"limite": cupo.por_defecto.reservar(_cuenta_del_pase(cuerpo.pase)["id"], cuerpo.con_medio)}


@router.post("/web/uso", dependencies=[Depends(exigir_secreto_web)])
def uso_web(cuerpo: PaseUsoRequest):
    cupo.por_defecto.sumar_tokens(_cuenta_del_pase(cuerpo.pase)["id"], cuerpo.tokens)
    return {"ok": True}
