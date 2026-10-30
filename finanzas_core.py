"""
finanzas_core.py - Lógica financiera compartida (sin FastAPI).

Reúne los cálculos que antes estaban duplicados en main.py y export_service.py:
- Conversión de moneda (convertir).
- Valuación de posiciones bursátiles (valorar_posiciones).
- Resumen patrimonial (calcular_resumen).
"""

import calendar
import os
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import json
import re
import sqlite3
from typing import Any, Dict, Iterable, Optional, Union


def ahora_local() -> datetime:
    """Retorna la fecha y hora actual en la zona horaria de México (CST / UTC-6).

    Solo en modo de pruebas (FINANZAS_MODO_PRUEBA=1) puede fijarse "hoy" con FINANZAS_HOY
    (por ejemplo "2026-10-20T10:00:00"), para que materialización y periodos sean reproducibles.
    En producción esa variable no tiene efecto."""
    try:
        from zoneinfo import ZoneInfo
        zona = ZoneInfo("America/Mexico_City")
    except Exception:
        zona = None
    if os.environ.get("FINANZAS_MODO_PRUEBA") == "1" and os.environ.get("FINANZAS_HOY"):
        fijo = datetime.fromisoformat(os.environ["FINANZAS_HOY"])
        return fijo.replace(tzinfo=zona) if zona else fijo
    return datetime.now(zona) if zona else datetime.now()


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

FECHA_MIN_OPERATIVA = date(2000, 1, 1)
FECHA_MAX_OPERATIVA = date(2100, 12, 31)

def validar_fecha_operativa(texto: str) -> str:
    """Acepta solo fechas ISO entre 2000 y 2100. Evita iteraciones enormes y desbordes en rendimientos."""
    fecha = date.fromisoformat(texto)
    if not (FECHA_MIN_OPERATIVA <= fecha <= FECHA_MAX_OPERATIVA):
        raise ValueError("La fecha debe estar entre 2000-01-01 y 2100-12-31.")
    return texto

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


def proxima_fecha_vigente(fecha_anterior: date, frecuencia: str, dia: Optional[int], hoy: date) -> date:
    """Calcula la siguiente fecha válida >= hoy, evitando cobros retroactivos tras pausar."""
    prox = fecha_anterior
    while prox < hoy:
        prox = siguiente_fecha(prox, frecuencia, dia)
    return prox


