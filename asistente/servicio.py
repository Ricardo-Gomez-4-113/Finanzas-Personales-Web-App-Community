"""asistente/servicio.py - Lo que el bot necesita de la app: de qué cuenta es un chat, vincular, desvincular y un token.

`ServicioLocal` habla con la base en el mismo proceso (pruebas y CLI). `ServicioHTTP` es el de producción: el bot, en su propio
contenedor y sin la base ni la clave JWT, se lo pide a la app por `routers/asistente_interno.py` con un secreto compartido.
"""

HEADER_SECRETO = "X-Asistente-Secreto"


class SinVinculo(Exception):
    """El chat no está vinculado a una cuenta activa."""


class ServicioLocal:
    def __init__(self, cupo=None):
        self._cupo = cupo

    def _del_chat(self, chat_id, cuenta_id):
        cuenta = self.usuario_de_chat(chat_id)
        if not cuenta or cuenta["id"] != cuenta_id:
            raise SinVinculo()
        return cuenta

    def reservar_cupo(self, chat_id, cuenta_id, con_medio=False):
        """None si el mensaje cabe en el tope diario de la cuenta; si no, `"dia"` o `"medios"`. El chat debe ser de esa cuenta."""
        from asistente import cupo
        self._del_chat(chat_id, cuenta_id)
        return (self._cupo or cupo.por_defecto).reservar(cuenta_id, con_medio)

    def sumar_tokens(self, chat_id, cuenta_id, tokens):
        from asistente import cupo
        self._del_chat(chat_id, cuenta_id)
        (self._cupo or cupo.por_defecto).sumar_tokens(cuenta_id, tokens)

    def usuario_de_chat(self, chat_id):
        from asistente import vinculos
        return vinculos.usuario_de_chat(chat_id)

    def consumir_codigo(self, codigo, chat_id, username=None):
        from asistente import vinculos
        estado, _, anterior, cuenta = vinculos.consumir_codigo(codigo, chat_id, username)
        return {"estado": estado, "chat_anterior": anterior, "cuenta": cuenta}

    def desvincular_chat(self, chat_id):
        from asistente import vinculos
        return vinculos.desvincular_chat(chat_id) is not None

    def token(self, chat_id, escritura=False, cuenta_id=None):
        from asistente.tokens import emitir_token
        cuenta = self.usuario_de_chat(chat_id)
        if not cuenta or (cuenta_id is not None and cuenta_id != cuenta["id"]):
            raise SinVinculo()
        return emitir_token(cuenta["id"], escritura=escritura)


class ServicioHTTP:
    """`cliente` es un cliente estilo httpx con `base_url` de la app (en las pruebas, un TestClient)."""

    def __init__(self, cliente, secreto):
        self._cliente = cliente
        self._cabeceras = {HEADER_SECRETO: secreto}

    def _post(self, ruta, cuerpo):
        return self._cliente.post("/internal/asistente" + ruta, json=cuerpo, headers=self._cabeceras)

    def usuario_de_chat(self, chat_id):
        r = self._post("/chat", {"chat_id": chat_id})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def consumir_codigo(self, codigo, chat_id, username=None):
        r = self._post("/vincular", {"codigo": codigo, "chat_id": chat_id, "username": username})
        r.raise_for_status()
        return r.json()

    def reservar_cupo(self, chat_id, cuenta_id, con_medio=False):
        r = self._post("/cupo", {"chat_id": chat_id, "cuenta_id": cuenta_id, "con_medio": bool(con_medio)})
        if r.status_code == 404:
            raise SinVinculo()
        r.raise_for_status()
        return r.json().get("limite")

    def sumar_tokens(self, chat_id, cuenta_id, tokens):
        r = self._post("/uso", {"chat_id": chat_id, "cuenta_id": cuenta_id, "tokens": max(0, int(tokens or 0))})
        if r.status_code == 404:
            raise SinVinculo()
        r.raise_for_status()

    def desvincular_chat(self, chat_id):
        r = self._post("/desvincular", {"chat_id": chat_id})
        r.raise_for_status()
        return bool(r.json().get("desvinculado"))

    def token(self, chat_id, escritura=False, cuenta_id=None):
        r = self._post("/token", {"chat_id": chat_id, "escritura": escritura, "cuenta_id": cuenta_id})
        if r.status_code == 404:
            raise SinVinculo()
        r.raise_for_status()
        return r.json()["token"]
