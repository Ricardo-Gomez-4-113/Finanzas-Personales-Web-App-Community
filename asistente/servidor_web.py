"""asistente/servidor_web.py - El asistente para la burbuja de la app web, como servicio aparte (compose: `finanzas-asistente`).

Misma imagen y mismo `Nucleo` que el bot de Telegram, otro canal. Corre aislado, igual que el bot: sin la base de datos, sin la clave JWT
real, sin root. SOLO lo llama la app, por una red interna de Docker que el bot no comparte y con `ASISTENTE_WEB_SECRETO` (otro valor que el
`ASISTENTE_SECRETO_INTERNO` del bot, que este servicio no tiene); el navegador nunca habla con él.

La app valida la sesión de la persona y le manda a este servicio `user_id`, un pase firmado (`asistente/pase.py`, con una clave que este servicio
no tiene) y el texto. Con el pase pide a la app tokens de lectura y cupo de ESA cuenta. Escribir solo ocurre al confirmar una propuesta: la app emite el token de escritura de 20 s en
ese momento y lo entrega junto con la orden de confirmar; este servicio no puede pedirlo por su cuenta.

Rutas (POST, JSON): /web/mensaje, /web/confirmar, /web/cancelar, /web/olvidar. GET /health.
"""

import base64
import binascii
import hmac
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from asistente import medios
from asistente.nucleo import MAX_ESTADO_EN_MEMORIA, OK, TEXTO_DE_LIMITE, Nucleo
from asistente.servicio import AsistenteDesactivado, HEADER_SECRETO, SinVinculo

PUERTO = 8100
MAX_CUERPO = 16 * 1024          # un mensaje de 2000 caracteres ocupa ~8 KB como mucho
MAX_CUERPO_ARCHIVO = 8 * 1024 * 1024   # solo /web/mensaje con un archivo: 5 MB en base64 ≈ 6.7 MB
PLAZO_DE_LECTURA_SEG = 10       # tiempo total para recibir cabeceras y cuerpo
MAX_CONEXIONES = 64             # hilos vivos; más allá se cierra la conexión sin atenderla
MAX_TEXTO = 2000
MAX_SIMULTANEAS = 16          # peticiones a la vez; las demás reciben 503 en vez de apilarse
SEGUNDOS_POR_PETICION = 10    # por operación de socket; el turno del modelo (60 s) es trabajo de este hilo, no de la conexión
_ID_PROPUESTA = re.compile(r"^[A-Za-z0-9_-]{6,24}$")


class ServicioWeb:
    """Lo que este servicio le pide a la app (ver `routers/asistente_interno.py`, rutas /web/*). Todo va con el pase de la cuenta."""

    def __init__(self, cliente, secreto):
        self._cliente = cliente
        self._cabeceras = {HEADER_SECRETO: secreto}

    def _post(self, ruta, cuerpo):
        r = self._cliente.post("/internal/asistente/web" + ruta, json=cuerpo, headers=self._cabeceras)
        if r.status_code == 404:
            raise SinVinculo()   # pase inválido o vencido
        if r.status_code == 403:
            raise AsistenteDesactivado()
        r.raise_for_status()
        return r.json()

    def token(self, pase):
        return self._post("/token", {"pase": pase})["token"]

    def reservar_cupo(self, pase, con_medio=False):
        return self._post("/cupo", {"pase": pase, "con_medio": bool(con_medio)}).get("limite")

    def sumar_tokens(self, pase, tokens):
        self._post("/uso", {"pase": pase, "tokens": max(0, int(tokens or 0))})


class _CupoWeb:
    def __init__(self, servicio):
        self._servicio = servicio

    def reservar(self, cuenta, clave, con_medio):
        return self._servicio.reservar_cupo(cuenta["pase"], con_medio)

    def sumar_tokens(self, cuenta, clave, tokens):
        self._servicio.sumar_tokens(cuenta["pase"], tokens)


