import json
import sqlite3
from typing import List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from finanzas_core import (
    mensualizar,
    convertir_frecuencias,
    convertir_de_mensual,
    generar_subperiodos_default,
    calcular_distribucion_subperiodos,
)
from security import get_tenant_db

router = APIRouter()


# -----------------------------------------------------------------------------
# Modelos Pydantic
# -----------------------------------------------------------------------------

class SubperiodoItem(BaseModel):
    id: str = Field(..., max_length=50)
    nombre: str = Field(..., min_length=1, max_length=60)
    porcentaje: float = Field(..., ge=0, le=100)


class ConfigPresupuestoUpdate(BaseModel):
    ingreso_base: float = Field(..., ge=0, le=1e12, description="Monto del ingreso fijo por periodo")
    frecuencia: Literal['semanal', 'quincenal', 'mensual'] = Field(..., description="Frecuencia del ingreso")
    subdivision_tipo: Optional[Literal['ninguna', 'quincenas', 'semanas', 'personalizada']] = 'ninguna'
    subdivision_config: Optional[List[SubperiodoItem]] = None



class TarjetaPresupuestoCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    icono: str = Field("wallet", max_length=50)
    color: str = Field("gold", max_length=30)
    porcentaje: Optional[float] = Field(None, ge=0, le=100)
    monto: Optional[float] = Field(None, ge=0, le=1e12)
    notas: Optional[str] = Field("", max_length=255)


class TarjetaPresupuestoUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=100)
    icono: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=30)
    porcentaje: Optional[float] = Field(None, ge=0, le=100)
    monto: Optional[float] = Field(None, ge=0, le=1e12)
    notas: Optional[str] = Field(None, max_length=255)
    orden: Optional[int] = None


class PlantillaRequest(BaseModel):
    plantilla: Literal['50-30-20', 'inversionista']


# -----------------------------------------------------------------------------
# Funciones Auxiliares
# -----------------------------------------------------------------------------



def obtener_config_presupuesto(cursor: sqlite3.Cursor) -> dict:
    """Obtiene o inicializa la fila de configuración única id=1."""
    row = cursor.execute("SELECT * FROM presupuesto_config WHERE id = 1").fetchone()
    if not row:
        cursor.execute("""
            INSERT OR IGNORE INTO presupuesto_config (id, ingreso_base, frecuencia, subdivision_tipo, subdivision_config)
            VALUES (1, 0.0, 'mensual', 'ninguna', '[]')
        """)
        return {
            "ingreso_base": 0.0,
            "frecuencia": "mensual",
            "subdivision_tipo": "ninguna",
            "subdivision_config": [],
            "actualizado_en": None
        }

    d = dict(row)
    sub_tipo = d.get("subdivision_tipo") or "ninguna"
    raw_config = d.get("subdivision_config") or "[]"
    try:
        parsed_config = json.loads(raw_config) if isinstance(raw_config, str) else (raw_config or [])
    except Exception:
        parsed_config = []

    frecuencia = d.get("frecuencia") or "mensual"
    ingreso_base = float(d.get("ingreso_base", 0.0))

    if sub_tipo in ('quincenas', 'semanas') and not parsed_config:
        parsed_config = generar_subperiodos_default(frecuencia, sub_tipo)

    subperiodos_con_monto = calcular_distribucion_subperiodos(ingreso_base, parsed_config) if sub_tipo != 'ninguna' else []

    return {
        "ingreso_base": ingreso_base,
        "frecuencia": frecuencia,
        "subdivision_tipo": sub_tipo,
        "subdivision_config": subperiodos_con_monto,
        "actualizado_en": d.get("actualizado_en")
    }


def detectar_fijos_recurrentes(cursor: sqlite3.Cursor, frecuencia_destino: str) -> dict:
    """Detecta nómina y gastos fijos registrados en la tabla 'recurrentes'."""
    filas = cursor.execute("SELECT * FROM recurrentes WHERE activo = 1").fetchall()
    ingreso_mensual = 0.0
    gasto_mensual = 0.0
    msi_mensual = 0.0

    for r in filas:
        m = mensualizar(float(r["monto"]), r["frecuencia"])
        if r["tipo"] == 'ingreso' and r["total_cuotas"] is None:
            ingreso_mensual += m
        elif r["tipo"] == 'gasto':
            if r["total_cuotas"] is not None:
                if int(r["cuotas_generadas"]) < int(r["total_cuotas"]):
                    msi_mensual += m
            else:
                gasto_mensual += m

    return {
        "ingreso_fijo_periodo": convertir_de_mensual(ingreso_mensual, frecuencia_destino),
        "gasto_fijo_periodo": convertir_de_mensual(gasto_mensual, frecuencia_destino),
        "cuota_msi_periodo": convertir_de_mensual(msi_mensual, frecuencia_destino),
        "ingreso_fijo_mensual": round(ingreso_mensual, 2),
        "gasto_fijo_mensual": round(gasto_mensual, 2),
        "cuota_msi_mensual": round(msi_mensual, 2)
    }


