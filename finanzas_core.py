"""
finanzas_core.py - Lógica financiera compartida (sin FastAPI).

Reúne los cálculos que antes estaban duplicados en main.py y export_service.py:
- Conversión de moneda (convertir).
- Valuación de posiciones bursátiles (valorar_posiciones).
- Resumen patrimonial (calcular_resumen).
"""

import calendar
from datetime import date, datetime, timedelta
import sqlite3
from typing import Any, Dict, Iterable, Optional


def ahora_local() -> datetime:
    """Retorna la fecha y hora actual en la zona horaria de México (CST / UTC-6)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Mexico_City"))
    except Exception:
        return datetime.now()


def convertir(monto: float, moneda_origen: str, moneda_base: str, tasa_usd_mxn: float) -> float:
    """Convierte un monto entre USD y MXN. Cualquier otro par devuelve el monto sin cambios."""
    origen = moneda_origen.upper()
    base = moneda_base.upper()
    if origen == "USD" and base == "MXN":
        return monto * tasa_usd_mxn
    if origen == "MXN" and base == "USD":
        return monto / tasa_usd_mxn if tasa_usd_mxn > 0 else monto
    return monto


def valorar_posiciones(filas: Iterable[Any], moneda_base: str, tasa_usd_mxn: float) -> Dict[str, Any]:
    """Valúa las posiciones bursátiles en la moneda base y calcula el PnL del portafolio."""
    moneda_base_upper = moneda_base.upper()

    posiciones = []
    total_invertido = 0.0
    total_valor_actual = 0.0

    for fila in filas:
        item = dict(fila)
        cant = float(item["cantidad"])
        costo_prom = float(item["precio_promedio"])
        precio_act = float(item["precio_actual"])
        moneda_activo = (item.get("moneda") or "USD").upper()

        cap_invertido_orig = round(cant * costo_prom, 2)
        val_act_orig = round(cant * precio_act, 2)

        cap_invertido_base = round(convertir(cap_invertido_orig, moneda_activo, moneda_base_upper, tasa_usd_mxn), 2)
        val_act_base = round(convertir(val_act_orig, moneda_activo, moneda_base_upper, tasa_usd_mxn), 2)
        pnl_base = round(val_act_base - cap_invertido_base, 2)
        pnl_pct = round(((val_act_orig - cap_invertido_orig) / cap_invertido_orig) * 100, 2) if cap_invertido_orig > 0 else 0.0

        total_invertido += cap_invertido_base
        total_valor_actual += val_act_base

        item["moneda"] = moneda_activo
        item["factor_conversion"] = convertir(1.0, moneda_activo, moneda_base_upper, tasa_usd_mxn)
        item["capital_invertido_orig"] = cap_invertido_orig
        item["valor_actual_orig"] = val_act_orig
        item["capital_invertido"] = cap_invertido_base
        item["valor_actual"] = val_act_base
        item["pnl"] = pnl_base
        item["pnl_porcentaje"] = pnl_pct
        posiciones.append(item)

    total_pnl = round(total_valor_actual - total_invertido, 2)
    total_pnl_pct = round((total_pnl / total_invertido) * 100, 2) if total_invertido > 0 else 0.0

    for pos in posiciones:
        pos["peso_porcentaje"] = round((pos["valor_actual"] / total_valor_actual) * 100, 2) if total_valor_actual > 0 else 0.0

    return {
        "moneda_base": moneda_base_upper,
        "tasa_usd_mxn": tasa_usd_mxn,
        "resumen": {
            "total_invertido": round(total_invertido, 2),
            "total_valor_actual": round(total_valor_actual, 2),
            "total_pnl": total_pnl,
            "total_pnl_porcentaje": total_pnl_pct
        },
        "posiciones": posiciones
    }


# =============================================================================
# FIJOS, MSI Y CUENTAS CON RENDIMIENTO
# =============================================================================

def ultimo_dia_mes(anio: int, mes: int) -> int:
    return calendar.monthrange(anio, mes)[1]


def siguiente_fecha(fecha: date, frecuencia: str, dia: Optional[int]) -> date:
    """Siguiente ocurrencia de un recurrente según su frecuencia."""
    if frecuencia == 'semanal':
        return fecha + timedelta(days=7)
    if frecuencia == 'quincenal':
        ultimo = ultimo_dia_mes(fecha.year, fecha.month)
        if fecha.day < 15:
            return date(fecha.year, fecha.month, 15)
        if fecha.day < ultimo:
            return date(fecha.year, fecha.month, ultimo)
        # Fin de mes -> 15 del mes siguiente
        anio, mes = (fecha.year + 1, 1) if fecha.month == 12 else (fecha.year, fecha.month + 1)
        return date(anio, mes, 15)
    # mensual: mes siguiente, día ancla limitado al último día del mes
    anio, mes = (fecha.year + 1, 1) if fecha.month == 12 else (fecha.year, fecha.month + 1)
    ancla = dia if dia else fecha.day
    return date(anio, mes, min(ancla, ultimo_dia_mes(anio, mes)))


def primera_fecha_quincenal(desde: date) -> date:
    """Primera fecha >= desde que sea día 15 o fin de mes."""
    if desde.day <= 15:
        return date(desde.year, desde.month, 15)
    return date(desde.year, desde.month, ultimo_dia_mes(desde.year, desde.month))


def materializar_recurrentes(conn: sqlite3.Connection, hoy: date) -> int:
    """
    Inserta en `transacciones` las ocurrencias de recurrentes cuya fecha ya llegó.
    Nunca inserta fechas futuras. Devuelve cuántas transacciones creó.
    """
    cursor = conn.cursor()
    filas = cursor.execute(
        "SELECT * FROM recurrentes WHERE activo = 1 AND proxima_fecha <= ?", (hoy.isoformat(),)
    ).fetchall()
    insertadas = 0
    for fila in filas:
        total = fila["total_cuotas"]
        monto = float(fila["monto"])
        generadas = int(fila["cuotas_generadas"])
        prox = date.fromisoformat(fila["proxima_fecha"])
        dia = fila["dia"]
        activo = 1

        while prox <= hoy and (total is None or generadas < total):
            if total is not None:
                n = generadas + 1
                if n == total and fila["monto_total"] is not None:
                    monto_cuota = round(float(fila["monto_total"]) - monto * (total - 1), 2)
                else:
                    monto_cuota = monto
                descripcion = f"{fila['descripcion']} ({n}/{total})"
            else:
                monto_cuota = monto
                descripcion = fila["descripcion"]
            cursor.execute(
                """
                INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion, recurrente_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (prox.isoformat(), monto_cuota, fila["tipo"], fila["categoria_id"], descripcion, fila["id"])
            )
            generadas += 1
            insertadas += 1
            prox = siguiente_fecha(prox, fila["frecuencia"], dia)

        if total is not None and generadas >= total:
            activo = 0
        cursor.execute(
            "UPDATE recurrentes SET proxima_fecha = ?, cuotas_generadas = ?, activo = ? WHERE id = ?",
            (prox.isoformat(), generadas, activo, fila["id"])
        )
    conn.commit()
    return insertadas


