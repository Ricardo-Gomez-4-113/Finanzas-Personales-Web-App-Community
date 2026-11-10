"""routers/asistente_web.py - El asistente en la burbuja de la app: la persona escribe aquí y la app lo reenvía al servicio del asistente.

La app es quien conoce la sesión. Valida el JWT de la persona, comprueba que el administrador no le haya apagado el asistente y le entrega al
servicio (`finanzas-asistente`, red interna de Docker) el `user_id` de ESA sesión con un pase firmado. El navegador nunca habla con el servicio
ni recibe tokens del asistente. Para confirmar una propuesta la app emite el token de escritura de 20 s en ese momento y lo manda en la orden.
Sin `ASISTENTE_WEB_URL` o sin `ASISTENTE_WEB_SECRETO` (otro valor que el secreto del bot de Telegram) el asistente web no existe y la burbuja no aparece.
"""

import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

import master_db
from asistente import medios, pase
from asistente.servicio import HEADER_SECRETO
from asistente.tokens import AsistenteDesactivado, emitir_token
from security import verify_token

router = APIRouter(prefix="/api/asistente/web", tags=["Asistente web"])

SEGUNDOS_DEL_MENSAJE = 75      # el turno del modelo corta a los 60 s
SEGUNDOS_DE_LA_SALUD = 2
CACHE_DE_SALUD = 30
MAX_EN_CURSO_POR_CUENTA = 1    # el servicio atiende a una cuenta de una en una: una segunda petición a la vez solo ocuparía un hilo de la app esperando
MAX_EN_CURSO_GLOBAL = 8        # entre todas las cuentas: cada petición bloquea un hilo de la app hasta 75 s, no puede acabarse el pool
MAX_TEXTO_RESPUESTA = 4000
MAX_RESUMEN = 400
_ID_PROPUESTA = re.compile(r"^[A-Za-z0-9_-]{6,24}$")

_salud = {"hasta": 0.0, "ok": False}
_candado = threading.Lock()
_en_curso = {}


class _SinRedireccion(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):   # una respuesta del servicio nunca manda a la app a otro sitio (ni con el secreto en la cabecera)
        return None


_abrir = urllib.request.build_opener(_SinRedireccion).open


class MensajeRequest(BaseModel):
    texto: str = Field(..., min_length=1, max_length=2000)


def _config():
    url = os.environ.get("ASISTENTE_WEB_URL", "").strip().rstrip("/")
    secreto = os.environ.get("ASISTENTE_WEB_SECRETO", "").strip()
    return (url, secreto) if url.startswith("http") and len(secreto) >= 32 else (None, None)


class ServicioNoDisponible(Exception):
    pass


def _llamar(ruta: str, cuerpo: dict | None = None, segundos: float = SEGUNDOS_DEL_MENSAJE) -> dict:
    """POST (o GET si no hay cuerpo) al servicio del asistente. Cualquier fallo de red o de formato es «no disponible»; nunca se devuelve su texto."""
    url, secreto = _config()
    if not url:
        raise ServicioNoDisponible()
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    peticion = urllib.request.Request(url + ruta, data=datos, method="POST" if cuerpo is not None else "GET",
                                      headers={"Content-Type": "application/json", HEADER_SECRETO: secreto})
    try:
        with _abrir(peticion, timeout=segundos) as respuesta:   # URL fija de la red interna, definida por el operador
            datos = json.loads(respuesta.read(1_000_000))
        if not isinstance(datos, dict):
            raise ServicioNoDisponible()
        return datos
    except urllib.error.HTTPError as error:
        if error.code == 403:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El asistente está desactivado para esta cuenta.")
        raise ServicioNoDisponible()
    except (urllib.error.URLError, OSError, ValueError, RecursionError):
        raise ServicioNoDisponible()


def _servicio_responde() -> bool:
    ahora = time.monotonic()
    if ahora < _salud["hasta"]:
        return _salud["ok"]
    try:
        ok = _llamar("/health", None, SEGUNDOS_DE_LA_SALUD).get("ok") is True
    except (ServicioNoDisponible, HTTPException):
        ok = False
    _salud.update(hasta=ahora + CACHE_DE_SALUD, ok=ok)
    return ok


def _cuenta_con_asistente(usuario: Dict[str, Any]) -> Dict[str, Any]:
    """La cuenta de la sesión, leída de la base (no del token), con el asistente encendido. Si no: 403."""
    user = master_db.get_user_by_id(int(usuario["id"]))
    if not user or not user.get("asistente_activo", 1):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El asistente está desactivado para esta cuenta.")
    return user


def _reenviar(user: Dict[str, Any], ruta: str, extra, segundos: float = SEGUNDOS_DEL_MENSAJE) -> dict:
    """`extra` es un dict o una función que lo arma (se llama ya con el cupo de peticiones tomado: así un token de escritura no se emite en balde)."""
    uid = int(user["id"])
    with _candado:
        if _en_curso.get(uid, 0) >= MAX_EN_CURSO_POR_CUENTA:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Espera a que el asistente termine de contestar.")
        if sum(_en_curso.values()) >= MAX_EN_CURSO_GLOBAL:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="El asistente está muy ocupado. Intenta en un momento.")
        _en_curso[uid] = _en_curso.get(uid, 0) + 1
    try:
        cuerpo = {"user_id": uid, "username": user["username"], "pase": pase.crear(uid), **(extra() if callable(extra) else extra)}
        try:
            return _llamar(ruta, cuerpo, segundos)
        except ServicioNoDisponible:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="El asistente no está disponible ahora. Intenta en un momento.")
    finally:
        with _candado:
            _en_curso[uid] -= 1
            if _en_curso[uid] <= 0:
                _en_curso.pop(uid, None)


