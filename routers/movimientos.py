"""routers/movimientos.py - Movimientos de caja y resúmenes patrimoniales."""

from fastapi import APIRouter
from fastapi import HTTPException, Depends
try:
    from fastapi import Query
except ImportError:
    def Query(default=None, *a, **kw): return default
from pydantic import BaseModel, Field
from typing import Literal, Optional, List
import re
import sqlite3

from finanzas_core import (
    ahora_local,
    calcular_resumen,
    convertir,
    materializar_recurrentes,
    validar_saldo_disponible_fecha,
    validar_eliminacion_transaccion,
    validar_edicion_transaccion,
    validar_limite_cargos_ventana,
    obtener_total_apartados,
)
from mercado import obtener_tasas_cambio
from security import (
    get_tenant_db
)

router = APIRouter()

class EliminarLoteRequest(BaseModel):
    ids: Optional[List[int]] = None
    todos: bool = False
    forzar: bool = False

class Transaccion(BaseModel):
    fecha: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="Fecha en formato ISO YYYY-MM-DD")
    monto: float = Field(..., gt=0, le=1e12, description="Monto mayor a 0")
    tipo: Literal['gasto', 'ingreso'] = Field(..., description="Tipo de movimiento")
    categoria_id: Optional[int] = Field(1, description="ID de categoría")
    descripcion: Optional[str] = Field(None, max_length=255, description="Concepto o detalle")


class CategoriaCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=50)
    tipo: Literal['gasto', 'ingreso']


def validar_categoria(cursor, categoria_id) -> None:
    if categoria_id is None:
        return
    if not cursor.execute("SELECT 1 FROM categorias WHERE id = ?", (categoria_id,)).fetchone():
        raise HTTPException(status_code=400, detail="La categoría indicada no existe.")


def es_cuota_msi(cursor, recurrente_id) -> bool:
    if recurrente_id is None:
        return False
    fila = cursor.execute("SELECT total_cuotas FROM recurrentes WHERE id = ?", (recurrente_id,)).fetchone()
    return bool(fila) and fila["total_cuotas"] is not None


class TransaccionUpdate(BaseModel):
    fecha: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    monto: Optional[float] = Field(None, gt=0, le=1e12)
    tipo: Optional[Literal['gasto', 'ingreso']] = None
    categoria_id: Optional[int] = None
    descripcion: Optional[str] = Field(None, max_length=255)


# =============================================================================
# ENDPOINTS MULTI-TENANT: MOVIMIENTOS (TRANSACCIONES DE CAJA)
# =============================================================================

