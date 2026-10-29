"""routers/recurrentes.py - Ingresos y gastos fijos, y compras a meses (MSI)."""

import sqlite3
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from finanzas_core import (
    validar_fecha_operativa,
    ahora_local,
    deuda_pendiente,
    fijos_por_periodo,
    materializar_recurrentes,
    mensualizar,
    primera_fecha_quincenal,
    proxima_fecha_vigente,
    saltar_recurrente_core,
    validar_sustentabilidad_gasto_fijo,
)
from routers.movimientos import validar_categoria
from security import get_tenant_db

router = APIRouter()


class SaltarPagoRequest(BaseModel):
    contabilizar_cuota: bool = False


class RecurrenteCreate(BaseModel):
    descripcion: str = Field(..., min_length=1, max_length=100)
    tipo: Literal['ingreso', 'gasto'] = 'gasto'
    frecuencia: Literal['semanal', 'quincenal', 'mensual'] = 'mensual'
    monto: Optional[float] = Field(None, gt=0, le=1e12, description="Monto por ocurrencia (fijos)")
    monto_total: Optional[float] = Field(None, gt=0, le=1e12, description="Monto total de la compra (MSI)")
    meses: Optional[int] = Field(None, ge=2, le=60, description="Número de meses (MSI)")
    fecha_inicio: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    categoria_id: Optional[int] = None
    apartado_id: Optional[int] = None
    presupuesto_tarjeta_id: Optional[int] = None

    @field_validator("fecha_inicio")
    @classmethod
    def _fecha_acotada(cls, v: str) -> str:
        return validar_fecha_operativa(v)


class RecurrenteUpdate(BaseModel):
    descripcion: Optional[str] = Field(None, min_length=1, max_length=100)
    monto: Optional[float] = Field(None, gt=0, le=1e12)
    frecuencia: Optional[str] = None
    proxima_fecha: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    apartado_id: Optional[int] = None

    @field_validator("proxima_fecha")
    @classmethod
    def _fecha_acotada(cls, v):
        return validar_fecha_operativa(v) if v is not None else v
    activo: Optional[bool] = None
    categoria_id: Optional[int] = None
    presupuesto_tarjeta_id: Optional[int] = None


def validar_apartado(cursor, apartado_id: Optional[int]) -> None:
    if apartado_id is not None:
        if not cursor.execute("SELECT 1 FROM apartados WHERE id = ?", (apartado_id,)).fetchone():
            raise HTTPException(status_code=400, detail="El apartado o meta seleccionado no existe.")


def _categoria_compras_a_meses(cursor) -> int:
    fila = cursor.execute(
        "SELECT id FROM categorias WHERE nombre = 'Compras a meses' AND tipo = 'gasto' LIMIT 1"
    ).fetchone()
    return fila["id"] if fila else 1


def _serializar(fila) -> dict:
    d = dict(fila)
    es_msi = d["total_cuotas"] is not None
    d["es_msi"] = es_msi
    d["deuda_pendiente"] = deuda_pendiente(fila)
    d["mensual"] = round(mensualizar(d["monto"], d["frecuencia"]), 2)
    d["progreso_pct"] = round(d["cuotas_generadas"] / d["total_cuotas"] * 100, 1) if es_msi else None
    d["activo"] = bool(d["activo"])
    d["apartado_id"] = d.get("apartado_id")
    d["apartado_nombre"] = d.get("apartado_nombre")
    d["presupuesto_tarjeta_id"] = d.get("presupuesto_tarjeta_id")
    d["tarjeta_nombre"] = d.get("tarjeta_nombre")
    d["tarjeta_monto_total"] = float(d.get("tarjeta_monto_total", 0.0)) if d.get("tarjeta_monto_total") is not None else None
    return d


@router.get("/api/recurrentes/periodos", summary="Fijos y compras a meses agrupados por periodo de cobro")
def listar_fijos_por_periodo(n: int = Query(3, ge=1, le=6), conn: sqlite3.Connection = Depends(get_tenant_db)):
    hoy = ahora_local().date()
    materializar_recurrentes(conn, hoy)
    conn.commit()
    return fijos_por_periodo(conn, hoy, n)


