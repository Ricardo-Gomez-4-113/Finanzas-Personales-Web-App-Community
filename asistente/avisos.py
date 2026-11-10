"""asistente/avisos.py - Los avisos de la app que llegan por Telegram a quien tiene el asistente vinculado.

Los avisos viven en la base de cada cuenta (`notificaciones`, los genera el core con plantillas). El bot no toca esa base: cada minuto le pide a la app los
pendientes (`reclamar`), los manda y le dice si salió (`resultado`). Nada de esto pasa por el modelo ni gasta el cupo diario del asistente.

- Solo a cuentas con un chat vinculado y el asistente activo (el interruptor del administrador corta también esto).
- Cada aviso se reclama de forma atómica: aunque dos bots o dos rondas coincidan, solo uno lo recibe. Si el bot muere con el aviso en la mano, el reclamo
  vence a los 10 minutos y vuelve a estar pendiente.
- Un fallo de Telegram se reintenta (hasta `MAX_INTENTOS`); después se deja de insistir y el aviso sigue en la campana de la app.
- Solo los de las últimas 24 horas: al vincular Telegram no llegan de golpe los avisos viejos.
- Quedar leído en la campana no cancela el envío ni al revés: son dos entregas independientes del mismo aviso.
"""

from datetime import timedelta

import master_db
from asistente import vinculos
from database import get_tenant_conn
from finanzas_core import ahora_local

MAX_INTENTOS = 5
VIGENCIA_HORAS = 24
RECLAMO_VENCE_MIN = 10
LIMITE_POR_RONDA = 10

PENDIENTE, RECLAMADO, ENTREGADO = 0, 1, 2


def _texto(momento):
    return momento.strftime("%Y-%m-%d %H:%M:%S")


def _chats_vinculados():
    conn = master_db.get_master_conn()
    try:
        return [fila["chat_id"] for fila in conn.execute("SELECT chat_id FROM telegram_vinculos ORDER BY user_id").fetchall()]
    finally:
        conn.close()


def _base_de(chat_id):
    """(cuenta, base) del chat si su vínculo sigue vigente y el asistente está activo; si no, None."""
    cuenta = vinculos.usuario_de_chat(chat_id, tocar=False)
    if not cuenta or not cuenta["asistente"]:
        return None
    fila = master_db.get_user_by_id(cuenta["id"])
    return (cuenta, fila["db_name"]) if fila and fila.get("db_name") else None


def reclamar(limite=LIMITE_POR_RONDA, ahora=None):
    """Los avisos pendientes de todos los chats vinculados, ya reclamados para el bot: `[{chat_id, id, titulo, texto}]`."""
    ahora = ahora or ahora_local()
    desde, reclamo_viejo = _texto(ahora - timedelta(hours=VIGENCIA_HORAS)), _texto(ahora - timedelta(minutes=RECLAMO_VENCE_MIN))
    salida = []
    for chat_id in _chats_vinculados():
        if len(salida) >= limite:
            break
        destino = _base_de(chat_id)
        if destino is None:
            continue
        conn = get_tenant_conn(destino[1])
        try:
            # Un reclamo vencido es un intento que no se confirmó (el bot murió, o la app no recibió su resultado): cuenta, para que un bot que siempre
            # muere con el aviso en la mano no lo reenvíe cada 10 minutos durante 24 horas. La entrega es «al menos una vez»: si Telegram sí lo aceptó
            # pero el resultado no llegó, puede repetirse.
            conn.execute("UPDATE notificaciones SET entrega_telegram = ?, intentos_telegram = intentos_telegram + 1 WHERE entrega_telegram = ? AND reclamado_en < ?",
                         (PENDIENTE, RECLAMADO, reclamo_viejo))
            filas = conn.execute(
                "SELECT id, titulo, texto FROM notificaciones WHERE entrega_telegram = ? AND intentos_telegram < ? AND creada_en >= ? ORDER BY id LIMIT ?",
                (PENDIENTE, MAX_INTENTOS, desde, limite - len(salida))).fetchall()
            for fila in filas:
                tomado = conn.execute("UPDATE notificaciones SET entrega_telegram = ?, reclamado_en = ? WHERE id = ? AND entrega_telegram = ?",
                                      (RECLAMADO, _texto(ahora), fila["id"], PENDIENTE)).rowcount
                if tomado == 1:
                    salida.append({"chat_id": chat_id, "id": fila["id"], "titulo": fila["titulo"], "texto": fila["texto"]})
            conn.commit()
        finally:
            conn.close()
    return salida


def resultado(chat_id, aviso_id, entregado):
    """El bot dice qué pasó con un aviso que reclamó. Solo cuenta si de verdad estaba reclamado y el chat sigue siendo de esa cuenta."""
    destino = _base_de(chat_id)
    if destino is None:
        return False
    conn = get_tenant_conn(destino[1])
    try:
        if entregado:
            cambio = conn.execute("UPDATE notificaciones SET entrega_telegram = ? WHERE id = ? AND entrega_telegram = ?", (ENTREGADO, aviso_id, RECLAMADO)).rowcount
        else:
            cambio = conn.execute("UPDATE notificaciones SET entrega_telegram = ?, intentos_telegram = intentos_telegram + 1 WHERE id = ? AND entrega_telegram = ?",
                                  (PENDIENTE, aviso_id, RECLAMADO)).rowcount
        conn.commit()
        return cambio == 1
    finally:
        conn.close()
