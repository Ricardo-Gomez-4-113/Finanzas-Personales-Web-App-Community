"""routers/notificaciones.py - Los avisos de la app (aporte omitido, meta cumplida, fijo retirado...).

Los genera el core (`finanzas_core.avisar`) con plantillas; aquí solo se leen y se marcan. Viven en la base de cada cuenta (tabla `notificaciones`).
El texto lleva nombres que escribió la persona: la interfaz lo muestra siempre como texto, nunca como HTML.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from security import get_tenant_db

router = APIRouter()

MAX_LISTA = 100


def _serializar(fila) -> dict:
    return {"id": fila["id"], "tipo": fila["tipo"], "titulo": fila["titulo"], "texto": fila["texto"],
            "creada_en": fila["creada_en"], "leida": bool(fila["leida"])}


@router.get("/api/notificaciones", summary="Avisos de la cuenta, del más reciente al más antiguo")
def listar_notificaciones(
    solo_no_leidas: bool = Query(False),
    limite: int = Query(50, ge=1, le=MAX_LISTA),
    conn: sqlite3.Connection = Depends(get_tenant_db),
):
    cursor = conn.cursor()
    filtro = "WHERE leida = 0" if solo_no_leidas else ""
    filas = cursor.execute(f"SELECT * FROM notificaciones {filtro} ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
    no_leidas = cursor.execute("SELECT COUNT(*) FROM notificaciones WHERE leida = 0").fetchone()[0]
    return {"no_leidas": no_leidas, "notificaciones": [_serializar(f) for f in filas]}


@router.post("/api/notificaciones/leer-todas", summary="Marcar todos los avisos como leídos")
def leer_todas(conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    cursor.execute("UPDATE notificaciones SET leida = 1 WHERE leida = 0")
    marcadas = cursor.rowcount
    conn.commit()
    return {"marcadas": marcadas}


@router.post("/api/notificaciones/{notificacion_id}/leer", summary="Marcar un aviso como leído")
def leer_notificacion(notificacion_id: int, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    cursor.execute("UPDATE notificaciones SET leida = 1 WHERE id = ?", (notificacion_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Aviso no encontrado.")
    conn.commit()
    return {"status": "ok"}


@router.delete("/api/notificaciones/{notificacion_id}", summary="Borrar un aviso")
def borrar_notificacion(notificacion_id: int, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notificaciones WHERE id = ?", (notificacion_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Aviso no encontrado.")
    conn.commit()
    return {"status": "ok"}


@router.delete("/api/notificaciones", summary="Borrar los avisos ya leídos")
def borrar_leidas(conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notificaciones WHERE leida = 1")
    borradas = cursor.rowcount
    conn.commit()
    return {"borradas": borradas}