def deuda_pendiente(fila) -> float:
    """Saldo MSI pendiente de un recurrente. 0 si no es MSI o ya terminó."""
    total = fila["total_cuotas"]
    if total is None or fila["monto_total"] is None:
        return 0.0
    generadas = int(fila["cuotas_generadas"])
    if generadas >= total:
        return 0.0
    return round(float(fila["monto_total"]) - float(fila["monto"]) * generadas, 2)


def mensualizar(monto: float, frecuencia: str) -> float:
    """Equivalente mensual de un monto periódico."""
    if frecuencia == 'quincenal':
        return float(monto) * 2
    if frecuencia == 'semanal':
        return round(float(monto) * 52 / 12, 2)
    return float(monto)


def convertir_frecuencias(monto: float, frecuencia_origen: str) -> Dict[str, float]:
    """Calcula el equivalente mensual, quincenal y semanal de un monto dado."""
    mensual = mensualizar(monto, frecuencia_origen)
    return {
        "mensual": round(mensual, 2),
        "quincenal": round(mensual / 2, 2),
        "semanal": round(mensual * 12 / 52, 2)
    }


def convertir_de_mensual(monto_mensual: float, frecuencia_destino: str) -> float:
    """Convierte un monto mensualizado a la frecuencia solicitada."""
    m = float(monto_mensual)
    if frecuencia_destino == 'mensual':
        return round(m, 2)
    if frecuencia_destino == 'quincenal':
        return round(m / 2, 2)
    return round(m * 12 / 52, 2)



