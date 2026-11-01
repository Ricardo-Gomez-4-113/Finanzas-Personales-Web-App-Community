"""routers/salud.py - Salud del servicio.

/health es público y solo dice si el servicio responde: 200 o 503 si la base maestra no responde, para que Docker
marque el contenedor unhealthy. El estado interno (base maestra) está en /api/salud/detalle, solo para administradores.
"""

import sqlite3

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from master_db import get_master_conn
from security import verify_admin

router = APIRouter()


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
    return {"status": "ok" if base == "ok" else "error", "base_maestra": base}


@router.get("/health", include_in_schema=False)
def health():
    estado = _estado_interno()
    publico = {"status": estado["status"]}
    return JSONResponse(publico, status_code=503 if estado["base_maestra"] == "error" else 200)


@router.get("/api/salud/detalle", summary="Estado interno del servicio (solo administradores)")
def salud_detalle(_admin=Depends(verify_admin)):
    return _estado_interno()
