"""asistente/cupo.py - Tope diario del asistente por cuenta: mensajes, tokens y fotos/audios.

Vive en la app (base maestra, tabla `asistente_uso`) y no en el proceso del bot, para que todos los canales y servicios
(Telegram, web) compartan UN solo cupo por cuenta y no se pierda al reiniciar. Los topes salen del entorno de la app:
`ASISTENTE_MAX_MENSAJES_DIA`, `ASISTENTE_MAX_TOKENS_DIA`, `ASISTENTE_MAX_MEDIOS_DIA`. Un valor vacío, mal escrito, 0 o negativo
no los apaga (se usa el de por defecto); apagarlos exige `ASISTENTE_SIN_TOPES=1`.
"""

import os

import master_db

MAX_MENSAJES_DIA = 100   # por cuenta y por día
MAX_TOKENS_DIA = 200_000
MAX_MEDIOS_DIA = 20      # fotos y audios: cuestan más que un texto
DIAS_QUE_SE_CONSERVAN = 40

DIA, MEDIOS = "dia", "medios"   # motivos de rechazo


def entero_de_entorno(nombre, defecto):
    """Entero positivo de una variable de entorno. Un valor vacío, mal escrito, 0 o negativo no apaga el tope: se usa el valor por defecto."""
    try:
        valor = int(os.environ.get(nombre, "").strip() or defecto)
    except ValueError:
        return defecto
    return valor if valor > 0 else defecto


def _hoy():
    from finanzas_core import ahora_local
    return ahora_local().date().isoformat()


class Cupo:
    """Sin argumentos lee los topes del entorno en cada llamada. Las pruebas pasan los suyos (0 = sin tope) y su propio `dia()`."""

    def __init__(self, max_mensajes=None, max_tokens=None, max_medios=None, dia=None):
        self._fijos = (max_mensajes, max_tokens, max_medios)
        self._dia = dia or _hoy

    def topes(self):
        if os.environ.get("ASISTENTE_SIN_TOPES", "").strip() == "1":
            return 0, 0, 0
        m, t, md = self._fijos
        return (entero_de_entorno("ASISTENTE_MAX_MENSAJES_DIA", MAX_MENSAJES_DIA) if m is None else m,
                entero_de_entorno("ASISTENTE_MAX_TOKENS_DIA", MAX_TOKENS_DIA) if t is None else t,
                entero_de_entorno("ASISTENTE_MAX_MEDIOS_DIA", MAX_MEDIOS_DIA) if md is None else md)

    def reservar(self, user_id, con_medio=False):
        """Cuenta un mensaje contra el día de la cuenta. None si cabe; si no, `"dia"` o `"medios"` y entonces no cuenta nada."""
        max_mensajes, max_tokens, max_medios = self.topes()
        dia = self._dia()
        conn = master_db.get_master_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")   # leer y sumar en un solo paso: dos servicios a la vez no se saltan el tope
            conn.execute("INSERT OR IGNORE INTO asistente_uso (user_id, dia) VALUES (?, ?)", (int(user_id), dia))
            fila = conn.execute("SELECT mensajes, tokens, medios FROM asistente_uso WHERE user_id = ? AND dia = ?", (int(user_id), dia)).fetchone()
            if (max_mensajes and fila["mensajes"] >= max_mensajes) or (max_tokens and fila["tokens"] >= max_tokens):
                conn.rollback()
                return DIA
            if con_medio and max_medios and fila["medios"] >= max_medios:
                conn.rollback()
                return MEDIOS
            conn.execute("UPDATE asistente_uso SET mensajes = mensajes + 1, medios = medios + ? WHERE user_id = ? AND dia = ?",
                         (1 if con_medio else 0, int(user_id), dia))
            conn.execute("DELETE FROM asistente_uso WHERE dia < date(?, ?)", (dia, f"-{DIAS_QUE_SE_CONSERVAN} days"))
            conn.commit()
            return None
        finally:
            conn.close()

    def sumar_tokens(self, user_id, tokens):
        tokens = max(0, int(tokens or 0))
        if not tokens:
            return
        dia = self._dia()
        conn = master_db.get_master_conn()
        try:
            conn.execute("INSERT OR IGNORE INTO asistente_uso (user_id, dia) VALUES (?, ?)", (int(user_id), dia))
            conn.execute("UPDATE asistente_uso SET tokens = tokens + ? WHERE user_id = ? AND dia = ?", (tokens, int(user_id), dia))
            conn.commit()
        finally:
            conn.close()

    def uso(self, user_id):
        conn = master_db.get_master_conn()
        try:
            fila = conn.execute("SELECT mensajes, tokens, medios FROM asistente_uso WHERE user_id = ? AND dia = ?", (int(user_id), self._dia())).fetchone()
            return dict(fila) if fila else {"mensajes": 0, "tokens": 0, "medios": 0}
        finally:
            conn.close()


por_defecto = Cupo()