def valor_rendimiento(capital: float, tasa_anual: float, fecha_base: date, hoy: date) -> float:
    """Valor de una cuenta con interés diario compuesto desde fecha_base."""
    dias = max(0, (hoy - fecha_base).days)
    return round(capital * (1 + tasa_anual / 100 / 365) ** dias, 2)


def calcular_resumen(conn: sqlite3.Connection, moneda_base: str, tasa_usd_mxn: float, hoy: date) -> Dict[str, Any]:
    """
    Resumen patrimonial en la moneda base.
    - balance: transacciones materializadas (las cuotas MSI bajan el balance solo al vencer).
    - patrimonio_neto: incluye cuentas con rendimiento y resta la deuda MSI pendiente.
    - mes_actual: excluye movimientos de apartados (no son gasto ni ingreso real).
    """
    moneda_base_upper = moneda_base.upper()
    cursor = conn.cursor()

    def conv(monto: float, origen: str = "MXN") -> float:
        return convertir(monto, origen, moneda_base_upper, tasa_usd_mxn)

    cursor.execute("SELECT SUM(monto) as total FROM transacciones WHERE tipo='ingreso'")
    ingresos = cursor.fetchone()["total"] or 0.0

    cursor.execute("SELECT SUM(monto) as total FROM transacciones WHERE tipo='gasto'")
    gastos = cursor.fetchone()["total"] or 0.0

    cursor.execute("SELECT SUM(monto_actual) as total FROM apartados")
    apartados = cursor.fetchone()["total"] or 0.0

    filas_inv = cursor.execute("SELECT cantidad, precio_actual, moneda FROM inversiones").fetchall()
    inversiones_total = 0.0
    for inv in filas_inv:
        val = float(inv["cantidad"]) * float(inv["precio_actual"])
        inversiones_total += convertir(val, (inv["moneda"] or "USD").upper(), moneda_base_upper, tasa_usd_mxn)

    filas_rec = cursor.execute("SELECT * FROM recurrentes").fetchall()
    deuda_msi = 0.0
    ingreso_fijo = 0.0
    gasto_fijo = 0.0
    for r in filas_rec:
        deuda_msi += deuda_pendiente(r)
        if r["total_cuotas"] is None and r["activo"]:
            if r["tipo"] == 'ingreso':
                ingreso_fijo += mensualizar(r["monto"], r["frecuencia"])
            else:
                gasto_fijo += mensualizar(r["monto"], r["frecuencia"])

    rendimiento_fijo = 0.0
    for c in cursor.execute("SELECT * FROM cuentas_rendimiento").fetchall():
        valor = valor_rendimiento(float(c["capital"]), float(c["tasa_anual"]),
                                  date.fromisoformat(c["fecha_base"]), hoy)
        rendimiento_fijo += valor

    mes = f"{hoy.year:04d}-{hoy.month:02d}"
    cursor.execute(
        """
        SELECT tipo, SUM(monto) AS total FROM transacciones
        WHERE strftime('%Y-%m', fecha) = ? AND apartado_id IS NULL
        GROUP BY tipo
        """,
        (mes,)
    )
    mes_tot = {row["tipo"]: (row["total"] or 0.0) for row in cursor.fetchall()}
    mes_ingresos = mes_tot.get('ingreso', 0.0)
    mes_gastos = mes_tot.get('gasto', 0.0)

    balance = ingresos - gastos
    patrimonio = conv(balance) + conv(apartados) + inversiones_total - conv(deuda_msi)
    patrimonio_sin_rendimiento = patrimonio

    return {
        "moneda_base": moneda_base_upper,
        "tasa_usd_mxn": tasa_usd_mxn,
        "ingresos": round(conv(ingresos), 2),
        "gastos": round(conv(gastos), 2),
        "balance": round(conv(balance), 2),
        "apartados": round(conv(apartados), 2),
        "inversiones": round(inversiones_total, 2),
        "rendimiento_fijo": round(conv(rendimiento_fijo), 2),
        "deuda_msi": round(conv(deuda_msi), 2),
        "ingreso_fijo_mensual": round(conv(ingreso_fijo), 2),
        "gasto_fijo_mensual": round(conv(gasto_fijo), 2),
        "mes_actual": {
            "ingresos": round(conv(mes_ingresos), 2),
            "gastos": round(conv(mes_gastos), 2),
            "ahorro": round(conv(mes_ingresos - mes_gastos), 2),
        },
        "patrimonio_sin_rendimiento": round(patrimonio_sin_rendimiento, 2),
        "patrimonio_neto": round(patrimonio, 2),
    }
