"""routers/movimientos.py - Movimientos de caja y resúmenes patrimoniales."""

from fastapi import APIRouter
from fastapi import HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Literal, Optional
import re
import sqlite3

from finanzas_core import ahora_local, calcular_resumen, convertir, materializar_recurrentes
from mercado import obtener_tasas_cambio
from security import (
    get_tenant_db
)

router = APIRouter()

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
def eliminar_transaccion(id_transaccion: int, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    trans = cursor.execute("SELECT * FROM transacciones WHERE id = ?", (id_transaccion,)).fetchone()
    if not trans:
        raise HTTPException(status_code=404, detail="Transacción no encontrada")

    # Si es cuota de un recurrente, decrementar cuotas_generadas si aplica
    if trans["recurrente_id"] is not None:
        rec = cursor.execute("SELECT * FROM recurrentes WHERE id = ?", (trans["recurrente_id"],)).fetchone()
        if rec and rec["cuotas_generadas"] > 0:
            cursor.execute(
                "UPDATE recurrentes SET cuotas_generadas = MAX(0, cuotas_generadas - 1) WHERE id = ?",
                (trans["recurrente_id"],)
            )

    cursor.execute("DELETE FROM transacciones WHERE id = ?", (id_transaccion,))
    conn.commit()
    return {"mensaje": "Transacción eliminada con éxito"}

@router.put("/api/transacciones/{id_transaccion}", summary="Editar movimiento")
def actualizar_transaccion(id_transaccion: int, t: TransaccionUpdate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    trans = cursor.execute("SELECT * FROM transacciones WHERE id = ?", (id_transaccion,)).fetchone()
    if not trans:
        raise HTTPException(status_code=404, detail="Transacción no encontrada")

    if trans["apartado_id"] is not None:
        if (t.monto is not None and round(float(t.monto), 2) != round(float(trans["monto"]), 2)) or (t.tipo is not None and t.tipo != trans["tipo"]):
            raise HTTPException(
                status_code=400,
                detail="No puedes alterar el monto ni el tipo de un movimiento vinculado a un apartado de ahorro. Utiliza la sección Metas / Apartados."
            )
    if es_cuota_msi(cursor, trans["recurrente_id"]):
        raise HTTPException(status_code=400, detail="Esta cuota pertenece a una compra a meses; adminístrala en Fijos y MSI.")
    validar_categoria(cursor, t.categoria_id)

    nueva_fecha = t.fecha if t.fecha is not None else trans["fecha"]
    nuevo_monto = t.monto if t.monto is not None else trans["monto"]
    nuevo_tipo = t.tipo if t.tipo is not None else trans["tipo"]
    nueva_desc = t.descripcion if t.descripcion is not None else trans["descripcion"]
    nueva_cat = t.categoria_id if t.categoria_id is not None else trans["categoria_id"]

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
               c.nombre AS categoria, t.recurrente_id, t.apartado_id
        FROM transacciones t
        LEFT JOIN categorias c ON c.id = t.categoria_id
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
