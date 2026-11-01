"""limite_cuerpo.py - Tope del tamaño del cuerpo de cada petición.

uvicorn no limita el cuerpo: sin este middleware, una petición de varios MB se lee completa antes de que la
validación la rechace. Las peticiones normales tienen 256 KB. La restauración (/api/restore) admite el respaldo
de 50 MB más la cabecera multipart. El tope se comprueba dos veces: por Content-Length (sin leer el cuerpo) y
contando los bytes que llegan (para peticiones chunked, que no declaran tamaño).
"""

from starlette.responses import JSONResponse

LIMITE_CUERPO_BYTES = 256 * 1024
LIMITE_RESTAURACION_BYTES = 51 * 1024 * 1024
RUTA_RESTAURACION = "/api/restore"
METODOS_CON_CUERPO = {"POST", "PUT", "PATCH"}
MENSAJE_EXCEDIDO = "La petición supera el tamaño máximo permitido."


class CuerpoDemasiadoGrande(Exception):
    """Se lanza al leer el cuerpo por encima del tope. La captura el propio middleware, que responde 413."""


def respuesta_demasiado_grande():
    return JSONResponse(status_code=413, content={"detail": MENSAJE_EXCEDIDO})


class LimiteTamanoCuerpo:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in METODOS_CON_CUERPO:
            await self.app(scope, receive, send)
            return

        limite = LIMITE_RESTAURACION_BYTES if scope["path"] == RUTA_RESTAURACION else LIMITE_CUERPO_BYTES
        declarado = dict(scope["headers"]).get(b"content-length", b"")
        if declarado.isdigit() and int(declarado) > limite:
            await respuesta_demasiado_grande()(scope, receive, send)
            return

        recibido = 0
        excedido = False
        respondido = False

        async def receive_acotado():
            nonlocal recibido, excedido
            mensaje = await receive()
            if mensaje["type"] == "http.request":
                recibido += len(mensaje.get("body", b""))
                if recibido > limite:
                    excedido = True
                    raise CuerpoDemasiadoGrande(limite)
            return mensaje

        async def send_acotado(mensaje):
            # FastAPI convierte el corte en un 400 o 422 de parseo: esa respuesta se descarta y se envía el 413
            nonlocal respondido
            if excedido:
                return
            respondido = True
            await send(mensaje)

        try:
            await self.app(scope, receive_acotado, send_acotado)
        except CuerpoDemasiadoGrande:
            pass
        if excedido and not respondido:
            await respuesta_demasiado_grande()(scope, receive, send)
