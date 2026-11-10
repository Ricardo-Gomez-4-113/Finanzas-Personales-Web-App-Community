"""asistente/bot_telegram.py - Bot de Telegram del asistente: un chat privado, una cuenta.

`Bot` es el núcleo y no sabe de red: recibe «actualizaciones» (los diccionarios de la API de Telegram) y contesta con un objeto
`telegram` que tiene `enviar`, `editar_botones` y `responder_callback`. `TelegramHTTP` es la implementación real y `main()` el
ciclo de long polling (la Pi no necesita abrir ningún puerto).

Reglas que no se negocian:
- La cuenta a cuyo nombre se actúa sale SIEMPRE de `servicio.usuario_de_chat(chat_id)` (la app, por su API interna), nunca del texto
  del mensaje ni del `callback_data`. El bot no toca la base ni tiene la clave JWT: solo pide tokens de un minuto de cuentas vinculadas. Un chat sin vínculo solo recibe la explicación de cómo vincularse.
- Solo chats privados; los grupos se ignoran.
- Confirmar una propuesta es un botón (callback), nunca un «sí» escrito: ese pasaría por el modelo.
- Los textos se mandan sin `parse_mode`: nada de lo que venga de la cuenta se interpreta como formato.
- No se registra ningún texto de mensajes ni nada que contenga el token (las excepciones de httpx traen la URL con el token:
  solo se registra el tipo).
"""

import contextlib
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from asistente import medios
from asistente.cupo import MAX_MEDIOS_DIA, MAX_MENSAJES_DIA, MAX_TOKENS_DIA, entero_de_entorno as _entero_de_entorno  # noqa: F401
from asistente.nucleo import MAX_ESTADO_EN_MEMORIA, MAX_MENSAJES_GLOBAL_POR_HORA, MAX_MENSAJES_POR_HORA, TEXTO_DE_LIMITE, TTL_CONVERSACION, Nucleo  # noqa: F401
from asistente.servicio import AsistenteDesactivado, SinVinculo

TRABAJADORES = 8                 # carriles: chats que se atienden a la vez
MAX_INTENTOS_VINCULO_POR_HORA = 10   # por chat: adivinar códigos de vinculación
MAX_INTENTOS_VINCULO_GLOBAL = 200    # entre todos los chats; cuenta aparte, para que la basura no gaste el cupo de los turnos reales
MAX_PENDIENTES_POR_CHAT = 3      # actualizaciones en cola de un mismo chat (las demás se descartan)
MAX_PENDIENTES_POR_CARRIL = 50   # cola máxima de un carril
MAX_TEXTO_TELEGRAM = 3500        # el límite de Telegram es 4096
PLAZO_DESCARGA_SEG = 30           # tiempo total para bajar un archivo de Telegram
AVISO_ESCRIBIENDO_SEG = 4        # Telegram apaga «escribiendo…» a los 5 s
_ID_PROPUESTA = re.compile(r"^[A-Za-z0-9_-]{6,24}$")

EXPLICACION = ("Soy el asistente de tus finanzas. Para usarme, vincula este chat con tu cuenta: abre la app, entra a Mi perfil, "
               "elige «Asistente en Telegram» y escanea el código QR con el teléfono.")
APAGADO = "El administrador desactivó el asistente para tu cuenta. La app sigue disponible."
AYUDA = ("Pregúntame por tu saldo, tus gastos, tus metas, tus fijos o tus inversiones. También puedo preparar un gasto, un ingreso, "
         "un depósito en una meta o saltar un fijo: siempre te pido confirmar con un botón. Puedes escribirme, mandarme una nota de voz o la foto de un ticket.\n\n"
         "/olvidar borra lo que hablamos. /desvincular quita este chat de tu cuenta.")


def _recortar(texto):
    texto = texto or ""
    return texto if len(texto) <= MAX_TEXTO_TELEGRAM else texto[:MAX_TEXTO_TELEGRAM].rstrip() + "…"


class _CupoDeChat:
    """El tope diario vive en la app: el bot lo pide por el servicio, indicando el chat (que debe ser de esa cuenta)."""

    def __init__(self, servicio):
        self._servicio = servicio

    def reservar(self, cuenta, chat_id, con_medio):
        return self._servicio.reservar_cupo(chat_id, cuenta["id"], con_medio)

    def sumar_tokens(self, cuenta, chat_id, tokens):
        self._servicio.sumar_tokens(chat_id, cuenta["id"], tokens)


