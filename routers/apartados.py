"""routers/apartados.py - Sobres virtuales y metas de ahorro."""

from fastapi import APIRouter
from fastapi import HTTPException, status, Depends
from pydantic import BaseModel, Field
from typing import Literal, Optional
import sqlite3

from finanzas_core import ahora_local, clase_movimiento
from security import (
    get_tenant_db
)

router = APIRouter()

class ApartadoCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    tipo: Literal['libre', 'meta']
    meta_monto: Optional[float] = Field(None, gt=0, le=1e12)


class MovimientoApartado(BaseModel):
    monto: float = Field(..., gt=0, le=1e12)
    accion: Literal['depositar', 'retirar']


# =============================================================================
# ENDPOINTS MULTI-TENANT: SOBRES VIRTUALES Y METAS DE AHORRO
# =============================================================================

@router.post("/api/apartados", status_code=status.HTTP_201_CREATED, summary="Crear apartado o meta")
def crear_apartado(item: ApartadoCreate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    if item.tipo == 'meta' and (item.meta_monto is None or item.meta_monto <= 0):
        raise HTTPException(status_code=400, detail="Una meta requiere un monto objetivo mayor a cero.")
    
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO apartados (nombre, tipo, monto_actual, meta_monto) VALUES (?, ?, 0.0, ?)",
            (item.nombre, item.tipo, item.meta_monto if item.tipo == 'meta' else None)
        )
        conn.commit()
        nuevo_id = cursor.lastrowid
        return {"id": nuevo_id, "mensaje": "Apartado creado exitosamente"}
    except sqlite3.IntegrityError:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Ya existe un apartado con ese nombre.")
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Error interno al registrar el apartado.")

@router.get("/api/apartados", summary="Listar apartados del tenant")
def listar_apartados(conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM apartados ORDER BY id DESC")
    filas = cursor.fetchall()
    
    resultado = []
    for fila in filas:
        item = dict(fila)
        if item["tipo"] == "meta" and item["meta_monto"] and item["meta_monto"] > 0:
            item["porcentaje"] = round((item["monto_actual"] / item["meta_monto"]) * 100, 2)
        else:
            item["porcentaje"] = None
        resultado.append(item)
    return resultado

@router.delete("/api/apartados/{apartado_id}", summary="Eliminar apartado")
def eliminar_apartado(apartado_id: int, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    apartado = cursor.execute('SELECT * FROM apartados WHERE id = ?', (apartado_id,)).fetchone()
    if not apartado:
        raise HTTPException(status_code=404, detail="Apartado no encontrado")

    saldo_actual = round(float(apartado["monto_actual"] or 0), 2)
    if saldo_actual > 0:
        raise HTTPException(
            status_code=400,
            detail=f"No puedes eliminar un apartado con saldo disponible (${saldo_actual:.2f}). Retira los fondos al balance disponible primero."
        )
        
    cursor.execute('DELETE FROM apartados WHERE id = ?', (apartado_id,))
    conn.commit()
    return {"status": "ok", "mensaje": "Apartado eliminado"}

@router.post("/api/apartados/{apartado_id}/movimiento", summary="Depositar o retirar en un apartado")
def movimiento_apartado(apartado_id: int, mov: MovimientoApartado, conn: sqlite3.Connection = Depends(get_tenant_db)):
    # Transacción inmediata para evitar condiciones de carrera en saldos concurrentes
    conn.execute("BEGIN IMMEDIATE")
    cursor = conn.cursor()
    try:
        apartado = cursor.execute('SELECT * FROM apartados WHERE id = ?', (apartado_id,)).fetchone()
        if not apartado:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Apartado no encontrado")
            
        monto_actual = round(float(apartado['monto_actual'] or 0), 2)
        fecha_hoy = ahora_local().strftime('%Y-%m-%d')
        
        if mov.accion == 'depositar':
            ingresos_row = cursor.execute("SELECT SUM(monto) as total FROM transacciones WHERE tipo='ingreso'").fetchone()
            gastos_row = cursor.execute("SELECT SUM(monto) as total FROM transacciones WHERE tipo='gasto'").fetchone()
            
            total_ingresos = float(ingresos_row['total']) if (ingresos_row and ingresos_row['total']) else 0.0
            total_gastos = float(gastos_row['total']) if (gastos_row and gastos_row['total']) else 0.0
            balance_disponible = round(total_ingresos - total_gastos, 2)
            
            if round(mov.monto, 2) > balance_disponible:
                conn.rollback()
                raise HTTPException(
                    status_code=400, 
                    detail=f"No puedes guardar dinero que no tienes. Tu balance disponible es de ${balance_disponible:.2f}"
                )
                
            nuevo_saldo = round(monto_actual + mov.monto, 2)
            desc = f"Ahorro destinado a: {apartado['nombre']}"
            cursor.execute(
                'INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion, apartado_id, rubro) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (fecha_hoy, mov.monto, 'gasto', 1, desc, apartado_id, clase_movimiento('gasto', apartado_id, None, False))
            )
            
        elif mov.accion == 'retirar':
            if round(mov.monto, 2) > monto_actual:
                conn.rollback()
                raise HTTPException(status_code=400, detail="No puedes retirar más de lo que hay en este apartado")
            
            nuevo_saldo = round(monto_actual - mov.monto, 2)
            desc = f"Retiro de ahorro: {apartado['nombre']}"
            cursor.execute(
                'INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion, apartado_id, rubro) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (fecha_hoy, mov.monto, 'ingreso', 1, desc, apartado_id, clase_movimiento('ingreso', apartado_id, None, False))
            )
        
        cursor.execute('UPDATE apartados SET monto_actual = ? WHERE id = ?', (nuevo_saldo, apartado_id))
        conn.commit()
        return {"status": "ok", "nuevo_saldo": nuevo_saldo}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Error inesperado al procesar el movimiento del apartado.")
