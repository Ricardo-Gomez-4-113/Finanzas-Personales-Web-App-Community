"""
observabilidad.py - Registro de fallos en formato JSON, una línea por evento.

Un único archivo rotativo en data/logs/app.log (o en FINANZAS_LOG_DIR). Cada línea trae:
ts, nivel, evento, mensaje y, según el caso, id_peticion, metodo, ruta, estado, duracion_ms,
usuario_id, excepcion y traza. Nunca se registran contraseñas, tokens, cuerpos de petición,
importes ni la cadena de consulta de la URL.

Si el archivo no se puede abrir o escribir, el registro sigue por stderr y la petición no se rompe.
"""
import json
import logging
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from fastapi import Request
from fastapi.responses import JSONResponse

from database import DATA_DIR
from security import decode_access_token

NOMBRE_LOGGER = "finanzas"
ARCHIVO_LOG = "app.log"
TAMANO_MAXIMO_BYTES = 5 * 1024 * 1024
ARCHIVOS_CONSERVADOS = 5  # app.log.1 … app.log.5: el disco queda acotado a unos 30 MB

CAMPOS_EXTRA = ("id_peticion", "metodo", "ruta", "estado", "duracion_ms", "usuario_id", "tipo", "origen", "linea")
MAXIMO_TEXTO = 300
MAXIMO_TRAZA = 4000

logger = logging.getLogger(NOMBRE_LOGGER)


class FormatoJson(logging.Formatter):
    """Una línea JSON por registro. Los campos opcionales solo aparecen si el evento los trae."""

    def format(self, record):
        datos = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="seconds"),
            "nivel": record.levelname,
            "evento": getattr(record, "evento", "general"),
            "mensaje": record.getMessage()[:MAXIMO_TEXTO],
        }
        for campo in CAMPOS_EXTRA:
            valor = getattr(record, campo, None)
            if valor is not None:
                datos[campo] = valor
        if record.exc_info:
            datos["excepcion"] = f"{record.exc_info[0].__name__}: {str(record.exc_info[1])[:MAXIMO_TEXTO]}"
            datos["traza"] = "".join(traceback.format_exception(*record.exc_info))[-MAXIMO_TRAZA:]
        return json.dumps(datos, ensure_ascii=False, default=str)


def configurar(directorio: str) -> str:
    """Conecta el logger al archivo de ese directorio. Devuelve la ruta usada, o 'stderr' si no se pudo abrir."""
    for existente in list(logger.handlers):
        logger.removeHandler(existente)
        existente.close()
    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        os.makedirs(directorio, exist_ok=True)
        ruta = os.path.join(directorio, ARCHIVO_LOG)
        manejador = RotatingFileHandler(
            ruta, maxBytes=TAMANO_MAXIMO_BYTES, backupCount=ARCHIVOS_CONSERVADOS, encoding="utf-8"
        )
        destino = ruta
    except OSError as error:
        manejador = logging.StreamHandler(sys.stderr)
        destino = "stderr"
        print(f"[LOG] No se pudo abrir el registro en {directorio}: {error}. Se escribe por stderr.", file=sys.stderr)

    manejador.setFormatter(FormatoJson())
    logger.addHandler(manejador)
    return destino


def directorio_por_defecto() -> str:
    """FINANZAS_LOG_DIR si está definida (pruebas y despliegues); si no, data/logs."""
    return os.environ.get("FINANZAS_LOG_DIR") or os.path.join(DATA_DIR, "logs")


def registrar_error_servidor(evento: str, mensaje: str, excepcion: BaseException = None, **campos):
    """Error del servidor con su traza. Sin excepción explícita, toma la que esté en curso dentro de un except."""
    exc_info = (type(excepcion), excepcion, excepcion.__traceback__) if excepcion else True
    logger.error(mensaje, exc_info=exc_info, extra={"evento": evento, **campos})


def _usuario_del_token(request: Request):
    """Id del usuario según la firma del token. Solo para el registro: la autorización la decide verify_token."""
    cabecera = request.headers.get("authorization", "")
    if not cabecera.lower().startswith("bearer "):
        return None
    payload = decode_access_token(cabecera[7:].strip())
    try:
        return int(payload["sub"]) if payload else None
    except (KeyError, TypeError, ValueError):
        return None


async def registrar_peticion(request: Request, call_next):
    """Asigna un id a cada petición, la registra si falla y responde 500 limpio si una excepción escapa."""
    id_peticion = uuid.uuid4().hex[:16]
    request.state.id_peticion = id_peticion
    inicio = time.perf_counter()
    base = {
        "id_peticion": id_peticion,
        "metodo": request.method,
        "ruta": request.url.path,
        "usuario_id": _usuario_del_token(request),
    }
    try:
        response = await call_next(request)
    except Exception:
        duracion = round((time.perf_counter() - inicio) * 1000, 1)
        registrar_error_servidor("error_servidor", "Excepción no controlada", estado=500, duracion_ms=duracion, **base)
        response = JSONResponse(status_code=500, content={"detail": "Error interno del servidor."})
    else:
        estado = response.status_code
        duracion = round((time.perf_counter() - inicio) * 1000, 1)
        if estado >= 500:
            logger.error("Respuesta de error del servidor", extra={"evento": "error_servidor", "estado": estado, "duracion_ms": duracion, **base})
        elif estado in (401, 403):
            logger.warning("Acceso rechazado", extra={"evento": "acceso_rechazado", "estado": estado, "duracion_ms": duracion, **base})
    response.headers["X-Request-Id"] = id_peticion
    return response


def registrar_reporte_cliente(tipo: str, mensaje: str, **campos):
    """Fallo que detectó el navegador: JavaScript, promesa rechazada o respuesta 5xx de la API."""
    logger.error(mensaje, extra={"evento": "cliente", "tipo": tipo, **campos})
