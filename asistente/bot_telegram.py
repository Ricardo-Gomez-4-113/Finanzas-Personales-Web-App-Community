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

import json
import os
import re
import sys
import time

TTL_CONVERSACION = 1800          # segundos sin hablar para olvidar la conversación
MAX_MENSAJES_POR_HORA = 40       # por chat
MAX_MENSAJES_GLOBAL_POR_HORA = 300   # entre todos los chats: tope de costo del modelo
MAX_TEXTO_TELEGRAM = 3500        # el límite de Telegram es 4096
_ID_PROPUESTA = re.compile(r"^[A-Za-z0-9_-]{6,24}$")

EXPLICACION = ("Soy el asistente de tus finanzas. Para usarme, vincula este chat con tu cuenta: abre la app, entra a Mi perfil, "
               "elige «Asistente en Telegram» y escanea el código QR con el teléfono.")
AYUDA = ("Pregúntame por tu saldo, tus gastos, tus metas, tus fijos o tus inversiones. También puedo preparar un gasto, un ingreso, "
         "un depósito en una meta o saltar un fijo: siempre te pido confirmar con un botón.\n\n"
         "/olvidar borra lo que hablamos. /desvincular quita este chat de tu cuenta.")


def _recortar(texto):
    texto = texto or ""
    return texto if len(texto) <= MAX_TEXTO_TELEGRAM else texto[:MAX_TEXTO_TELEGRAM].rstrip() + "…"