@router.post("/api/transacciones", summary="Registrar nuevo movimiento")
def crear_transaccion(t: Transaccion, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    validar_categoria(cursor, t.categoria_id)

    if t.tipo == 'gasto':
        try:
            validar_saldo_disponible_fecha(cursor, t.fecha, t.monto, t.tipo)
            validar_limite_cargos_ventana(cursor, t.fecha, t.monto)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    cursor.execute(
        """
        INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion) 
        VALUES (?, ?, ?, ?, ?)
        """,
        (t.fecha, t.monto, t.tipo, t.categoria_id, t.descripcion)
    )
    conn.commit()
    return {"mensaje": "Transacción guardada con éxito"}

@router.delete("/api/transacciones/{id_transaccion}", summary="Eliminar movimiento")
def eliminar_transaccion(
    id_transaccion: int,
    conn: sqlite3.Connection = Depends(get_tenant_db),
    forzar: bool = Query(False)
):
    if isinstance(forzar, sqlite3.Connection):
        conn = forzar
        forzar = False
    elif not isinstance(forzar, bool):
        forzar = bool(getattr(forzar, 'default', False))

    cursor = conn.cursor()
    trans = cursor.execute("SELECT * FROM transacciones WHERE id = ?", (id_transaccion,)).fetchone()
    if not trans:
        raise HTTPException(status_code=404, detail="Transacción no encontrada")

    if trans["tipo"] == 'ingreso' and not forzar:
        try:
            validar_eliminacion_transaccion(cursor, id_transaccion)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    # Si está vinculado a un apartado de ahorro / meta
    # Si es gasto (depósito/ahorro): se descuenta de la meta.
    # Si es ingreso (retiro de ahorro): NO se reincorpora a la meta (el dinero sacado no vuelve a la meta).
    if trans["apartado_id"] is not None and trans["tipo"] == 'gasto':
        ap = cursor.execute("SELECT * FROM apartados WHERE id = ?", (trans["apartado_id"],)).fetchone()
        if ap:
            monto_actual = float(ap["monto_actual"])
            monto_trans = float(trans["monto"])
            nuevo_monto = max(0.0, round(monto_actual - monto_trans, 2))
            cursor.execute(
                "UPDATE apartados SET monto_actual = ? WHERE id = ?",
                (nuevo_monto, trans["apartado_id"])
            )

    # Si es cuota de un recurrente, decrementar cuotas_generadas si aplica
    if trans["recurrente_id"] is not None:
        rec = cursor.execute("SELECT * FROM recurrentes WHERE id = ?", (trans["recurrente_id"],)).fetchone()
        if rec:
            cuotas = int(rec["cuotas_generadas"] or 0)
            nuevas_cuotas = max(0, cuotas - 1)
            if rec["total_cuotas"] is not None and rec["activo"] == 0:
                cursor.execute(
                    "UPDATE recurrentes SET cuotas_generadas = ?, activo = 1 WHERE id = ?",
                    (nuevas_cuotas, trans["recurrente_id"])
                )
            else:
                cursor.execute(
                    "UPDATE recurrentes SET cuotas_generadas = ? WHERE id = ?",
                    (nuevas_cuotas, trans["recurrente_id"])
                )

    cursor.execute("DELETE FROM transacciones WHERE id = ?", (id_transaccion,))
    conn.commit()
    return {"mensaje": "Transacción eliminada con éxito"}


@router.post("/api/transacciones/eliminar-lote", summary="Eliminar movimientos por lote o vaciado total")
def eliminar_transacciones_lote(
    req: EliminarLoteRequest,
    conn: sqlite3.Connection = Depends(get_tenant_db)
):
    cursor = conn.cursor()
    if req.todos:
        transacciones = cursor.execute("SELECT * FROM transacciones").fetchall()
    elif req.ids:
        if not req.ids:
            return {"mensaje": "No se especificaron movimientos para eliminar", "eliminados": 0}
        placeholders = ",".join("?" for _ in req.ids)
        transacciones = cursor.execute(
            f"SELECT * FROM transacciones WHERE id IN ({placeholders})",
            tuple(req.ids)
        ).fetchall()
    else:
        return {"mensaje": "No se especificaron movimientos para eliminar", "eliminados": 0}

    if not transacciones:
        return {"mensaje": "No se encontraron movimientos para eliminar", "eliminados": 0}

    # Validación de déficit si no es forzado y no es vaciado total
    ingresos_externos = [t for t in transacciones if t["tipo"] == 'ingreso' and t["apartado_id"] is None]
    if not req.forzar and not req.todos and ingresos_externos:
        ids_set = tuple(t["id"] for t in transacciones)
        placeholders_sim = ",".join("?" for _ in ids_set)
        t_min = min(t["fecha"] for t in ingresos_externos)
        cursor.execute("SAVEPOINT sim_lote")
        try:
            for t in transacciones:
                if t["apartado_id"] is not None and t["tipo"] == 'gasto':
                    ap = cursor.execute("SELECT monto_actual FROM apartados WHERE id = ?", (t["apartado_id"],)).fetchone()
                    if ap:
                        m_act = float(ap["monto_actual"])
                        m_t = float(t["monto"])
                        nuevo_m = max(0.0, round(m_act - m_t, 2))
                        cursor.execute("UPDATE apartados SET monto_actual = ? WHERE id = ?", (nuevo_m, t["apartado_id"]))
            cursor.execute(f"DELETE FROM transacciones WHERE id IN ({placeholders_sim})", ids_set)
            
            tot_ap = obtener_total_apartados(cursor)

            row_prev = cursor.execute("""
                SELECT COALESCE(SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END), 0.0)
                FROM transacciones
                WHERE fecha < ? AND apartado_id IS NULL
            """, (t_min,)).fetchone()
            saldo_corriente = float(row_prev[0]) - tot_ap

            filas_saldo = cursor.execute("""
                SELECT fecha, SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END) as delta
                FROM transacciones
                WHERE fecha >= ? AND apartado_id IS NULL
                GROUP BY fecha
                ORDER BY fecha ASC
            """, (t_min,)).fetchall()

            deltas_por_fecha = {row["fecha"]: float(row["delta"]) for row in filas_saldo}
            if t_min not in deltas_por_fecha:
                deltas_por_fecha[t_min] = 0.0

            deficit_encontrado = None
            fecha_deficit = None
            for f in sorted(deltas_por_fecha.keys()):
                saldo_corriente += deltas_por_fecha[f]
                if saldo_corriente < -0.001:
                    deficit_encontrado = abs(saldo_corriente)
                    fecha_deficit = f
                    break
        finally:
            cursor.execute("ROLLBACK TO sim_lote")
            cursor.execute("RELEASE sim_lote")
            
        if deficit_encontrado is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"No es posible eliminar los movimientos seleccionados: provocaría un saldo negativo de "
                    f"-${deficit_encontrado:,.2f} en fecha {fecha_deficit}. Puedes forzar la eliminación si deseas continuar."
                )
            )

    # Procesar ajustes en una transacción SQL atómica
    for t in transacciones:
        if t["apartado_id"] is not None and t["tipo"] == 'gasto':
            ap = cursor.execute("SELECT * FROM apartados WHERE id = ?", (t["apartado_id"],)).fetchone()
            if ap:
                monto_actual = float(ap["monto_actual"])
                monto_trans = float(t["monto"])
                nuevo_monto = max(0.0, round(monto_actual - monto_trans, 2))
                cursor.execute(
                    "UPDATE apartados SET monto_actual = ? WHERE id = ?",
                    (nuevo_monto, t["apartado_id"])
                )

        if t["recurrente_id"] is not None:
            rec = cursor.execute("SELECT * FROM recurrentes WHERE id = ?", (t["recurrente_id"],)).fetchone()
            if rec:
                cuotas = int(rec["cuotas_generadas"] or 0)
                nuevas_cuotas = max(0, cuotas - 1)
                if rec["total_cuotas"] is not None and rec["activo"] == 0:
                    cursor.execute(
                        "UPDATE recurrentes SET cuotas_generadas = ?, activo = 1 WHERE id = ?",
                        (nuevas_cuotas, t["recurrente_id"])
                    )
                else:
                    cursor.execute(
                        "UPDATE recurrentes SET cuotas_generadas = ? WHERE id = ?",
                        (nuevas_cuotas, t["recurrente_id"])
                    )

    if req.todos:
        cursor.execute("DELETE FROM transacciones")
    else:
        ids_del = tuple(t["id"] for t in transacciones)
        pl = ",".join("?" for _ in ids_del)
        cursor.execute(f"DELETE FROM transacciones WHERE id IN ({pl})", ids_del)

    conn.commit()
    return {
        "mensaje": f"Se eliminaron {len(transacciones)} movimientos con éxito",
        "eliminados": len(transacciones)
    }

