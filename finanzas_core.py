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


def proxima_fecha_vigente(fecha_anterior: date, frecuencia: str, dia: Optional[int], hoy: date) -> date:
    """Calcula la siguiente fecha válida >= hoy, evitando cobros retroactivos tras pausar."""
    prox = fecha_anterior
    while prox < hoy:
        prox = siguiente_fecha(prox, frecuencia, dia)
    return prox


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
                    INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion, recurrente_id, apartado_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (prox.isoformat(), monto_cuota, fila["tipo"], fila["categoria_id"], descripcion, fila["id"], apartado_id)
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


def calcular_distribucion_subperiodos(monto_total: float, subperiodos: list) -> list:
    """Calcula el monto asignado a cada sub-periodo en base a su porcentaje."""
    res = []
    total = float(monto_total)
    for sp in subperiodos:
        pct = float(sp.get("porcentaje", 0.0))
        monto_sp = round(total * (pct / 100.0), 2)
        res.append({
            "id": sp.get("id"),
            "nombre": sp.get("nombre"),
            "porcentaje": pct,
            "monto": monto_sp
        })
    return res


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


def exportar_presupuesto_a_fijos_core(
    cursor: sqlite3.Cursor,
    tarjetas_ids: list = None,
    incluir_ingreso: bool = True,
    actualizar_existentes: bool = True,
    fecha_base: date = None
) -> dict:
    """
    Exporta las tarjetas del presupuesto a gastos fijos periódicos (`recurrentes`).
    Garantiza que pasen con la misma periodicidad definida en el presupuesto (mensual, quincenal, semanal).
    """
    hoy = fecha_base or ahora_local().date()
    cfg = cursor.execute("SELECT ingreso_base, frecuencia FROM presupuesto_config WHERE id = 1").fetchone()
    frecuencia = (cfg["frecuencia"] if cfg else "mensual")
    ingreso_base = float(cfg["ingreso_base"] if cfg else 0.0)

    creados = 0
    actualizados = 0
    detalles = []

    # 1. Exportar Ingreso Base (Nómina) si se solicitó y es > 0
    if incluir_ingreso and ingreso_base > 0:
        cat_ingreso = resolver_categoria_por_nombre(cursor, "Nómina", tipo="ingreso")
        proxima = primera_fecha_quincenal(hoy) if frecuencia == 'quincenal' else hoy
        existente = cursor.execute(
            "SELECT id FROM recurrentes WHERE tipo = 'ingreso' AND (LOWER(descripcion) LIKE '%nomina%' OR LOWER(descripcion) LIKE '%nómina%' OR LOWER(descripcion) LIKE '%ingreso base%') LIMIT 1"
        ).fetchone()

        if existente and actualizar_existentes:
            cursor.execute("""
                UPDATE recurrentes
                SET monto = ?, frecuencia = ?, dia = ?, proxima_fecha = ?, activo = 1
                WHERE id = ?
            """, (round(ingreso_base, 2), frecuencia, proxima.day, proxima.isoformat(), existente["id"]))
            actualizados += 1
            detalles.append({"descripcion": "Nómina / Ingreso Fijo", "monto": ingreso_base, "frecuencia": frecuencia, "accion": "actualizado"})
        elif not existente:
            cursor.execute("""
                INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, categoria_id, activo)
                VALUES ('Nómina Fija (Presupuesto)', 'ingreso', ?, ?, ?, ?, ?, 1)
            """, (round(ingreso_base, 2), frecuencia, proxima.day, proxima.isoformat(), cat_ingreso))
            creados += 1
            detalles.append({"descripcion": "Nómina Fija (Presupuesto)", "monto": ingreso_base, "frecuencia": frecuencia, "accion": "creado"})

    # 2. Exportar Tarjetas de Gasto
    tarjetas_query = "SELECT * FROM presupuesto_tarjetas"
    if tarjetas_ids:
        placeholders = ",".join("?" for _ in tarjetas_ids)
        tarjetas_query += f" WHERE id IN ({placeholders})"
        filas_tarjetas = cursor.execute(tarjetas_query, tuple(tarjetas_ids)).fetchall()
    else:
        filas_tarjetas = cursor.execute(tarjetas_query).fetchall()

    for t in filas_tarjetas:
        monto = float(t["monto"])
        if monto <= 0:
            continue
        nombre = t["nombre"].strip()
        cat_id = resolver_categoria_por_nombre(cursor, f"{nombre} {t['notas'] or ''}", tipo="gasto")

        # Buscar apartado vinculado si coincide el nombre con una meta
        ap = cursor.execute("SELECT id FROM apartados WHERE LOWER(TRIM(nombre)) = LOWER(?) LIMIT 1", (nombre,)).fetchone()
        apartado_id = ap["id"] if ap else None

        proxima = primera_fecha_quincenal(hoy) if frecuencia == 'quincenal' else hoy

        # Buscar si ya existe un recurrente con esa descripción
        existente = cursor.execute(
            "SELECT id, total_cuotas FROM recurrentes WHERE LOWER(TRIM(descripcion)) = LOWER(?) AND tipo = 'gasto' LIMIT 1",
            (nombre,)
        ).fetchone()

        if existente:
            # Si es una compra a meses activa, preservamos su naturaleza MSI
            if existente["total_cuotas"] is not None:
                detalles.append({"descripcion": nombre, "monto": monto, "frecuencia": frecuencia, "accion": "ignorado_msi"})
                continue

            if actualizar_existentes:
                cursor.execute("""
                    UPDATE recurrentes
                    SET monto = ?, frecuencia = ?, dia = ?, proxima_fecha = ?, categoria_id = ?, apartado_id = COALESCE(?, apartado_id), activo = 1
                    WHERE id = ?
                """, (round(monto, 2), frecuencia, proxima.day, proxima.isoformat(), cat_id, apartado_id, existente["id"]))
                actualizados += 1
                detalles.append({"descripcion": nombre, "monto": monto, "frecuencia": frecuencia, "accion": "actualizado"})
        else:
            cursor.execute("""
                INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, categoria_id, apartado_id, activo)
                VALUES (?, 'gasto', ?, ?, ?, ?, ?, ?, 1)
            """, (nombre, round(monto, 2), frecuencia, proxima.day, proxima.isoformat(), cat_id, apartado_id))
            creados += 1
            detalles.append({"descripcion": nombre, "monto": monto, "frecuencia": frecuencia, "accion": "creado"})

    return {
        "frecuencia": frecuencia,
        "creados": creados,
        "actualizados": actualizados,
        "total_procesados": creados + actualizados,
        "detalles": detalles
    }




