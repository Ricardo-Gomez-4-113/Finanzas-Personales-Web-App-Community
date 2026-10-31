"""routers/inversiones.py - Inversiones bursátiles, cotizaciones y tipos de cambio."""

from fastapi import APIRouter
from fastapi import HTTPException, status, Depends
from pydantic import BaseModel, Field, field_validator
import time
from typing import Literal, Optional, Dict, Any
import sqlite3

from datetime import date

from finanzas_core import ahora_local, convertir, valor_rendimiento, valorar_posiciones, validar_fecha_operativa
from mercado import consultar_precio_y_moneda_mercado, buscar_tickers_mercado, obtener_tasas_cambio, cotizar_varios, TASA_MXN_RESPALDO, TASA_EUR_RESPALDO
from security import (
    verify_token,
    get_tenant_db
)

router = APIRouter()

class InversionCreate(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=15, pattern=r"^[A-Z0-9.\-=^]{1,15}$")
    cantidad: float = Field(..., gt=0, le=1e9)
    precio_promedio: float = Field(..., gt=0, le=1e12)
    precio_actual: Optional[float] = Field(None, gt=0, le=1e12)
    moneda: Optional[Literal['USD', 'MXN', 'EUR']] = "USD"


class InversionUpdate(BaseModel):
    cantidad: Optional[float] = Field(None, gt=0, le=1e9)
    precio_promedio: Optional[float] = Field(None, gt=0, le=1e12)
    precio_actual: Optional[float] = Field(None, gt=0, le=1e12)
    moneda: Optional[Literal['USD', 'MXN', 'EUR']] = None


# =============================================================================
# ENDPOINTS MULTI-TENANT: INVERSIONES & ASSET ALLOCATION
# =============================================================================

