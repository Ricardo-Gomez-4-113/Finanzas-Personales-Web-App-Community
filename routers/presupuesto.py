import json
import sqlite3
from datetime import date
from typing import List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from finanzas_core import (
    mensualizar,
    convertir_frecuencias,
    convertir_de_mensual,
    generar_subperiodos_default,
    calcular_distribucion_subperiodos,
    calcular_distribucion_tarjeta_asimetrica,
    sugerir_icono_color,
    detectar_fijos_recurrentes_core as detectar_fijos_recurrentes,
    sincronizar_fijos_presupuesto_core,
    calcular_fecha_inicio_exportacion,
    exportar_presupuesto_a_fijos_core,
    materializar_recurrentes,
    ahora_local,
    unidades_activas,
    validar_dias_unidades,
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
    dia_pago: Optional[int] = Field(None, ge=1, le=31, description="Día del mes de cobro del ingreso fijo")
    subdivision_tipo: Optional[Literal['ninguna', 'quincenas', 'semanas', 'personalizada']] = 'ninguna'
    subdivision_config: Optional[List[SubperiodoItem]] = None



class TarjetaPresupuestoCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    icono: str = Field("wallet", max_length=50)
    color: str = Field("gold", max_length=30, pattern=r"^[a-z][a-z-]{0,29}$")
    porcentaje: Optional[float] = Field(None, ge=0, le=100)
    monto: Optional[float] = Field(None, ge=0, le=1e12)
    notas: Optional[str] = Field("", max_length=255)
    dia_pago: Optional[int] = Field(None, ge=1, le=31, description="Día del mes programado para el cargo general (1 a 31)")
    distribucion_subperiodos: Optional[List[dict]] = None
    unidad_orden: Optional[int] = Field(None, ge=1, le=4, description="Pago al que pertenece la tarjeta; vacío para el tablero general")


class UnidadPagoUpdate(BaseModel):
    dia_pago: Optional[int] = Field(None, ge=1, le=31, description="Día del mes en que se cobra este pago")


class TarjetaPresupuestoUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=100)
    icono: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=30, pattern=r"^[a-z][a-z-]{0,29}$")
    porcentaje: Optional[float] = Field(None, ge=0, le=100)
    monto: Optional[float] = Field(None, ge=0, le=1e12)
    notas: Optional[str] = Field(None, max_length=255)
    orden: Optional[int] = None
    dia_pago: Optional[int] = Field(None, ge=1, le=31, description="Día del mes programado para el cargo general (1 a 31)")
    distribucion_subperiodos: Optional[List[dict]] = None


class PlantillaRequest(BaseModel):
    plantilla: Literal['50-30-20', 'inversionista']


class ImportarFijosRequest(BaseModel):
    modo: Optional[Literal['individual', 'categoria', 'unificado']] = 'individual'
    recurrentes_ids: Optional[List[int]] = None
    actualizar_ingreso: bool = True
    reemplazar: bool = False


class ExportarFijosRequest(BaseModel):
    tarjetas_ids: Optional[List[int]] = None
    incluir_ingreso: bool = True
    inicio_cargos: Optional[Literal['natural', 'siguiente_quincena', 'proximo_mes', 'personalizada']] = 'natural'
    fecha_base: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


# -----------------------------------------------------------------------------
# Funciones Auxiliares
# -----------------------------------------------------------------------------



