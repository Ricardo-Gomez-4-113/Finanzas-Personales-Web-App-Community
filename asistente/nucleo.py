"""asistente/nucleo.py - El asistente sin canal: conversación, ritmo, tope diario, propuestas y eventos.

Telegram (`bot_telegram.py`) y la web usan lo mismo. Cada canal solo se ocupa de lo suyo: recibir el mensaje, saber de qué cuenta es,
bajar archivos y mostrar la respuesta. Aquí no hay nada de Telegram ni de HTTP.

`clave` identifica una conversación de un canal (el id del chat en Telegram). La cuenta (`cuenta["id"]`) la pone el canal a partir de
algo que ya validó: el vínculo del chat o la sesión de la app; nunca sale del texto ni del modelo.
"""

import contextlib
import threading
import time
from dataclasses import dataclass, field

TTL_CONVERSACION = 1800          # segundos sin hablar para olvidar la conversación
MAX_MENSAJES_POR_HORA = 40       # por conversación
MAX_MENSAJES_GLOBAL_POR_HORA = 300   # entre todas: tope de costo del modelo
MAX_ESTADO_EN_MEMORIA = 2000     # entradas por diccionario antes de purgar las caducadas

RITMO, DIA, MEDIOS, OK = "ritmo", "dia", "medios", "ok"


@dataclass
class Resultado:
    estado: str                      # «ok», o por qué no se atendió: «ritmo», «dia», «medios»
    texto: str = ""
    propuestas: list = field(default_factory=list)   # [(id, resumen)] por confirmar, ya con el resumen armado por el código
    tokens: int = 0
    error: str = ""