def ancla_al_editar_proximo_cargo(dia_guardado: Optional[int], nueva_fecha: date, frecuencia: str) -> int:
    """Día ancla de un recurrente tras fijar a mano su próximo cargo.
    Un mensual del 31 movido al último día de un mes corto (28 feb) conserva el 31: el ancla no es esa fecha,
    solo está limitada por el mes. Cualquier otra fecha elegida pasa a ser el nuevo ancla."""
    if frecuencia == 'mensual' and dia_guardado and nueva_fecha.day == ultimo_dia_mes(nueva_fecha.year, nueva_fecha.month) \
            and dia_guardado > nueva_fecha.day:
        return dia_guardado
    return nueva_fecha.day


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
        apartado_id = fila["apartado_id"] if "apartado_id" in fila.keys() else None
        activo = 1

        while prox <= hoy and (total is None or generadas < total):
            # Prevenir transacciones duplicadas para el mismo recurrente en la misma fecha
            ya_existe = cursor.execute(
                "SELECT 1 FROM transacciones WHERE recurrente_id = ? AND fecha = ?",
                (fila["id"], prox.isoformat())
            ).fetchone()
            if ya_existe:
                generadas += 1
                prox = siguiente_fecha(prox, fila["frecuencia"], dia)
                continue

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
            # Inserción blindada con manejo de concurrencia e índice único
            try:
                cursor.execute(
                    """
                    INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion, recurrente_id, apartado_id, rubro)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (prox.isoformat(), monto_cuota, fila["tipo"], fila["categoria_id"], descripcion, fila["id"], apartado_id,
                     clase_movimiento(fila["tipo"], apartado_id, fila["id"], total is not None))
                )
                if apartado_id and fila["tipo"] == 'gasto':
                    cursor.execute(
                        "UPDATE apartados SET monto_actual = monto_actual + ? WHERE id = ?",
                        (monto_cuota, apartado_id)
                    )
                generadas += 1
                insertadas += 1
            except sqlite3.IntegrityError:
                # Ya fue insertado por otra conexión concurrente
                generadas += 1

            prox = siguiente_fecha(prox, fila["frecuencia"], dia)

        if total is not None and generadas >= total:
            activo = 0
        cursor.execute(
            "UPDATE recurrentes SET proxima_fecha = ?, cuotas_generadas = ?, activo = ? WHERE id = ?",
            (prox.isoformat(), generadas, activo, fila["id"])
        )
    conn.commit()
    return insertadas


def saltar_recurrente_core(
    cursor: sqlite3.Cursor,
    recurrente_id: int,
    contabilizar_cuota: bool = False,
    hoy: Optional[date] = None
) -> dict:
    """
    Avanza la próxima fecha de un recurrente al siguiente ciclo sin generar movimiento en transacciones.
    Para compras a meses (MSI):
      - contabilizar_cuota=True: incrementa cuotas_generadas (computa como pagada por fuera) y desactiva si se alcanza el total.
      - contabilizar_cuota=False: solo aplaza la fecha manteniendo las cuotas generadas intactas.
    """
    hoy_ref = hoy or ahora_local().date()
    fila = cursor.execute("SELECT * FROM recurrentes WHERE id = ?", (recurrente_id,)).fetchone()
    if not fila:
        raise ValueError("El gasto o ingreso recurrente no existe.")
    if not fila["activo"]:
        raise ValueError("No se puede saltar el pago de un recurrente inactivo o pausado.")

    prox_actual = date.fromisoformat(fila["proxima_fecha"])
    frecuencia = fila["frecuencia"]
    dia = fila["dia"]
    total_cuotas = fila["total_cuotas"]
    cuotas_generadas = int(fila["cuotas_generadas"] or 0)

    nueva_fecha = siguiente_fecha(prox_actual, frecuencia, dia)
    while nueva_fecha <= hoy_ref:
        nueva_fecha = siguiente_fecha(nueva_fecha, frecuencia, dia)

    activo = 1
    if total_cuotas is not None:
        if contabilizar_cuota:
            cuotas_generadas += 1
            if cuotas_generadas >= total_cuotas:
                activo = 0
        else:
            activo = 1
    else:
        activo = 1

    cursor.execute(
        "UPDATE recurrentes SET proxima_fecha = ?, cuotas_generadas = ?, activo = ? WHERE id = ?",
        (nueva_fecha.isoformat(), cuotas_generadas, activo, recurrente_id)
    )

    return {
        "id": recurrente_id,
        "descripcion": fila["descripcion"],
        "fecha_anterior": prox_actual.isoformat(),
        "nueva_proxima_fecha": nueva_fecha.isoformat(),
        "cuotas_generadas": cuotas_generadas,
        "total_cuotas": total_cuotas,
        "activo": bool(activo),
        "es_msi": total_cuotas is not None,
        "cuota_contabilizada": bool(contabilizar_cuota if total_cuotas is not None else False)
    }


def deuda_pendiente(fila) -> float:
    """Saldo MSI pendiente de un recurrente. 0 si no es MSI o ya terminó."""
    total = fila["total_cuotas"]
    if total is None or fila["monto_total"] is None:
        return 0.0
    generadas = int(fila["cuotas_generadas"])
    if generadas >= total:
        return 0.0
    return max(0.0, round(float(fila["monto_total"]) - float(fila["monto"]) * generadas, 2))


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


def generar_subperiodos_default(frecuencia: str, subdivision_tipo: str) -> list:
    """Genera la lista por defecto de sub-periodos según el tipo y frecuencia base."""
    if subdivision_tipo == 'quincenas':
        return [
            {"id": "q1", "nombre": "Quincena 1", "porcentaje": 50.0},
            {"id": "q2", "nombre": "Quincena 2", "porcentaje": 50.0}
        ]
    if subdivision_tipo == 'semanas':
        if frecuencia == 'quincenal':
            return [
                {"id": "s1", "nombre": "Semana 1", "porcentaje": 50.0},
                {"id": "s2", "nombre": "Semana 2", "porcentaje": 50.0}
            ]
        return [
            {"id": "s1", "nombre": "Semana 1", "porcentaje": 25.0},
            {"id": "s2", "nombre": "Semana 2", "porcentaje": 25.0},
            {"id": "s3", "nombre": "Semana 3", "porcentaje": 25.0},
            {"id": "s4", "nombre": "Semana 4", "porcentaje": 25.0}
        ]
    return []


def repartir_en_centavos(monto_total: float, porcentajes: list) -> list:
    """Reparte un monto según porcentajes. Si suman 100, los montos suman exactamente el total:
    se trabaja en centavos enteros y los sobrantes van a los residuos mayores (ningún centavo se pierde)."""
    centavos = int((Decimal(str(monto_total)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    pesos = [Decimal(str(p)) for p in porcentajes]
    if not pesos:
        return []
    if abs(sum(pesos) - 100) > Decimal("0.01"):
        return [float((Decimal(centavos) * p / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 100) for p in pesos]
    exactos = [Decimal(centavos) * p / sum(pesos) for p in pesos]
    base = [int(e) for e in exactos]
    sobrante = centavos - sum(base)
    for i in sorted(range(len(pesos)), key=lambda i: exactos[i] - base[i], reverse=True)[:sobrante]:
        base[i] += 1
    return [c / 100 for c in base]


def calcular_distribucion_subperiodos(monto_total: float, subperiodos: list) -> list:
    """Calcula el monto asignado a cada sub-periodo en base a su porcentaje."""
    res = []
    pcts = [float(sp.get("porcentaje", 0.0)) for sp in subperiodos]
    montos = repartir_en_centavos(monto_total, pcts)
    for sp, pct, monto_sp in zip(subperiodos, pcts, montos):
        res.append({
            "id": sp.get("id"),
            "nombre": sp.get("nombre"),
            "porcentaje": pct,
            "monto": monto_sp
        })
    return res


def calcular_distribucion_tarjeta_asimetrica(
    monto_total: float,
    subperiodos_config: list,
    distribucion_custom_json: Optional[Union[str, list]] = None
) -> list:
    """Calcula la distribución de una tarjeta respetando asignaciones asimétricas si existen."""
    import json
    if distribucion_custom_json:
        try:
            custom = json.loads(distribucion_custom_json) if isinstance(distribucion_custom_json, str) else [dict(x) for x in distribucion_custom_json]
            if isinstance(custom, list) and len(custom) > 0:
                if subperiodos_config and len(custom) == len(subperiodos_config):
                    for idx, sp_cfg in enumerate(subperiodos_config):
                        custom[idx]["id"] = sp_cfg.get("id", custom[idx].get("id"))
                        custom[idx]["nombre"] = sp_cfg.get("nombre", custom[idx].get("nombre"))
                total = float(monto_total)
                suma_montos = sum(float(sp.get("monto", 0.0)) for sp in custom)
                if round(suma_montos, 2) != round(total, 2):
                    if all(sp.get("porcentaje") is not None for sp in custom):
                        montos = repartir_en_centavos(total, [float(sp["porcentaje"]) for sp in custom])
                        for sp, m in zip(custom, montos):
                            sp["monto"] = m
                    elif suma_montos > 0:
                        ratios = [float(sp.get("monto", 0.0)) / suma_montos for sp in custom]
                        montos = repartir_en_centavos(total, [r * 100.0 for r in ratios])
                        for sp, r, m in zip(custom, ratios, montos):
                            sp["monto"] = m
                            sp["porcentaje"] = round(r * 100.0, 1)
                    else:
                        for sp in custom:
                            if sp.get("porcentaje") is not None:
                                sp["monto"] = repartir_en_centavos(total, [float(sp["porcentaje"])])[0]
                for sp in custom:
                    if float(sp.get("monto", 0.0) or 0.0) <= 0:
                        sp["dia"] = None
                return custom
        except Exception:
            pass
    return calcular_distribucion_subperiodos(monto_total, subperiodos_config)


def validar_sustentabilidad_gasto_fijo(
    cursor: sqlite3.Cursor,
    tipo: str,
    monto: float,
    frecuencia: str,
    total_cuotas: Optional[int] = None,
    recurrente_id: Optional[int] = None
) -> None:
    """
    Regla 3.2: Sustentabilidad Exclusiva de Gastos Fijos vs. Remanente Temporal.
    1. Precondición: Prohibido registrar o activar gastos fijos sin al menos un ingreso fijo activo.
    2. Independencia de remanente: Un gasto fijo debe ser autosustentable con los ingresos fijos recurrentes.
    """
    if tipo == 'ingreso':
        return

    # 1. Precondición de Ingreso Fijo
    ingreso_fijo_count = cursor.execute(
        "SELECT COUNT(*) FROM recurrentes WHERE tipo = 'ingreso' AND activo = 1 AND total_cuotas IS NULL"
    ).fetchone()[0]
    if ingreso_fijo_count < 1:
        raise ValueError(
            "No es posible registrar un gasto fijo sin contar con al menos un ingreso fijo recurrente activo (ej. sueldo, pensión o nómina)."
        )

    # 2. Capacidad Fija = SUM(Ingresos Fijos) - [SUM(Gastos Fijos Previos) + SUM(Cuotas MSI)]
    recurrentes_activos = cursor.execute(
        "SELECT id, tipo, monto, frecuencia, total_cuotas, cuotas_generadas FROM recurrentes WHERE activo = 1"
    ).fetchall()

    ingresos_fijos_total = 0.0
    compromisos_previos_total = 0.0

    for r in recurrentes_activos:
        if recurrente_id is not None and r["id"] == recurrente_id:
            continue
        m = mensualizar(float(r["monto"]), r["frecuencia"])
        if r["tipo"] == 'ingreso' and r["total_cuotas"] is None:
            ingresos_fijos_total += m
        elif r["tipo"] == 'gasto':
            if r["total_cuotas"] is not None:
                if int(r["cuotas_generadas"]) < int(r["total_cuotas"]):
                    compromisos_previos_total += m
            else:
                compromisos_previos_total += m

    capacidad_fija = ingresos_fijos_total - compromisos_previos_total
    nuevo_compromiso_mensual = mensualizar(float(monto), frecuencia)

    # Tolerancia numérica por redondeo (0.01)
    if nuevo_compromiso_mensual - capacidad_fija > 0.01:
        raise ValueError(
            "No es posible registrar este compromiso como Gasto Fijo. Aunque cuentas con saldo disponible gracias al remanente de periodos pasados, tus ingresos fijos recurrentes no son suficientes para cubrirlo a largo plazo. Regístralo como un gasto normal puntual o incrementa tus ingresos fijos."
        )


def obtener_total_apartados(cursor: sqlite3.Cursor) -> float:
    """Calcula la suma de fondos retenidos en apartados activos (monto_actual)."""
    try:
        row = cursor.execute("SELECT COALESCE(SUM(monto_actual), 0.0) FROM apartados").fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0
    except Exception:
        return 0.0


def validar_saldo_disponible_fecha(
    cursor: sqlite3.Cursor,
    fecha: str,
    monto: float,
    tipo: str = 'gasto',
    transaccion_id: Optional[int] = None
) -> None:
    """
    Regla 3.3: Restricción Temporal de Fondos (No Gastar Antes de Cobrar / Sin Sobregiro).
    Invariante: Saldo Disponible Operativo(T) >= 0 para toda fecha T.
    Saldo Disponible Operativo = Saldo en Cuenta (externo) - sum(Apartados Activos).
    """
    if tipo == 'ingreso':
        if transaccion_id is not None:
            validar_edicion_transaccion(cursor, transaccion_id, fecha, monto, tipo)
        return

    # Si se edita una transacción existente, permitir la actualización para homologar con presupuesto
    if transaccion_id is not None:
        return

    # Saldo acumulado en cuenta a la fecha solicitada (solo transacciones externas: apartado_id IS NULL)
    query_base = """
        SELECT COALESCE(SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END), 0.0) AS saldo
        FROM transacciones
        WHERE fecha <= ? AND apartado_id IS NULL
    """
    params = [fecha]
    if transaccion_id is not None:
        query_base += " AND id != ?"
        params.append(transaccion_id)

    saldo_en_cuenta = float(cursor.execute(query_base, tuple(params)).fetchone()[0])
    total_apartados = obtener_total_apartados(cursor)
    saldo_operativo = saldo_en_cuenta - total_apartados

    if saldo_operativo - monto < -0.001:
        deficit = abs(saldo_operativo - monto)
        if total_apartados > 0 and (saldo_en_cuenta - monto >= -0.001):
            raise ValueError(
                f"Fondos insuficientes: no es posible registrar un gasto de ${monto:,.2f} con fecha {fecha}. "
                f"El saldo disponible operativo es de ${saldo_operativo:,.2f} (saldo en cuenta ${saldo_en_cuenta:,.2f} "
                f"menos ${total_apartados:,.2f} retenidos en apartados). Este gasto comprometería fondos guardados en apartados."
            )
        else:
            raise ValueError(
                f"Fondos insuficientes: no es posible registrar un gasto de ${monto:,.2f} con fecha {fecha}. "
                f"El saldo disponible a esa fecha sería de -${deficit:,.2f}. "
                f"No puedes gastar antes de recibir tus ingresos a menos que cuentes con saldo transferido de periodos pasados."
            )

    # Verificar fechas posteriores para asegurar que no se produzca sobregiro acumulativo
    query_post = """
        SELECT fecha, SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END) as delta
        FROM transacciones
        WHERE fecha > ? AND apartado_id IS NULL
    """
    params_post = [fecha]
    if transaccion_id is not None:
        query_post += " AND id != ?"
        params_post.append(transaccion_id)
    query_post += " GROUP BY fecha ORDER BY fecha ASC"

    filas_post = cursor.execute(query_post, tuple(params_post)).fetchall()
    saldo_corriente = saldo_operativo - monto
    for fp in filas_post:
        saldo_corriente += float(fp["delta"])
        if saldo_corriente < -0.001:
            deficit_post = abs(saldo_corriente)
            if total_apartados > 0:
                raise ValueError(
                    f"Fondos insuficientes: registrar este gasto provocaría un saldo negativo de -${deficit_post:,.2f} "
                    f"en fecha posterior ({fp['fecha']}) comprometiendo fondos retenidos en apartados."
                )
            else:
                raise ValueError(
                    f"Fondos insuficientes: registrar este gasto provocaría un saldo negativo de -${deficit_post:,.2f} en fecha posterior ({fp['fecha']})."
                )


def validar_eliminacion_transaccion(cursor: sqlite3.Cursor, transaccion_id: int) -> None:
    """
    Protección Retroactiva de Ingresos:
    Invariante: un ingreso histórico no puede eliminarse si respalda gastos ejecutados posteriormente.
    Simula la deducción del ingreso para toda fecha t >= T_ingreso.
    Si provoca saldo operativo negativo en cualquier fecha t >= T_ingreso, lanza ValueError.
    """
    trans = cursor.execute("SELECT * FROM transacciones WHERE id = ?", (transaccion_id,)).fetchone()
    if not trans:
        return
    if trans["tipo"] != 'ingreso':
        return

    # Los movimientos vinculados a apartados son movimientos internos de metas; no bloquean como ingreso externo
    if "apartado_id" in trans.keys() and trans["apartado_id"] is not None:
        return

    t_ingreso = trans["fecha"]
    monto_ingreso = float(trans["monto"])
    total_apartados = obtener_total_apartados(cursor)

    row_prev = cursor.execute("""
        SELECT COALESCE(SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END), 0.0)
        FROM transacciones
        WHERE fecha < ? AND apartado_id IS NULL AND id != ?
    """, (t_ingreso, transaccion_id)).fetchone()
    saldo_corriente = float(row_prev[0]) - total_apartados

    filas = cursor.execute("""
        SELECT fecha, SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END) as delta
        FROM transacciones
        WHERE fecha >= ? AND apartado_id IS NULL AND id != ?
        GROUP BY fecha
        ORDER BY fecha ASC
    """, (t_ingreso, transaccion_id)).fetchall()

    deltas_por_fecha = {row["fecha"]: float(row["delta"]) for row in filas}
    if t_ingreso not in deltas_por_fecha:
        deltas_por_fecha[t_ingreso] = 0.0

    for f in sorted(deltas_por_fecha.keys()):
        saldo_corriente += deltas_por_fecha[f]
        if saldo_corriente < -0.001:
            deficit = abs(saldo_corriente)
            raise ValueError(
                f"No es posible eliminar este ingreso de ${monto_ingreso:,.2f} con fecha {t_ingreso}: "
                f"dicho ingreso respalda gastos ya ejecutados. Provocaría un saldo negativo de -${deficit:,.2f} "
                f"en fecha {f}."
            )


def validar_edicion_transaccion(
    cursor: sqlite3.Cursor,
    transaccion_id: int,
    nueva_fecha: str,
    nuevo_monto: float,
    nuevo_tipo: str
) -> None:
    """
    Protección Retroactiva de Ingresos:
    Invariante: si se reduce el monto de un ingreso, se posterga su fecha o se cambia a gasto,
    simula la cronología resultante. Si genera déficit en cualquier fecha t, lanza ValueError.
    """
    trans = cursor.execute("SELECT * FROM transacciones WHERE id = ?", (transaccion_id,)).fetchone()
    if not trans:
        return

    if "apartado_id" in trans.keys() and trans["apartado_id"] is not None:
        raise ValueError("No puedes modificar un movimiento vinculado a un apartado de ahorro. Utiliza la sección Metas / Apartados.")

    tipo_orig = trans["tipo"]
    fecha_orig = trans["fecha"]

    if tipo_orig != 'ingreso' and nuevo_tipo != 'ingreso':
        return

    total_apartados = obtener_total_apartados(cursor)
    t_min = min(fecha_orig, nueva_fecha)

    row_prev = cursor.execute("""
        SELECT COALESCE(SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END), 0.0)
        FROM transacciones
        WHERE fecha < ? AND apartado_id IS NULL AND id != ?
    """, (t_min, transaccion_id)).fetchone()
    saldo_corriente = float(row_prev[0]) - total_apartados

    filas = cursor.execute("""
        SELECT fecha, SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END) as delta
        FROM transacciones
        WHERE fecha >= ? AND apartado_id IS NULL AND id != ?
        GROUP BY fecha
        ORDER BY fecha ASC
    """, (t_min, transaccion_id)).fetchall()

    deltas_por_fecha = {row["fecha"]: float(row["delta"]) for row in filas}

    delta_nuevo = nuevo_monto if nuevo_tipo == 'ingreso' else -nuevo_monto
    deltas_por_fecha[nueva_fecha] = deltas_por_fecha.get(nueva_fecha, 0.0) + delta_nuevo
    if fecha_orig not in deltas_por_fecha:
        deltas_por_fecha[fecha_orig] = 0.0

    for f in sorted(deltas_por_fecha.keys()):
        saldo_corriente += deltas_por_fecha[f]
        if saldo_corriente < -0.001:
            deficit = abs(saldo_corriente)
            raise ValueError(
                f"No es posible modificar este ingreso: dicho ingreso respalda gastos ya ejecutados. "
                f"Provocaría un saldo negativo de -${deficit:,.2f} en fecha {f}."
            )


def _safe_date(year: int, month: int, day: int) -> date:
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, max_day))


def obtener_ventana_corte_fecha(
    cursor: sqlite3.Cursor,
    fecha_str: str,
    transaccion_id: Optional[int] = None
) -> dict:
    """
    Regla 3.1: Esquema de Períodos de Pago Personalizados & Delimitación de Ventana Activa.
    Calcula los cortes de pago [D1, D2 - 1] para la fecha dada y determina la capacidad de la ventana.
    """
    f_target = date.fromisoformat(fecha_str)

    # Sincronización automática de nómina de presupuesto si la configuración fue actualizada
    cfg = cursor.execute("SELECT * FROM presupuesto_config WHERE id = 1").fetchone()
    cfg_ingreso = float(cfg["ingreso_base"]) if cfg and "ingreso_base" in cfg.keys() and cfg["ingreso_base"] else 0.0
    cfg_frecuencia = (cfg["frecuencia"] if cfg and "frecuencia" in cfg.keys() and cfg["frecuencia"] else "mensual")
    cfg_dia = int(cfg["dia_pago"]) if cfg and "dia_pago" in cfg.keys() and cfg["dia_pago"] is not None else None

    if cfg_ingreso > 0:
        cursor.execute("""
            UPDATE recurrentes
            SET monto = ?, frecuencia = ?
            WHERE tipo = 'ingreso' AND activo = 1 AND total_cuotas IS NULL
            AND (presupuesto_tarjeta_id IS NOT NULL OR LOWER(descripcion) LIKE '%nomina%' OR LOWER(descripcion) LIKE '%nómina%' OR LOWER(descripcion) LIKE '%ingreso base%')
        """, (cfg_ingreso, cfg_frecuencia))

    # Obtener ingresos fijos activos para deducir fechas de corte
    ingresos = cursor.execute("""
        SELECT dia, monto, frecuencia FROM recurrentes
        WHERE tipo = 'ingreso' AND activo = 1 AND total_cuotas IS NULL
        ORDER BY dia ASC
    """).fetchall()

    if not ingresos:
        if cfg_ingreso > 0:
            d_base = cfg_dia if cfg_dia is not None else (15 if cfg_frecuencia == 'quincenal' else 1)
            if cfg_frecuencia == 'quincenal':
                if d_base == 15 or d_base >= 28:
                    dias_corte = {15, 30}
                elif d_base == 1 or d_base == 16:
                    dias_corte = {1, 16}
                else:
                    dias_corte = {d_base, d_base + 15 if d_base <= 15 else d_base - 15}
            else:
                dias_corte = {d_base}
            dias_ordenados = sorted(list(dias_corte))

            cortes_previos = [d for d in dias_ordenados if _safe_date(f_target.year, f_target.month, d) <= f_target]
            if cortes_previos:
                d1_dia = cortes_previos[-1]
                idx = dias_ordenados.index(d1_dia)
                d_inicio = _safe_date(f_target.year, f_target.month, d1_dia)
                if idx + 1 < len(dias_ordenados):
                    d2_dia = dias_ordenados[idx + 1]
                    d_next = _safe_date(f_target.year, f_target.month, d2_dia)
                else:
                    d2_dia = dias_ordenados[0]
                    m_next = 1 if f_target.month == 12 else f_target.month + 1
                    y_next = f_target.year + 1 if f_target.month == 12 else f_target.year
                    d_next = _safe_date(y_next, m_next, d2_dia)
                d_fin = d_next - timedelta(days=1)
            else:
                d1_dia = dias_ordenados[-1]
                d2_dia = dias_ordenados[0]
                m_prev = 12 if f_target.month == 1 else f_target.month - 1
                y_prev = f_target.year - 1 if f_target.month == 1 else f_target.year
                d_inicio = _safe_date(y_prev, m_prev, d1_dia)
                d_next = _safe_date(f_target.year, f_target.month, d2_dia)
                d_fin = d_next - timedelta(days=1)

            ingreso_ventana = cfg_ingreso if cfg_frecuencia == 'quincenal' else cfg_ingreso / max(1, len(dias_ordenados))
        else:
            d_inicio = date(f_target.year, f_target.month, 1)
            max_d = calendar.monthrange(f_target.year, f_target.month)[1]
            d_fin = date(f_target.year, f_target.month, max_d)
            ingreso_ventana = 0.0
    else:
        dias_corte = set()
        for ing in ingresos:
            d = int(ing["dia"]) if ing["dia"] else 1
            if ing["frecuencia"] == 'quincenal':
                if d == 15 or d >= 28:
                    dias_corte.add(15)
                    dias_corte.add(30)
                elif d == 1 or d == 16:
                    dias_corte.add(1)
                    dias_corte.add(16)
                else:
                    dias_corte.add(d)
                    dias_corte.add(d + 15 if d <= 15 else d - 15)
            else:
                dias_corte.add(d)

        dias_ordenados = sorted(list(dias_corte))

        # Encontrar corte correspondiente para f_target
        cortes_previos = [d for d in dias_ordenados if _safe_date(f_target.year, f_target.month, d) <= f_target]
        if cortes_previos:
            d1_dia = cortes_previos[-1]
            idx = dias_ordenados.index(d1_dia)
            d_inicio = _safe_date(f_target.year, f_target.month, d1_dia)
            if idx + 1 < len(dias_ordenados):
                d2_dia = dias_ordenados[idx + 1]
                d_next = _safe_date(f_target.year, f_target.month, d2_dia)
            else:
                d2_dia = dias_ordenados[0]
                m_next = 1 if f_target.month == 12 else f_target.month + 1
                y_next = f_target.year + 1 if f_target.month == 12 else f_target.year
                d_next = _safe_date(y_next, m_next, d2_dia)
            d_fin = d_next - timedelta(days=1)
        else:
            d1_dia = dias_ordenados[-1]
            d2_dia = dias_ordenados[0]
            m_prev = 12 if f_target.month == 1 else f_target.month - 1
            y_prev = f_target.year - 1 if f_target.month == 1 else f_target.year
            d_inicio = _safe_date(y_prev, m_prev, d1_dia)
            d_next = _safe_date(f_target.year, f_target.month, d2_dia)
            d_fin = d_next - timedelta(days=1)

        ingreso_ventana = 0.0
        for ing in ingresos:
            m = float(ing["monto"])
            if ing["frecuencia"] == 'quincenal':
                ingreso_ventana += m
            elif ing["frecuencia"] == 'semanal':
                ingreso_ventana += m * 2
            else:
                ingreso_ventana += m / max(1, len(dias_ordenados))

    # Reconocer ingresos reales registrados en transacciones durante la ventana
    query_ingresos_reales = """
        SELECT COALESCE(SUM(monto), 0.0) FROM transacciones
        WHERE tipo = 'ingreso' AND fecha BETWEEN ? AND ?
    """
    params_ing = [d_inicio.isoformat(), d_fin.isoformat()]
    if transaccion_id is not None:
        query_ingresos_reales += " AND id != ?"
        params_ing.append(transaccion_id)
    ingresos_reales = float(cursor.execute(query_ingresos_reales, tuple(params_ing)).fetchone()[0])
    ingreso_ventana = max(ingreso_ventana, ingresos_reales)

    query_rem = """
        SELECT COALESCE(SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END), 0.0)
        FROM transacciones WHERE fecha < ?
    """
    params_rem = [d_inicio.isoformat()]
    if transaccion_id is not None:
        query_rem += " AND id != ?"
        params_rem.append(transaccion_id)
    remanente = cursor.execute(query_rem, tuple(params_rem)).fetchone()[0]

    remanente_disponible = max(0.0, float(remanente))
    capacidad_total = round(ingreso_ventana + remanente_disponible, 2)

    query_cargos = """
        SELECT COALESCE(SUM(monto), 0.0) FROM transacciones
        WHERE tipo = 'gasto' AND fecha BETWEEN ? AND ?
    """
    params_cargos = [d_inicio.isoformat(), d_fin.isoformat()]
    if transaccion_id is not None:
        query_cargos += " AND id != ?"
        params_cargos.append(transaccion_id)
    cargos = cursor.execute(query_cargos, tuple(params_cargos)).fetchone()[0]

    return {
        "inicio": d_inicio.isoformat(),
        "fin": d_fin.isoformat(),
        "ingreso_ventana": round(ingreso_ventana, 2),
        "remanente_disponible": round(remanente_disponible, 2),
        "capacidad_total": capacidad_total,
        "cargos_actuales": round(float(cargos), 2)
    }


def validar_limite_cargos_ventana(
    cursor: sqlite3.Cursor,
    fecha_str: str,
    monto: float,
    transaccion_id: Optional[int] = None
) -> None:
    """Valida que la suma de cargos en la ventana activa no exceda la capacidad asignada."""
    if transaccion_id is not None:
        # Permitir edición de transacciones existentes para homologar con la edición individual de presupuesto
        return

    v = obtener_ventana_corte_fecha(cursor, fecha_str, transaccion_id=transaccion_id)
    if v["capacidad_total"] > 0:
        if v["cargos_actuales"] + monto - v["capacidad_total"] > 0.01:
            total_proyectado = v["cargos_actuales"] + monto
            raise ValueError(
                f"El presupuesto del período ({v['inicio']} al {v['fin']}) ha sido superado. "
                f"La capacidad total asignada es de ${v['capacidad_total']:,.2f} y los compromisos acumulados alcanzarían ${total_proyectado:,.2f}."
            )


def sugerir_icono_color(descripcion: str, categoria: str = "") -> tuple:
    """Sugiere icono SVG y token de color según nombre del concepto y categoría."""
    txt = f"{descripcion or ''} {categoria or ''}".lower()
    if any(k in txt for k in ["etf", "cet", "cetes", "bolsa", "invers", "rendimiento", "gbm", "fondo"]):
        return "trending-up", "emerald"
    if any(k in txt for k in ["nu", "ahorro", "apartado", "meta", "reserva", "emergencia"]):
        return "shield", "purple"
    if any(k in txt for k in ["dentista", "doctor", "salud", "medico", "farmacia", "seguro", "mama", "madre"]):
        return "heart", "rose"
    if any(k in txt for k in ["comida", "despensa", "super", "restaurante", "alimento"]):
        return "shopping-bag", "amber"
    if any(k in txt for k in ["gasolina", "transporte", "auto", "uber", "coche", "camion"]):
        return "wallet", "cyan"
    if any(k in txt for k in ["renta", "vivienda", "casa", "depa", "hipoteca"]):
        return "home", "blue"
    if any(k in txt for k in ["streaming", "netflix", "spotify", "ocio", "juego", "cine"]):
        return "smile", "purple"
    if any(k in txt for k in ["msi", "meses", "tarjeta", "credito"]):
        return "credit-card", "amber"
    if any(k in txt for k in ["luz", "agua", "internet", "cfe", "gas", "servicio", "telefono"]):
        return "home", "blue"
    if any(k in txt for k in ["curso", "escuela", "educacion", "libro", "colegiatura"]):
        return "book", "cyan"
    return "wallet", "blue"


def detectar_fijos_recurrentes_core(cursor: sqlite3.Cursor, frecuencia_destino: str) -> dict:
    """Detecta nómina y gastos periódicos activos con desglose granular."""
    filas = cursor.execute("""
        SELECT r.*, c.nombre as categoria_nombre 
        FROM recurrentes r
        LEFT JOIN categorias c ON r.categoria_id = c.id
        WHERE r.activo = 1
    """).fetchall()
    ingreso_mensual = 0.0
    gasto_mensual = 0.0
    msi_mensual = 0.0
    items_ingreso = []
    items_gasto = []

    for r in filas:
        m = mensualizar(float(r["monto"]), r["frecuencia"])
        m_periodo = convertir_de_mensual(m, frecuencia_destino)
        es_msi = (r["total_cuotas"] is not None)
        icono, color = sugerir_icono_color(r["descripcion"], r["categoria_nombre"])

        item_data = {
            "id": r["id"],
            "descripcion": r["descripcion"],
            "tipo": r["tipo"],
            "es_msi": es_msi,
            "monto_original": float(r["monto"]),
            "frecuencia_original": r["frecuencia"],
            "monto_mensual": round(m, 2),
            "monto_periodo": round(m_periodo, 2),
            "cuotas_info": f"Mes {int(r['cuotas_generadas']) + 1}/{r['total_cuotas']}" if es_msi else None,
            "categoria_id": r["categoria_id"],
            "categoria_nombre": r["categoria_nombre"] or "General",
            "icono": "credit-card" if es_msi else icono,
            "color": "amber" if es_msi else color
        }

        if r["tipo"] == 'ingreso' and r["total_cuotas"] is None:
            ingreso_mensual += m
            items_ingreso.append(item_data)
        elif r["tipo"] == 'gasto':
            if es_msi:
                if int(r["cuotas_generadas"]) < int(r["total_cuotas"]):
                    msi_mensual += m
                    items_gasto.append(item_data)
            else:
                gasto_mensual += m
                items_gasto.append(item_data)

    return {
        "ingreso_fijo_periodo": convertir_de_mensual(ingreso_mensual, frecuencia_destino),
        "gasto_fijo_periodo": convertir_de_mensual(gasto_mensual, frecuencia_destino),
        "cuota_msi_periodo": convertir_de_mensual(msi_mensual, frecuencia_destino),
        "ingreso_fijo_mensual": round(ingreso_mensual, 2),
        "gasto_fijo_mensual": round(gasto_mensual, 2),
        "cuota_msi_mensual": round(msi_mensual, 2),
        "items_ingreso": items_ingreso,
        "items_gasto": items_gasto,
        "conteo_gastos": len(items_gasto)
    }


def sincronizar_fijos_presupuesto_core(
    cursor: sqlite3.Cursor,
    modo: str = "individual",
    recurrentes_ids: list = None,
    actualizar_ingreso: bool = True,
    reemplazar: bool = False
) -> None:
    """Aplica la sincronización de nómina y gastos periódicos a las tarjetas del presupuesto."""
    cfg = cursor.execute("SELECT ingreso_base, frecuencia FROM presupuesto_config WHERE id = 1").fetchone()
    frecuencia = cfg["frecuencia"] if cfg else "mensual"
    fijos = detectar_fijos_recurrentes_core(cursor, frecuencia)

    ingreso_importado = fijos["ingreso_fijo_periodo"]
    if actualizar_ingreso and ingreso_importado > 0:
        cursor.execute("""
            UPDATE presupuesto_config
            SET ingreso_base = ?, actualizado_en = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (ingreso_importado,))
        ingreso_actual = ingreso_importado
    else:
        ingreso_actual = float(cfg["ingreso_base"]) if cfg else 0.0

    if reemplazar:
        cursor.execute("DELETE FROM presupuesto_tarjetas")
    else:
        if modo in ("individual", "categoria"):
            cursor.execute("""
                DELETE FROM presupuesto_tarjetas 
                WHERE notas = 'Importado de Fijos y MSI' 
                   OR notas = 'Cuotas mensuales activas'
                   OR (nombre LIKE '%Gastos Fijos%' AND (notas LIKE '%Fijos%' OR notas LIKE '%Renta, servicios%'))
                   OR (nombre LIKE '%Compras a Meses%' AND (notas LIKE '%MSI activas%' OR notas LIKE '%Cuotas%'))
            """)

    items_a_procesar = []
    for it in fijos.get("items_gasto", []):
        if recurrentes_ids is None or it["id"] in recurrentes_ids:
            items_a_procesar.append(it)

    if modo == "unificado":
        gasto_total = sum(it["monto_periodo"] for it in items_a_procesar if not it["es_msi"])
        msi_total = sum(it["monto_periodo"] for it in items_a_procesar if it["es_msi"])

        if gasto_total > 0:
            pct_gasto = round((gasto_total / ingreso_actual) * 100.0, 2) if ingreso_actual > 0 else 0.0
            existente = cursor.execute("SELECT id FROM presupuesto_tarjetas WHERE nombre LIKE '%Gastos Fijos%' LIMIT 1").fetchone()
            if existente:
                cursor.execute("""
                    UPDATE presupuesto_tarjetas
                    SET monto = ?, porcentaje = ?, notas = 'Importado de Fijos y MSI'
                    WHERE id = ?
                """, (gasto_total, pct_gasto, existente["id"]))
            else:
                cursor.execute("""
                    INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden)
                    VALUES ('Gastos Fijos', 'home', 'blue', ?, ?, 'Renta, servicios y suscripciones fijas', 10)
                """, (pct_gasto, gasto_total))

        if msi_total > 0:
            pct_msi = round((msi_total / ingreso_actual) * 100.0, 2) if ingreso_actual > 0 else 0.0
            existente_msi = cursor.execute("SELECT id FROM presupuesto_tarjetas WHERE nombre LIKE '%Meses%' OR nombre LIKE '%MSI%' LIMIT 1").fetchone()
            if existente_msi:
                cursor.execute("""
                    UPDATE presupuesto_tarjetas
                    SET monto = ?, porcentaje = ?, notas = 'Cuotas mensuales activas'
                    WHERE id = ?
                """, (msi_total, pct_msi, existente_msi["id"]))
            else:
                cursor.execute("""
                    INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden)
                    VALUES ('Compras a Meses (MSI)', 'credit-card', 'amber', ?, ?, 'Cuotas mensuales de MSI activas', 20)
                """, (pct_msi, msi_total))

    elif modo == "categoria":
        grupos = {}
        for it in items_a_procesar:
            cat = it["categoria_nombre"] or "General"
            if cat not in grupos:
                grupos[cat] = {
                    "monto": 0.0,
                    "icono": it["icono"],
                    "color": it["color"],
                    "items": []
                }
            grupos[cat]["monto"] += it["monto_periodo"]
            grupos[cat]["items"].append(it["descripcion"])

        orden = 10
        for cat_nombre, info in grupos.items():
            monto_cat = round(info["monto"], 2)
            pct_cat = round((monto_cat / ingreso_actual) * 100.0, 2) if ingreso_actual > 0 else 0.0
            notas = ", ".join(info["items"][:3])
            existente = cursor.execute("SELECT id FROM presupuesto_tarjetas WHERE LOWER(nombre) = LOWER(?) LIMIT 1", (cat_nombre,)).fetchone()
            if existente:
                cursor.execute("""
                    UPDATE presupuesto_tarjetas
                    SET monto = ?, porcentaje = ?, notas = ?
                    WHERE id = ?
                """, (monto_cat, pct_cat, notas, existente["id"]))
            else:
                cursor.execute("""
                    INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (cat_nombre, info["icono"], info["color"], pct_cat, monto_cat, notas, orden))
                orden += 10

    else:
        # Modo individual
        orden = 10
        for it in items_a_procesar:
            monto_item = round(it["monto_periodo"], 2)
            pct_item = round((monto_item / ingreso_actual) * 100.0, 2) if ingreso_actual > 0 else 0.0
            nombre_item = it["descripcion"]
            if it["es_msi"] and "msi" not in nombre_item.lower():
                nombre_item = f"{nombre_item} (MSI)"

            notas = it["cuotas_info"] if it["es_msi"] else it["categoria_nombre"]

            existente = cursor.execute("""
                SELECT id FROM presupuesto_tarjetas 
                WHERE LOWER(nombre) = LOWER(?)
                   OR LOWER(nombre) = LOWER(?)
                   OR (LOWER(nombre) LIKE '%' || LOWER(?) || '%' AND LENGTH(?) >= 3)
                   OR (LOWER(?) LIKE '%' || LOWER(nombre) || '%' AND LENGTH(nombre) >= 3)
                LIMIT 1
            """, (nombre_item, it["descripcion"], it["descripcion"], it["descripcion"], it["descripcion"])).fetchone()

            if existente:
                cursor.execute("""
                    UPDATE presupuesto_tarjetas
                    SET monto = ?, porcentaje = ?, notas = COALESCE(NULLIF(notas, ''), ?)
                    WHERE id = ?
                """, (monto_item, pct_item, notas, existente["id"]))
            else:
                cursor.execute("""
                    INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (nombre_item, it["icono"], it["color"], pct_item, monto_item, notas, orden))
                orden += 10

    if actualizar_ingreso and ingreso_importado > 0:
        cursor.execute("SELECT id, monto FROM presupuesto_tarjetas")
        for t in cursor.fetchall():
            m = float(t["monto"])
            pct = round((m / ingreso_actual) * 100.0, 2) if ingreso_actual > 0 else 0.0
            cursor.execute("UPDATE presupuesto_tarjetas SET porcentaje = ? WHERE id = ?", (pct, t["id"]))