def valor_rendimiento(capital: float, tasa_anual: float, fecha_base: date, hoy: date) -> float:
    """Valor de una cuenta con interés diario compuesto desde fecha_base."""
    dias = max(0, (hoy - fecha_base).days)
    return round(capital * (1 + tasa_anual / 100 / 365) ** dias, 2)


def calcular_resumen(conn: sqlite3.Connection, moneda_base: str, tasa_usd_mxn: float, hoy: date) -> Dict[str, Any]:
    """
    Resumen patrimonial en la moneda base.
    - balance: transacciones materializadas (las cuotas MSI bajan el balance solo al vencer).
    - patrimonio_neto: suma apartados (metas) + inversiones (el efectivo operativo se gestiona por separado).
      (rendimiento_fijo se reporta como métrica informativa independiente).
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
    cuota_msi_mensual = 0.0
    for r in filas_rec:
        deuda_msi += deuda_pendiente(r)
        if r["activo"]:
            if r["total_cuotas"] is not None:
                if int(r["cuotas_generadas"]) < int(r["total_cuotas"]):
                    cuota_msi_mensual += mensualizar(float(r["monto"]), r["frecuencia"])
            else:
                if r["tipo"] == 'ingreso':
                    ingreso_fijo += mensualizar(float(r["monto"]), r["frecuencia"])
                else:
                    gasto_fijo += mensualizar(float(r["monto"]), r["frecuencia"])

    rendimiento_fijo = 0.0
    for c in cursor.execute("SELECT * FROM cuentas_rendimiento").fetchall():
        valor = valor_rendimiento(float(c["capital"]), float(c["tasa_anual"]),
                                  date.fromisoformat(c["fecha_base"]), hoy)
        rendimiento_fijo += valor

    mes = f"{hoy.year:04d}-{hoy.month:02d}"
    cursor.execute(
        """
        SELECT 
            COALESCE(SUM(CASE WHEN tipo = 'gasto' AND apartado_id IS NULL AND recurrente_id IS NULL THEN monto ELSE 0 END), 0.0) AS gastos_normales,
            COALESCE(SUM(CASE WHEN tipo = 'gasto' AND apartado_id IS NULL THEN monto ELSE 0 END), 0.0) AS mes_gastos,
            COALESCE(SUM(CASE WHEN tipo = 'ingreso' AND apartado_id IS NULL THEN monto ELSE 0 END), 0.0) AS mes_ingresos
        FROM transacciones
        WHERE strftime('%Y-%m', fecha) = ?
        """,
        (mes,)
    )
    fila_mes = cursor.fetchone()
    gastos_normales_mes = float(fila_mes["gastos_normales"]) if fila_mes else 0.0
    mes_gastos = float(fila_mes["mes_gastos"]) if fila_mes else 0.0
    mes_ingresos = float(fila_mes["mes_ingresos"]) if fila_mes else 0.0

    balance = ingresos - gastos
    patrimonio = conv(apartados) + inversiones_total
    patrimonio_sin_rendimiento = patrimonio

    compromiso_mensual_total = gasto_fijo + cuota_msi_mensual

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
        "cuota_msi_mensual": round(conv(cuota_msi_mensual), 2),
        "ingreso_fijo_mensual": round(conv(ingreso_fijo), 2),
        "gasto_fijo_mensual": round(conv(gasto_fijo), 2),
        "compromiso_mensual_total": round(conv(compromiso_mensual_total), 2),
        "gastos_normales_mes": round(conv(gastos_normales_mes), 2),
        "libre_al_mes": round(conv(ingreso_fijo - compromiso_mensual_total - gastos_normales_mes), 2),
        "mes_actual": {
            "ingresos": round(conv(mes_ingresos), 2),
            "gastos": round(conv(mes_gastos), 2),
            "ahorro": round(conv(mes_ingresos - mes_gastos), 2),
        },
        "patrimonio_sin_rendimiento": round(patrimonio_sin_rendimiento, 2),
        "patrimonio_neto": round(patrimonio, 2),
    }