class Bot:
    """El canal de Telegram: lee las actualizaciones, vincula chats, baja archivos y muestra las respuestas. La conversación, el ritmo,
    el tope diario y las propuestas son del `Nucleo` (`self.nucleo`), que la web usa igual."""

    def __init__(self, telegram, servicio, crear_motor, propuestas, ejecutar, reloj=time.monotonic, registrar=None):
        """`servicio` responde de qué cuenta es un chat, vincula/desvincula y reserva el tope diario (`asistente/servicio.py`);
        `crear_motor(cuenta, chat_id) -> Motor`; `propuestas` es el `Propuestas` que comparten las herramientas;
        `ejecutar(propuesta, chat_id) -> (ok, texto)` ejecuta una propuesta ya confirmada.
        `registrar(evento, **campos)` recibe los eventos del bot: solo tipos, conteos y el id numérico de la cuenta; nunca texto,
        importes ni ids de chat."""
        self.tg = telegram
        self.servicio = servicio
        self.propuestas = propuestas
        self._reloj = reloj
        self._registrar = registrar
        self.nucleo = Nucleo(crear_motor, propuestas, ejecutar, _CupoDeChat(servicio), reloj, lambda n, **c: self._evento(n, **c), canal="telegram")
        self._candado = threading.RLock()   # protege los diccionarios compartidos entre chats que se atienden a la vez
        self._candados_chat = {}            # chat_id -> Lock: los mensajes de un mismo chat se atienden en orden
        self._vinculos = {}         # chat_id -> [t, ...] de la última hora: intentos de vincular
        self._vinculos_global = []  # [t, ...] de la última hora, todos los chats

    def _evento(self, nombre, **campos):
        if self._registrar:   # se lee en cada llamada: un registro puesto después de crear el bot también se usa
            try:
                self._registrar(nombre, **campos)
            except Exception:
                pass   # el registro nunca tumba la conversación

    # --- entrada ---------------------------------------------------------------------------------------------------
    def procesar(self, actualizacion):
        """Atiende una actualización. Los mensajes de un mismo chat van en orden; chats distintos pueden ir a la vez."""
        chat_id = self._chat_de(actualizacion)
        if chat_id is None:
            return
        with self._candado:
            candado = self._candados_chat.setdefault(chat_id, threading.Lock())
        with candado:
            self._procesar(actualizacion)

    @staticmethod
    def _chat_de(actualizacion):
        consulta = actualizacion.get("callback_query")
        mensaje = consulta.get("message") if consulta else actualizacion.get("message")
        chat = (mensaje or {}).get("chat") or {}
        return chat.get("id") if isinstance(chat.get("id"), int) and not isinstance(chat.get("id"), bool) else None

    def _procesar(self, actualizacion):
        if actualizacion.get("callback_query"):
            return self._callback(actualizacion["callback_query"])
        mensaje = actualizacion.get("message")
        if not mensaje:
            return
        chat = mensaje.get("chat") or {}
        if chat.get("type") != "private" or not isinstance(chat.get("id"), int):
            return   # grupos y canales: se ignoran
        origen = mensaje.get("from") or {}
        if origen.get("is_bot") or origen.get("id") != chat["id"]:
            return
        texto = mensaje.get("text")
        try:
            medio = medios.elegir(mensaje)
        except medios.Rechazo as rechazo:
            if self.servicio.usuario_de_chat(chat["id"]):
                self.tg.enviar(chat["id"], str(rechazo))
            else:
                self.tg.enviar(chat["id"], EXPLICACION)
            return
        if medio:
            texto = mensaje.get("caption")   # el pie de la foto es lo que la persona escribe; un «/comando» ahí no se ejecuta
            if not isinstance(texto, str):
                texto = ""
        self._mensaje(chat["id"], origen.get("username"), texto, medio)

    # --- mensajes --------------------------------------------------------------------------------------------------
    def _mensaje(self, chat_id, username, texto, medio=None):
        if not isinstance(texto, str):
            cuenta = self.servicio.usuario_de_chat(chat_id)
            self.tg.enviar(chat_id, medios.NO_ENTIENDE if cuenta else EXPLICACION)
            return
        if not medio and texto.startswith("/"):
            partes = texto.split(maxsplit=1)
            return self._comando(chat_id, username, partes[0].split("@")[0].lower(), partes[1].strip() if len(partes) > 1 else "")
        cuenta = self.servicio.usuario_de_chat(chat_id)
        if not cuenta:
            self.tg.enviar(chat_id, EXPLICACION)
            return
        if not cuenta.get("asistente", True):
            self.tg.enviar(chat_id, APAGADO)
            return
        self._purgar(self._reloj())
        try:
            resultado = self.nucleo.turno(chat_id, cuenta, texto, (lambda: [self._descargar(medio)]) if medio else None,
                                          medio["tipo"] if medio else "", lambda: self._escribiendo(chat_id))
        except medios.Rechazo as rechazo:
            self.tg.enviar(chat_id, str(rechazo))
            return
        except SinVinculo:   # el chat se desvinculó entre la comprobación y el turno
            self.tg.enviar(chat_id, EXPLICACION)
            return
        except AsistenteDesactivado:   # el administrador lo apagó en este momento
            self.tg.enviar(chat_id, APAGADO)
            return
        if resultado.estado in TEXTO_DE_LIMITE:
            self.tg.enviar(chat_id, TEXTO_DE_LIMITE[resultado.estado])
        else:
            self.tg.enviar(chat_id, _recortar(resultado.texto))
            for id_, resumen in resultado.propuestas:
                self.tg.enviar(chat_id, "Confirma esta operación:\n" + resumen, botones=[[("Confirmar", f"c:{id_}"), ("Cancelar", f"x:{id_}")]])

    @contextlib.contextmanager
    def _escribiendo(self, chat_id):
        """Mantiene el aviso «escribiendo…» mientras el modelo piensa (Telegram lo apaga a los 5 s, así que se repite). Un fallo aquí nunca afecta al turno."""
        aviso = getattr(self.tg, "escribiendo", None)
        parar = threading.Event()

        def repetir():
            while not parar.is_set():
                try:
                    aviso(chat_id)
                except Exception:
                    pass
                parar.wait(AVISO_ESCRIBIENDO_SEG)

        hilo = threading.Thread(target=repetir, daemon=True) if aviso else None
        if hilo:
            hilo.start()
        try:
            yield
        finally:
            parar.set()

    def _comando(self, chat_id, username, comando, argumento):
        if comando == "/start":
            if argumento:
                return self._vincular(chat_id, username, argumento)
            self.tg.enviar(chat_id, AYUDA if self.servicio.usuario_de_chat(chat_id) else EXPLICACION)
        elif comando == "/desvincular":
            cuenta = self.servicio.usuario_de_chat(chat_id)
            if self.servicio.desvincular_chat(chat_id):
                self._evento("asistente_vinculo", resultado="desvinculado", cuenta=cuenta["id"] if cuenta else None)
                self.nucleo.olvidar(chat_id)
                if cuenta:
                    self.propuestas.cancelar_de_cuenta(cuenta["id"])
                self.tg.enviar(chat_id, "Listo: este chat ya no está vinculado a tu cuenta.")
            else:
                self.tg.enviar(chat_id, "Este chat no está vinculado a ninguna cuenta.")
        elif comando == "/olvidar":
            if self.servicio.usuario_de_chat(chat_id):
                self.nucleo.olvidar(chat_id)
                self.tg.enviar(chat_id, "Listo, olvidé lo que hablamos.")
            else:
                self.tg.enviar(chat_id, EXPLICACION)
        elif comando == "/ayuda":
            self.tg.enviar(chat_id, AYUDA if self.servicio.usuario_de_chat(chat_id) else EXPLICACION)
        else:
            self.tg.enviar(chat_id, "No conozco ese comando. Escribe /ayuda.")

    def _vincular(self, chat_id, username, codigo):
        if self._vinculo_excedido(chat_id):   # adivinar códigos tiene su propio tope: no gasta el de los turnos con el modelo
            self.tg.enviar(chat_id, "Has intentado vincular demasiadas veces. Espera un rato y genera un código nuevo en Mi perfil.")
            return
        resultado = self.servicio.consumir_codigo(codigo, chat_id, username)
        estado = resultado.get("estado")
        self._evento("asistente_vinculo", resultado=estado if estado in ("ok", "chat_ocupado") else "invalido")
        if estado == "ok":
            self.nucleo.olvidar(chat_id)
            self.tg.enviar(chat_id, f"Listo: este chat quedó vinculado a la cuenta «{resultado.get('cuenta')}». Pregúntame por tu saldo, tus gastos o tus metas. "
                                    "Si no reconoces esa cuenta, escribe /desvincular.")
            if resultado.get("chat_anterior") is not None:
                self._avisar(resultado["chat_anterior"], "Se vinculó otro chat de Telegram a tu cuenta; este dejó de estar vinculado.")
        elif estado == "chat_ocupado":
            self.tg.enviar(chat_id, "Este chat ya está vinculado a otra cuenta. Escribe /desvincular y vuelve a escanear el código.")
        else:
            self.tg.enviar(chat_id, "Ese código no sirve: ya se usó o venció. Genera uno nuevo en Mi perfil.")

    def _avisar(self, chat_id, texto):
        try:
            self.tg.enviar(chat_id, texto)
        except Exception:
            pass   # el chat anterior pudo borrarse o bloquear al bot

    # --- botones ---------------------------------------------------------------------------------------------------
    def _callback(self, consulta):
        mensaje = consulta.get("message") or {}
        chat = mensaje.get("chat") or {}
        chat_id = chat.get("id")
        id_consulta = consulta.get("id")
        if chat.get("type") != "private" or not isinstance(chat_id, int) or (consulta.get("from") or {}).get("id") != chat_id:
            return
        accion, _, id_ = str(consulta.get("data") or "").partition(":")
        cuenta = self.servicio.usuario_de_chat(chat_id)   # la cuenta sale del vínculo del chat, no del callback
        if not cuenta:
            self.tg.responder_callback(id_consulta, "Este chat ya no está vinculado.")
            return
        if accion not in ("c", "x") or not _ID_PROPUESTA.match(id_):
            self.tg.responder_callback(id_consulta, "Esa opción no es válida.")
            return
        _, texto = self.nucleo.confirmar(chat_id, cuenta, id_) if accion == "c" else self.nucleo.cancelar(cuenta, id_)
        self.tg.responder_callback(id_consulta, "")
        if mensaje.get("message_id") is not None:
            try:
                self.tg.editar_botones(chat_id, mensaje["message_id"], None)   # los botones se quitan: un doble toque ya no hace nada
            except Exception:
                pass
        self.tg.enviar(chat_id, _recortar(texto))

    # --- estado propio de Telegram ---------------------------------------------------------------------------------
    def _purgar(self, ahora):
        """Olvida lo caducado cuando un diccionario crece de más (un atacante con muchas cuentas de Telegram no llena la memoria)."""
        if len(self._vinculos) > MAX_ESTADO_EN_MEMORIA:
            self._vinculos = {c: v for c, v in ((c, [x for x in v if ahora - x < 3600]) for c, v in self._vinculos.items()) if v}
        if len(self._candados_chat) > MAX_ESTADO_EN_MEMORIA:
            self._candados_chat = {c: k for c, k in self._candados_chat.items() if k.locked()}

    def _vinculo_excedido(self, chat_id):
        """Anota un intento de vincular y dice si ya pasó el tope (del chat o global). Cuenta aparte de los turnos."""
        with self._candado:
            ahora = self._reloj()
            self._vinculos_global = [x for x in self._vinculos_global if ahora - x < 3600]
            recientes = [x for x in self._vinculos.get(chat_id, []) if ahora - x < 3600]
            if len(recientes) >= MAX_INTENTOS_VINCULO_POR_HORA or len(self._vinculos_global) >= MAX_INTENTOS_VINCULO_GLOBAL:
                self._vinculos[chat_id] = recientes
                return True
            recientes.append(ahora)
            self._vinculos[chat_id] = recientes
            self._vinculos_global.append(ahora)
            return False

    def _descargar(self, medio):
        """Baja el archivo de Telegram y lo identifica por sus bytes. Lanza `Rechazo` con un texto para la persona."""
        try:
            datos = self.tg.descargar(medio["file_id"], medios.MAX_BYTES_AUDIO if medio["tipo"] == "audio" else medios.MAX_BYTES)
        except Exception:
            raise medios.Rechazo("No pude descargar ese archivo. Intenta de nuevo.") from None
        real = medios.clasificar(datos)
        if real[0] != medio["tipo"]:   # lo que Telegram dice que es y lo que son los bytes deben coincidir: así los topes de cada tipo no se esquivan
            raise medios.Rechazo(medios.FORMATO_NO_RECONOCIDO)
        return {"mime": real[1], "datos": datos}