def resolver_categoria_por_nombre(cursor: sqlite3.Cursor, texto: str, tipo: str = 'gasto') -> int:
    """Asigna la categoría idónea de acuerdo al nombre o descripción del gasto o ingreso."""
    filas = cursor.execute("SELECT id, nombre FROM categorias WHERE tipo = ?", (tipo,)).fetchall()
    if not filas:
        return 1
    t_lower = (texto or "").lower()
    for f in filas:
        if f["nombre"].lower() == t_lower:
            return f["id"]
    for f in filas:
        nom_cat = f["nombre"].lower()
        if nom_cat in t_lower or t_lower in nom_cat:
            return f["id"]
    if any(k in t_lower for k in ("etf", "etfs", "gbm", "bolsa", "cetes", "nu", "fondo", "inversion", "inversión")):
        for f in filas:
            if "inversion" in f["nombre"].lower() or "inversión" in f["nombre"].lower():
                return f["id"]
    if any(k in t_lower for k in ("nomina", "nómina", "sueldo", "salario")):
        for f in filas:
            if "nomina" in f["nombre"].lower() or "nómina" in f["nombre"].lower():
                return f["id"]
    if any(k in t_lower for k in ("salud", "dentista", "doctor", "farmacia", "medico", "médico")):
        for f in filas:
            if "salud" in f["nombre"].lower():
                return f["id"]
    if any(k in t_lower for k in ("transporte", "gasolina", "uber", "coche", "auto")):
        for f in filas:
            if "transporte" in f["nombre"].lower():
                return f["id"]
    if any(k in t_lower for k in ("comida", "despensa", "super", "súper", "restaurante")):
        for f in filas:
            if "comida" in f["nombre"].lower():
                return f["id"]
    if any(k in t_lower for k in ("servicio", "servicios", "renta", "madre", "luz", "agua", "internet")):
        for f in filas:
            if "servicio" in f["nombre"].lower() or "vivienda" in f["nombre"].lower():
                return f["id"]
    return filas[0]["id"]