def _texto(valor, tope=MAX_TEXTO_RESPUESTA) -> str:
    return valor[:tope] if isinstance(valor, str) else ""


def _propuestas(datos: dict) -> list:
    """La respuesta del servicio no es de fiar: solo pasan ids con la forma de un id de propuesta y textos acotados."""
    lista = datos.get("propuestas")
    return [{"id": p["id"], "resumen": _texto(p.get("resumen"), MAX_RESUMEN)} for p in (lista if isinstance(lista, list) else [])[:5]
            if isinstance(p, dict) and isinstance(p.get("id"), str) and _ID_PROPUESTA.match(p["id"])]


@router.get("/estado", summary="¿Está disponible el asistente para esta cuenta?")
def estado(usuario: Dict[str, Any] = Depends(verify_token)):
    user = master_db.get_user_by_id(int(usuario["id"]))
    if _config()[0] is None:
        return {"disponible": False, "motivo": "no_configurado"}
    if not user or not user.get("asistente_activo", 1):
        return {"disponible": False, "motivo": "desactivado"}
    if not _servicio_responde():
        return {"disponible": False, "motivo": "no_responde"}
    return {"disponible": True, "motivo": ""}


@router.post("/mensaje", summary="Un mensaje de la persona al asistente")
def mensaje(cuerpo: MensajeRequest, usuario: Dict[str, Any] = Depends(verify_token)):
    user = _cuenta_con_asistente(usuario)
    datos = _reenviar(user, "/web/mensaje", {"texto": cuerpo.texto})
    return _respuesta_de_mensaje(datos)


def _respuesta_de_mensaje(datos: dict) -> dict:
    estado_ = datos.get("estado")
    return {"estado": estado_ if estado_ in ("ok", "ritmo", "dia", "medios", "archivo") else "error", "texto": _texto(datos.get("texto")), "propuestas": _propuestas(datos)}


@router.post("/mensaje-archivo", summary="Un mensaje con una foto o una nota de voz")
def mensaje_con_archivo(texto: str = Form("", max_length=2000), archivo: UploadFile = File(...), usuario: Dict[str, Any] = Depends(verify_token)):
    """Multipart: `texto` (opcional, como pie) y `archivo`. La app revisa los bytes (formato y tamaño) antes de reenviarlos; el servicio los revisa otra vez."""
    user = _cuenta_con_asistente(usuario)
    datos = archivo.file.read(medios.MAX_BYTES + 1)
    if not datos:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo está vacío.")
    try:
        medios.clasificar(datos)
    except medios.Rechazo as rechazo:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(rechazo))
    return _respuesta_de_mensaje(_reenviar(user, "/web/mensaje", {"texto": texto, "adjunto": {"datos_b64": base64.b64encode(datos).decode("ascii")}}))


def _id_valido(id_: str) -> str:
    if not _ID_PROPUESTA.match(id_):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Propuesta inválida.")
    return id_


@router.post("/confirmar/{id_}", summary="Confirmar una propuesta del asistente")
def confirmar(id_: str, usuario: Dict[str, Any] = Depends(verify_token)):
    user = _cuenta_con_asistente(usuario)
    _id_valido(id_)

    def cuerpo():
        try:
            token = emitir_token(int(user["id"]), escritura=True)   # solo aquí, y solo para la cuenta de la sesión
        except (AsistenteDesactivado, ValueError):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El asistente está desactivado para esta cuenta.")
        return {"id": id_, "token_escritura": token}

    datos = _reenviar(user, "/web/confirmar", cuerpo)
    return {"resultado": _texto(datos.get("resultado"), 40), "texto": _texto(datos.get("texto"))}


@router.post("/cancelar/{id_}", summary="Cancelar una propuesta del asistente")
def cancelar(id_: str, usuario: Dict[str, Any] = Depends(verify_token)):
    user = _cuenta_con_asistente(usuario)
    datos = _reenviar(user, "/web/cancelar", {"id": _id_valido(id_)})
    return {"resultado": _texto(datos.get("resultado"), 40), "texto": _texto(datos.get("texto"))}


@router.post("/olvidar", summary="Empezar una conversación nueva")
def olvidar(usuario: Dict[str, Any] = Depends(verify_token)):
    user = master_db.get_user_by_id(int(usuario["id"]))   # olvidar no gasta nada: funciona aunque el asistente esté apagado
    if user:
        _reenviar(user, "/web/olvidar", {}, SEGUNDOS_DE_LA_SALUD * 5)
    return {"ok": True}


def olvidar_cuenta(user_id: int) -> None:
    """Mejor esfuerzo: que el servicio olvide la conversación y cancele las propuestas pendientes de esa cuenta (al apagarle el asistente)."""
    user = master_db.get_user_by_id(int(user_id))
    if user:
        try:
            _reenviar(user, "/web/olvidar", {}, SEGUNDOS_DE_LA_SALUD * 5)
        except (HTTPException, Exception):
            pass
