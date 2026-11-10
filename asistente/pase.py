"""asistente/pase.py - Pase de un mensaje web: prueba, firmada por la app, de que ESA cuenta acaba de escribir en su sesión.

El servicio del asistente web no ve el JWT de la persona. Cuando la app reenvía un mensaje le manda un pase de unos minutos
(`user_id.vence.firma`). Con él puede pedirle a la app tokens de lectura y cupo para ESA cuenta, y solo mientras el pase valga: un servicio
comprometido no puede tocar cuentas que no hayan escrito hace un momento. La firma usa una clave derivada de la clave JWT, que solo tiene la
app: el servicio tiene el secreto interno (lo necesita para hablar con la app), así que firmar con ese le permitiría falsificar pases. Por lo
mismo el servicio no verifica pases: los verifica la app en cada petición que el servicio le hace.
La escritura nunca va por el pase: el token de escritura (20 s) lo emite la app al confirmar y se lo entrega al servicio.
"""

import hashlib
import hmac
import re
import time

import security

SEGUNDOS_DEL_PASE = 300


def _secreto():
    return hmac.new(security.SECRET_KEY.encode("utf-8"), b"asistente-pase-web", hashlib.sha256).digest()


def _firma(secreto, user_id, vence):
    return hmac.new(secreto, f"web:{user_id}:{vence}".encode("utf-8"), hashlib.sha256).hexdigest()


def crear(user_id, ahora=None, segundos=SEGUNDOS_DEL_PASE):
    secreto = _secreto()
    vence = int((time.time() if ahora is None else ahora) + segundos)
    return f"{int(user_id)}.{vence}.{_firma(secreto, int(user_id), vence)}"


def verificar(pase, ahora=None):
    """`user_id` de un pase válido y vigente, o None."""
    secreto = _secreto()
    if not isinstance(pase, str) or len(pase) > 200:
        return None
    partes = pase.split(".")
    if len(partes) != 3 or not all(re.fullmatch(r"[0-9]{1,12}", x) for x in partes[:2]):   # solo dígitos ASCII (isdigit() acepta «²» y «٥»)
        return None
    user_id, vence = int(partes[0]), int(partes[1])
    if (time.time() if ahora is None else ahora) > vence:
        return None
    if not hmac.compare_digest(partes[2], _firma(secreto, user_id, vence)):
        return None
    return user_id