class Bot:
    def __init__(self, telegram, servicio, crear_motor, propuestas, ejecutar, reloj=time.monotonic):
        """`servicio` responde de qué cuenta es un chat y vincula/desvincula (`asistente/servicio.py`);
        `crear_motor(cuenta, chat_id) -> Motor`; `propuestas` es el `Propuestas` que comparten las herramientas;
        `ejecutar(propuesta, chat_id) -> (ok, texto)` ejecuta una propuesta ya confirmada."""
        self.tg = telegram
        self.servicio = servicio
        self.crear_motor = crear_motor
        self.propuestas = propuestas
        self.ejecutar = ejecutar
        self._reloj = reloj
        self._conversaciones = {}   # chat_id -> {"historial": [...], "ultimo": t}
        self._mensajes = {}         # chat_id -> [t, ...] de la última hora
        self._global = []           # [t, ...] de la última hora, todos los chats

    # --- entrada ---------------------------------------------------------------------------------------------------
    def procesar(self, actualizacion):
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
        self._mensaje(chat["id"], origen.get("username"), mensaje.get("text"))

    # --- mensajes --------------------------------------------------------------------------------------------------
    def _mensaje(self, chat_id, username, texto):
        if not isinstance(texto, str):
            cuenta = self.servicio.usuario_de_chat(chat_id)
            self.tg.enviar(chat_id, "Por ahora solo entiendo texto." if cuenta else EXPLICACION)
            return
        if texto.startswith("/"):
            partes = texto.split(maxsplit=1)
            return self._comando(chat_id, username, partes[0].split("@")[0].lower(), partes[1].strip() if len(partes) > 1 else "")
        cuenta = self.servicio.usuario_de_chat(chat_id)
        if not cuenta:
            self.tg.enviar(chat_id, EXPLICACION)
            return
        if self._excede_el_ritmo(chat_id):
            self.tg.enviar(chat_id, "Has escrito muchos mensajes seguidos. Intenta de nuevo en un rato.")
            return
        historial = self._historial(chat_id, cuenta["id"])
        turno = self.crear_motor(cuenta, chat_id).responder(historial, texto)
        self.tg.enviar(chat_id, _recortar(turno.texto))
        for id_ in turno.propuestas:
            propuesta = self.propuestas.obtener(id_, cuenta["id"])
            if propuesta:
                self.tg.enviar(chat_id, "Confirma esta operación:\n" + propuesta.resumen,
                               botones=[[("Confirmar", f"c:{id_}"), ("Cancelar", f"x:{id_}")]])

    def _comando(self, chat_id, username, comando, argumento):
        if comando == "/start":
            if argumento:
                return self._vincular(chat_id, username, argumento)
            self.tg.enviar(chat_id, AYUDA if self.servicio.usuario_de_chat(chat_id) else EXPLICACION)
        elif comando == "/desvincular":
            cuenta = self.servicio.usuario_de_chat(chat_id)
            if self.servicio.desvincular_chat(chat_id):
                self._conversaciones.pop(chat_id, None)
                if cuenta:
                    self.propuestas.cancelar_de_cuenta(cuenta["id"])
                self.tg.enviar(chat_id, "Listo: este chat ya no está vinculado a tu cuenta.")
            else:
                self.tg.enviar(chat_id, "Este chat no está vinculado a ninguna cuenta.")
        elif comando == "/olvidar":
            if self.servicio.usuario_de_chat(chat_id):
                self._conversaciones.pop(chat_id, None)
                self.tg.enviar(chat_id, "Listo, olvidé lo que hablamos.")
            else:
                self.tg.enviar(chat_id, EXPLICACION)
        elif comando == "/ayuda":
            self.tg.enviar(chat_id, AYUDA if self.servicio.usuario_de_chat(chat_id) else EXPLICACION)
        else:
            self.tg.enviar(chat_id, "No conozco ese comando. Escribe /ayuda.")

    def _vincular(self, chat_id, username, codigo):
        if self._excede_el_ritmo(chat_id):   # adivinar códigos también tiene tope
            self.tg.enviar(chat_id, "Has escrito muchos mensajes seguidos. Intenta de nuevo en un rato.")
            return
        resultado = self.servicio.consumir_codigo(codigo, chat_id, username)
        estado = resultado.get("estado")
        if estado == "ok":
            self._conversaciones.pop(chat_id, None)
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
        if accion == "c":
            _, texto = self.propuestas.confirmar(id_, cuenta["id"], lambda p: self.ejecutar(p, chat_id))
        else:
            texto = "Cancelada." if self.propuestas.cancelar(id_, cuenta["id"]) else "Esa propuesta ya no está disponible."
        self.tg.responder_callback(id_consulta, "")
        if mensaje.get("message_id") is not None:
            try:
                self.tg.editar_botones(chat_id, mensaje["message_id"], None)   # los botones se quitan: un doble toque ya no hace nada
            except Exception:
                pass
        self.tg.enviar(chat_id, _recortar(texto))

    # --- estado por chat -------------------------------------------------------------------------------------------
    def _historial(self, chat_id, user_id):
        ahora = self._reloj()
        conv = self._conversaciones.get(chat_id)
        if not conv or ahora - conv["ultimo"] > TTL_CONVERSACION or conv["user_id"] != user_id:
            conv = self._conversaciones[chat_id] = {"historial": [], "ultimo": ahora, "user_id": user_id}
        conv["ultimo"] = ahora
        return conv["historial"]

    def _excede_el_ritmo(self, chat_id):
        ahora = self._reloj()
        self._global = [x for x in self._global if ahora - x < 3600]
        recientes = [x for x in self._mensajes.get(chat_id, []) if ahora - x < 3600]
        if len(recientes) >= MAX_MENSAJES_POR_HORA or len(self._global) >= MAX_MENSAJES_GLOBAL_POR_HORA:
            self._mensajes[chat_id] = recientes
            return True
        recientes.append(ahora)
        self._global.append(ahora)
        self._mensajes[chat_id] = recientes
        return False


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
    bot = Bot(telegram, servicio, crear_motor, propuestas, ejecutar)
    offset, espera = 0, 1
    _evento("bot_iniciado")
    while True:
        try:
            for actualizacion in telegram.obtener_actualizaciones(offset):
                offset = actualizacion["update_id"] + 1
                try:
                    bot.procesar(actualizacion)
                except Exception as error:   # una actualización mala no tumba al bot; nunca se registra su texto
                    _evento("bot_error", tipo=type(error).__name__)
            espera = 1
        except KeyboardInterrupt:
            break
        except Exception as error:
            _evento("bot_sin_telegram", tipo=type(error).__name__)
            time.sleep(espera)
            espera = min(espera * 2, 60)


if __name__ == "__main__":
    main()