@router.get("/api/inversiones", summary="Listar portafolio con rendimientos del tenant")
def listar_inversiones(moneda_base: str = "MXN", conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    filas = cursor.execute("SELECT * FROM inversiones ORDER BY id DESC").fetchall()
    tasas = obtener_tasas_cambio()
    return valorar_posiciones(filas, moneda_base, tasas.get("MXN", TASA_MXN_RESPALDO), tasas.get("EUR", TASA_EUR_RESPALDO))

@router.post("/api/inversiones", status_code=status.HTTP_201_CREATED, summary="Registrar posición bursátil")
def crear_inversion(item: InversionCreate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    ticker = item.ticker.strip().upper()
    precio_actual = item.precio_actual
    moneda = (item.moneda or "USD").strip().upper()
    
    if precio_actual is None or precio_actual <= 0:
        precio_mercado, moneda_mercado = consultar_precio_y_moneda_mercado(ticker)
        precio_actual = precio_mercado if precio_mercado else item.precio_promedio
        if not item.moneda:
            moneda = moneda_mercado

    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO inversiones (ticker, cantidad, precio_promedio, precio_actual, moneda) VALUES (?, ?, ?, ?, ?)",
            (ticker, item.cantidad, item.precio_promedio, precio_actual, moneda)
        )
        conn.commit()
        nuevo_id = cursor.lastrowid
        return {"id": nuevo_id, "mensaje": f"Inversión {ticker} ({moneda}) agregada con éxito."}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Ya tienes una posición con el ticker {ticker}. Puedes editarla.")

@router.put("/api/inversiones/{inversion_id}", summary="Actualizar posición bursátil")
def actualizar_inversion(inversion_id: int, item: InversionUpdate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    inv = cursor.execute("SELECT * FROM inversiones WHERE id = ?", (inversion_id,)).fetchone()
    if not inv:
        raise HTTPException(status_code=404, detail="Inversión no encontrada")

    nueva_cant = item.cantidad if item.cantidad is not None else inv["cantidad"]
    nuevo_prom = item.precio_promedio if item.precio_promedio is not None else inv["precio_promedio"]
    nuevo_act = item.precio_actual if item.precio_actual is not None else inv["precio_actual"]
    nueva_moneda = item.moneda.upper() if item.moneda else (inv["moneda"] or "USD")

    cursor.execute(
        """
        UPDATE inversiones 
        SET cantidad = ?, precio_promedio = ?, precio_actual = ?, moneda = ?, actualizado_en = CURRENT_TIMESTAMP 
        WHERE id = ?
        """,
        (nueva_cant, nuevo_prom, nuevo_act, nueva_moneda, inversion_id)
    )
    conn.commit()
    return {"mensaje": "Inversión actualizada con éxito."}

@router.delete("/api/inversiones/{inversion_id}", summary="Eliminar posición bursátil")
def eliminar_inversion(inversion_id: int, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    inv = cursor.execute("SELECT * FROM inversiones WHERE id = ?", (inversion_id,)).fetchone()
    if not inv:
        raise HTTPException(status_code=404, detail="Inversión no encontrada")

    cursor.execute("DELETE FROM inversiones WHERE id = ?", (inversion_id,))
    conn.commit()
    return {"mensaje": "Inversión eliminada."}

MAX_POSICIONES_SYNC = 100
COOLDOWN_SYNC_SEG = 30
_ultima_sync: Dict[str, float] = {}

@router.post("/api/inversiones/sincronizar", summary="Sincronizar cotizaciones en vivo")
def sincronizar_precios(
    conn: sqlite3.Connection = Depends(get_tenant_db),
    current_user: Dict[str, Any] = Depends(verify_token),
):
    cursor = conn.cursor()
    filas = cursor.execute("SELECT id, ticker FROM inversiones").fetchall()
    if len(filas) > MAX_POSICIONES_SYNC:
        raise HTTPException(status_code=422, detail=f"Demasiadas posiciones para sincronizar a la vez (máximo {MAX_POSICIONES_SYNC}).")
    ahora_sync = time.time()
    clave_sync = current_user["db_name"]
    if ahora_sync - _ultima_sync.get(clave_sync, 0.0) < COOLDOWN_SYNC_SEG:
        raise HTTPException(status_code=429, detail="Espera unos segundos antes de volver a sincronizar.")
    _ultima_sync[clave_sync] = ahora_sync
    actualizados = []
    no_actualizados = []

    pendientes = []
    cotizaciones = cotizar_varios([fila["ticker"] for fila in filas])
    for fila in filas:
        t = fila["ticker"]
        precio, moneda = cotizaciones[t]
        if precio is not None:
            moneda_actualizada = moneda or ("MXN" if t.endswith(".MX") else "USD")
            pendientes.append((precio, moneda_actualizada, fila["id"]))
            actualizados.append({"ticker": t, "precio": precio, "moneda": moneda_actualizada})
        else:
            no_actualizados.append(t)

    if pendientes:
        cursor.executemany(
            """
            UPDATE inversiones 
            SET precio_actual = ?, moneda = ?, actualizado_en = CURRENT_TIMESTAMP 
            WHERE id = ?
            """,
            pendientes
        )
        conn.commit()
    return {
        "actualizados": actualizados,
        "no_actualizados": no_actualizados,
        "mensaje": f"Se sincronizaron {len(actualizados)} de {len(filas)} posiciones."
    }

@router.get("/api/inversiones/cotizar/{ticker}", summary="Cotización individual en tiempo real")
def cotizar_ticker(ticker: str, _user: Dict[str, Any] = Depends(verify_token)):
    precio, moneda = consultar_precio_y_moneda_mercado(ticker)
    if precio is None:
        raise HTTPException(status_code=404, detail=f"No se pudo consultar la cotización para {ticker.upper()}")
    return {"ticker": ticker.upper(), "precio": precio, "moneda": moneda}

@router.get("/api/inversiones/buscar", summary="Buscador predictivo de tickers")
def api_buscar_ticker(q: str = "", _user: Dict[str, Any] = Depends(verify_token)):
    if not q or len(q.strip()) < 1:
        return []
    return buscar_tickers_mercado(q)

# =============================================================================
# TIPOS DE CAMBIO (ver mercado.py)
# =============================================================================

@router.get("/api/tipo-cambio", summary="Consultar tipos de cambio vigentes")
def get_tipo_cambio(_user: Dict[str, Any] = Depends(verify_token)):
    tasas = obtener_tasas_cambio()
    return {
        "base": "USD",
        "tasas": tasas,
        "actualizado_en": ahora_local().strftime("%Y-%m-%d %H:%M:%S")
    }

@router.get("/api/public/tipo-cambio", summary="Consultar tipos de cambio públicos")
def get_tipo_cambio_publico():
    """Retorna las tasas de cambio de moneda (MXN/USD) sin requerir autenticación para la página de presentación."""
    tasas = obtener_tasas_cambio()
    return {
        "base": "USD",
        "tasas": tasas,
        "actualizado_en": ahora_local().strftime("%Y-%m-%d %H:%M:%S")
    }


# =============================================================================
# CUENTAS CON RENDIMIENTO (tasa fija, interés diario compuesto)
# =============================================================================

FECHA_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class RendimientoCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=60)
    capital: float = Field(..., gt=0, le=1e12)
    tasa_anual: float = Field(..., ge=0, le=100)
    fecha_inicio: str = Field(..., pattern=FECHA_PATTERN)

    @field_validator("fecha_inicio")
    @classmethod
    def _fecha_acotada(cls, v: str) -> str:
        return validar_fecha_operativa(v)


class RendimientoMovimiento(BaseModel):
    monto: float = Field(..., gt=0, le=1e12)
    accion: Literal['aportar', 'retirar']


class RendimientoUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=60)
    tasa_anual: Optional[float] = Field(None, ge=0, le=100)


def _cuenta_valorada(fila, hoy: date, base: str, tasa_fx: float, tasa_eur: float) -> Dict[str, Any]:
    capital = float(fila["capital"])
    tasa = float(fila["tasa_anual"])
    valor = valor_rendimiento(capital, tasa, date.fromisoformat(fila["fecha_base"]), hoy)
    interes = round(valor - capital, 2)
    ganancia_mes = round(valor * tasa / 100 / 12, 2)
    return {
        "id": fila["id"],
        "nombre": fila["nombre"],
        "tasa_anual": tasa,
        "fecha_base": fila["fecha_base"],
        "capital": round(convertir(capital, "MXN", base, tasa_fx, tasa_eur), 2),
        "valor_actual": round(convertir(valor, "MXN", base, tasa_fx, tasa_eur), 2),
        "interes_generado": round(convertir(interes, "MXN", base, tasa_fx, tasa_eur), 2),
        "ganancia_mensual_estimada": round(convertir(ganancia_mes, "MXN", base, tasa_fx, tasa_eur), 2),
    }


def _capitalizar(cursor, fila, hoy: date) -> float:
    """Fija el valor de hoy como nuevo capital y reinicia la fecha base."""
    valor = valor_rendimiento(float(fila["capital"]), float(fila["tasa_anual"]),
                              date.fromisoformat(fila["fecha_base"]), hoy)
    cursor.execute("UPDATE cuentas_rendimiento SET capital = ?, fecha_base = ? WHERE id = ?",
                   (valor, hoy.isoformat(), fila["id"]))
    return valor


def _obtener_cuenta(cursor, cuenta_id: int):
    fila = cursor.execute("SELECT * FROM cuentas_rendimiento WHERE id = ?", (cuenta_id,)).fetchone()
    if not fila:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    return fila


@router.get("/api/rendimiento", summary="Cuentas con rendimiento y su valor actual")
def listar_rendimiento(moneda_base: str = "MXN", conn: sqlite3.Connection = Depends(get_tenant_db)):
    hoy = ahora_local().date()
    base = moneda_base.upper()
    tasas = obtener_tasas_cambio()
    filas = conn.execute("SELECT * FROM cuentas_rendimiento ORDER BY id").fetchall()
    cuentas = [_cuenta_valorada(f, hoy, base, tasas.get("MXN", TASA_MXN_RESPALDO), tasas.get("EUR", TASA_EUR_RESPALDO)) for f in filas]
    total = {
        "valor_actual": round(sum(c["valor_actual"] for c in cuentas), 2),
        "interes_generado": round(sum(c["interes_generado"] for c in cuentas), 2),
        "ganancia_mensual_estimada": round(sum(c["ganancia_mensual_estimada"] for c in cuentas), 2),
    }
    return {"moneda_base": base, "cuentas": cuentas, "total": total}


@router.post("/api/rendimiento", status_code=201, summary="Crear cuenta con rendimiento")
def crear_rendimiento(req: RendimientoCreate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    nombre = req.nombre.strip()
    if not nombre:
        raise HTTPException(status_code=400, detail="El nombre no puede estar vacío.")
    try:
        date.fromisoformat(req.fecha_inicio)
    except ValueError:
        raise HTTPException(status_code=400, detail="La fecha de inicio no es válida.")
    if cursor.execute("SELECT 1 FROM cuentas_rendimiento WHERE nombre = ?", (nombre,)).fetchone():
        raise HTTPException(status_code=400, detail="Ya existe una cuenta con ese nombre.")
    cursor.execute(
        "INSERT INTO cuentas_rendimiento (nombre, capital, tasa_anual, fecha_base) VALUES (?, ?, ?, ?)",
        (nombre, req.capital, req.tasa_anual, req.fecha_inicio)
    )
    conn.commit()
    return {"id": cursor.lastrowid, "mensaje": "Cuenta creada."}


@router.post("/api/rendimiento/{cuenta_id}/movimiento", summary="Aportar o retirar de una cuenta con rendimiento")
def movimiento_rendimiento(cuenta_id: int, mov: RendimientoMovimiento, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    fila = _obtener_cuenta(cursor, cuenta_id)
    hoy = ahora_local().date()
    valor = valor_rendimiento(float(fila["capital"]), float(fila["tasa_anual"]),
                              date.fromisoformat(fila["fecha_base"]), hoy)
    if mov.accion == 'retirar':
        if mov.monto > valor:
            raise HTTPException(status_code=400, detail="No puedes retirar más de lo que tiene la cuenta.")
        nuevo_capital = valor - mov.monto
    else:
        nuevo_capital = valor + mov.monto
    cursor.execute("UPDATE cuentas_rendimiento SET capital = ?, fecha_base = ? WHERE id = ?",
                   (round(nuevo_capital, 2), hoy.isoformat(), cuenta_id))
    conn.commit()
    return {"mensaje": "Movimiento registrado.", "valor_actual": round(nuevo_capital, 2)}


@router.put("/api/rendimiento/{cuenta_id}", summary="Editar nombre o tasa de una cuenta con rendimiento")
def editar_rendimiento(cuenta_id: int, req: RendimientoUpdate, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    fila = _obtener_cuenta(cursor, cuenta_id)
    hoy = ahora_local().date()
    if req.nombre is not None:
        nombre = req.nombre.strip()
        if not nombre:
            raise HTTPException(status_code=400, detail="El nombre no puede estar vacío.")
        if cursor.execute("SELECT 1 FROM cuentas_rendimiento WHERE nombre = ? AND id != ?", (nombre, cuenta_id)).fetchone():
            raise HTTPException(status_code=400, detail="Ya existe una cuenta con ese nombre.")
        cursor.execute("UPDATE cuentas_rendimiento SET nombre = ? WHERE id = ?", (nombre, cuenta_id))
    if req.tasa_anual is not None and float(req.tasa_anual) != float(fila["tasa_anual"]):
        # Capitalizar con la tasa anterior antes de cambiarla
        _capitalizar(cursor, fila, hoy)
        cursor.execute("UPDATE cuentas_rendimiento SET tasa_anual = ? WHERE id = ?", (req.tasa_anual, cuenta_id))
    conn.commit()
    return {"mensaje": "Cuenta actualizada."}


@router.delete("/api/rendimiento/{cuenta_id}", summary="Eliminar cuenta con rendimiento")
def eliminar_rendimiento(cuenta_id: int, conn: sqlite3.Connection = Depends(get_tenant_db)):
    cursor = conn.cursor()
    _obtener_cuenta(cursor, cuenta_id)
    cursor.execute("DELETE FROM cuentas_rendimiento WHERE id = ?", (cuenta_id,))
    conn.commit()
    return {"mensaje": "Cuenta eliminada."}