class Servidor:
    """La lógica, sin sockets: `manejar(metodo, ruta, cabeceras, cuerpo) -> (código, dict)`. Así se prueba sin red."""

    def __init__(self, servicio, crear_motor, propuestas, ejecutar_con_token, secreto, reloj=time.monotonic, registrar=None):
        """`crear_motor(cuenta, clave)` arma el Motor (la cuenta trae su pase); `ejecutar_con_token(propuesta, token) -> (ok, texto)` ejecuta
        una propuesta confirmada con el token de escritura que dio la app."""
        self._secreto = secreto.encode("utf-8")
        self._ejecutar_con_token = ejecutar_con_token
        self._tokens_escritura = {}
        self._candado = threading.Lock()
        self._candados_cuenta = {}
        self.nucleo = Nucleo(crear_motor, propuestas, self._ejecutar, _CupoWeb(servicio), reloj, registrar, canal="web")

    # --- escritura ---
    def _ejecutar(self, propuesta, clave):
        with self._candado:
            token = self._tokens_escritura.pop(clave, None)
        if not token:
            return False, "No se pudo ejecutar: falta la autorización de la app."
        return self._ejecutar_con_token(propuesta, token)

    # --- entrada ---
    def autorizado(self, cabeceras):
        return hmac.compare_digest(str(cabeceras.get(HEADER_SECRETO.lower(), "")).encode("utf-8"), self._secreto)

    def manejar(self, metodo, ruta, cabeceras, cuerpo):
        if metodo == "GET" and ruta == "/health":
            return 200, {"ok": True}
        if not self.autorizado(cabeceras):
            return 401, {"detail": "No autorizado."}
        if metodo != "POST" or ruta not in ("/web/mensaje", "/web/confirmar", "/web/cancelar", "/web/olvidar"):
            return 404, {"detail": "No existe."}
        user_id = cuerpo.get("user_id") if isinstance(cuerpo, dict) else None
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id < 1:
            return 400, {"detail": "Petición inválida."}
        cuenta = {"id": user_id, "username": str(cuerpo.get("username") or "")[:64], "pase": cuerpo.get("pase")}
        if ruta in ("/web/mensaje", "/web/confirmar", "/web/cancelar") and not isinstance(cuenta["pase"], str):
            return 400, {"detail": "Petición inválida."}   # el pase lo verifica la app en cada petición que este servicio le hace
        clave = ("web", user_id)
        with self._candado_de(user_id):   # los mensajes de una misma cuenta van en orden aunque haya dos pestañas
            try:
                if ruta == "/web/mensaje":
                    return self._mensaje(clave, cuenta, cuerpo.get("texto"), cuerpo.get("adjunto"))
                if ruta == "/web/confirmar":
                    return self._confirmar(clave, cuenta, cuerpo.get("id"), cuerpo.get("token_escritura"))
                if ruta == "/web/cancelar":
                    return self._cancelar(cuenta, cuerpo.get("id"))
                self.nucleo.olvidar(clave)
                self.nucleo.propuestas.cancelar_de_cuenta(user_id)   # lo que quedó pendiente tampoco sobrevive a «nueva conversación»
                return 200, {"ok": True}
            except AsistenteDesactivado:
                return 403, {"detail": "El asistente está desactivado para esta cuenta."}
            except SinVinculo:
                return 403, {"detail": "Pase inválido."}

    def _candado_de(self, user_id):
        with self._candado:
            if len(self._candados_cuenta) > MAX_ESTADO_EN_MEMORIA:
                self._candados_cuenta = {u: k for u, k in self._candados_cuenta.items() if k.locked()}
            return self._candados_cuenta.setdefault(user_id, threading.Lock())

    def _mensaje(self, clave, cuenta, texto, adjunto=None):
        archivos, tipo = None, ""
        if adjunto is not None:
            try:
                datos = base64.b64decode(adjunto.get("datos_b64") if isinstance(adjunto, dict) else None, validate=True)
                tipo, mime = medios.clasificar(datos)   # el servicio revisa los bytes por su cuenta: es quien los pasa al modelo
            except (binascii.Error, TypeError, ValueError):
                return 400, {"detail": "Archivo inválido."}
            except medios.Rechazo as rechazo:
                return 200, {"estado": "archivo", "texto": str(rechazo), "propuestas": []}
            archivos = [{"mime": mime, "datos": datos}]
        if not isinstance(texto, str) or len(texto) > MAX_TEXTO or (not texto.strip() and not archivos):
            return 400, {"detail": "El mensaje debe tener entre 1 y 2000 caracteres."}
        resultado = self.nucleo.turno(clave, cuenta, texto, archivos, tipo)
        if resultado.estado != OK:
            return 200, {"estado": resultado.estado, "texto": TEXTO_DE_LIMITE[resultado.estado], "propuestas": []}
        return 200, {"estado": OK, "texto": resultado.texto, "propuestas": [{"id": i, "resumen": r} for i, r in resultado.propuestas]}

    def _confirmar(self, clave, cuenta, id_, token):
        if not isinstance(id_, str) or not _ID_PROPUESTA.match(id_) or not isinstance(token, str) or not token:
            return 400, {"detail": "Petición inválida."}
        with self._candado:
            self._tokens_escritura[clave] = token
        try:
            resultado, texto = self.nucleo.confirmar(clave, cuenta, id_)
        finally:
            with self._candado:
                self._tokens_escritura.pop(clave, None)   # el token de escritura no se queda aquí: sirve para esa orden y nada más
        return 200, {"resultado": resultado, "texto": texto}

    def _cancelar(self, cuenta, id_):
        if not isinstance(id_, str) or not _ID_PROPUESTA.match(id_):
            return 400, {"detail": "Petición inválida."}
        resultado, texto = self.nucleo.cancelar(cuenta, id_)
        return 200, {"resultado": resultado, "texto": texto}


