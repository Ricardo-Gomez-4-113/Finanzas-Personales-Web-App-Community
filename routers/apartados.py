"""routers/apartados.py - Sobres virtuales y metas de ahorro."""

from fastapi import APIRouter
from fastapi import HTTPException, Query, status, Depends
from pydantic import BaseModel, Field
from typing import Literal, Optional
import sqlite3

from dinero import MONTO_MINIMO, a_centavos
from finanzas_core import (
    ahora_local, avisar, balance_disponible_a_fecha, validar_deposito_no_sobregira_despues, cerrar_meta_si_cumplida, clase_movimiento, nombre_en_aviso, pesos_en_mensaje,
)
from security import (
    get_tenant_db
)

router = APIRouter()

class ApartadoCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    tipo: Literal['libre', 'meta']
    meta_monto: Optional[float] = Field(None, ge=MONTO_MINIMO, le=1e12)


class MovimientoApartado(BaseModel):
    monto: float = Field(..., ge=MONTO_MINIMO, le=1e12)
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
            "INSERT INTO apartados (nombre, tipo, monto_actual, meta_monto) VALUES (?, ?, 0, ?)",
            (item.nombre, item.tipo, a_centavos(item.meta_monto) if item.tipo == 'meta' else None)
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
def eliminar_apartado(
    apartado_id: int,
    confirmar_fijos: bool = Query(False, description="Confirma que también se eliminan los gastos fijos que le aportan"),
    conn: sqlite3.Connection = Depends(get_tenant_db),
):
    cursor = conn.cursor()
    apartado = cursor.execute('SELECT * FROM apartados WHERE id = ?', (apartado_id,)).fetchone()
    if not apartado:
        raise HTTPException(status_code=404, detail="Apartado no encontrado")

    saldo_actual = round(float(apartado["monto_actual"] or 0), 2)
    # Un gasto fijo que aporta a esta meta no puede quedarse suelto: seguiría cobrándose como gasto normal. Se elimina con la meta, y solo si la
    # persona lo confirma (la interfaz le muestra cuáles son antes de pedir la confirmación).
    fijos = cursor.execute("SELECT id, descripcion, monto FROM recurrentes WHERE apartado_id = ? ORDER BY id", (apartado_id,)).fetchall()
    if fijos and not confirmar_fijos:
        lista = ", ".join(f"«{nombre_en_aviso(f['descripcion'])}» ({pesos_en_mensaje(float(f['monto']))})" for f in fijos)
        raise HTTPException(
            status_code=409,
            detail=f"Esta meta recibe aportes de {'un gasto fijo' if len(fijos) == 1 else 'varios gastos fijos'}: {lista}. "
                   f"Si la eliminas, {'ese gasto fijo' if len(fijos) == 1 else 'esos gastos fijos'} también se eliminará. Confirma para continuar.",
        )
    for f in fijos:
        cursor.execute("DELETE FROM recurrentes WHERE id = ?", (f["id"],))   # lo ya aportado se conserva en los movimientos
        avisar(cursor, "fijo_retirado", f"fijo_retirado:{f['id']}", "Quité un gasto fijo",
               f"Eliminaste la meta «{nombre_en_aviso(apartado['nombre'])}», así que también quité el gasto fijo «{nombre_en_aviso(f['descripcion'])}» que le aportaba.")
    # Una meta en cero se borra sin más (su historial queda en Movimientos, donde se puede eliminar). Una meta con dinero lo devuelve a
    # los movimientos como un ingreso normal: ese dinero vuelve a la caja y la persona puede quitarlo de ahí eliminando el movimiento.
    if saldo_actual > 0:
        categoria_ingreso = cursor.execute("SELECT id FROM categorias WHERE tipo = 'ingreso' AND nombre = 'Otros ingresos'").fetchone()
        if categoria_ingreso is None:
            categoria_ingreso = cursor.execute("SELECT id FROM categorias WHERE tipo = 'ingreso' ORDER BY id LIMIT 1").fetchone()
        cursor.execute(
            'INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion, apartado_id, rubro) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (ahora_local().strftime('%Y-%m-%d'), a_centavos(saldo_actual), 'ingreso', categoria_ingreso["id"],
             f"Dinero de la meta «{apartado['nombre']}» (eliminada)", None, clase_movimiento('ingreso', None, None, False))
        )
    cursor.execute('DELETE FROM apartados WHERE id = ?', (apartado_id,))
    conn.commit()
    return {"status": "ok", "mensaje": "Apartado eliminado", "fijos_eliminados": len(fijos), "pasado_a_movimientos": saldo_actual}

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
        # Todo se calcula en centavos enteros: el saldo de la meta y el movimiento se guardan igual (con 3 decimales, p. ej. 0.015,
        # el saldo en pesos y el movimiento en centavos se desfasaban)
        monto_c = a_centavos(mov.monto)
        saldo_c = a_centavos(monto_actual)

        if mov.accion == 'depositar':
            balance_disponible = balance_disponible_a_fecha(cursor, fecha_hoy)

            if monto_c > a_centavos(balance_disponible):
                conn.rollback()
                raise HTTPException(
                    status_code=400, 
                    detail=f"No puedes guardar dinero que no tienes. Tu balance disponible es de ${balance_disponible:.2f} MXN"
                )
                
            try:
                validar_deposito_no_sobregira_despues(cursor, fecha_hoy, monto_c / 100.0)
            except ValueError as e:
                conn.rollback()
                raise HTTPException(status_code=400, detail=str(e))

            nuevo_saldo_c = saldo_c + monto_c
            desc = f"Ahorro destinado a: {apartado['nombre']}"
            cursor.execute(
                'INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion, apartado_id, rubro) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (fecha_hoy, monto_c, 'gasto', 1, desc, apartado_id, clase_movimiento('gasto', apartado_id, None, False))
            )
            
        elif mov.accion == 'retirar':
            if monto_c > saldo_c:
                conn.rollback()
                raise HTTPException(status_code=400, detail="No puedes retirar más de lo que hay en este apartado")
            
            nuevo_saldo_c = saldo_c - monto_c
            desc = f"Retiro de ahorro: {apartado['nombre']}"
            cursor.execute(
                'INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion, apartado_id, rubro) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (fecha_hoy, monto_c, 'ingreso', 1, desc, apartado_id, clase_movimiento('ingreso', apartado_id, None, False))
            )
        
        nuevo_saldo = nuevo_saldo_c / 100.0
        cursor.execute('UPDATE apartados SET monto_actual = ? WHERE id = ?', (nuevo_saldo_c, apartado_id))
        if mov.accion == 'depositar':
            cerrar_meta_si_cumplida(cursor, apartado_id, fecha_hoy)   # avisa y quita los fijos que le aportaban
        conn.commit()
        return {"status": "ok", "nuevo_saldo": nuevo_saldo}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Error inesperado al procesar el movimiento del apartado.")
