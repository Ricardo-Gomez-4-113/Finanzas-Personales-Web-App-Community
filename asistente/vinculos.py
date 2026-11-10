"""asistente/vinculos.py - Vincular un chat privado de Telegram con una cuenta, por un código de un solo uso.

El código sale de «Mi perfil» (con la contraseña actual) y viaja en el QR dentro del enlace `t.me/<bot>?start=<código>`.
Se guarda solo su hash, vence a los 5 minutos y sirve una vez. La cuenta de un chat se saca siempre de esta tabla,
nunca de lo que diga el mensaje. El vínculo lleva la huella de la contraseña con que se hizo: si la contraseña cambia (perfil,
recuperación o administrador) o la cuenta se desactiva, el vínculo se borra y hay que volver a escanear.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import master_db
import security

MINUTOS_DEL_CODIGO = 5
FORMATO = "%Y-%m-%d %H:%M:%S"

OK, INVALIDO, CHAT_OCUPADO = "ok", "invalido", "chat_ocupado"


def _ahora():
    return datetime.now(timezone.utc)


def _texto(momento):
    return momento.strftime(FORMATO)


def _hash(codigo):
    return hashlib.sha256(str(codigo).encode("utf-8")).hexdigest()


def crear_codigo(user_id, ahora=None):
    """Código nuevo para esa cuenta (el anterior sin usar deja de valer). Devuelve `(codigo, segundos_de_vida)`."""
    ahora = ahora or _ahora()
    codigo = secrets.token_urlsafe(24)   # 32 caracteres de [A-Za-z0-9_-]: el único alfabeto que admite /start
    conn = master_db.get_master_conn()
    try:
        conn.execute("DELETE FROM telegram_codigos WHERE user_id = ? OR vence_en < ?", (int(user_id), _texto(ahora - timedelta(hours=1))))
        conn.execute("INSERT INTO telegram_codigos (codigo_hash, user_id, vence_en, usado, creado_en) VALUES (?, ?, ?, 0, ?)",
                     (_hash(codigo), int(user_id), _texto(ahora + timedelta(minutes=MINUTOS_DEL_CODIGO)), _texto(ahora)))
        conn.commit()
    finally:
        conn.close()
    return codigo, MINUTOS_DEL_CODIGO * 60


def consumir_codigo(codigo, chat_id, username=None, ahora=None):
    """Vincula el chat con la cuenta dueña del código. Devuelve `(estado, user_id, chat_anterior, nombre_de_la_cuenta)`:
    - `ok`: quedó vinculado; `chat_anterior` es otro chat que tenía esa cuenta (para avisarle) o None; el cuarto valor es el nombre de la cuenta.
    - `invalido`: el código no existe, ya se usó, venció o la cuenta ya no está activa.
    - `chat_ocupado`: ese chat ya está vinculado a otra cuenta; el código no se gasta."""
    ahora = ahora or _ahora()
    if not isinstance(codigo, str) or not 16 <= len(codigo) <= 64:
        return INVALIDO, None, None, None
    conn = master_db.get_master_conn()
    try:
        # Un código que no existe no toma el bloqueo de escritura: adivinar no frena a la app
        if not conn.execute("SELECT 1 FROM telegram_codigos WHERE codigo_hash = ?", (_hash(codigo),)).fetchone():
            return INVALIDO, None, None, None
        conn.execute("BEGIN IMMEDIATE")
        fila = conn.execute("SELECT c.user_id, u.username, u.password_hash, u.is_active, u.subscription_status FROM telegram_codigos c JOIN users u ON u.id = c.user_id "
                            "WHERE c.codigo_hash = ? AND c.usado = 0 AND c.vence_en >= ?", (_hash(codigo), _texto(ahora))).fetchone()
        if not fila or not fila["is_active"] or fila["subscription_status"] in ("suspended", "cancelled"):
            conn.rollback()
            return INVALIDO, None, None, None
        user_id = fila["user_id"]
        ocupante = conn.execute("SELECT v.user_id, v.huella_clave, u.password_hash, u.is_active, u.subscription_status FROM telegram_vinculos v "
                                "JOIN users u ON u.id = v.user_id WHERE v.chat_id = ?", (int(chat_id),)).fetchone()
        if ocupante and ocupante["user_id"] != user_id:
            vigente = (ocupante["is_active"] and ocupante["subscription_status"] not in ("suspended", "cancelled")
                       and ocupante["huella_clave"] == security.huella_clave(ocupante["password_hash"]))
            if vigente:
                conn.rollback()
                return CHAT_OCUPADO, None, None, None
            conn.execute("DELETE FROM telegram_vinculos WHERE chat_id = ?", (int(chat_id),))   # un vínculo caducado no ocupa el chat
        anterior = conn.execute("SELECT chat_id FROM telegram_vinculos WHERE user_id = ?", (user_id,)).fetchone()
        conn.execute("UPDATE telegram_codigos SET usado = 1 WHERE codigo_hash = ?", (_hash(codigo),))
        conn.execute("INSERT INTO telegram_vinculos (user_id, chat_id, username, huella_clave, creado_en, ultimo_uso) VALUES (?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(user_id) DO UPDATE SET chat_id = excluded.chat_id, username = excluded.username, huella_clave = excluded.huella_clave, "
                     "creado_en = excluded.creado_en, ultimo_uso = excluded.ultimo_uso",
                     (user_id, int(chat_id), (username or None), security.huella_clave(fila["password_hash"]), _texto(ahora), _texto(ahora)))
        conn.commit()
        cambio = anterior["chat_id"] if anterior and anterior["chat_id"] != int(chat_id) else None
        return OK, user_id, cambio, fila["username"]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def usuario_de_chat(chat_id, ahora=None):
    """La cuenta ACTIVA vinculada a ese chat, o None. Es lo único que decide a nombre de quién se actúa.
    Si la cuenta se desactivó o suspendió, o su contraseña cambió desde que se vinculó, el vínculo se borra."""
    ahora = ahora or _ahora()
    conn = master_db.get_master_conn()
    try:
        fila = conn.execute("SELECT u.id, u.username, u.password_hash, u.is_active, u.subscription_status, u.asistente_activo, v.huella_clave FROM telegram_vinculos v "
                            "JOIN users u ON u.id = v.user_id WHERE v.chat_id = ?", (int(chat_id),)).fetchone()
        if not fila:
            return None
        if (not fila["is_active"] or fila["subscription_status"] in ("suspended", "cancelled")
                or fila["huella_clave"] != security.huella_clave(fila["password_hash"])):
            conn.execute("DELETE FROM telegram_vinculos WHERE chat_id = ?", (int(chat_id),))
            conn.commit()
            return None
        conn.execute("UPDATE telegram_vinculos SET ultimo_uso = ? WHERE chat_id = ?", (_texto(ahora), int(chat_id)))
        conn.commit()
        return {"id": fila["id"], "username": fila["username"], "asistente": bool(fila["asistente_activo"])}
    finally:
        conn.close()


def estado(user_id):
    """`{"vinculado": bool, "username": ..., "desde": ...}` de esa cuenta (un vínculo de antes de un cambio de contraseña no cuenta)."""
    conn = master_db.get_master_conn()
    try:
        fila = conn.execute("SELECT v.username, v.creado_en, v.huella_clave, u.password_hash FROM telegram_vinculos v JOIN users u ON u.id = v.user_id "
                            "WHERE v.user_id = ?", (int(user_id),)).fetchone()
        valido = bool(fila) and fila["huella_clave"] == security.huella_clave(fila["password_hash"])
        return {"vinculado": valido, "username": fila["username"] if valido else None, "desde": fila["creado_en"] if valido else None}
    finally:
        conn.close()


def desvincular_cuenta(user_id):
    """Borra el vínculo de la cuenta. Devuelve el chat que tenía (para avisarle) o None."""
    conn = master_db.get_master_conn()
    try:
        fila = conn.execute("SELECT chat_id FROM telegram_vinculos WHERE user_id = ?", (int(user_id),)).fetchone()
        conn.execute("DELETE FROM telegram_vinculos WHERE user_id = ?", (int(user_id),))
        conn.execute("DELETE FROM telegram_codigos WHERE user_id = ?", (int(user_id),))
        conn.commit()
        return fila["chat_id"] if fila else None
    finally:
        conn.close()


def desvincular_chat(chat_id):
    """Borra el vínculo de ese chat. Devuelve la cuenta que tenía o None."""
    conn = master_db.get_master_conn()
    try:
        fila = conn.execute("SELECT user_id FROM telegram_vinculos WHERE chat_id = ?", (int(chat_id),)).fetchone()
        conn.execute("DELETE FROM telegram_vinculos WHERE chat_id = ?", (int(chat_id),))
        conn.commit()
        return fila["user_id"] if fila else None
    finally:
        conn.close()