def calcular_fecha_inicio_exportacion(
    inicio_cargos: str = 'natural',
    fecha_base_custom: Optional[date] = None,
    hoy: Optional[date] = None
) -> date:
    """
    Calcula la fecha de corte/inicio para la exportación de presupuesto a gastos fijos.
    Permite evitar cobros retroactivos de fechas ya pasadas en el mes:
      - 'natural': parte desde hoy.
      - 'siguiente_quincena': si hoy <= 15, arranca el día 16; si hoy > 15, arranca el día 1 del próximo mes.
      - 'proximo_mes': arranca el día 1 del próximo mes.
      - 'personalizada': utiliza fecha_base_custom.
    """
    hoy_d = hoy or ahora_local().date()
    if inicio_cargos == 'personalizada' and fecha_base_custom:
        return fecha_base_custom
    if fecha_base_custom and inicio_cargos not in ('siguiente_quincena', 'proximo_mes', 'natural'):
        return fecha_base_custom

    if inicio_cargos == 'proximo_mes':
        anio, mes = (hoy_d.year + 1, 1) if hoy_d.month == 12 else (hoy_d.year, hoy_d.month + 1)
        return date(anio, mes, 1)

    if inicio_cargos == 'siguiente_quincena':
        if hoy_d.day <= 15:
            return date(hoy_d.year, hoy_d.month, 16)
        anio, mes = (hoy_d.year + 1, 1) if hoy_d.month == 12 else (hoy_d.year, hoy_d.month + 1)
        return date(anio, mes, 1)

    return hoy_d


