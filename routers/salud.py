"""routers/salud.py - Endpoint /health para Docker, Portainer o un monitor externo.

Público a propósito: no expone datos de usuarios, solo si la base maestra responde y la antigüedad del último
respaldo automático. Responde 503 si la base maestra no responde, para que Docker marque el contenedor unhealthy.
"""

import os
import sqlite3
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from database import DATA_DIR
from master_db import get_master_conn

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


@router.get("/health", include_in_schema=False)
def health():
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
    cuerpo = {
        "status": "ok" if base == "ok" and respaldo == "ok" else ("error" if base != "ok" else "degradado"),
        "base_maestra": base,
        "respaldo": respaldo,
        "horas_desde_respaldo": horas,
    }
    return JSONResponse(cuerpo, status_code=200 if base == "ok" else 503)