@router.put("/api/transacciones/{id_transaccion}", summary="Editar movimiento")
def actualizar_transaccion(id_transaccion: int, t: TransaccionUpdate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    trans = cursor.execute("SELECT * FROM transacciones WHERE id = ?", (id_transaccion,)).fetchone()
    if not trans:
        raise HTTPException(status_code=404, detail="Transacción no encontrada")

    if trans["apartado_id"] is not None:
        if (t.monto is not None and round(float(t.monto), 2) != round(float(trans["monto"]), 2)) or \
           (t.tipo is not None and t.tipo != trans["tipo"]) or \
           (t.fecha is not None and t.fecha != trans["fecha"]):
            raise HTTPException(
                status_code=400,
                detail="No puedes alterar la fecha, el monto ni el tipo de un movimiento vinculado a un apartado de ahorro. Utiliza la sección Metas / Apartados."
            )
    if es_cuota_msi(cursor, trans["recurrente_id"]):
        raise HTTPException(status_code=400, detail="Esta cuota pertenece a una compra a meses; adminístrala en Fijos y MSI.")
    validar_categoria(cursor, t.categoria_id)

    nueva_fecha = t.fecha if t.fecha is not None else trans["fecha"]
    nuevo_monto = t.monto if t.monto is not None else trans["monto"]
    nuevo_tipo = t.tipo if t.tipo is not None else trans["tipo"]
    nueva_desc = t.descripcion if t.descripcion is not None else trans["descripcion"]
    nueva_cat = t.categoria_id if t.categoria_id is not None else trans["categoria_id"]

    try:
        if trans["apartado_id"] is None:
            if trans["tipo"] == 'ingreso' or nuevo_tipo == 'ingreso':
                validar_edicion_transaccion(cursor, id_transaccion, nueva_fecha, nuevo_monto, nuevo_tipo)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    cursor.execute(
        """
        UPDATE transacciones 
        SET fecha = ?, monto = ?, tipo = ?, categoria_id = ?, descripcion = ?
        WHERE id = ?
        """,
        (nueva_fecha, nuevo_monto, nuevo_tipo, nueva_cat, nueva_desc, id_transaccion)
    )
    conn.commit()
    return {"mensaje": "Transacción actualizada con éxito"}

@router.get("/api/resumen", summary="Resumen patrimonial consolidado del tenant")
def obtener_resumen(moneda_base: str = "MXN", conn: sqlite3.Connection = Depends(get_tenant_db)):
    hoy = ahora_local().date()
    materializar_recurrentes(conn, hoy)
    tasa_usd_mxn = obtener_tasas_cambio().get("MXN", 18.40)
    return calcular_resumen(conn, moneda_base, tasa_usd_mxn, hoy)

@router.get("/api/resumen-mensual", summary="Historial agrupado por mes")
def resumen_mensual(conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT strftime('%Y-%m', fecha) as mes,
               SUM(CASE WHEN tipo='ingreso' THEN monto ELSE 0 END) as ingresos,
               SUM(CASE WHEN tipo='gasto' THEN monto ELSE 0 END) as gastos
        FROM transacciones
        GROUP BY mes
        ORDER BY mes DESC
    """)
    filas = cursor.fetchall()
    return [dict(fila) for fila in filas]

@router.get("/api/transacciones", summary="Listar todas las transacciones del tenant")
def listar_transacciones(conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT t.id, t.fecha, t.monto, t.tipo, t.descripcion, t.categoria_id,
               c.nombre AS categoria, t.recurrente_id, t.apartado_id,
               r.presupuesto_tarjeta_id, pt.nombre AS tarjeta_nombre, pt.monto AS tarjeta_monto_total
        FROM transacciones t
        LEFT JOIN categorias c ON c.id = t.categoria_id
        LEFT JOIN recurrentes r ON r.id = t.recurrente_id
        LEFT JOIN presupuesto_tarjetas pt ON pt.id = r.presupuesto_tarjeta_id
        ORDER BY t.fecha DESC, t.id DESC
        """
    )
    filas = cursor.fetchall()
    return [dict(fila) for fila in filas]


# =============================================================================
# CATEGORÍAS Y GASTO POR CATEGORÍA
# =============================================================================

@router.get("/api/categorias", summary="Listar categorías")
def listar_categorias(conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    filas = cursor.execute("SELECT id, nombre, tipo FROM categorias ORDER BY tipo, nombre").fetchall()
    return [dict(f) for f in filas]


@router.post("/api/categorias", status_code=201, summary="Crear categoría")
def crear_categoria(c: CategoriaCreate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    nombre = c.nombre.strip()
    if not nombre:
        raise HTTPException(status_code=400, detail="El nombre de la categoría no puede estar vacío.")
    if cursor.execute("SELECT 1 FROM categorias WHERE LOWER(nombre) = LOWER(?) AND tipo = ?", (nombre, c.tipo)).fetchone():
        raise HTTPException(status_code=400, detail="Ya existe una categoría con ese nombre para este tipo.")
    cursor.execute("INSERT INTO categorias (nombre, tipo) VALUES (?, ?)", (nombre, c.tipo))
    conn.commit()
    return {"id": cursor.lastrowid, "nombre": nombre, "tipo": c.tipo}


@router.get("/api/gastos-por-categoria", summary="Gastos del mes agrupados por categoría")
def gastos_por_categoria(mes: Optional[str] = None, moneda_base: str = "MXN",
                         conn: sqlite3.Connection = Depends(get_tenant_db)):
    hoy = ahora_local()
    mes = mes or f"{hoy.year:04d}-{hoy.month:02d}"
    if not re.match(r"^\d{4}-\d{2}$", mes):
        raise HTTPException(status_code=400, detail="El mes debe tener formato YYYY-MM.")
    base = moneda_base.upper()
    tasa = obtener_tasas_cambio().get("MXN", 18.40)
    cursor = conn.cursor()
    filas = cursor.execute(
        """
        SELECT COALESCE(c.nombre, 'Sin categoría') AS categoria, SUM(t.monto) AS total
        FROM transacciones t
        LEFT JOIN categorias c ON c.id = t.categoria_id
        WHERE t.tipo = 'gasto' AND t.apartado_id IS NULL AND strftime('%Y-%m', t.fecha) = ?
        GROUP BY t.categoria_id
        ORDER BY total DESC
        """,
        (mes,)
    ).fetchall()
    categorias = [
        {"categoria": f["categoria"], "total": round(convertir(float(f["total"] or 0), "MXN", base, tasa), 2)}
        for f in filas
    ]
    return {
        "mes": mes,
        "moneda_base": base,
        "total": round(sum(c["total"] for c in categorias), 2),
        "categorias": categorias,
    }