class Despachador:
    """Atiende varios chats a la vez sin desordenar uno: cada chat cae siempre en el mismo carril (un hilo por carril),
    así que sus mensajes se atienden en el orden en que llegaron, y chats de carriles distintos van en paralelo.
    Las colas tienen tope: un chat que inunda (o un carril lleno) descarta lo que sobra en lugar de retrasar a los demás."""

    def __init__(self, bot, carriles=TRABAJADORES, al_fallar=None, al_descartar=None,
                 max_por_chat=MAX_PENDIENTES_POR_CHAT, max_por_carril=MAX_PENDIENTES_POR_CARRIL):
        self._bot = bot
        self._carriles = [ThreadPoolExecutor(max_workers=1) for _ in range(max(1, carriles))]
        self._al_fallar = al_fallar
        self._al_descartar = al_descartar
        self._max_por_chat = max_por_chat
        self._max_por_carril = max_por_carril
        self._candado = threading.Lock()
        self._en_cola_por_chat = {}
        self._en_cola_por_carril = [0] * len(self._carriles)

    def enviar(self, actualizacion):
        """Encola la actualización. Devuelve el `Future`, o None si se descartó por exceso de cola."""
        chat_id = Bot._chat_de(actualizacion)
        clave = chat_id or 0
        carril = clave % len(self._carriles)
        with self._candado:
            if self._en_cola_por_chat.get(clave, 0) >= self._max_por_chat or self._en_cola_por_carril[carril] >= self._max_por_carril:
                descartar = True
            else:
                descartar = False
                self._en_cola_por_chat[clave] = self._en_cola_por_chat.get(clave, 0) + 1
                self._en_cola_por_carril[carril] += 1
        if descartar:
            if self._al_descartar:
                self._al_descartar()
            return None
        return self._carriles[carril].submit(self._atender, actualizacion, clave, carril)

    def _atender(self, actualizacion, clave, carril):
        try:
            self._bot.procesar(actualizacion)
        except Exception as error:   # una actualización mala no tumba al bot; nunca se registra su texto
            if self._al_fallar:
                self._al_fallar(type(error).__name__)
        finally:
            with self._candado:
                self._en_cola_por_carril[carril] -= 1
                restantes = self._en_cola_por_chat.get(clave, 1) - 1
                if restantes > 0:
                    self._en_cola_por_chat[clave] = restantes
                else:
                    self._en_cola_por_chat.pop(clave, None)

    def cerrar(self):
        for carril in self._carriles:
            carril.shutdown(wait=True)