NOMINA_PRESUPUESTO = "Nómina Fija (Presupuesto)"

# Pagos por frecuencia del ingreso fijo: semanal 4 (semanas), quincenal 2 (quincenas), mensual 1.
UNIDADES_POR_FRECUENCIA = {'semanal': 4, 'quincenal': 2, 'mensual': 1}


def unidades_activas(frecuencia: str) -> int:
    return UNIDADES_POR_FRECUENCIA.get(frecuencia, 1)


def validar_dias_unidades(dias: dict) -> None:
    """dias: día de cobro por pago que se va a exportar ({orden: día}). Lanza ValueError si a alguno le falta
    la fecha o no van en orden creciente (cada pago después del anterior)."""
    ordenados = sorted(dias.items())
    if any(d is None for _, d in ordenados):
        raise ValueError("Define la fecha de cada pago que tiene tarjetas antes de exportar.")
    valores = [d for _, d in ordenados]
    if any(a >= b for a, b in zip(valores, valores[1:])):
        raise ValueError("Las fechas de pago deben ir en orden creciente: cada pago después del anterior.")


def _es_del_presupuesto(fila) -> bool:
    """Fijo que administra el presupuesto: ligado a una tarjeta o la nómina que él crea.
    Un fijo hecho a mano nunca se empareja con el presupuesto, aunque comparta nombre."""
    return fila["presupuesto_tarjeta_id"] is not None or fila["descripcion"] == NOMINA_PRESUPUESTO


def _sin_cargo_previo(cursor: sqlite3.Cursor, item: dict) -> date:
    """Primera fecha >= la propuesta sin un movimiento ya registrado con el mismo nombre."""
    prox = item["proxima"]
    while cursor.execute(
        "SELECT 1 FROM transacciones WHERE descripcion = ? AND fecha = ? AND tipo = ? LIMIT 1",
        (item["descripcion"], prox.isoformat(), item["tipo"]),
    ).fetchone():
        prox = siguiente_fecha(prox, item["frecuencia"], item["dia"])
    return prox


