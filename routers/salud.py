"""routers/salud.py - Salud del servicio.

/health es público y solo dice si el servicio responde: 200 o 503 si la base maestra no responde, para que Docker
marque el contenedor unhealthy. El estado interno (base maestra, antigüedad del último respaldo) está en
/api/salud/detalle, solo para administradores: un atacante no debe saber cuándo está vencido el respaldo.
"""

import os
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from database import DATA_DIR
from master_db import get_master_conn
from security import verify_admin

router = APIRouter()

RESPALDOS_DIR = os.path.join(DATA_DIR, "backups")
# El cron corre a diario; más de 26 h sin respaldo nuevo significa que falló o no está programado.
RESPALDO_VENCIDO_HORAS = 26


def _horas_desde_ultimo_respaldo():
    try:
        sellos = [d for d in os.listdir(RESPALDOS_DIR) if os.path.isdir(os.path.join(RESPALDOS_DIR, d))]
        ultimo = max(datetime.strptime(d, "%Y%m%d_%H%M%S") for d in sellos if len(d) == 15)
    except (OSError, ValueError):
        return None
    return round((datetime.now() - ultimo).total_seconds() / 3600, 1)


def _estado_interno():
    try:
        conn = get_master_conn()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        base = "ok"
    except sqlite3.Error:
        base = "error"

    horas = _horas_desde_ultimo_respaldo()
    respaldo = "ok" if horas is not None and horas <= RESPALDO_VENCIDO_HORAS else "vencido"
    estado = "ok" if base == "ok" and respaldo == "ok" else ("error" if base != "ok" else "degradado")
    return {"status": estado, "base_maestra": base, "respaldo": respaldo, "horas_desde_respaldo": horas}


@router.get("/health", include_in_schema=False)
def health():
    estado = _estado_interno()
    publico = {"status": "error" if estado["base_maestra"] == "error" else "ok"}
    return JSONResponse(publico, status_code=503 if estado["base_maestra"] == "error" else 200)


@router.get("/api/salud/detalle", summary="Estado interno del servicio (solo administradores)")
def salud_detalle(_admin=Depends(verify_admin)):
    return _estado_interno()