# --- Telegram real -------------------------------------------------------------------------------------------------

class TelegramHTTP:
    """API de bots de Telegram por HTTPS. El token va en la URL, así que ninguna excepción se imprime completa."""

    def __init__(self, token, cliente=None):
        import httpx
        self._base = f"https://api.telegram.org/bot{token}/"
        self._cliente = cliente or httpx.Client(timeout=40)

    def _llamar(self, metodo, **datos):
        respuesta = self._cliente.post(self._base + metodo, json=datos)
        cuerpo = respuesta.json()
        if not cuerpo.get("ok"):
            raise RuntimeError(f"Telegram rechazó {metodo}: {cuerpo.get('error_code')}")
        return cuerpo.get("result")

    def obtener_actualizaciones(self, offset, segundos=25):
        return self._llamar("getUpdates", offset=offset, timeout=segundos, allowed_updates=["message", "callback_query"])

    def enviar(self, chat_id, texto, botones=None):
        datos = {"chat_id": chat_id, "text": texto, "link_preview_options": {"is_disabled": True}}
        if botones:
            datos["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in fila] for fila in botones]}
        return self._llamar("sendMessage", **datos)

    def escribiendo(self, chat_id):
        return self._llamar("sendChatAction", chat_id=chat_id, action="typing")

    def descargar(self, file_id, max_bytes):
        """Bytes de un archivo de Telegram, con tope de tamaño. Ningún error lleva la URL (trae el token)."""
        info = self._llamar("getFile", file_id=file_id)
        ruta = (info or {}).get("file_path")
        if not isinstance(ruta, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", ruta) or ".." in ruta:
            raise RuntimeError("Telegram no dio una ruta de archivo válida")
        try:
            with self._cliente.stream("GET", self._base.replace("/bot", "/file/bot", 1) + ruta) as respuesta:
                if respuesta.status_code != 200:
                    raise RuntimeError(f"Telegram no entregó el archivo: {respuesta.status_code}")
                declarado = respuesta.headers.get("content-length", "") if hasattr(respuesta, "headers") else ""
                if declarado.isdigit() and int(declarado) > max_bytes:
                    raise RuntimeError("archivo demasiado grande")
                limite, partes, total = time.monotonic() + PLAZO_DESCARGA_SEG, [], 0   # el plazo es del total: una descarga a gotas no ocupa un carril
                for parte in respuesta.iter_bytes():
                    total += len(parte)
                    if total > max_bytes:
                        raise RuntimeError("archivo demasiado grande")
                    if time.monotonic() > limite:
                        raise RuntimeError("descarga demasiado lenta")
                    partes.append(parte)
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("falló la descarga del archivo") from None
        return b"".join(partes)

    def editar_botones(self, chat_id, message_id, botones=None):
        markup = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in fila] for fila in botones]} if botones else {"inline_keyboard": []}
        return self._llamar("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=markup)

    def responder_callback(self, id_consulta, texto=""):
        return self._llamar("answerCallbackQuery", callback_query_id=id_consulta, text=texto)


def _evento(nombre, **campos):
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "evento": nombre, **campos}), flush=True)