def obtener_config_presupuesto(cursor: sqlite3.Cursor) -> dict:
    """Obtiene o inicializa la fila de configuración única id=1."""
    row = cursor.execute("SELECT * FROM presupuesto_config WHERE id = 1").fetchone()
    if not row:
        cursor.execute("""
            INSERT OR IGNORE INTO presupuesto_config (id, ingreso_base, frecuencia, dia_pago, subdivision_tipo, subdivision_config)
            VALUES (1, 0.0, 'mensual', NULL, 'ninguna', '[]')
        """)
        return {
            "ingreso_base": 0.0,
            "frecuencia": "mensual",
            "dia_pago": None,
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
    dia_pago = d.get("dia_pago")

    if sub_tipo in ('quincenas', 'semanas') and not parsed_config:
        parsed_config = generar_subperiodos_default(frecuencia, sub_tipo)

    subperiodos_con_monto = calcular_distribucion_subperiodos(ingreso_base, parsed_config) if sub_tipo != 'ninguna' else []

    return {
        "ingreso_base": ingreso_base,
        "frecuencia": frecuencia,
        "dia_pago": dia_pago,
        "subdivision_tipo": sub_tipo,
        "subdivision_config": subperiodos_con_monto,
        "actualizado_en": d.get("actualizado_en")
    }





def serializar_tarjeta(t: sqlite3.Row, ingreso_base: float, frecuencia: str, subperiodos_config: list = None) -> dict:
    d = dict(t)
    monto = float(d["monto"])
    pct = float(d["porcentaje"])
    eq = convertir_frecuencias(monto, frecuencia)
    d["monto"] = round(monto, 2)
    d["porcentaje"] = round(pct, 2)
    d["equivalentes"] = eq
    custom_dist = d.get("distribucion_subperiodos")
    if isinstance(custom_dist, str):
        try:
            d["distribucion_subperiodos"] = json.loads(custom_dist)
        except Exception:
            d["distribucion_subperiodos"] = None
    if subperiodos_config:
        d["subdivision"] = calcular_distribucion_tarjeta_asimetrica(monto, subperiodos_config, d.get("distribucion_subperiodos"))
    else:
        d["subdivision"] = []
    return d


def _estado_unidades(cursor: sqlite3.Cursor, cfg: dict) -> list:
    """Los cuatro pagos del presupuesto. Solo los activos según la frecuencia tienen ingreso y libre;
    los inactivos no se muestran, pero sus tarjetas se conservan (y se impide cambiar a una frecuencia que las dejaría fuera)."""
    activas = unidades_activas(cfg["frecuencia"])
    ingreso = float(cfg["ingreso_base"])
    filas_unidades = cursor.execute("SELECT orden, dia_pago FROM presupuesto_unidades ORDER BY orden").fetchall()
    filas_tarjetas = cursor.execute(
        "SELECT * FROM presupuesto_tarjetas WHERE unidad_orden IS NOT NULL ORDER BY orden ASC, id ASC"
    ).fetchall()
    unidades = []
    for u in filas_unidades:
        orden = u["orden"]
        activa = orden <= activas
        tarjetas = [serializar_tarjeta(t, ingreso, cfg["frecuencia"]) for t in filas_tarjetas if t["unidad_orden"] == orden]
        total = round(sum(t["monto"] for t in tarjetas), 2)
        unidades.append({
            "orden": orden,
            "activa": activa,
            "dia_pago": u["dia_pago"],
            "ingreso": round(ingreso, 2) if activa else 0.0,
            "total_asignado": total,
            "libre": max(0.0, round(ingreso - total, 2)) if activa else 0.0,
            "excedido": activa and total > ingreso + 0.005,
            "tarjetas": tarjetas,
        })
    return unidades


def _validar_capacidad_unidad(cursor: sqlite3.Cursor, cfg: dict, orden: int, monto_nuevo: float, excluir_id: Optional[int] = None):
    """Las tarjetas de un pago no pueden sumar más que el ingreso de ese pago."""
    ingreso = float(cfg["ingreso_base"])
    total = cursor.execute(
        "SELECT COALESCE(SUM(monto), 0) FROM presupuesto_tarjetas WHERE unidad_orden = ? AND id IS NOT ?",
        (orden, excluir_id),
    ).fetchone()[0]
    if float(total) + monto_nuevo > ingreso + 0.005:
        restante = max(0.0, round(ingreso - float(total), 2))
        raise HTTPException(
            status_code=422,
            detail=f"El pago {orden} solo tiene {restante:,.2f} disponibles de su ingreso; esta tarjeta suma {monto_nuevo:,.2f}.",
        )


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
    filas_tarjetas = cursor.execute(
        "SELECT * FROM presupuesto_tarjetas WHERE unidad_orden IS NULL ORDER BY orden ASC, id ASC"
    ).fetchall()
    tarjetas = [serializar_tarjeta(t, ingreso_base, frecuencia, subperiodos) for t in filas_tarjetas]

    total_monto = sum(t["monto"] for t in tarjetas)
    total_pct = sum(t["porcentaje"] for t in tarjetas)
    disponible_monto = max(0.0, round(ingreso_base - total_monto, 2))
    disponible_pct = max(0.0, round(100.0 - total_pct, 2)) if ingreso_base > 0 else 0.0

    return {
        "config": cfg,
        "fijos_detectados": fijos,
        "tarjetas": tarjetas,
        "unidades": _estado_unidades(cursor, cfg),
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

    # Una frecuencia más corta no puede dejar tarjetas huérfanas en pagos que ya no existen
    tarjetas_fuera = cursor.execute(
        "SELECT COUNT(*) FROM presupuesto_tarjetas WHERE unidad_orden > ?", (unidades_activas(frecuencia_nueva),)
    ).fetchone()[0]
    if tarjetas_fuera:
        raise HTTPException(
            status_code=422,
            detail=f"Hay tarjetas en pagos que la frecuencia {frecuencia_nueva} no usa. Muévelas o bórralas antes de cambiarla.",
        )

    if sub_tipo == 'ninguna':
        config_list = []
    elif req.subdivision_config:
        suma_pct = sum(float(item.porcentaje) for item in req.subdivision_config)
        if abs(suma_pct - 100.0) > 0.2:
            raise HTTPException(status_code=400, detail=f"La suma de porcentajes de los sub-periodos debe ser exactamente 100% (suma actual: {round(suma_pct, 1)}%)")
        config_list = [item.dict() for item in req.subdivision_config]
    else:
        config_list = generar_subperiodos_default(frecuencia_nueva, sub_tipo)

    dia_pago = req.dia_pago
    if req.dia_pago is not None:
        try:
            dia_pago = int(req.dia_pago)
            if dia_pago < 1 or dia_pago > 31:
                dia_pago = None
        except (ValueError, TypeError):
            dia_pago = None

    cursor.execute("""
        UPDATE presupuesto_config
        SET ingreso_base = ?, frecuencia = ?, dia_pago = ?, subdivision_tipo = ?, subdivision_config = ?, actualizado_en = CURRENT_TIMESTAMP
        WHERE id = 1
    """, (ingreso_nuevo, frecuencia_nueva, dia_pago, sub_tipo, json.dumps(config_list)))

    # Preservar el monto exacto de cada tarjeta y actualizar su porcentaje respecto al nuevo ingreso_base
    filas = cursor.execute("SELECT id, porcentaje, monto FROM presupuesto_tarjetas").fetchall()
    for fila in filas:
        t_id = fila["id"]
        monto_actual = float(fila["monto"] or 0.0)
        pct = float(fila["porcentaje"] or 0.0)
        if monto_actual > 0:
            pct_recalc = round((monto_actual / ingreso_nuevo) * 100.0, 2) if ingreso_nuevo > 0 else 0.0
            cursor.execute("UPDATE presupuesto_tarjetas SET porcentaje = ? WHERE id = ?", (pct_recalc, t_id))
        else:
            monto_recalc = round(ingreso_nuevo * (pct / 100.0), 2) if ingreso_nuevo > 0 else 0.0
            cursor.execute("UPDATE presupuesto_tarjetas SET monto = ? WHERE id = ?", (monto_recalc, t_id))

    # Sincronizar nómina en recurrentes si existe
    rec_nomina = cursor.execute("""
        SELECT id, dia FROM recurrentes
        WHERE tipo = 'ingreso' AND (presupuesto_tarjeta_id IS NOT NULL OR LOWER(descripcion) LIKE '%nomina%' OR LOWER(descripcion) LIKE '%nómina%' OR LOWER(descripcion) LIKE '%ingreso base%')
        LIMIT 1
    """).fetchone()
    if rec_nomina:
        dia_fijo = dia_pago if dia_pago is not None else rec_nomina["dia"]
        cursor.execute("""
            UPDATE recurrentes
            SET monto = ?, frecuencia = ?, dia = COALESCE(?, dia)
            WHERE id = ?
        """, (ingreso_nuevo, frecuencia_nueva, dia_fijo, rec_nomina["id"]))

    conn.commit()
    return get_presupuesto(conn)



@router.put("/api/presupuesto/unidades/{orden}", summary="Fijar el día de cobro de un pago del presupuesto")
def actualizar_unidad_pago(orden: int, req: UnidadPagoUpdate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    if orden < 1 or orden > 4:
        raise HTTPException(status_code=404, detail="Pago de presupuesto no encontrado.")
    cursor = conn.cursor()
    cfg = obtener_config_presupuesto(cursor)
    if orden > unidades_activas(cfg["frecuencia"]):
        raise HTTPException(status_code=422, detail=f"La frecuencia {cfg['frecuencia']} no tiene el pago {orden}.")
    cursor.execute("UPDATE presupuesto_unidades SET dia_pago = ? WHERE orden = ?", (req.dia_pago, orden))
    # Los días de los pagos activos con fecha deben ir en orden creciente
    cursor.execute("SELECT orden, dia_pago FROM presupuesto_unidades WHERE orden <= ? ORDER BY orden", (unidades_activas(cfg["frecuencia"]),))
    dias = [u["dia_pago"] for u in cursor.fetchall() if u["dia_pago"] is not None]
    if any(a >= b for a, b in zip(dias, dias[1:])):
        conn.rollback()
        raise HTTPException(status_code=422, detail="Las fechas de pago deben ir en orden creciente: cada pago después del anterior.")
    conn.commit()
    return get_presupuesto(conn)


@router.post("/api/presupuesto/importar-fijos", summary="Importar nómina y gastos de Fijos y MSI")
def importar_fijos_a_presupuesto(
    req: Optional[ImportarFijosRequest] = None,
    conn: sqlite3.Connection = Depends(get_tenant_db)
):
    raise HTTPException(
        status_code=400,
        detail="Operación bloqueada: El flujo de importación es estrictamente unidireccional (Presupuesto -> Gastos Fijos). El presupuesto es una herramienta de planeación estratégica y no permite sobreescribirse automáticamente desde los gastos fijos."
    )


@router.post("/api/presupuesto/exportar-a-fijos", summary="Exportar tarjetas de presupuesto a Movimientos y Fijos con la misma periodicidad")
def exportar_presupuesto_a_fijos(
    req: Optional[ExportarFijosRequest] = None,
    conn: sqlite3.Connection = Depends(get_tenant_db)
):
    if isinstance(req, sqlite3.Connection):
        conn = req
        req = None
    if req is None:
        req = ExportarFijosRequest()
    cursor = conn.cursor()
    hoy_local = ahora_local().date()
    fecha_base_custom = None
    if req.fecha_base:
        try:
            fecha_base_custom = date.fromisoformat(req.fecha_base)
        except ValueError:
            raise HTTPException(status_code=400, detail="La fecha base especificada no es válida.")

    fecha_inicio_efectiva = calcular_fecha_inicio_exportacion(
        inicio_cargos=req.inicio_cargos or 'natural',
        fecha_base_custom=fecha_base_custom,
        hoy=hoy_local
    )

    try:
        res = exportar_presupuesto_a_fijos_core(
            cursor=cursor,
            tarjetas_ids=req.tarjetas_ids,
            incluir_ingreso=req.incluir_ingreso,
            fecha_base=fecha_inicio_efectiva
        )
    except ValueError as e:
        conn.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    materializadas = materializar_recurrentes(conn, hoy_local)
    conn.commit()
    res["transacciones_materializadas"] = materializadas
    res["fecha_inicio_cargos"] = fecha_inicio_efectiva.isoformat()
    res["inicio_cargos_modo"] = req.inicio_cargos or 'natural'
    return res


@router.post("/api/presupuesto/plantilla", summary="Aplicar plantilla predeterminada de presupuesto")
def aplicar_plantilla(req: PlantillaRequest, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    cfg = obtener_config_presupuesto(cursor)
    ingreso = float(cfg["ingreso_base"])

    # Limpiar las tarjetas del tablero general (las de cada pago se conservan)
    cursor.execute("DELETE FROM presupuesto_tarjetas WHERE unidad_orden IS NULL")

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


def _resolver_montos(monto, porcentaje, ingreso):
    """Monto y porcentaje coherentes. Un monto vacío o en cero con porcentaje manda el porcentaje
    (la interfaz envía los dos campos). Sin ingreso base no se puede convertir un porcentaje."""
    if porcentaje and not monto:
        if ingreso <= 0:
            raise HTTPException(status_code=422, detail="Define primero tu ingreso fijo base para asignar tarjetas por porcentaje.")
        monto = round(ingreso * (porcentaje / 100.0), 2)
    if monto is None:
        return 0.0, 0.0
    return round(monto, 2), (round((monto / ingreso) * 100.0, 2) if ingreso > 0 else 0.0)


@router.post("/api/presupuesto/tarjetas", status_code=201, summary="Crear tarjeta de presupuesto")
def crear_tarjeta(req: TarjetaPresupuestoCreate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    cfg = obtener_config_presupuesto(cursor)
    ingreso = float(cfg["ingreso_base"])

    monto, porcentaje = _resolver_montos(req.monto, req.porcentaje, ingreso)

    if req.unidad_orden is not None:
        if req.unidad_orden > unidades_activas(cfg["frecuencia"]):
            raise HTTPException(status_code=422, detail=f"La frecuencia {cfg['frecuencia']} no tiene el pago {req.unidad_orden}.")
        _validar_capacidad_unidad(cursor, cfg, req.unidad_orden, round(monto, 2))
        # El día lo marca el pago; una tarjeta de pago no se subdivide
        req.dia_pago = None
        req.distribucion_subperiodos = None

    # Normalizar distribucion_subperiodos y sincronizar dia_pago si es de un solo sub-período activo
    dist_list = None
    if req.distribucion_subperiodos:
        dist_list = [dict(x) for x in req.distribucion_subperiodos]
        positivos = [sp for sp in dist_list if float(sp.get("monto", 0.0) or 0.0) > 0]
        for sp in dist_list:
            if float(sp.get("monto", 0.0) or 0.0) <= 0:
                sp["dia"] = None
        if len(positivos) == 1 and req.dia_pago is not None:
            positivos[0]["dia"] = req.dia_pago
        elif len(positivos) == 1 and positivos[0].get("dia") is not None and req.dia_pago is None:
            req.dia_pago = int(positivos[0]["dia"])

    # Determinar orden consecutivo
    dist_json = json.dumps(dist_list) if dist_list else None
    max_orden = cursor.execute(
        "SELECT COALESCE(MAX(orden), 0) FROM presupuesto_tarjetas WHERE unidad_orden IS ?", (req.unidad_orden,)
    ).fetchone()[0]
    cursor.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden, dia_pago, distribucion_subperiodos, unidad_orden)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (req.nombre.strip(), req.icono, req.color, round(porcentaje, 2), round(monto, 2), req.notas or "", max_orden + 1, req.dia_pago, dist_json, req.unidad_orden))

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

    # Una tarjeta de pago toma su día del pago: ignora día y subdivisiones que llegue a enviar
    es_pago = fila["unidad_orden"] is not None
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
    dia_efectivo = req.dia_pago if "dia_pago" in req.model_fields_set else fila["dia_pago"]

    if "distribucion_subperiodos" in req.model_fields_set and not es_pago:
        dist_list = [dict(x) for x in req.distribucion_subperiodos] if req.distribucion_subperiodos else None
        if dist_list:
            positivos = [sp for sp in dist_list if float(sp.get("monto", 0.0) or 0.0) > 0]
            for sp in dist_list:
                if float(sp.get("monto", 0.0) or 0.0) <= 0:
                    sp["dia"] = None
            if len(positivos) == 1 and "dia_pago" in req.model_fields_set and req.dia_pago is not None:
                positivos[0]["dia"] = req.dia_pago
            elif len(positivos) == 1 and positivos[0].get("dia") is not None:
                dia_efectivo = int(positivos[0]["dia"])
        dist_json = json.dumps(dist_list) if dist_list else None
        campos.append("distribucion_subperiodos = ?"); valores.append(dist_json)
    elif "dia_pago" in req.model_fields_set and fila["distribucion_subperiodos"] and not es_pago:
        try:
            dist_list = json.loads(fila["distribucion_subperiodos"])
            if isinstance(dist_list, list):
                positivos = [sp for sp in dist_list if float(sp.get("monto", 0.0) or 0.0) > 0]
                for sp in dist_list:
                    if float(sp.get("monto", 0.0) or 0.0) <= 0:
                        sp["dia"] = None
                if len(positivos) == 1 and req.dia_pago is not None:
                    positivos[0]["dia"] = req.dia_pago
                    campos.append("distribucion_subperiodos = ?"); valores.append(json.dumps(dist_list))
        except Exception:
            pass

    if not es_pago and ("dia_pago" in req.model_fields_set or dia_efectivo != fila["dia_pago"]):
        campos.append("dia_pago = ?"); valores.append(dia_efectivo)

    # Si se actualiza monto o porcentaje, priorizar monto explícito y sincronizar el otro campo
    if req.monto is not None or req.porcentaje is not None:
        monto, pct = _resolver_montos(req.monto, req.porcentaje, ingreso)
        if es_pago:
            _validar_capacidad_unidad(cursor, cfg, fila["unidad_orden"], monto, excluir_id=tarjeta_id)
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

    # Sus fijos se borran junto con la tarjeta (los movimientos ya generados se conservan)
    cursor.execute(
        "DELETE FROM recurrentes WHERE presupuesto_tarjeta_id = ? AND total_cuotas IS NULL",
        (tarjeta_id,),
    )
    cursor.execute("DELETE FROM presupuesto_tarjetas WHERE id = ?", (tarjeta_id,))
    conn.commit()
    return {"mensaje": "Tarjeta eliminada con éxito."}