def serializar_tarjeta(t: sqlite3.Row, ingreso_base: float, frecuencia: str, subperiodos_config: list = None) -> dict:
    d = dict(t)
    monto = float(d["monto"])
    pct = float(d["porcentaje"])
    eq = convertir_frecuencias(monto, frecuencia)
    d["monto"] = round(monto, 2)
    d["porcentaje"] = round(pct, 2)
    d["equivalentes"] = eq
    if subperiodos_config:
        d["subdivision"] = calcular_distribucion_subperiodos(monto, subperiodos_config)
    else:
        d["subdivision"] = []
    return d


# -----------------------------------------------------------------------------
# Endpoints de la API
# -----------------------------------------------------------------------------

@router.get("/api/presupuesto", summary="Obtener estado completo del presupuesto")
def get_presupuesto(conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    cfg = obtener_config_presupuesto(cursor)
    frecuencia = cfg["frecuencia"]
    ingreso_base = float(cfg["ingreso_base"])
    cfg["equivalentes"] = convertir_frecuencias(ingreso_base, frecuencia)

    fijos = detectar_fijos_recurrentes(cursor, frecuencia)

    subperiodos = cfg["subdivision_config"] if cfg["subdivision_tipo"] != 'ninguna' else []
    filas_tarjetas = cursor.execute("SELECT * FROM presupuesto_tarjetas ORDER BY orden ASC, id ASC").fetchall()
    tarjetas = [serializar_tarjeta(t, ingreso_base, frecuencia, subperiodos) for t in filas_tarjetas]

    total_monto = sum(t["monto"] for t in tarjetas)
    total_pct = sum(t["porcentaje"] for t in tarjetas)
    disponible_monto = max(0.0, round(ingreso_base - total_monto, 2))
    disponible_pct = max(0.0, round(100.0 - total_pct, 2)) if ingreso_base > 0 else 0.0

    return {
        "config": cfg,
        "fijos_detectados": fijos,
        "tarjetas": tarjetas,
        "resumen": {
            "total_asignado_monto": round(total_monto, 2),
            "total_asignado_pct": round(total_pct, 2),
            "disponible_monto": disponible_monto,
            "disponible_pct": disponible_pct,
            "excedido": total_pct > 100.0 or (ingreso_base > 0 and total_monto > ingreso_base)
        }
    }


@router.put("/api/presupuesto/config", summary="Actualizar ingreso fijo base, frecuencia y sub-división")
def update_config_presupuesto(req: ConfigPresupuestoUpdate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    ingreso_nuevo = round(float(req.ingreso_base), 2)
    frecuencia_nueva = req.frecuencia
    sub_tipo = req.subdivision_tipo or 'ninguna'

    if frecuencia_nueva == 'semanal':
        sub_tipo = 'ninguna'
    elif frecuencia_nueva == 'quincenal' and sub_tipo == 'quincenas':
        sub_tipo = 'semanas'

    if sub_tipo == 'ninguna':
        config_list = []
    elif req.subdivision_config:
        suma_pct = sum(float(item.porcentaje) for item in req.subdivision_config)
        if abs(suma_pct - 100.0) > 0.2:
            raise HTTPException(status_code=400, detail=f"La suma de porcentajes de los sub-periodos debe ser exactamente 100% (suma actual: {round(suma_pct, 1)}%)")
        config_list = [item.dict() for item in req.subdivision_config]
    else:
        config_list = generar_subperiodos_default(frecuencia_nueva, sub_tipo)

    cursor.execute("""
        UPDATE presupuesto_config
        SET ingreso_base = ?, frecuencia = ?, subdivision_tipo = ?, subdivision_config = ?, actualizado_en = CURRENT_TIMESTAMP
        WHERE id = 1
    """, (ingreso_nuevo, frecuencia_nueva, sub_tipo, json.dumps(config_list)))

    # Recalcular los montos de las tarjetas según sus porcentajes
    filas = cursor.execute("SELECT id, porcentaje FROM presupuesto_tarjetas").fetchall()
    for fila in filas:
        t_id = fila["id"]
        pct = float(fila["porcentaje"])
        monto_recalc = round(ingreso_nuevo * (pct / 100.0), 2) if ingreso_nuevo > 0 else 0.0
        cursor.execute("UPDATE presupuesto_tarjetas SET monto = ? WHERE id = ?", (monto_recalc, t_id))

    conn.commit()
    return get_presupuesto(conn)



@router.post("/api/presupuesto/importar-fijos", summary="Importar nómina y gastos de Fijos y MSI")
def importar_fijos_a_presupuesto(conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    cfg = obtener_config_presupuesto(cursor)
    frecuencia = cfg["frecuencia"]
    fijos = detectar_fijos_recurrentes(cursor, frecuencia)

    ingreso_importado = fijos["ingreso_fijo_periodo"]
    gasto_importado = fijos["gasto_fijo_periodo"]
    msi_importado = fijos["cuota_msi_periodo"]

    # Si hay ingreso fijo registrado, actualizar ingreso_base
    if ingreso_importado > 0:
        cursor.execute("""
            UPDATE presupuesto_config
            SET ingreso_base = ?, actualizado_en = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (ingreso_importado,))
        ingreso_actual = ingreso_importado
    else:
        ingreso_actual = cfg["ingreso_base"]

    # Crear o actualizar tarjeta "Gastos Fijos"
    if gasto_importado > 0:
        pct_gasto = round((gasto_importado / ingreso_actual) * 100.0, 2) if ingreso_actual > 0 else 0.0
        existente = cursor.execute("SELECT id FROM presupuesto_tarjetas WHERE nombre LIKE '%Gastos Fijos%' LIMIT 1").fetchone()
        if existente:
            cursor.execute("""
                UPDATE presupuesto_tarjetas
                SET monto = ?, porcentaje = ?, notas = 'Importado de Fijos y MSI'
                WHERE id = ?
            """, (gasto_importado, pct_gasto, existente["id"]))
        else:
            cursor.execute("""
                INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden)
                VALUES ('Gastos Fijos', 'home', 'blue', ?, ?, 'Renta, servicios y suscripciones fijas', 10)
            """, (pct_gasto, gasto_importado))

    # Crear o actualizar tarjeta "Compras a Meses (MSI)"
    if msi_importado > 0:
        pct_msi = round((msi_importado / ingreso_actual) * 100.0, 2) if ingreso_actual > 0 else 0.0
        existente_msi = cursor.execute("SELECT id FROM presupuesto_tarjetas WHERE nombre LIKE '%Meses%' OR nombre LIKE '%MSI%' LIMIT 1").fetchone()
        if existente_msi:
            cursor.execute("""
                UPDATE presupuesto_tarjetas
                SET monto = ?, porcentaje = ?, notas = 'Cuotas mensuales activas'
                WHERE id = ?
            """, (msi_importado, pct_msi, existente_msi["id"]))
        else:
            cursor.execute("""
                INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden)
                VALUES ('Compras a Meses (MSI)', 'credit-card', 'amber', ?, ?, 'Cuotas mensuales de MSI activas', 20)
            """, (pct_msi, msi_importado))

    conn.commit()
    return get_presupuesto(conn)


@router.post("/api/presupuesto/plantilla", summary="Aplicar plantilla predeterminada de presupuesto")
def aplicar_plantilla(req: PlantillaRequest, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    cfg = obtener_config_presupuesto(cursor)
    ingreso = float(cfg["ingreso_base"])

    # Limpiar tarjetas previas
    cursor.execute("DELETE FROM presupuesto_tarjetas")

    if req.plantilla == '50-30-20':
        plantillas = [
            ("Gastos Fijos y Necesidades", "home", "blue", 50.0, "Vivienda, comida, transporte y servicios", 1),
            ("Estilo de Vida y Ocio", "smile", "purple", 30.0, "Salidas, compras personales, streaming", 2),
            ("Inversión en ETFs y Ahorro", "trending-up", "emerald", 20.0, "Portafolio bursátil y fondos", 3),
        ]
    else:  # 'inversionista'
        plantillas = [
            ("Gastos Fijos", "home", "blue", 50.0, "Costos de vida esenciales", 1),
            ("Inversión en ETFs", "trending-up", "emerald", 25.0, "VOO, QQQ, Fibras y bolsa", 2),
            ("Fondo Renta Fija / CETES", "shield", "gold", 15.0, "CETES, Nu, pagarés y fondo de emergencia", 3),
            ("Libre / Ocio", "smile", "purple", 10.0, "Gastos personales flexibles", 4),
        ]

    for nombre, icono, color, pct, notas, orden in plantillas:
        monto = round(ingreso * (pct / 100.0), 2) if ingreso > 0 else 0.0
        cursor.execute("""
            INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (nombre, icono, color, pct, monto, notas, orden))

    conn.commit()
    return get_presupuesto(conn)


@router.post("/api/presupuesto/tarjetas", status_code=201, summary="Crear tarjeta de presupuesto")
def crear_tarjeta(req: TarjetaPresupuestoCreate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    cfg = obtener_config_presupuesto(cursor)
    ingreso = float(cfg["ingreso_base"])

    monto = req.monto
    porcentaje = req.porcentaje

    if porcentaje is not None and monto is None:
        monto = round(ingreso * (porcentaje / 100.0), 2) if ingreso > 0 else 0.0
    elif monto is not None and porcentaje is None:
        porcentaje = round((monto / ingreso) * 100.0, 2) if ingreso > 0 else 0.0
    elif porcentaje is None and monto is None:
        porcentaje = 0.0
        monto = 0.0

    # Determinar orden consecutivo
    max_orden = cursor.execute("SELECT COALESCE(MAX(orden), 0) FROM presupuesto_tarjetas").fetchone()[0]
    cursor.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (req.nombre.strip(), req.icono, req.color, round(porcentaje, 2), round(monto, 2), req.notas or "", max_orden + 1))

    tarjeta_id = cursor.lastrowid
    conn.commit()

    subperiodos = cfg["subdivision_config"] if cfg.get("subdivision_tipo") != 'ninguna' else []
    fila = cursor.execute("SELECT * FROM presupuesto_tarjetas WHERE id = ?", (tarjeta_id,)).fetchone()
    return serializar_tarjeta(fila, ingreso, cfg["frecuencia"], subperiodos)


@router.patch("/api/presupuesto/tarjetas/{tarjeta_id}", summary="Actualizar tarjeta de presupuesto")
def actualizar_tarjeta(tarjeta_id: int, req: TarjetaPresupuestoUpdate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    fila = cursor.execute("SELECT * FROM presupuesto_tarjetas WHERE id = ?", (tarjeta_id,)).fetchone()
    if not fila:
        raise HTTPException(status_code=404, detail="Tarjeta de presupuesto no encontrada.")

    cfg = obtener_config_presupuesto(cursor)
    ingreso = float(cfg["ingreso_base"])

    campos, valores = [], []
    if req.nombre is not None:
        campos.append("nombre = ?"); valores.append(req.nombre.strip())
    if req.icono is not None:
        campos.append("icono = ?"); valores.append(req.icono)
    if req.color is not None:
        campos.append("color = ?"); valores.append(req.color)
    if req.notas is not None:
        campos.append("notas = ?"); valores.append(req.notas)
    if req.orden is not None:
        campos.append("orden = ?"); valores.append(req.orden)

    # Si se actualiza monto o porcentaje, sincronizar el otro campo
    if req.porcentaje is not None:
        pct = round(req.porcentaje, 2)
        monto = round(ingreso * (pct / 100.0), 2) if ingreso > 0 else 0.0
        campos.extend(["porcentaje = ?", "monto = ?"])
        valores.extend([pct, monto])
    elif req.monto is not None:
        monto = round(req.monto, 2)
        pct = round((monto / ingreso) * 100.0, 2) if ingreso > 0 else 0.0
        campos.extend(["monto = ?", "porcentaje = ?"])
        valores.extend([monto, pct])

    if campos:
        valores.append(tarjeta_id)
        cursor.execute(f"UPDATE presupuesto_tarjetas SET {', '.join(campos)} WHERE id = ?", valores)
        conn.commit()

    subperiodos = cfg["subdivision_config"] if cfg.get("subdivision_tipo") != 'ninguna' else []
    fila_actualizada = cursor.execute("SELECT * FROM presupuesto_tarjetas WHERE id = ?", (tarjeta_id,)).fetchone()
    return serializar_tarjeta(fila_actualizada, ingreso, cfg["frecuencia"], subperiodos)



@router.delete("/api/presupuesto/tarjetas/{tarjeta_id}", summary="Eliminar tarjeta de presupuesto")
def eliminar_tarjeta(tarjeta_id: int, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    if not cursor.execute("SELECT 1 FROM presupuesto_tarjetas WHERE id = ?", (tarjeta_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada.")

    cursor.execute("DELETE FROM presupuesto_tarjetas WHERE id = ?", (tarjeta_id,))
    conn.commit()
    return {"mensaje": "Tarjeta eliminada con éxito."}