def main():
    import httpx
    from asistente.api import ejecutar_propuesta, obtener_con_proveedor
    from asistente.herramientas import Herramientas
    from asistente.modelo_gemini import ModeloGemini
    from asistente.motor import Motor
    from asistente.propuestas import Propuestas
    from asistente.servicio import ServicioHTTP
    from finanzas_core import ahora_local

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    secreto = os.environ.get("ASISTENTE_SECRETO_INTERNO", "").strip()
    if not token or len(secreto) < 32:
        sys.exit("Faltan TELEGRAM_BOT_TOKEN o ASISTENTE_SECRETO_INTERNO (de 32 caracteres o más)")
    app = httpx.Client(base_url=os.environ.get("APP_URL", "http://finanzas-community:8001"), timeout=30)
    servicio = ServicioHTTP(app, secreto)
    modelo = ModeloGemini()
    propuestas = Propuestas()
    hoy = lambda: ahora_local().date()  # noqa: E731

    def crear_motor(cuenta, chat_id):
        obtener = obtener_con_proveedor(app, lambda: servicio.token(chat_id, cuenta_id=cuenta["id"]))
        return Motor(modelo, Herramientas(obtener, propuestas, cuenta["id"], hoy), hoy)

    def ejecutar(propuesta, chat_id):
        return ejecutar_propuesta(app, propuesta, lambda: servicio.token(chat_id, escritura=True, cuenta_id=propuesta.user_id))

    telegram = TelegramHTTP(token)
    bot = Bot(telegram, servicio, crear_motor, propuestas, ejecutar, registrar=_evento)   # los topes diarios los lleva la app (asistente/cupo.py)
    despachador = Despachador(bot, TRABAJADORES, al_fallar=lambda tipo: _evento("bot_error", tipo=tipo),
                              al_descartar=lambda: _evento("bot_cola_llena"))
    offset, espera = 0, 1
    _evento("bot_iniciado", trabajadores=TRABAJADORES)
    try:
        while True:
            try:
                for actualizacion in telegram.obtener_actualizaciones(offset):
                    offset = actualizacion["update_id"] + 1
                    despachador.enviar(actualizacion)
                espera = 1
            except KeyboardInterrupt:
                break
            except Exception as error:
                _evento("bot_sin_telegram", tipo=type(error).__name__)
                time.sleep(espera)
                espera = min(espera * 2, 60)
    finally:
        despachador.cerrar()


if __name__ == "__main__":
    main()
