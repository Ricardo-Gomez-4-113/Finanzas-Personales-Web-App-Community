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
import threading
import time
from concurrent.futures import ThreadPoolExecutor

TTL_CONVERSACION = 1800          # segundos sin hablar para olvidar la conversación
MAX_MENSAJES_POR_HORA = 40       # por chat
MAX_MENSAJES_GLOBAL_POR_HORA = 300   # entre todos los chats: tope de costo del modelo
MAX_MENSAJES_DIA = 100           # por cuenta y por día (configurable: ASISTENTE_MAX_MENSAJES_DIA)
MAX_TOKENS_DIA = 200_000         # por cuenta y por día (configurable: ASISTENTE_MAX_TOKENS_DIA); 0 = sin tope
TRABAJADORES = 8                 # carriles: chats que se atienden a la vez
MAX_INTENTOS_VINCULO_POR_HORA = 10   # por chat: adivinar códigos de vinculación
MAX_INTENTOS_VINCULO_GLOBAL = 200    # entre todos los chats; cuenta aparte, para que la basura no gaste el cupo de los turnos reales
MAX_PENDIENTES_POR_CHAT = 3      # actualizaciones en cola de un mismo chat (las demás se descartan)
MAX_PENDIENTES_POR_CARRIL = 50   # cola máxima de un carril
MAX_ESTADO_EN_MEMORIA = 2000     # entradas por diccionario antes de purgar las caducadas
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
    def __init__(self, telegram, servicio, crear_motor, propuestas, ejecutar, reloj=time.monotonic, registrar=None,
                 max_mensajes_dia=MAX_MENSAJES_DIA, max_tokens_dia=MAX_TOKENS_DIA, dia=None):
        """`servicio` responde de qué cuenta es un chat y vincula/desvincula (`asistente/servicio.py`);
        `crear_motor(cuenta, chat_id) -> Motor`; `propuestas` es el `Propuestas` que comparten las herramientas;
        `ejecutar(propuesta, chat_id) -> (ok, texto)` ejecuta una propuesta ya confirmada.
        `registrar(evento, **campos)` recibe los eventos del bot: solo tipos, conteos y el id numérico de la cuenta; nunca texto,
        importes ni ids de chat. `dia()` da la fecha que reinicia los topes diarios."""
        self.tg = telegram
        self.servicio = servicio
        self.crear_motor = crear_motor
        self.propuestas = propuestas
        self.ejecutar = ejecutar
        self._reloj = reloj
        self._registrar = registrar
        self._max_mensajes_dia = max_mensajes_dia
        self._max_tokens_dia = max_tokens_dia
        self._dia = dia or (lambda: time.strftime("%Y-%m-%d"))
        self._candado = threading.RLock()   # protege los diccionarios compartidos entre chats que se atienden a la vez
        self._candados_chat = {}            # chat_id -> Lock: los mensajes de un mismo chat se atienden en orden
        self._conversaciones = {}   # chat_id -> {"historial": [...], "ultimo": t, "user_id": id}
        self._mensajes = {}         # chat_id -> [t, ...] de la última hora
        self._global = []           # [t, ...] de la última hora, todos los chats
        self._uso_dia = {}          # user_id -> {"dia": fecha, "mensajes": n, "tokens": n}
        self._vinculos = {}         # chat_id -> [t, ...] de la última hora: intentos de vincular
        self._vinculos_global = []  # [t, ...] de la última hora, todos los chats

    def _evento(self, nombre, **campos):
        if self._registrar:
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
        # Primero se comprueba todo sin gastar cupo; solo un mensaje que llega al modelo cuenta contra los topes
        if self._ritmo_excedido(chat_id):
            self._evento("asistente_limite", tipo="ritmo", cuenta=cuenta["id"])
            self.tg.enviar(chat_id, "Has escrito muchos mensajes seguidos. Intenta de nuevo en un rato.")
            return
        if not self._anotar_dia(cuenta["id"]):
            self._evento("asistente_limite", tipo="dia", cuenta=cuenta["id"])
            self.tg.enviar(chat_id, "Llegaste al límite de mensajes de hoy con el asistente. Mañana puedes seguir; mientras tanto, la app sigue disponible.")
            return
        self._anotar_ritmo(chat_id)
        historial = self._historial(chat_id, cuenta["id"])
        inicio = time.perf_counter()
        turno = None
        try:
            turno = self.crear_motor(cuenta, chat_id).responder(historial, texto)
        finally:
            if turno is not None:
                self._sumar_tokens(cuenta["id"], turno.tokens)
        self._evento("asistente_turno", cuenta=cuenta["id"], duracion_ms=round((time.perf_counter() - inicio) * 1000),
                     herramientas=turno.herramientas_usadas, propuestas=len(turno.propuestas), tokens=turno.tokens, error=turno.error)
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
                self._evento("asistente_vinculo", resultado="desvinculado", cuenta=cuenta["id"] if cuenta else None)
                with self._candado:
                    self._conversaciones.pop(chat_id, None)
                if cuenta:
                    self.propuestas.cancelar_de_cuenta(cuenta["id"])
                self.tg.enviar(chat_id, "Listo: este chat ya no está vinculado a tu cuenta.")
            else:
                self.tg.enviar(chat_id, "Este chat no está vinculado a ninguna cuenta.")
        elif comando == "/olvidar":
            if self.servicio.usuario_de_chat(chat_id):
                with self._candado:
                    self._conversaciones.pop(chat_id, None)
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
            with self._candado:
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
            ok, texto = self.propuestas.confirmar(id_, cuenta["id"], lambda p: self.ejecutar(p, chat_id))
            resultado = "confirmada" if ok else ("incierta" if texto.startswith("No pude confirmar") else "rechazada" if texto.startswith("No se pudo") else "no_disponible")
        else:
            cancelada = self.propuestas.cancelar(id_, cuenta["id"])
            texto = "Cancelada." if cancelada else "Esa propuesta ya no está disponible."
            resultado = "cancelada" if cancelada else "no_disponible"
        self._evento("asistente_escritura", resultado=resultado, cuenta=cuenta["id"])
        self.tg.responder_callback(id_consulta, "")
        if mensaje.get("message_id") is not None:
            try:
                self.tg.editar_botones(chat_id, mensaje["message_id"], None)   # los botones se quitan: un doble toque ya no hace nada
            except Exception:
                pass
        self.tg.enviar(chat_id, _recortar(texto))

    # --- estado por chat -------------------------------------------------------------------------------------------
    def _historial(self, chat_id, user_id):
        with self._candado:
            ahora = self._reloj()
            conv = self._conversaciones.get(chat_id)
            if not conv or ahora - conv["ultimo"] > TTL_CONVERSACION or conv["user_id"] != user_id:
                conv = self._conversaciones[chat_id] = {"historial": [], "ultimo": ahora, "user_id": user_id}
            conv["ultimo"] = ahora
            return conv["historial"]

    def _purgar(self, ahora):
        """Olvida lo caducado cuando un diccionario crece de más (un atacante con muchas cuentas de Telegram no llena la memoria)."""
        if len(self._mensajes) > MAX_ESTADO_EN_MEMORIA:
            self._mensajes = {c: v for c, v in ((c, [x for x in v if ahora - x < 3600]) for c, v in self._mensajes.items()) if v}
        if len(self._vinculos) > MAX_ESTADO_EN_MEMORIA:
            self._vinculos = {c: v for c, v in ((c, [x for x in v if ahora - x < 3600]) for c, v in self._vinculos.items()) if v}
        if len(self._conversaciones) > MAX_ESTADO_EN_MEMORIA:
            self._conversaciones = {c: v for c, v in self._conversaciones.items() if ahora - v["ultimo"] <= TTL_CONVERSACION}
        if len(self._uso_dia) > MAX_ESTADO_EN_MEMORIA:
            dia = self._dia()
            self._uso_dia = {u: v for u, v in self._uso_dia.items() if v["dia"] == dia}
        if len(self._candados_chat) > MAX_ESTADO_EN_MEMORIA:
            self._candados_chat = {c: k for c, k in self._candados_chat.items() if k.locked()}

    def _ritmo_excedido(self, chat_id):
        """¿Ya no caben más turnos de este chat (o de todos) en la última hora? No anota nada."""
        with self._candado:
            ahora = self._reloj()
            self._purgar(ahora)
            self._global = [x for x in self._global if ahora - x < 3600]
            recientes = [x for x in self._mensajes.get(chat_id, []) if ahora - x < 3600]
            self._mensajes[chat_id] = recientes
            return len(recientes) >= MAX_MENSAJES_POR_HORA or len(self._global) >= MAX_MENSAJES_GLOBAL_POR_HORA

    def _anotar_ritmo(self, chat_id):
        with self._candado:
            ahora = self._reloj()
            self._mensajes.setdefault(chat_id, []).append(ahora)
            self._global.append(ahora)

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

    def _uso_de(self, user_id):
        dia = self._dia()
        uso = self._uso_dia.get(user_id)
        if not uso or uso["dia"] != dia:
            uso = self._uso_dia[user_id] = {"dia": dia, "mensajes": 0, "tokens": 0}
        return uso

    def _anotar_dia(self, user_id):
        """Cuenta el mensaje contra el tope diario de la cuenta (mensajes y tokens). False si ya no cabe (y entonces no cuenta)."""
        with self._candado:
            uso = self._uso_de(user_id)
            if (self._max_mensajes_dia and uso["mensajes"] >= self._max_mensajes_dia) or (self._max_tokens_dia and uso["tokens"] >= self._max_tokens_dia):
                return False
            uso["mensajes"] += 1
            return True

    def _sumar_tokens(self, user_id, tokens):
        with self._candado:
            self._uso_de(user_id)["tokens"] += max(0, int(tokens or 0))


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
    sin_topes = os.environ.get("ASISTENTE_SIN_TOPES", "").strip() == "1"   # único modo de apagar los topes diarios: explícito
    max_mensajes = 0 if sin_topes else _entero_de_entorno("ASISTENTE_MAX_MENSAJES_DIA", MAX_MENSAJES_DIA)
    max_tokens = 0 if sin_topes else _entero_de_entorno("ASISTENTE_MAX_TOKENS_DIA", MAX_TOKENS_DIA)
    bot = Bot(telegram, servicio, crear_motor, propuestas, ejecutar, registrar=_evento, max_mensajes_dia=max_mensajes,
              max_tokens_dia=max_tokens, dia=lambda: ahora_local().date().isoformat())
    despachador = Despachador(bot, TRABAJADORES, al_fallar=lambda tipo: _evento("bot_error", tipo=tipo),
                              al_descartar=lambda: _evento("bot_cola_llena"))
    offset, espera = 0, 1
    _evento("bot_iniciado", trabajadores=TRABAJADORES, max_mensajes_dia=max_mensajes, max_tokens_dia=max_tokens, sin_topes=sin_topes)
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


def _entero_de_entorno(nombre, defecto):
    """Entero positivo de una variable de entorno. Un valor vacío, mal escrito, 0 o negativo no apaga el tope: se usa el valor por defecto."""
    try:
        valor = int(os.environ.get(nombre, "").strip() or defecto)
    except ValueError:
        return defecto
    return valor if valor > 0 else defecto


if __name__ == "__main__":
    main()
