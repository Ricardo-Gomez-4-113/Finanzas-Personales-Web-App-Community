"""tarea_programada.py - Lo que la app hace sola cada cierto tiempo, sin que nadie abra la pantalla.

Los fijos (y las aportaciones a metas) se materializan al abrir la app; esta tarea los materializa también con la app cerrada, para que la
aportación del día y sus avisos (aporte omitido por falta de saldo, meta cumplida) existan aunque la persona no entre. Abrir la app sigue
materializando como siempre: `materializar_recurrentes` es idempotente (índice único por fijo y fecha), así que los dos caminos dan el mismo resultado.

- Apagada por defecto: se activa con `NOTIFICACIONES_TAREA=1` (el compose la enciende). `NOTIFICACIONES_PERIODO_MIN` fija cada cuánto (60 por defecto).
- Una sola ronda a la vez y sin repetirse antes de tiempo, aunque haya varios procesos: candado en la base maestra (`tareas_programadas`) con vencimiento,
  así un reinicio a mitad de ronda no lo deja bloqueado.
- Cada cuenta se procesa aislada: si una falla se registra (sin datos financieros) y las demás siguen. Las cuentas desactivadas no se tocan.
"""

import asyncio
import os
import time

import master_db
from database import get_tenant_conn, get_tenant_path
from finanzas_core import ahora_local, materializar_recurrentes
from observabilidad import logger

NOMBRE = "materializar_fijos"
PERIODO_POR_DEFECTO_MIN = 60
LEASE_SEG = 600          # una ronda tarda segundos; si el proceso muere a medias, el candado vence solo
RETRASO_INICIAL_SEG = 30  # dejar que la app termine de arrancar antes de la primera ronda


def activa() -> bool:
    return os.environ.get("NOTIFICACIONES_TAREA", "").strip() == "1"


def periodo_seg() -> int:
    try:
        minutos = int(os.environ.get("NOTIFICACIONES_PERIODO_MIN", "").strip() or PERIODO_POR_DEFECTO_MIN)
    except ValueError:
        minutos = PERIODO_POR_DEFECTO_MIN
    return min(max(minutos, 1), 1440) * 60


def ronda(ahora: float = None):
    """Materializa los fijos vencidos de todas las cuentas activas. Devuelve `{"cuentas", "movimientos", "errores"}`, o None si otra ronda
    corre o corrió hace menos de lo permitido."""
    instante = time.time() if ahora is None else ahora
    if not master_db.adquirir_tarea(NOMBRE, LEASE_SEG, periodo_seg() * 0.9, ahora=instante):
        return None
    resumen = {"cuentas": 0, "movimientos": 0, "errores": 0}
    try:
        hoy = ahora_local().date()
        for cuenta in master_db.cuentas_activas():
            # La lista se armó al empezar: la cuenta pudo suspenderse o borrarse mientras se procesaban otras. Abrir una base borrada la recrearía vacía.
            if not master_db.cuenta_activa(cuenta["id"]) or not os.path.exists(get_tenant_path(cuenta["db_name"])):
                continue
            try:
                conn = get_tenant_conn(cuenta["db_name"])
                try:
                    resumen["movimientos"] += materializar_recurrentes(conn, hoy)
                finally:
                    conn.close()
                resumen["cuentas"] += 1
            except Exception as error:   # una cuenta rota no frena a las demás; el registro lleva el tipo del error, nunca sus datos
                resumen["errores"] += 1
                # Solo el tipo del error: la traza de una excepción lleva su texto, y ese texto puede traer datos de la cuenta
                logger.error(type(error).__name__, extra={"evento": "tarea_materializar_fallo", "user_id": cuenta["id"]})
    finally:
        master_db.liberar_tarea(NOMBRE, instante)
    return resumen


async def bucle():
    """Corre una ronda cada periodo hasta que la app se apague. Una ronda que falla no mata el bucle."""
    await asyncio.sleep(RETRASO_INICIAL_SEG)
    while True:
        try:
            await asyncio.to_thread(ronda)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(type(error).__name__, extra={"evento": "tarea_materializar_ronda"})
        await asyncio.sleep(periodo_seg())