def exportar_presupuesto_a_fijos_core(
    cursor: sqlite3.Cursor,
    tarjetas_ids: list = None,
    incluir_ingreso: bool = True,
    fecha_base: date = None
) -> dict:
    """
    Reemplazo total. Se borran todos los fijos, incluidos los aportes a metas y las compras a meses
    (MSI) que no estén en el presupuesto, y se crean desde los pagos activos:
    - La nómina del ingreso fijo, con la frecuencia del presupuesto.
    - Cada tarjeta de pago: un fijo mensual en la fecha de su pago. Una tarjeta MSI es una compra a
      cuotas con el plazo indicado.
    Los fijos del presupuesto que siguen vigentes se actualizan en su lugar para no duplicar el cargo
    del mes. Los movimientos ya generados se conservan, sin enlace a un fijo.
    Lanza ValueError si las unidades activas no tienen fechas válidas.
    """
    hoy = fecha_base or ahora_local().date()
    if cursor.connection and not cursor.connection.row_factory:
        cursor.connection.row_factory = sqlite3.Row
    cfg = cursor.execute("SELECT * FROM presupuesto_config WHERE id = 1").fetchone()
    frecuencia = (cfg["frecuencia"] if cfg else "mensual")
    ingreso_base = float(cfg["ingreso_base"] if cfg else 0.0)
    dia_pago_ingreso = None
    try:
        if cfg and "dia_pago" in cfg.keys() and cfg["dia_pago"] is not None:
            dia_pago_ingreso = int(cfg["dia_pago"])
    except Exception:
        pass

    unidades = cursor.execute("SELECT orden, dia_pago FROM presupuesto_unidades ORDER BY orden").fetchall()
    dias_unidades = {u["orden"]: u["dia_pago"] for u in unidades}
    activas = unidades_activas(frecuencia)
    con_tarjetas = {r["unidad_orden"] for r in cursor.execute(
        "SELECT DISTINCT unidad_orden FROM presupuesto_tarjetas WHERE unidad_orden IS NOT NULL AND monto > 0 AND unidad_orden <= ?",
        (activas,),
    ).fetchall()}
    validar_dias_unidades({o: dias_unidades.get(o) for o in sorted(con_tarjetas)})
    excedidas = cursor.execute("""
        SELECT unidad_orden, SUM(monto) AS total FROM presupuesto_tarjetas
        WHERE unidad_orden IS NOT NULL AND unidad_orden <= ?
        GROUP BY unidad_orden HAVING SUM(monto) > ? + 0.005
        ORDER BY unidad_orden
    """, (activas, ingreso_base)).fetchall()
    if excedidas:
        orden = excedidas[0]["unidad_orden"]
        raise ValueError(f"Las tarjetas del pago {orden} suman más que su ingreso. Ajústalas antes de exportar.")

    def _calc_proxima_fecha(dia_n: Optional[int], freq: str, hoy_d: date) -> tuple[int, date]:
        if dia_n is not None:
            dia_f = dia_n
            if freq == 'mensual':
                max_d = ultimo_dia_mes(hoy_d.year, hoy_d.month)
                if hoy_d.day <= dia_f:
                    prox = date(hoy_d.year, hoy_d.month, min(dia_f, max_d))
                else:
                    a, m = (hoy_d.year + 1, 1) if hoy_d.month == 12 else (hoy_d.year, hoy_d.month + 1)
                    prox = date(a, m, min(dia_f, ultimo_dia_mes(a, m)))
            elif freq == 'quincenal':
                dia2 = (dia_f + 15) if dia_f <= 15 else (dia_f - 15)
                c1, c2 = min(dia_f, dia2), max(dia_f, dia2)
                if hoy_d.day <= c1:
                    prox = date(hoy_d.year, hoy_d.month, min(c1, ultimo_dia_mes(hoy_d.year, hoy_d.month)))
                elif hoy_d.day <= c2:
                    prox = date(hoy_d.year, hoy_d.month, min(c2, ultimo_dia_mes(hoy_d.year, hoy_d.month)))
                else:
                    a, m = (hoy_d.year + 1, 1) if hoy_d.month == 12 else (hoy_d.year, hoy_d.month + 1)
                    prox = date(a, m, min(c1, ultimo_dia_mes(a, m)))
            else:
                prox = hoy_d
        else:
            prox = primera_fecha_quincenal(hoy_d) if freq == 'quincenal' else hoy_d
            dia_f = prox.day
        return dia_f, prox

    # 1. Estado deseado: lo que el presupuesto debe tener como fijos
    deseados = []
    detalles = []
    if incluir_ingreso and ingreso_base > 0:
        cat_ingreso = resolver_categoria_por_nombre(cursor, "Nómina", tipo="ingreso")
        dia_fijo, proxima = _calc_proxima_fecha(dia_pago_ingreso, frecuencia, hoy)
        deseados.append({
            "tarjeta_id": None, "tipo": "ingreso", "descripcion": NOMINA_PRESUPUESTO,
            "monto": round(ingreso_base, 2), "frecuencia": frecuencia, "dia": dia_fijo,
            "proxima": proxima, "categoria_id": cat_ingreso, "apartado_id": None,
        })

    tarjetas_query = "SELECT * FROM presupuesto_tarjetas"
    if tarjetas_ids:
        placeholders = ",".join("?" for _ in tarjetas_ids)
        tarjetas_query += f" WHERE id IN ({placeholders})"
        filas_tarjetas = cursor.execute(tarjetas_query, tuple(tarjetas_ids)).fetchall()
    else:
        filas_tarjetas = cursor.execute(tarjetas_query).fetchall()

    for t in filas_tarjetas:
        monto_tarjeta = float(t["monto"])
        if monto_tarjeta <= 0:
            continue
        nombre = t["nombre"].strip()
        tarjeta_id = t["id"]

        cat_id = resolver_categoria_por_nombre(cursor, f"{nombre} {t['notas'] or ''}", tipo="gasto")
        ap = cursor.execute("SELECT id FROM apartados WHERE LOWER(TRIM(nombre)) = LOWER(?) LIMIT 1", (nombre,)).fetchone()
        apartado_id = ap["id"] if ap else None

        # Tarjeta de una unidad de pago: un fijo mensual en la fecha de su pago, sin subdivisiones
        if t["unidad_orden"] is not None:
            if t["unidad_orden"] > activas:
                continue
            dia_f, prox_f = _calc_proxima_fecha(dias_unidades[t["unidad_orden"]], 'mensual', hoy)
            # Compra a meses: su monto es la cuota y el fijo cobra hasta completar el plazo
            msi_total = int(t["msi_cuotas"]) if t["tipo"] == 'msi' and t["msi_cuotas"] else None
            deseados.append({
                "tarjeta_id": tarjeta_id, "tipo": "gasto", "descripcion": nombre,
                "monto": round(monto_tarjeta, 2), "frecuencia": 'mensual', "dia": dia_f,
                "proxima": prox_f, "categoria_id": cat_id, "apartado_id": apartado_id,
                "total_cuotas": msi_total,
            })
            continue

        sub_list = None
        if "distribucion_subperiodos" in t.keys() and t["distribucion_subperiodos"]:
            try:
                raw = json.loads(t["distribucion_subperiodos"]) if isinstance(t["distribucion_subperiodos"], str) else t["distribucion_subperiodos"]
                if isinstance(raw, list):
                    sub_list = raw
            except Exception:
                sub_list = None
        sub_positivos = [sp for sp in (sub_list or []) if float(sp.get("monto", 0.0) or 0.0) > 0]

        items = []
        if len(sub_positivos) > 1:
            # Tarjeta dividida en subperiodos: un fijo mensual por subperiodo
            for idx_sp, sp in enumerate(sub_positivos):
                s_monto = round(float(sp["monto"]), 2)
                s_dia = int(sp["dia"]) if sp.get("dia") is not None else (int(t["dia_pago"]) if "dia_pago" in t.keys() and t["dia_pago"] is not None else None)
                if s_dia is None:
                    if frecuencia == 'quincenal' or (cfg and cfg.get("subdivision_tipo") == 'quincenas'):
                        s_dia = 1 if idx_sp == 0 else 16
                    else:
                        s_dia = idx_sp + 1
                s_nombre = sp.get("nombre") or f"Sub-{idx_sp + 1}"
                dia_f, prox_f = _calc_proxima_fecha(s_dia, 'mensual', hoy)
                items.append({"descripcion": f"{nombre} ({s_nombre})", "monto": s_monto,
                              "frecuencia": 'mensual', "dia": dia_f, "proxima": prox_f})
        else:
            if len(sub_positivos) == 1:
                sp = sub_positivos[0]
                s_monto = round(float(sp["monto"]), 2)
                s_dia = int(sp["dia"]) if sp.get("dia") is not None else (int(t["dia_pago"]) if "dia_pago" in t.keys() and t["dia_pago"] is not None else None)
            else:
                s_monto = round(monto_tarjeta, 2)
                s_dia = int(t["dia_pago"]) if "dia_pago" in t.keys() and t["dia_pago"] is not None else None
            s_frec = frecuencia
            if s_dia is not None and frecuencia in ('mensual', 'quincenal'):
                s_frec = 'mensual'
            dia_f, prox_f = _calc_proxima_fecha(s_dia, s_frec, hoy)
            items.append({"descripcion": nombre, "monto": s_monto, "frecuencia": s_frec, "dia": dia_f, "proxima": prox_f})

        # Presupuesto quincenal del tablero general: el monto es por quincena y un día fijo se repite
        # en la otra quincena. El motor de fijos mensuales lo representa con un fijo en el día y otro en su par (d±15).
        if frecuencia == 'quincenal':
            expandidos = []
            for itm in items:
                expandidos.append(itm)
                if itm["frecuencia"] == 'mensual' and itm["dia"] is not None:
                    pareja = itm["dia"] + 15 if itm["dia"] <= 15 else itm["dia"] - 15
                    dia_2, prox_2 = _calc_proxima_fecha(pareja, 'mensual', hoy)
                    expandidos.append({**itm, "descripcion": f"{itm['descripcion']} · 2.ª quincena",
                                       "dia": dia_2, "proxima": prox_2})
            items = expandidos

        for itm in items:
            deseados.append({
                "tarjeta_id": tarjeta_id, "tipo": "gasto", "descripcion": itm["descripcion"],
                "monto": itm["monto"], "frecuencia": itm["frecuencia"], "dia": itm["dia"],
                "proxima": itm["proxima"], "categoria_id": cat_id, "apartado_id": apartado_id,
            })

    # 2. Reemplazo. Los fijos del presupuesto que siguen vigentes se actualizan en su lugar (no se
    #    recrean, así no se duplica el cargo del mes); el resto de fijos, incluidos los hechos a mano
    #    y las compras a meses, se borra.
    libres = cursor.execute("SELECT * FROM recurrentes ORDER BY id ASC").fetchall()
    emparejados = []
    pendientes = []
    for item in deseados:
        fila = next((r for r in libres if _es_del_presupuesto(r)
                     and r["presupuesto_tarjeta_id"] == item["tarjeta_id"] and r["tipo"] == item["tipo"]
                     and r["descripcion"] == item["descripcion"]), None)
        if fila is not None:
            libres.remove(fila)
            emparejados.append((item, fila))
        else:
            pendientes.append(item)
    for item in pendientes:
        # Tarjeta renombrada o con otro subperiodo: se reutiliza su fijo
        fila = next((r for r in libres if _es_del_presupuesto(r)
                     and r["presupuesto_tarjeta_id"] == item["tarjeta_id"] and r["tipo"] == item["tipo"]), None)
        if fila is not None:
            libres.remove(fila)
        emparejados.append((item, fila))

    creados = actualizados = eliminados = 0
    for item, fila in emparejados:
        item["proxima"] = _sin_cargo_previo(cursor, item)
        valores = (item["descripcion"], item["tipo"], item["monto"], item["frecuencia"], item["dia"],
                   item["proxima"].isoformat(), item["categoria_id"], item["apartado_id"], item["tarjeta_id"],
                   item.get("total_cuotas"))
        if fila is not None:
            cursor.execute("""
                UPDATE recurrentes
                SET descripcion = ?, tipo = ?, monto = ?, frecuencia = ?, dia = ?, proxima_fecha = ?,
                    categoria_id = ?, apartado_id = ?, presupuesto_tarjeta_id = ?, total_cuotas = ?, activo = 1
                WHERE id = ?
            """, valores + (fila["id"],))
            actualizados += 1
            accion = "actualizado"
        else:
            cursor.execute("""
                INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha,
                                         categoria_id, apartado_id, presupuesto_tarjeta_id, total_cuotas, activo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, valores)
            creados += 1
            accion = "creado"
        detalles.append({"descripcion": item["descripcion"], "monto": item["monto"], "frecuencia": item["frecuencia"], "accion": accion})

    for sobrante in libres:
        cursor.execute("DELETE FROM recurrentes WHERE id = ?", (sobrante["id"],))
        eliminados += 1
        detalles.append({"descripcion": sobrante["descripcion"], "monto": float(sobrante["monto"]),
                         "frecuencia": sobrante["frecuencia"], "accion": "eliminado"})

    return {
        "frecuencia": frecuencia,
        "creados": creados,
        "actualizados": actualizados,
        "eliminados": eliminados,
        "total_procesados": creados + actualizados,
        "detalles": detalles
    }


def resumen_mensual_core(conn: sqlite3.Connection) -> list:
    """Ingresos y gastos por mes, sin movimientos de apartados (depósitos y retiros de metas
    no son ingreso ni gasto real). Misma regla que `mes_actual` en calcular_resumen."""
    filas = conn.execute("""
        SELECT strftime('%Y-%m', fecha) AS mes,
               COALESCE(SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE 0 END), 0) AS ingresos,
               COALESCE(SUM(CASE WHEN tipo = 'gasto' THEN monto ELSE 0 END), 0) AS gastos
        FROM transacciones
        WHERE apartado_id IS NULL
        GROUP BY mes
        ORDER BY mes DESC
    """).fetchall()
    return [dict(f) for f in filas]


def revertir_movimiento_apartado(cursor: sqlite3.Cursor, apartado_id: int, tipo: str, monto: float) -> None:
    """Deshace en el saldo de una meta un movimiento que se borra. Un depósito (gasto) se resta de la
    meta; un retiro (ingreso) vuelve a sumarse. Así el saldo guardado sigue igual a sus movimientos.
    Un depósito no se puede borrar si parte de su dinero ya se retiró: la meta no lo tendría."""
    delta = -float(monto) if tipo == 'gasto' else float(monto)
    if delta < 0:
        actual = cursor.execute("SELECT monto_actual FROM apartados WHERE id = ?", (apartado_id,)).fetchone()
        if actual is not None and float(actual[0]) + delta < -0.001:
            raise ValueError(
                "No es posible eliminar este depósito porque parte de ese dinero ya se retiró de la meta. "
                "Primero elimina los retiros posteriores."
            )
    cursor.execute(
        "UPDATE apartados SET monto_actual = MAX(0.0, ROUND(monto_actual + ?, 2)) WHERE id = ?",
        (delta, apartado_id),
    )


def deficit_al_eliminar_lote(cursor: sqlite3.Cursor, transacciones: list):
    """Simula borrar `transacciones` y devuelve (déficit, fecha) del primer día con saldo negativo,
    o None si el saldo nunca baja de cero. No deja cambios: todo corre dentro de un savepoint."""
    ingresos_externos = [t for t in transacciones if t["tipo"] == 'ingreso' and t["apartado_id"] is None]
    if not ingresos_externos:
        return None
    ids_set = tuple(t["id"] for t in transacciones)
    placeholders_sim = ",".join("?" for _ in ids_set)
    t_min = min(t["fecha"] for t in ingresos_externos)
    cursor.execute("SAVEPOINT sim_lote")
    try:
        for t in transacciones:
            if t["apartado_id"] is not None:
                revertir_movimiento_apartado(cursor, t["apartado_id"], t["tipo"], t["monto"])
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
        deltas_por_fecha.setdefault(t_min, 0.0)

        for f in sorted(deltas_por_fecha.keys()):
            saldo_corriente += deltas_por_fecha[f]
            if saldo_corriente < -0.001:
                return abs(saldo_corriente), f
        return None
    finally:
        cursor.execute("ROLLBACK TO sim_lote")
        cursor.execute("RELEASE sim_lote")


def valor_rendimiento(capital: float, tasa_anual: float, fecha_base: date, hoy: date) -> float:
    """Valor de una cuenta con interés diario compuesto desde fecha_base."""
    dias = max(0, (hoy - fecha_base).days)
    return round(capital * (1 + tasa_anual / 100 / 365) ** dias, 2)


def _inicio_periodo(fin: date, frecuencia: str, dia: Optional[int] = None) -> date:
    """Fecha de cobro anterior a `fin` para un ingreso de la frecuencia dada.
    En mensual el día de cobro es el ancla (como en siguiente_fecha), no el día de `fin`."""
    if frecuencia == 'semanal':
        return fin - timedelta(days=7)
    if frecuencia == 'quincenal':
        if fin.day <= 15:
            anio, mes = (fin.year - 1, 12) if fin.month == 1 else (fin.year, fin.month - 1)
            return date(anio, mes, ultimo_dia_mes(anio, mes))
        return date(fin.year, fin.month, 15)
    anio, mes = (fin.year - 1, 12) if fin.month == 1 else (fin.year, fin.month - 1)
    ancla = dia if dia else fin.day
    return date(anio, mes, min(ancla, ultimo_dia_mes(anio, mes)))


def _recurrentes_vigentes(conn: sqlite3.Connection) -> list:
    """Recurrentes activos que aún generan cargos (los MSI con todas sus cuotas quedan fuera)."""
    filas = conn.execute("SELECT * FROM recurrentes WHERE activo = 1").fetchall()
    return [r for r in filas if not (r["total_cuotas"] is not None and int(r["cuotas_generadas"]) >= int(r["total_cuotas"]))]


def _ingreso_principal(vigentes: list):
    """Ingreso fijo que marca la periodicidad: el de mayor equivalente mensual; si empatan, el de cobro más próximo."""
    ingresos = [r for r in vigentes if r["tipo"] == 'ingreso' and r["proxima_fecha"] and r["apartado_id"] is None]
    if not ingresos:
        return None
    return min(ingresos, key=lambda r: (-mensualizar(float(r["monto"]), r["frecuencia"]), r["proxima_fecha"]))


def _limites_periodos(principal, hoy: date, n: int) -> list:
    """(inicio, fin inclusivo, fecha del siguiente cobro) de n periodos consecutivos, empezando por el que contiene hoy."""
    limites = []
    if principal is None:
        anio, mes = hoy.year, hoy.month
        for _ in range(n):
            limites.append((date(anio, mes, 1), date(anio, mes, ultimo_dia_mes(anio, mes)), None))
            anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
        return limites
    frecuencia, dia = principal["frecuencia"], principal["dia"]
    cobro = date.fromisoformat(principal["proxima_fecha"])
    while cobro <= hoy:
        cobro = siguiente_fecha(cobro, frecuencia, dia)
    inicio = min(_inicio_periodo(cobro, frecuencia, dia), hoy)
    for _ in range(n):
        limites.append((inicio, cobro - timedelta(days=1), cobro))
        inicio, cobro = cobro, siguiente_fecha(cobro, frecuencia, dia)
    return limites


def _ocurrencias_programadas(vigentes: list, hasta: date):
    """Ocurrencias aún no materializadas de cada recurrente hasta `hasta`, con el importe real de cada cuota MSI."""
    for r in vigentes:
        if not r["proxima_fecha"]:
            continue
        fecha = date.fromisoformat(r["proxima_fecha"])
        total = int(r["total_cuotas"]) if r["total_cuotas"] is not None else None
        n = int(r["cuotas_generadas"])
        monto = float(r["monto"])
        while fecha <= hasta and (total is None or n < total):
            n += 1
            if total is not None and n == total and r["monto_total"] is not None:
                importe = round(float(r["monto_total"]) - monto * (total - 1), 2)  # misma regla que materializar_recurrentes
            else:
                importe = monto
            yield r, fecha, importe
            fecha = siguiente_fecha(fecha, r["frecuencia"], r["dia"])


def clase_movimiento(tipo: str, apartado_id, recurrente_id, es_msi: bool) -> str:
    """Rubro de un movimiento dentro de un periodo: ingreso, normales, fijos, msi o metas."""
    if apartado_id is not None:
        return "metas"
    if tipo == 'ingreso':
        return "ingreso"
    if recurrente_id is None:
        return "normales"
    return "msi" if es_msi else "fijos"


def calcular_periodos(conn: sqlite3.Connection, hoy: date, n: int = 6) -> Dict[str, Any]:
    """
    Periodos sucesivos con la periodicidad del ingreso fijo principal (sin ingreso fijo: meses calendario).
    Cada periodo va del cobro al día anterior al siguiente cobro y compara el ingreso del periodo con los
    gastos fechados dentro: lo ya registrado más lo programado (fijos, cuotas MSI, aportaciones a metas)
    que aún no se cargó, con sus fechas reales. Las aportaciones a metas se reportan aparte de los gastos.
    remanente = ingreso - gastos - metas; el saldo al cierre de un periodo es el saldo inicial del siguiente.
    Montos en MXN: la conversión de moneda la aplica quien presenta.
    """
    vigentes = _recurrentes_vigentes(conn)
    principal = _ingreso_principal(vigentes)
    limites = _limites_periodos(principal, hoy, max(1, n))
    desde, hasta = limites[0][0], limites[-1][1]
    rubros = ("ingreso", "normales", "fijos", "msi", "metas")
    registrado = [dict.fromkeys(rubros, 0.0) for _ in limites]
    programado = [dict.fromkeys(rubros, 0.0) for _ in limites]
    eventos = [[] for _ in limites]

    def indice(fecha: date):
        for i, (ini, fin, _) in enumerate(limites):
            if ini <= fecha <= fin:
                return i
        return None

    def importe_con_signo(clase: str, tipo: str, monto: float) -> float:
        # En metas un depósito (gasto) suma y un retiro (ingreso) devuelve dinero al periodo
        return -monto if clase == "metas" and tipo == 'ingreso' else monto

    filas = conn.execute(
        """
        SELECT t.fecha, t.monto, t.tipo, t.apartado_id, t.recurrente_id, t.rubro, r.total_cuotas
        FROM transacciones t LEFT JOIN recurrentes r ON r.id = t.recurrente_id
        WHERE substr(t.fecha, 1, 10) BETWEEN ? AND ?
        """,
        (desde.isoformat(), hasta.isoformat()),
    ).fetchall()
    for f in filas:
        i = indice(date.fromisoformat(f["fecha"][:10]))
        if i is None:
            continue
        clase = f["rubro"] or clase_movimiento(f["tipo"], f["apartado_id"], f["recurrente_id"], f["total_cuotas"] is not None)
        registrado[i][clase] += importe_con_signo(clase, f["tipo"], float(f["monto"]))

    for r, fecha, importe in _ocurrencias_programadas(vigentes, hasta):
        i = indice(fecha) if fecha >= desde else None
        if i is None:
            continue
        clase = clase_movimiento(r["tipo"], r["apartado_id"], r["id"], r["total_cuotas"] is not None)
        programado[i][clase] += importe_con_signo(clase, r["tipo"], importe)
        if r["tipo"] == 'gasto':
            eventos[i].append({
                "fecha": fecha.isoformat(),
                "descripcion": r["descripcion"],
                "monto": round(importe, 2),
                "es_msi": r["total_cuotas"] is not None,
                "es_meta": r["apartado_id"] is not None,
            })

    saldo = float(conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END), 0) FROM transacciones WHERE substr(fecha, 1, 10) < ?",
        (desde.isoformat(),),
    ).fetchone()[0])
    periodos = []
    for i, (inicio, fin, fecha_cobro) in enumerate(limites):
        reg, prog = registrado[i], programado[i]
        total = {k: reg[k] + prog[k] for k in rubros}
        gastos = total["normales"] + total["fijos"] + total["msi"]
        remanente = total["ingreso"] - gastos - total["metas"]
        saldo_inicial = saldo
        saldo += remanente
        periodos.append({
            "inicio": inicio.isoformat(),
            "fin": fin.isoformat(),
            "fecha_cobro": fecha_cobro.isoformat() if fecha_cobro else None,
            "en_curso": inicio <= hoy <= fin,
            "ingresos": {
                "recibido": round(reg["ingreso"], 2),
                "por_recibir": round(prog["ingreso"], 2),
                "total": round(total["ingreso"], 2),
            },
            "gastos": {
                "normales": round(total["normales"], 2),
                "fijos": round(total["fijos"], 2),
                "msi": round(total["msi"], 2),
                "fijos_y_msi": round(total["fijos"] + total["msi"], 2),
                "pagado": round(reg["normales"] + reg["fijos"] + reg["msi"], 2),
                "por_pagar": round(prog["normales"] + prog["fijos"] + prog["msi"], 2),
                "total": round(gastos, 2),
            },
            "metas": round(total["metas"], 2),
            "remanente": round(remanente, 2),
            "saldo_inicial": round(saldo_inicial, 2),
            "saldo_final": round(saldo, 2),
            "eventos": sorted(eventos[i], key=lambda e: (e["fecha"], e["descripcion"])),
        })
    cobro_info = None
    if principal is not None:
        cobro_info = {"descripcion": principal["descripcion"], "monto": round(float(principal["monto"]), 2), "frecuencia": principal["frecuencia"]}
    return {"ingreso_principal": cobro_info, "periodos": periodos}


def _fijo_en_periodo(r, fecha: date, monto: float, estado: str, cuota=None) -> Dict[str, Any]:
    """Una fila de Fijos y MSI dentro de un periodo: un cargo registrado o programado de un recurrente."""
    return {
        "id": r["id"],
        "descripcion": r["descripcion"],
        "tipo": r["tipo"],
        "fecha": fecha.isoformat(),
        "monto": round(monto, 2),
        "estado": estado,
        "es_msi": r["total_cuotas"] is not None,
        "cuota": cuota,
        "total_cuotas": r["total_cuotas"],
        "meta": r["apartado_id"] is not None,
    }


def fijos_por_periodo(conn: sqlite3.Connection, hoy: date, n: int = 3) -> Dict[str, Any]:
    """
    Fijos y compras a meses agrupados por periodo de cobro, desde el periodo en curso.
    Cada cargo cae en el periodo que contiene su fecha: 'aplicado' si ya se registró en Movimientos y
    'programado' si aún no se cargó. Los pausados van aparte, porque no generan cargos.
    Los saltos no dejan registro, así que no se reconstruyen en periodos pasados.
    """
    vigentes = _recurrentes_vigentes(conn)
    principal = _ingreso_principal(vigentes)
    limites = _limites_periodos(principal, hoy, max(1, n))
    desde, hasta = limites[0][0], limites[-1][1]
    grupos = [{
        "inicio": ini.isoformat(),
        "fin": fin.isoformat(),
        "fecha_cobro": cobro.isoformat() if cobro else None,
        "en_curso": ini <= hoy <= fin,
        "items": [],
    } for ini, fin, cobro in limites]

    def grupo_de(fecha: date):
        for (ini, fin, _), grupo in zip(limites, grupos):
            if ini <= fecha <= fin:
                return grupo
        return None

    nombres = {f["id"]: (f["apartado_nombre"], f["tarjeta_nombre"]) for f in conn.execute(
        """
        SELECT r.id, a.nombre AS apartado_nombre, pt.nombre AS tarjeta_nombre
        FROM recurrentes r
        LEFT JOIN apartados a ON a.id = r.apartado_id
        LEFT JOIN presupuesto_tarjetas pt ON pt.id = r.presupuesto_tarjeta_id
        """
    ).fetchall()}

    for f in conn.execute(
        """
        SELECT t.fecha, t.monto, t.tipo, t.descripcion, t.recurrente_id, t.apartado_id, r.total_cuotas
        FROM transacciones t JOIN recurrentes r ON r.id = t.recurrente_id
        WHERE substr(t.fecha, 1, 10) BETWEEN ? AND ?
        ORDER BY t.fecha, t.id
        """,
        (desde.isoformat(), hasta.isoformat()),
    ).fetchall():
        fecha = date.fromisoformat(f["fecha"][:10])
        grupo = grupo_de(fecha)
        if grupo is None:
            continue
        grupo["items"].append(_fijo_en_periodo(
            {"id": f["recurrente_id"], "descripcion": f["descripcion"], "tipo": f["tipo"],
             "total_cuotas": f["total_cuotas"], "apartado_id": f["apartado_id"]},
            fecha, float(f["monto"]), "aplicado"))

    cuotas_vistas = {}
    for r, fecha, importe in _ocurrencias_programadas(vigentes, hasta):
        grupo = grupo_de(fecha) if fecha >= desde else None
        if grupo is None:
            continue
        k = cuotas_vistas.get(r["id"], 0) + 1
        cuotas_vistas[r["id"]] = k
        cuota = int(r["cuotas_generadas"]) + k if r["total_cuotas"] is not None else None
        fila = _fijo_en_periodo(r, fecha, importe, "programado", cuota)
        fila["primera_de_su_fijo"] = k == 1
        grupo["items"].append(fila)

    for grupo in grupos:
        grupo["items"].sort(key=lambda i: (i["fecha"], i["descripcion"]))
        for i in grupo["items"]:
            i["apartado_nombre"], i["tarjeta_nombre"] = nombres.get(i["id"], (None, None))
        ingresos = [i for i in grupo["items"] if i["tipo"] == 'ingreso']
        gastos = [i for i in grupo["items"] if i["tipo"] != 'ingreso']
        grupo["totales"] = {
            "ingresos": round(sum(i["monto"] for i in ingresos), 2),
            "fijos": round(sum(i["monto"] for i in gastos if not i["es_msi"]), 2),
            "msi": round(sum(i["monto"] for i in gastos if i["es_msi"]), 2),
            "por_pagar": round(sum(i["monto"] for i in grupo["items"] if i["estado"] == 'programado' and i["tipo"] != 'ingreso'), 2),
        }

    # Libre y saldo al cierre salen de calcular_periodos: mismos límites, una sola fórmula
    for grupo, resumen in zip(grupos, calcular_periodos(conn, hoy, max(1, n))["periodos"]):
        grupo["totales"]["libre"] = resumen["remanente"]
        grupo["totales"]["saldo_al_cierre"] = resumen["saldo_final"]

    pausados = []
    for r in conn.execute("SELECT * FROM recurrentes WHERE activo = 0").fetchall():
        if r["total_cuotas"] is not None and int(r["cuotas_generadas"]) >= int(r["total_cuotas"]):
            continue
        cuota = int(r["cuotas_generadas"]) + 1 if r["total_cuotas"] is not None else None
        fila = _fijo_en_periodo(r, date.fromisoformat(r["proxima_fecha"]), float(r["monto"]), "pausado", cuota)
        fila["frecuencia"] = r["frecuencia"]
        fila["apartado_nombre"], fila["tarjeta_nombre"] = nombres.get(r["id"], (None, None))
        pausados.append(fila)
    pausados.sort(key=lambda i: i["descripcion"])
    return {"periodos": grupos, "pausados": pausados}


def calcular_periodo_actual(conn: sqlite3.Connection, hoy: date, balance: float) -> Dict[str, Any]:
    """
    Vista del periodo en curso de calcular_periodos para el tablero: del último cobro al siguiente.
    Lista los cargos programados que faltan antes del próximo cobro y calcula cuánto queda libre del
    balance actual, sumando los ingresos que aún llegan dentro del periodo.
    """
    datos = calcular_periodos(conn, hoy, 1)
    p = datos["periodos"][0]
    inicio = date.fromisoformat(p["inicio"])
    fin = date.fromisoformat(p["fecha_cobro"] or p["fin"])  # el tablero cuenta hasta el día del cobro
    eventos = [{k: e[k] for k in ("fecha", "descripcion", "monto", "es_msi")} for e in p["eventos"] if e["fecha"] > hoy.isoformat()]
    comprometido = round(sum(e["monto"] for e in eventos), 2)
    por_recibir = p["ingresos"]["por_recibir"]
    return {
        "inicio": inicio.isoformat(),
        "fin": fin.isoformat(),
        "hoy": hoy.isoformat(),
        "dias_totales": max(1, (fin - inicio).days),
        "dias_restantes": max(0, (fin - hoy).days),
        "cobro": datos["ingreso_principal"],
        "eventos": eventos,
        "disponible": round(balance, 2),
        "comprometido": comprometido,
        "por_recibir": por_recibir,
        "libre": round(balance + por_recibir - comprometido, 2),
    }


def ventana_periodo_actual(conn: sqlite3.Connection, hoy: date) -> Dict[str, str]:
    """Inicio y fin (inclusive) del periodo de cobro en curso, con el mismo corte que el Resumen."""
    p = calcular_periodos(conn, hoy, 1)["periodos"][0]
    return {"inicio": p["inicio"], "fin": p["fin"]}


def gastos_por_categoria_periodo(conn: sqlite3.Connection, desde: str, hasta: str, moneda_base: str, tasa_usd_mxn: float) -> Dict[str, Any]:
    """Gastos normales y fijos del rango agrupados por categoría (sin metas), en la moneda base."""
    filas = conn.execute(
        """
        SELECT COALESCE(c.nombre, 'Sin categoría') AS categoria, SUM(t.monto) AS total
        FROM transacciones t
        LEFT JOIN categorias c ON c.id = t.categoria_id
        WHERE t.tipo = 'gasto' AND t.apartado_id IS NULL AND substr(t.fecha, 1, 10) BETWEEN ? AND ?
        GROUP BY t.categoria_id
        ORDER BY total DESC
        """,
        (desde, hasta),
    ).fetchall()
    categorias = [
        {"categoria": f["categoria"], "total": round(convertir(float(f["total"] or 0), "MXN", moneda_base, tasa_usd_mxn), 2)}
        for f in filas
    ]
    return {
        "desde": desde,
        "hasta": hasta,
        "moneda_base": moneda_base,
        "total": round(sum(c["total"] for c in categorias), 2),
        "categorias": categorias,
    }


def calcular_resumen(conn: sqlite3.Connection, moneda_base: str, tasa_usd_mxn: float, hoy: date) -> Dict[str, Any]:
    """
    Resumen patrimonial en la moneda base.
    - balance: transacciones materializadas (las cuotas MSI bajan el balance solo al vencer).
    - patrimonio_neto: suma apartados (metas) + inversiones (el efectivo operativo se gestiona por separado).
    - mes_actual: excluye movimientos de apartados (no son gasto ni ingreso real).
    - deuda_msi: incluye las compras pausadas; la deuda sigue pendiente aunque no se cobre.
    - periodos: flujo por periodo del ingreso fijo (ver calcular_periodos).
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

    # Pausadas incluidas: una compra a meses pausada sigue debiéndose
    deuda_msi = sum(deuda_pendiente(r) for r in cursor.execute("SELECT * FROM recurrentes").fetchall())

    mes = f"{hoy.year:04d}-{hoy.month:02d}"
    cursor.execute(
        """
        SELECT 
            COALESCE(SUM(CASE WHEN tipo = 'gasto' AND apartado_id IS NULL THEN monto ELSE 0 END), 0.0) AS mes_gastos,
            COALESCE(SUM(CASE WHEN tipo = 'ingreso' AND apartado_id IS NULL THEN monto ELSE 0 END), 0.0) AS mes_ingresos
        FROM transacciones
        WHERE strftime('%Y-%m', fecha) = ?
        """,
        (mes,)
    )
    fila_mes = cursor.fetchone()
    mes_gastos = float(fila_mes["mes_gastos"]) if fila_mes else 0.0
    mes_ingresos = float(fila_mes["mes_ingresos"]) if fila_mes else 0.0

    balance = ingresos - gastos
    patrimonio = conv(apartados) + inversiones_total

    return {
        "moneda_base": moneda_base_upper,
        "tasa_usd_mxn": tasa_usd_mxn,
        "ingresos": round(conv(ingresos), 2),
        "gastos": round(conv(gastos), 2),
        "balance": round(conv(balance), 2),
        "apartados": round(conv(apartados), 2),
        "inversiones": round(inversiones_total, 2),
        "deuda_msi": round(conv(deuda_msi), 2),
        "mes_actual": {
            "ingresos": round(conv(mes_ingresos), 2),
            "gastos": round(conv(mes_gastos), 2),
            "ahorro": round(conv(mes_ingresos - mes_gastos), 2),
        },
        "patrimonio_neto": round(patrimonio, 2),
        "periodo": _periodo_en_moneda(calcular_periodo_actual(conn, hoy, balance), conv),
        "periodos": _periodos_en_moneda(calcular_periodos(conn, hoy)["periodos"], conv),
    }


def _periodos_en_moneda(periodos: list, conv) -> list:
    for p in periodos:
        for grupo in ("ingresos", "gastos"):
            for clave, valor in p[grupo].items():
                p[grupo][clave] = round(conv(valor), 2)
        for clave in ("metas", "remanente", "saldo_inicial", "saldo_final"):
            p[clave] = round(conv(p[clave]), 2)
        for e in p["eventos"]:
            e["monto"] = round(conv(e["monto"]), 2)
    return periodos


def _periodo_en_moneda(periodo: Dict[str, Any], conv) -> Dict[str, Any]:
    for clave in ("disponible", "comprometido", "por_recibir", "libre"):
        periodo[clave] = round(conv(periodo[clave]), 2)
    for e in periodo["eventos"]:
        e["monto"] = round(conv(e["monto"]), 2)
    if periodo["cobro"]:
        periodo["cobro"]["monto"] = round(conv(periodo["cobro"]["monto"]), 2)
    return periodo