class Nucleo:
    def __init__(self, crear_motor, propuestas, ejecutar, cupo, reloj=time.monotonic, registrar=None, canal="telegram"):
        """`crear_motor(cuenta, clave) -> Motor`; `propuestas` es el `Propuestas` que comparten las herramientas; `ejecutar(propuesta, clave)
        -> (ok, texto)` ejecuta una propuesta ya confirmada; `cupo` tiene `reservar(cuenta, clave, con_medio)` (None o «dia»/«medios») y
        `sumar_tokens(cuenta, clave, tokens)`. `registrar(evento, **campos)` recibe solo tipos, conteos y el id numérico de la cuenta."""
        self.crear_motor = crear_motor
        self.propuestas = propuestas
        self.ejecutar = ejecutar
        self.cupo = cupo
        self._reloj = reloj
        self._registrar = registrar
        self.canal = canal
        self._candado = threading.RLock()
        self._conversaciones = {}   # clave -> {"historial": [...], "ultimo": t, "user_id": id}
        self._mensajes = {}         # clave -> [t, ...] de la última hora
        self._global = []           # [t, ...] de la última hora, todas las conversaciones

    def evento(self, nombre, **campos):
        if self._registrar:
            try:
                self._registrar(nombre, **campos)
            except Exception:
                pass   # el registro nunca tumba la conversación

    # --- un turno --------------------------------------------------------------------------------------------------
    def turno(self, clave, cuenta, texto, adjuntos=None, medio="", envolver=None):
        """Atiende un mensaje de la cuenta. Primero se comprueba todo sin gastar cupo; solo un mensaje que llega al modelo cuenta.
        `adjuntos` es la lista de fotos/audios o una función que la da (se llama ya con el cupo reservado y dentro de `envolver`, un
        administrador de contexto, por ejemplo el aviso «escribiendo…»; si lanza algo, sale tal cual y no queda nada en el historial)."""
        if self.ritmo_excedido(clave):
            self.evento("asistente_limite", tipo="ritmo", cuenta=cuenta["id"], canal=self.canal)
            return Resultado(RITMO)
        limite = self.cupo.reservar(cuenta, clave, bool(medio))
        if limite:
            self.evento("asistente_limite", tipo=limite, cuenta=cuenta["id"], canal=self.canal)
            return Resultado(limite)
        self.anotar_ritmo(clave)
        historial = self.historial(clave, cuenta["id"])
        inicio = time.perf_counter()
        turno = None
        try:
            with (envolver() if envolver else contextlib.nullcontext()):
                archivos = adjuntos() if callable(adjuntos) else (adjuntos or [])
                turno = self.crear_motor(cuenta, clave).responder(historial, texto, archivos)
        finally:
            if turno is not None:
                self.cupo.sumar_tokens(cuenta, clave, turno.tokens)
        self.evento("asistente_turno", cuenta=cuenta["id"], duracion_ms=round((time.perf_counter() - inicio) * 1000),
                    herramientas=turno.herramientas_usadas, propuestas=len(turno.propuestas), tokens=turno.tokens, error=turno.error,
                    medio=medio or "", canal=self.canal)
        pendientes = []
        for id_ in turno.propuestas:
            propuesta = self.propuestas.obtener(id_, cuenta["id"])
            if propuesta:
                pendientes.append((id_, propuesta.resumen))
        return Resultado(OK, turno.texto, pendientes, turno.tokens, turno.error)

    # --- propuestas ------------------------------------------------------------------------------------------------
    def confirmar(self, clave, cuenta, id_):
        """Ejecuta una propuesta de ESTA cuenta, una sola vez. Devuelve `(resultado, texto)`."""
        ok, texto = self.propuestas.confirmar(id_, cuenta["id"], lambda p: self.ejecutar(p, clave))
        resultado = "confirmada" if ok else ("incierta" if texto.startswith("No pude confirmar") else "rechazada" if texto.startswith("No se pudo") else "no_disponible")
        self.evento("asistente_escritura", resultado=resultado, cuenta=cuenta["id"], canal=self.canal)
        return resultado, texto

    def cancelar(self, cuenta, id_):
        cancelada = self.propuestas.cancelar(id_, cuenta["id"])
        resultado = "cancelada" if cancelada else "no_disponible"
        self.evento("asistente_escritura", resultado=resultado, cuenta=cuenta["id"], canal=self.canal)
        return resultado, ("Cancelada." if cancelada else "Esa propuesta ya no está disponible.")

    # --- estado por conversación -----------------------------------------------------------------------------------
    def historial(self, clave, user_id):
        with self._candado:
            ahora = self._reloj()
            conv = self._conversaciones.get(clave)
            if not conv or ahora - conv["ultimo"] > TTL_CONVERSACION or conv["user_id"] != user_id:
                conv = self._conversaciones[clave] = {"historial": [], "ultimo": ahora, "user_id": user_id}
            conv["ultimo"] = ahora
            return conv["historial"]

    def olvidar(self, clave):
        with self._candado:
            self._conversaciones.pop(clave, None)

    def purgar(self, ahora):
        """Olvida lo caducado cuando un diccionario crece de más (muchas conversaciones no llenan la memoria)."""
        if len(self._mensajes) > MAX_ESTADO_EN_MEMORIA:
            self._mensajes = {c: v for c, v in ((c, [x for x in v if ahora - x < 3600]) for c, v in self._mensajes.items()) if v}
        if len(self._conversaciones) > MAX_ESTADO_EN_MEMORIA:
            self._conversaciones = {c: v for c, v in self._conversaciones.items() if ahora - v["ultimo"] <= TTL_CONVERSACION}

    def ritmo_excedido(self, clave):
        """¿Ya no caben más turnos de esta conversación (o de todas) en la última hora? No anota nada."""
        with self._candado:
            ahora = self._reloj()
            self.purgar(ahora)
            self._global = [x for x in self._global if ahora - x < 3600]
            recientes = [x for x in self._mensajes.get(clave, []) if ahora - x < 3600]
            self._mensajes[clave] = recientes
            return len(recientes) >= MAX_MENSAJES_POR_HORA or len(self._global) >= MAX_MENSAJES_GLOBAL_POR_HORA

    def anotar_ritmo(self, clave):
        with self._candado:
            ahora = self._reloj()
            self._mensajes.setdefault(clave, []).append(ahora)
            self._global.append(ahora)