def _manejador(servidor, libres):
    class Manejador(BaseHTTPRequestHandler):
        timeout = SEGUNDOS_POR_PETICION
        protocol_version = "HTTP/1.0"

        def log_message(self, *args):   # sin registro de accesos: las rutas no llevan datos, pero el cuerpo sí
            pass

        def _responder(self, codigo, datos):
            cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _leer(self, largo):
            """El cuerpo, con un plazo total: quien lo manda a gotas no retiene el hilo."""
            limite, partes, faltan = time.monotonic() + PLAZO_DE_LECTURA_SEG * (1 + largo // MAX_CUERPO // 64), [], largo   # un archivo grande tiene más plazo
            while faltan > 0:
                if time.monotonic() > limite:
                    raise TimeoutError()
                self.connection.settimeout(max(0.05, limite - time.monotonic()))   # cada lectura espera como mucho lo que queda del plazo total
                trozo = self.rfile.read1(min(faltan, 4096)) if hasattr(self.rfile, "read1") else self.rfile.read(min(faltan, 4096))
                if not trozo:
                    raise TimeoutError()
                partes.append(trozo)
                faltan -= len(trozo)
            return b"".join(partes)

        def _atender(self, metodo):
            if not libres.acquire(blocking=False):
                return self._responder(503, {"detail": "Ocupado."})
            try:
                cabeceras = {k.lower(): v for k, v in self.headers.items()}
                if not (metodo == "GET" and self.path.split("?")[0] == "/health") and not servidor.autorizado(cabeceras):
                    return self._responder(401, {"detail": "No autorizado."})   # sin el secreto no se lee ni el cuerpo
                try:
                    largo = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return self._responder(400, {"detail": "Petición inválida."})
                con_archivo = metodo == "POST" and self.path.split("?")[0] == "/web/mensaje"   # ya autenticado: solo esta ruta admite un cuerpo grande
                if largo < 0 or largo > (MAX_CUERPO_ARCHIVO if con_archivo else MAX_CUERPO):
                    return self._responder(413, {"detail": "Cuerpo demasiado grande."})
                try:
                    crudo = self._leer(largo) if largo else b""
                except (TimeoutError, OSError):
                    return self._responder(408, {"detail": "Lectura demasiado lenta."})
                try:
                    cuerpo = json.loads(crudo) if crudo else {}
                except ValueError:
                    return self._responder(400, {"detail": "JSON inválido."})
                codigo, datos = servidor.manejar(metodo, self.path.split("?")[0], cabeceras, cuerpo)
                self._responder(codigo, datos)
            except Exception as error:   # un fallo no debe tumbar el servicio ni filtrar datos por el error
                _evento("web_error", tipo=type(error).__name__)
                try:
                    self._responder(500, {"detail": "Error interno."})
                except Exception:
                    pass
            finally:
                libres.release()

        def do_GET(self):
            self._atender("GET")

        def do_POST(self):
            self._atender("POST")
    return Manejador


class _ServidorAcotado(ThreadingHTTPServer):
    """Un hilo por conexión, pero con tope: una avalancha de conexiones abiertas no agota hilos ni memoria."""
    daemon_threads = True

    def __init__(self, *args, max_conexiones=MAX_CONEXIONES, **kwargs):
        super().__init__(*args, **kwargs)
        self._cupo_conexiones = threading.BoundedSemaphore(max_conexiones)

    def process_request(self, request, client_address):
        if not self._cupo_conexiones.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._cupo_conexiones.release()


def _evento(nombre, **campos):
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "evento": nombre, **campos}, ensure_ascii=False), flush=True)


def main():
    import httpx

    from asistente.api import ejecutar_propuesta, obtener_con_proveedor
    from asistente.herramientas import Herramientas
    from asistente.modelo_gemini import ModeloGemini
    from asistente.motor import Motor
    from asistente.propuestas import Propuestas
    from finanzas_core import ahora_local

    secreto = os.environ.get("ASISTENTE_WEB_SECRETO", "").strip()
    if len(secreto) < 32:
        sys.exit("Falta ASISTENTE_WEB_SECRETO (de 32 caracteres o más)")
    app = httpx.Client(base_url=os.environ.get("APP_URL", "http://finanzas-community:8001"), timeout=30)
    servicio = ServicioWeb(app, secreto)
    modelo = ModeloGemini()
    propuestas = Propuestas()
    hoy = lambda: ahora_local().date()  # noqa: E731

    def crear_motor(cuenta, clave):
        obtener = obtener_con_proveedor(app, lambda: servicio.token(cuenta["pase"]))
        return Motor(modelo, Herramientas(obtener, propuestas, cuenta["id"], hoy), hoy)

    def ejecutar_con_token(propuesta, token):
        return ejecutar_propuesta(app, propuesta, lambda: token)

    servidor = Servidor(servicio, crear_motor, propuestas, ejecutar_con_token, secreto, registrar=_evento)
    http = _ServidorAcotado(("0.0.0.0", PUERTO), _manejador(servidor, threading.BoundedSemaphore(MAX_SIMULTANEAS)))
    _evento("web_iniciado", puerto=PUERTO, simultaneas=MAX_SIMULTANEAS)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        http.server_close()


if __name__ == "__main__":
    main()