@router.get("/api/recurrentes", summary="Listar fijos y compras a meses")
def listar_recurrentes(conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    filas = cursor.execute("""
        SELECT r.*, a.nombre AS apartado_nombre, pt.nombre AS tarjeta_nombre, pt.monto AS tarjeta_monto_total
        FROM recurrentes r
        LEFT JOIN apartados a ON a.id = r.apartado_id
        LEFT JOIN presupuesto_tarjetas pt ON pt.id = r.presupuesto_tarjeta_id
        ORDER BY r.activo DESC, r.proxima_fecha ASC, r.id ASC
    """).fetchall()
    return [_serializar(f) for f in filas]


@router.post("/api/recurrentes", status_code=201, summary="Crear fijo o compra a meses")
def crear_recurrente(req: RecurrenteCreate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    try:
        fecha = date.fromisoformat(req.fecha_inicio)
    except ValueError:
        raise HTTPException(status_code=400, detail="La fecha de inicio no es válida.")

    if req.meses:
        if req.monto_total is None:
            raise HTTPException(status_code=400, detail="Una compra a meses requiere el monto total.")
        tipo, frecuencia = 'gasto', 'mensual'
        total_cuotas = req.meses
        monto = round(req.monto_total / req.meses, 2)
        monto_total = req.monto_total
        categoria_id = req.categoria_id or _categoria_compras_a_meses(cursor)
    else:
        if req.monto is None:
            raise HTTPException(status_code=400, detail="Un fijo requiere el monto por ocurrencia.")
        tipo, frecuencia = req.tipo, req.frecuencia
        total_cuotas = None
        monto = req.monto
        monto_total = None
        categoria_id = req.categoria_id or 1

    validar_categoria(cursor, categoria_id)
    validar_apartado(cursor, req.apartado_id)
    if req.apartado_id is not None and tipo == 'ingreso':
        raise HTTPException(
            status_code=400,
            detail="Solo los gastos recurrentes pueden vincularse a un sobre o meta de ahorro."
        )

    # Validar sustentabilidad financiera exclusiva de gastos fijos y compras a meses
    if tipo == 'gasto':
        try:
            validar_sustentabilidad_gasto_fijo(cursor, tipo, monto, frecuencia, total_cuotas)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    proxima = primera_fecha_quincenal(fecha) if frecuencia == 'quincenal' else fecha
    cursor.execute(
        """
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha,
                                 total_cuotas, cuotas_generadas, monto_total, categoria_id, apartado_id, presupuesto_tarjeta_id, activo)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 1)
        """,
        (req.descripcion.strip(), tipo, monto, frecuencia, fecha.day, proxima.isoformat(),
         total_cuotas, monto_total, categoria_id, req.apartado_id, req.presupuesto_tarjeta_id)
    )
    nuevo_id = cursor.lastrowid
    conn.commit()
    materializar_recurrentes(conn, ahora_local().date())
    fila = cursor.execute("""
        SELECT r.*, a.nombre AS apartado_nombre, pt.nombre AS tarjeta_nombre, pt.monto AS tarjeta_monto_total
        FROM recurrentes r
        LEFT JOIN apartados a ON a.id = r.apartado_id
        LEFT JOIN presupuesto_tarjetas pt ON pt.id = r.presupuesto_tarjeta_id
        WHERE r.id = ?
    """, (nuevo_id,)).fetchone()
    return _serializar(fila)


@router.patch("/api/recurrentes/{recurrente_id}", summary="Editar fijo o compra a meses")
def editar_recurrente(recurrente_id: int, req: RecurrenteUpdate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    fila = cursor.execute("SELECT * FROM recurrentes WHERE id = ?", (recurrente_id,)).fetchone()
    if not fila:
        raise HTTPException(status_code=404, detail="Registro no encontrado.")
    es_msi = fila["total_cuotas"] is not None
    if req.monto is not None and es_msi:
        raise HTTPException(status_code=400, detail="Una compra a meses no admite cambiar el monto por cuota.")
    validar_categoria(cursor, req.categoria_id)

    if fila["tipo"] == 'gasto':
        nuevo_monto = req.monto if req.monto is not None else float(fila["monto"])
        nueva_frec = req.frecuencia if req.frecuencia is not None else fila["frecuencia"]
        activo_efectivo = req.activo if req.activo is not None else bool(fila["activo"])
        if activo_efectivo:
            try:
                validar_sustentabilidad_gasto_fijo(
                    cursor, 'gasto', nuevo_monto, nueva_frec, fila["total_cuotas"], recurrente_id=recurrente_id
                )
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))

    elif fila["tipo"] == 'ingreso':
        nuevo_monto = req.monto if req.monto is not None else float(fila["monto"])
        nueva_frec = req.frecuencia if req.frecuencia is not None else fila["frecuencia"]
        activo_efectivo = req.activo if req.activo is not None else bool(fila["activo"])
        gastos_fijos_activos = cursor.execute("SELECT COUNT(*) FROM recurrentes WHERE tipo = 'gasto' AND activo = 1").fetchone()[0]
        if gastos_fijos_activos > 0:
            otros_ingresos = cursor.execute(
                "SELECT monto, frecuencia FROM recurrentes WHERE tipo = 'ingreso' AND activo = 1 AND total_cuotas IS NULL AND id != ?",
                (recurrente_id,)
            ).fetchall()
            if not activo_efectivo and len(otros_ingresos) == 0:
                raise HTTPException(
                    status_code=422,
                    detail="No es posible pausar este ingreso fijo porque existen gastos fijos activos que requieren de un ingreso recurrente de respaldo."
                )
            total_ingreso_mensual = sum(mensualizar(float(oi["monto"]), oi["frecuencia"]) for oi in otros_ingresos)
            if activo_efectivo:
                total_ingreso_mensual += mensualizar(nuevo_monto, nueva_frec)
            recurrentes_gastos = cursor.execute(
                "SELECT monto, frecuencia, total_cuotas, cuotas_generadas FROM recurrentes WHERE tipo = 'gasto' AND activo = 1"
            ).fetchall()
            compromisos_total = 0.0
            for rg in recurrentes_gastos:
                m_g = mensualizar(float(rg["monto"]), rg["frecuencia"])
                if rg["total_cuotas"] is not None:
                    if int(rg["cuotas_generadas"] or 0) < int(rg["total_cuotas"]):
                        compromisos_total += m_g
                else:
                    compromisos_total += m_g
            if compromisos_total - total_ingreso_mensual > 0.01:
                raise HTTPException(
                    status_code=422,
                    detail="No es posible modificar o pausar este ingreso fijo porque los gastos fijos activos superarían la capacidad recurrente."
                )

    campos, valores = [], []
    if req.descripcion is not None:
        campos.append("descripcion = ?"); valores.append(req.descripcion.strip())
    if req.monto is not None:
        campos.append("monto = ?"); valores.append(req.monto)
    if req.frecuencia is not None:
        if es_msi:
            raise HTTPException(status_code=400, detail="Una compra a meses no admite cambiar la periodicidad.")
        if req.frecuencia not in ('semanal', 'quincenal', 'mensual', 'anual'):
            raise HTTPException(status_code=400, detail="Periodicidad no válida.")
        campos.append("frecuencia = ?"); valores.append(req.frecuencia)
    if req.proxima_fecha is not None:
        try:
            nueva_fecha = date.fromisoformat(req.proxima_fecha)
        except ValueError:
            raise HTTPException(status_code=400, detail="La fecha de próximo cargo no es válida.")
        if (req.frecuencia or fila["frecuencia"]) == 'quincenal':
            nueva_fecha = primera_fecha_quincenal(nueva_fecha)  # día 15 o fin de mes, igual que al crear

        ultimo_cargo = cursor.execute(
            "SELECT MAX(fecha) as ult FROM transacciones WHERE recurrente_id = ?",
            (recurrente_id,)
        ).fetchone()
        if req.proxima_fecha != fila["proxima_fecha"] and ultimo_cargo and ultimo_cargo["ult"]:
            fecha_ult = date.fromisoformat(ultimo_cargo["ult"])
            if nueva_fecha <= fecha_ult:
                raise HTTPException(
                    status_code=400,
                    detail=f"La fecha de próximo cargo debe ser posterior al último movimiento generado ({ultimo_cargo['ult']})."
                )

        campos.append("proxima_fecha = ?"); valores.append(nueva_fecha.isoformat())
        campos.append("dia = ?"); valores.append(nueva_fecha.day)
    if "apartado_id" in req.model_fields_set:
        validar_apartado(cursor, req.apartado_id)
        if req.apartado_id is not None and fila["tipo"] == 'ingreso':
            raise HTTPException(
                status_code=400,
                detail="Solo los gastos recurrentes pueden vincularse a un sobre o meta de ahorro."
            )
        campos.append("apartado_id = ?"); valores.append(req.apartado_id)

    reactivando = req.activo is True and not bool(fila["activo"])
    if reactivando and es_msi and int(fila["cuotas_generadas"]) >= int(fila["total_cuotas"]):
        raise HTTPException(status_code=400, detail="Esta compra a meses ya fue liquidada por completo; no se puede reactivar.")
    if req.activo is not None:
        campos.append("activo = ?"); valores.append(1 if req.activo else 0)

    # Si se reactiva un recurrente pausado y no se especificó proxima_fecha,
    # avanzar proxima_fecha para evitar cobros retroactivos acumulados mientras estuvo inactivo
    if reactivando and req.proxima_fecha is None:
        hoy = ahora_local().date()
        fecha_actual = date.fromisoformat(fila["proxima_fecha"])
        if fecha_actual < hoy:
            nueva_vigente = proxima_fecha_vigente(fecha_actual, fila["frecuencia"], fila["dia"], hoy)
            campos.append("proxima_fecha = ?"); valores.append(nueva_vigente.isoformat())
            campos.append("dia = ?"); valores.append(nueva_vigente.day)
    if req.categoria_id is not None:
        campos.append("categoria_id = ?"); valores.append(req.categoria_id)
    if "presupuesto_tarjeta_id" in req.model_fields_set:
        campos.append("presupuesto_tarjeta_id = ?"); valores.append(req.presupuesto_tarjeta_id)

    if campos:
        valores.append(recurrente_id)
        cursor.execute(f"UPDATE recurrentes SET {', '.join(campos)} WHERE id = ?", valores)
        conn.commit()
        materializar_recurrentes(conn, ahora_local().date())

    fila = cursor.execute("""
        SELECT r.*, a.nombre AS apartado_nombre, pt.nombre AS tarjeta_nombre, pt.monto AS tarjeta_monto_total
        FROM recurrentes r
        LEFT JOIN apartados a ON a.id = r.apartado_id
        LEFT JOIN presupuesto_tarjetas pt ON pt.id = r.presupuesto_tarjeta_id
        WHERE r.id = ?
    """, (recurrente_id,)).fetchone()
    return _serializar(fila)


@router.delete("/api/recurrentes/{recurrente_id}", summary="Eliminar fijo o compra a meses")
def eliminar_recurrente(recurrente_id: int, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    fila_del = cursor.execute("SELECT tipo, activo FROM recurrentes WHERE id = ?", (recurrente_id,)).fetchone()
    if not fila_del:
        raise HTTPException(status_code=404, detail="Registro no encontrado.")
    if fila_del["tipo"] == 'ingreso' and bool(fila_del["activo"]):
        gastos_fijos_activos = cursor.execute("SELECT COUNT(*) FROM recurrentes WHERE tipo = 'gasto' AND activo = 1").fetchone()[0]
        if gastos_fijos_activos > 0:
            otros_ingresos = cursor.execute(
                "SELECT monto, frecuencia FROM recurrentes WHERE tipo = 'ingreso' AND activo = 1 AND total_cuotas IS NULL AND id != ?",
                (recurrente_id,)
            ).fetchall()
            if len(otros_ingresos) == 0:
                raise HTTPException(
                    status_code=422,
                    detail="No es posible eliminar este ingreso fijo porque existen compromisos de gastos fijos activos que dependen de él."
                )
            total_ingreso_mensual = sum(mensualizar(float(oi["monto"]), oi["frecuencia"]) for oi in otros_ingresos)
            recurrentes_gastos = cursor.execute(
                "SELECT monto, frecuencia, total_cuotas, cuotas_generadas FROM recurrentes WHERE tipo = 'gasto' AND activo = 1"
            ).fetchall()
            compromisos_total = 0.0
            for rg in recurrentes_gastos:
                m_g = mensualizar(float(rg["monto"]), rg["frecuencia"])
                if rg["total_cuotas"] is not None:
                    if int(rg["cuotas_generadas"] or 0) < int(rg["total_cuotas"]):
                        compromisos_total += m_g
                else:
                    compromisos_total += m_g
            if compromisos_total - total_ingreso_mensual > 0.01:
                raise HTTPException(
                    status_code=422,
                    detail="No es posible eliminar este ingreso fijo porque los gastos fijos activos superarían la capacidad recurrente."
                )
    # Las transacciones ya generadas se conservan (ON DELETE SET NULL)
    cursor.execute("DELETE FROM recurrentes WHERE id = ?", (recurrente_id,))
    conn.commit()
    return {"mensaje": "Registro eliminado. Los movimientos ya generados se conservaron."}


@router.post("/api/recurrentes/{recurrente_id}/saltar", summary="Saltar próximo ciclo de pago")
def saltar_recurrente(
    recurrente_id: int,
    req: Optional[SaltarPagoRequest] = None,
    conn: sqlite3.Connection = Depends(get_tenant_db)
):
    if isinstance(req, sqlite3.Connection):
        conn = req
        req = None
    contabilizar = req.contabilizar_cuota if req else False
    cursor = conn.cursor()
    fila_pre = cursor.execute("SELECT id FROM recurrentes WHERE id = ?", (recurrente_id,)).fetchone()
    if not fila_pre:
        raise HTTPException(status_code=404, detail="Gasto o ingreso recurrente no encontrado.")
    try:
        info = saltar_recurrente_core(cursor, recurrente_id, contabilizar_cuota=contabilizar, hoy=ahora_local().date())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    conn.commit()

    fila = cursor.execute("""
        SELECT r.*, a.nombre AS apartado_nombre, pt.nombre AS tarjeta_nombre, pt.monto AS tarjeta_monto_total
        FROM recurrentes r
        LEFT JOIN apartados a ON a.id = r.apartado_id
        LEFT JOIN presupuesto_tarjetas pt ON pt.id = r.presupuesto_tarjeta_id
        WHERE r.id = ?
    """, (recurrente_id,)).fetchone()
    if not fila:
        raise HTTPException(status_code=404, detail="El recurrente no fue encontrado tras la actualización.")

    res = _serializar(fila)
    res["detalle_salto"] = info
    return res

