"""
export_service.py - Servicio de Exportación y Generación de Reportes Financieros en Excel

Este módulo genera un libro de cálculo profesional (.xlsx) utilizando la biblioteca openpyxl.
El reporte consolida:
1. Resumen General y Métricas Patrimoniales (Efectivo, Sobres/Metas y Portafolio).
2. Historial cronológico completo de Movimientos (Ingresos y Gastos).
3. Portafolio de Inversiones valorado con cotizaciones en tiempo real.
4. Gráfica nativa de Pastel (PieChart) de Asignación de Activos (Asset Allocation).
5. Conversión multimoneda según la divisa base solicitada (MXN o USD).
"""

import io
from datetime import datetime
import sqlite3
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import PieChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.utils import get_column_letter
from finanzas_core import ahora_local, convertir, deuda_pendiente, valor_rendimiento
from mercado import obtener_tasas_cambio, cotizar_varios

def generar_reporte_excel(moneda_base: str = "MXN", conn: sqlite3.Connection = None, db_name: str = "user_1.db") -> io.BytesIO:
    """
    Construye y retorna un libro de Excel (.xlsx) en memoria a través de io.BytesIO.
    
    Parámetros:
    -----------
    moneda_base : str (opcional, default 'MXN')
        Divisa base para consolidar todas las métricas patrimoniales ('MXN' o 'USD').
    conn : sqlite3.Connection (opcional)
        Conexión inyectada hacia la base de datos del tenant autenticado.
    db_name : str (opcional)
        Nombre de archivo de la base de datos del tenant en caso de abrir nueva conexión.
    
    Retorna:
    --------
    io.BytesIO
        Stream binario con el archivo Excel generado y listo para ser enviado en la respuesta HTTP.
    """
    from database import get_tenant_conn

    # Normalizar la moneda base seleccionada (solo se aceptan MXN o USD)
    moneda_base = moneda_base.strip().upper()
    if moneda_base not in ["MXN", "USD"]:
        moneda_base = "MXN"

    # =========================================================================
    # PASO 1: OBTENCIÓN DE TASAS DE CAMBIO
    # =========================================================================
    # Se obtienen los tipos de cambio en vivo o en caché para conversiones multimoneda.
    tasas = obtener_tasas_cambio()
    tasa_usd_mxn = tasas.get("MXN", 18.40)
    
    # Factor para convertir flujos originados en pesos mexicanos (MXN) a la divisa solicitada
    factor_mxn_a_base = (1.0 / tasa_usd_mxn) if (moneda_base == "USD" and tasa_usd_mxn > 0) else 1.0

    # =========================================================================
    # PASO 2: CONEXIÓN A BD Y ACTUALIZACIÓN EN VIVO DE COTIZACIONES
    # =========================================================================
    debe_cerrar = False
    if conn is None:
        conn = get_tenant_conn(db_name)
        debe_cerrar = True
    cursor = conn.cursor()

    # Sincronizar precios de mercado al momento exacto de la exportación para máxima fidelidad
    filas_inv_raw = cursor.execute("SELECT * FROM inversiones ORDER BY id DESC").fetchall()
    actualizaciones_mkt = []
    cotizaciones = cotizar_varios([inv["ticker"] for inv in filas_inv_raw])
    for inv in filas_inv_raw:
        precio_mkt, moneda_mkt = cotizaciones[inv["ticker"]]
        if precio_mkt and precio_mkt > 0:
            actualizaciones_mkt.append((precio_mkt, moneda_mkt, inv["id"]))

    if actualizaciones_mkt:
        cursor.executemany(
            """
            UPDATE inversiones 
            SET precio_actual = ?, moneda = ?, actualizado_en = CURRENT_TIMESTAMP 
            WHERE id = ?
            """,
            actualizaciones_mkt
        )
        conn.commit()

    # Consultar las tres entidades clave del modelo relacional
    transacciones = cursor.execute("SELECT * FROM transacciones ORDER BY fecha DESC, id DESC").fetchall()
    apartados = cursor.execute("SELECT * FROM apartados ORDER BY id DESC").fetchall()
    inversiones_db = cursor.execute("SELECT * FROM inversiones ORDER BY id DESC").fetchall()
    recurrentes_db = cursor.execute("SELECT * FROM recurrentes ORDER BY activo DESC, proxima_fecha ASC, id ASC").fetchall()
    cuentas_rend_db = cursor.execute("SELECT * FROM cuentas_rendimiento ORDER BY id").fetchall()
    if debe_cerrar:
        conn.close()

    # =========================================================================
    # PASO 3: CÁLCULOS MATEMÁTICOS Y PATRIMONIALES DE INVERSIONES
    # =========================================================================
    posiciones = []
    total_inv_invertido = 0.0
    total_inv_valor = 0.0

    for inv in inversiones_db:
        cant = float(inv["cantidad"])
        costo_prom = float(inv["precio_promedio"])
        precio_act = float(inv["precio_actual"])
        moneda_activo = (inv["moneda"] or "USD").upper()

        # Montos consolidados en la moneda base del reporte
        cap_inv = round(convertir(cant * costo_prom, moneda_activo, moneda_base, tasa_usd_mxn), 2)
        val_mkt = round(convertir(cant * precio_act, moneda_activo, moneda_base, tasa_usd_mxn), 2)
        pnl = round(val_mkt - cap_inv, 2)
        pnl_pct = (pnl / cap_inv) if cap_inv > 0 else 0.0

        total_inv_invertido += cap_inv
        total_inv_valor += val_mkt

        posiciones.append({
            "ticker": inv["ticker"],
            "moneda": moneda_activo,
            "cantidad": cant,
            "precio_promedio": costo_prom,
            "precio_actual": precio_act,
            "capital_invertido": cap_inv,
            "valor_actual": val_mkt,
            "pnl": pnl,
            "pnl_porcentaje": pnl_pct,
            "peso_porcentaje": 0.0
        })

    # PnL agregado de todo el portafolio
    total_inv_pnl = round(total_inv_valor - total_inv_invertido, 2)
    total_inv_pnl_pct = (total_inv_pnl / total_inv_invertido) if total_inv_invertido > 0 else 0.0

    # Calcular peso porcentual relativo (% Asset Allocation) de cada activo
    for pos in posiciones:
        pos["peso_porcentaje"] = (pos["valor_actual"] / total_inv_valor) if total_inv_valor > 0 else 0.0

    # =========================================================================
    # PASO 4: CÁLCULOS DE APARTADOS Y FLUJO DE EFECTIVO
    # =========================================================================
    total_ingresos_mxn = sum(float(t["monto"]) for t in transacciones if t["tipo"] == "ingreso")
    total_gastos_mxn = sum(float(t["monto"]) for t in transacciones if t["tipo"] == "gasto")
    balance_mxn = total_ingresos_mxn - total_gastos_mxn
    total_apartados_mxn = sum(float(a["monto_actual"]) for a in apartados)

    # Conversión a la divisa base seleccionada
    ingresos_base = round(total_ingresos_mxn * factor_mxn_a_base, 2)
    gastos_base = round(total_gastos_mxn * factor_mxn_a_base, 2)
    balance_base = round(balance_mxn * factor_mxn_a_base, 2)
    apartados_base = round(total_apartados_mxn * factor_mxn_a_base, 2)
    
    # Patrimonio Neto = Balance Disponible + Ahorro en Sobres/Metas + Valor de Inversiones
    patrimonio_neto = round(apartados_base + total_inv_valor, 2)  # metas + inversiones; el disponible no es patrimonio

    # =========================================================================
    # PASO 5: DISEÑO Y ESTILOS INSTITUCIONALES EN OPENPYXL
    # =========================================================================
    wb = openpyxl.Workbook()

    # Rellenos (Fills) con paleta corporativa Navy Blue
    fill_header = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")      # Azul oscuro institucional
    fill_subheader = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")   # Azul suave para resaltar
    fill_zebra = PatternFill(start_color="F9FAFB", end_color="F9FAFB", fill_type="solid")       # Alternancia sutil de filas
    fill_total = PatternFill(start_color="EAEEF3", end_color="EAEEF3", fill_type="solid")       # Fila de totales

    # Tipografías (Fonts)
    font_title = Font(name="Calibri", size=15, bold=True, color="1F4E78")
    font_subtitle = Font(name="Calibri", size=10, italic=True, color="595959")
    font_section = Font(name="Calibri", size=12, bold=True, color="1F4E78")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_bold = Font(name="Calibri", size=11, bold=True)
    font_regular = Font(name="Calibri", size=11)
    font_green = Font(name="Calibri", size=11, color="1E7E34", bold=True)  # Rendimientos positivos
    font_red = Font(name="Calibri", size=11, color="C82333", bold=True)    # Rendimientos negativos / Gastos

    # Bordes (Borders)
    border_thin = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )
    border_total = Border(
        top=Side(style='thin', color='000000'),
        bottom=Side(style='double', color='000000')  # Línea doble contable para totales
    )

    # Alineaciones (Alignments)
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    # Formatos numéricos compatibles con Excel
    fmt_currency = '"$"#,##0.00'
    fmt_pct = '0.00%'
    fmt_qty = '#,##0.0000'

    # Marca temporal de emisión del reporte
    fecha_hora_emision = ahora_local().strftime('%d/%m/%Y %H:%M:%S')

    # =========================================================================
    # HOJA 1: RESUMEN GENERAL Y MÉTRICAS PATRIMONIALES
    # =========================================================================
    ws1 = wb.active
    ws1.title = "Resumen General"
    ws1.views.sheetView[0].showGridLines = True  # Mantener visibles las líneas de cuadrícula

    # Título principal del reporte y metadata
    ws1['A1'] = "REPORTE FINANCIERO Y PATRIMONIAL"
    ws1['A1'].font = font_title
    ws1['A2'] = f"Emitido el: {fecha_hora_emision} | Moneda Base: {moneda_base} | Tipo de cambio: 1 USD ≈ ${tasa_usd_mxn:.2f} MXN"
    ws1['A2'].font = font_subtitle

    # Sección 1.1: Indicadores Patrimoniales Principales
    ws1['A4'] = "1. INDICADORES PATRIMONIALES Y FINANCIEROS"
    ws1['A4'].font = font_section

    headers_kpi = ["Métrica / Indicador", f"Monto ({moneda_base})", "Detalle / Concepto"]
    for col_idx, h in enumerate(headers_kpi, start=1):
        cell = ws1.cell(row=5, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center if col_idx != 1 else align_left
        cell.border = border_thin

    kpis = [
        ("Patrimonio Neto Total", patrimonio_neto, "Metas y sobres de ahorro + Inversiones"),
        ("Balance en Efectivo Disponible", balance_base, "Flujo de caja libre y disponible (Ingresos - Gastos)"),
        ("Total en Sobres de Ahorro / Metas", apartados_base, "Capital reservado en fondos de emergencia y metas"),
        ("Valor de Mercado del Portafolio", total_inv_valor, "Valuación actual de mercado de acciones y ETFs"),
        ("Capital Invertido Inicial", total_inv_invertido, "Costo base ponderado de compra"),
        ("Ganancia / Pérdida en Inversiones (PnL)", total_inv_pnl, f"Rendimiento neto: {total_inv_pnl_pct * 100:.2f}% acumulado"),
        ("Total Ingresos Históricos", ingresos_base, "Entradas totales registradas en caja"),
        ("Total Gastos Históricos", gastos_base, "Salidas totales registradas en caja")
    ]

    for row_idx, (nombre, valor, desc) in enumerate(kpis, start=6):
        c1 = ws1.cell(row=row_idx, column=1, value=nombre)
        c2 = ws1.cell(row=row_idx, column=2, value=valor)
        c3 = ws1.cell(row=row_idx, column=3, value=desc)

        c1.font = font_bold if "Total" in nombre or "Patrimonio" in nombre else font_regular
        c2.font = font_bold if "Total" in nombre or "Patrimonio" in nombre else font_regular
        c3.font = font_subtitle

        c1.border = border_thin
        c2.border = border_thin
        c3.border = border_thin

        c1.alignment = align_left
        c2.alignment = align_right
        c3.alignment = align_left

        c2.number_format = fmt_currency

        # Resaltar la fila de Patrimonio Neto con fondo institucional suave
        if "Patrimonio" in nombre:
            c1.fill = fill_subheader
            c2.fill = fill_subheader
            c3.fill = fill_subheader
            c2.font = font_section

    # Sección 1.2: Desglose de Sobres de Ahorro y Metas
    start_row_ap = 16
    ws1.cell(row=start_row_ap, column=1, value="2. SOBRES DE AHORRO Y METAS").font = font_section

    headers_ap = ["ID", "Nombre del Apartado", "Tipo", f"Saldo Actual ({moneda_base})", f"Meta ({moneda_base})", "% Cumplimiento"]
    for col_idx, h in enumerate(headers_ap, start=1):
        cell = ws1.cell(row=start_row_ap + 1, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
        cell.border = border_thin

    if not apartados:
        cell_empty = ws1.cell(row=start_row_ap + 2, column=1, value="Sin apartados o metas registradas.")
        cell_empty.font = font_subtitle
    else:
        for idx, ap in enumerate(apartados, start=start_row_ap + 2):
            saldo_ap = round(float(ap["monto_actual"]) * factor_mxn_a_base, 2)
            meta_ap = round(float(ap["meta_monto"]) * factor_mxn_a_base, 2) if ap["meta_monto"] else None
            pct_ap = (saldo_ap / meta_ap) if (meta_ap and meta_ap > 0) else None

            c_id = ws1.cell(row=idx, column=1, value=ap["id"])
            c_nom = ws1.cell(row=idx, column=2, value=ap["nombre"])
            if isinstance(ap["nombre"], str) and ap["nombre"][:1] in ("=", "+", "-", "@"):
                c_nom.data_type = "s"  # nombre escrito por el usuario: nunca una fórmula
            c_tipo = ws1.cell(row=idx, column=3, value=ap["tipo"].capitalize())
            c_saldo = ws1.cell(row=idx, column=4, value=saldo_ap)
            c_meta = ws1.cell(row=idx, column=5, value=meta_ap if meta_ap else "-")
            c_pct = ws1.cell(row=idx, column=6, value=pct_ap if pct_ap is not None else "-")

            for c in [c_id, c_nom, c_tipo, c_saldo, c_meta, c_pct]:
                c.border = border_thin
                c.font = font_regular
                if idx % 2 == 0:
                    c.fill = fill_zebra

            c_id.alignment = align_center
            c_nom.alignment = align_left
            c_tipo.alignment = align_center
            c_saldo.alignment = align_right
            c_meta.alignment = align_right
            c_pct.alignment = align_center

            c_saldo.number_format = fmt_currency
            if meta_ap:
                c_meta.number_format = fmt_currency
            if pct_ap is not None:
                c_pct.number_format = fmt_pct

    # =========================================================================
    # HOJA 2: HISTORIAL DE MOVIMIENTOS (TRANSACCIONES)
    # =========================================================================
    ws2 = wb.create_sheet(title="Movimientos")
    ws2.views.sheetView[0].showGridLines = True

    ws2['A1'] = "HISTORIAL DE MOVIMIENTOS (INGRESOS Y GASTOS)"
    ws2['A1'].font = font_title
    ws2['A2'] = f"Reporte al {fecha_hora_emision} | Denominado en {moneda_base}"
    ws2['A2'].font = font_subtitle

    headers_mov = ["ID", "Fecha", "Tipo", "Descripción", f"Monto ({moneda_base})"]
    for col_idx, h in enumerate(headers_mov, start=1):
        cell = ws2.cell(row=4, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
        cell.border = border_thin

    if not transacciones:
        ws2.cell(row=5, column=1, value="No hay movimientos registrados.").font = font_subtitle
    else:
        for idx, t in enumerate(transacciones, start=5):
            monto_conv = round(float(t["monto"]) * factor_mxn_a_base, 2)
            es_ingreso = (t["tipo"] == "ingreso")

            c_id = ws2.cell(row=idx, column=1, value=t["id"])
            c_fecha = ws2.cell(row=idx, column=2, value=t["fecha"])
            c_tipo = ws2.cell(row=idx, column=3, value=t["tipo"].upper())
            c_desc = ws2.cell(row=idx, column=4, value=t["descripcion"] or "-")
            c_monto = ws2.cell(row=idx, column=5, value=monto_conv)

            for c in [c_id, c_fecha, c_tipo, c_desc, c_monto]:
                c.border = border_thin
                c.font = font_regular
                if idx % 2 == 0:
                    c.fill = fill_zebra

            c_id.alignment = align_center
            c_fecha.alignment = align_center
            c_tipo.alignment = align_center
            c_desc.alignment = align_left
            c_monto.alignment = align_right

            # Resaltar visualmente el tipo de flujo
            c_tipo.font = font_green if es_ingreso else font_red
            c_monto.number_format = fmt_currency

        # Fila de Totales de Movimientos (Balance Neto de Caja)
        last_row = len(transacciones) + 5
        ws2.cell(row=last_row, column=1, value="")
        ws2.cell(row=last_row, column=2, value="")
        ws2.cell(row=last_row, column=3, value="BALANCE")
        ws2.cell(row=last_row, column=4, value="Total Neto Disponible (Ingresos - Gastos)")
        c_tot = ws2.cell(row=last_row, column=5, value=balance_base)

        for col in range(1, 6):
            cell = ws2.cell(row=last_row, column=col)
            cell.border = border_total
            cell.fill = fill_total
            cell.font = font_bold
        c_tot.alignment = align_right
        c_tot.number_format = fmt_currency

    # =========================================================================
    # HOJA 3: PORTAFOLIO DE INVERSIONES & ASSET ALLOCATION
    # =========================================================================
    ws3 = wb.create_sheet(title="Inversiones & Allocation")
    ws3.views.sheetView[0].showGridLines = True

    ws3['A1'] = "PORTAFOLIO DE INVERSIONES & ASSET ALLOCATION"
    ws3['A1'].font = font_title
    ws3['A2'] = f"Cotizaciones en vivo al {fecha_hora_emision} | Moneda Base de Valuación: {moneda_base}"
    ws3['A2'].font = font_subtitle

    headers_inv = [
        "Ticker",
        "Divisa Orig.",
        "Títulos / Cantidad",
        "P. Promedio Compra (Orig.)",
        "P. Actual Mercado (Orig.)",
        f"Capital Invertido ({moneda_base})",
        f"Valor de Mercado ({moneda_base})",
        f"PnL Ganancia/Pérdida ({moneda_base})",
        "Rendimiento (%)",
        "Peso en Portafolio (%)"
    ]

    for col_idx, h in enumerate(headers_inv, start=1):
        cell = ws3.cell(row=4, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
        cell.border = border_thin

    if not posiciones:
        ws3.cell(row=5, column=1, value="Sin posiciones de inversión registradas.").font = font_subtitle
    else:
        for idx, pos in enumerate(posiciones, start=5):
            c_tkr = ws3.cell(row=idx, column=1, value=pos["ticker"])
            c_mon = ws3.cell(row=idx, column=2, value=pos["moneda"])
            c_cant = ws3.cell(row=idx, column=3, value=pos["cantidad"])
            c_prom = ws3.cell(row=idx, column=4, value=pos["precio_promedio"])
            c_act = ws3.cell(row=idx, column=5, value=pos["precio_actual"])
            c_cap = ws3.cell(row=idx, column=6, value=pos["capital_invertido"])
            c_val = ws3.cell(row=idx, column=7, value=pos["valor_actual"])
            c_pnl = ws3.cell(row=idx, column=8, value=pos["pnl"])
            c_rend = ws3.cell(row=idx, column=9, value=pos["pnl_porcentaje"])
            c_peso = ws3.cell(row=idx, column=10, value=pos["peso_porcentaje"])

            for c in [c_tkr, c_mon, c_cant, c_prom, c_act, c_cap, c_val, c_pnl, c_rend, c_peso]:
                c.border = border_thin
                c.font = font_regular
                if idx % 2 == 0:
                    c.fill = fill_zebra

            c_tkr.alignment = align_center
            c_tkr.font = font_bold
            c_mon.alignment = align_center
            c_cant.alignment = align_right
            c_prom.alignment = align_right
            c_act.alignment = align_right
            c_cap.alignment = align_right
            c_val.alignment = align_right
            c_pnl.alignment = align_right
            c_rend.alignment = align_center
            c_peso.alignment = align_center

            # Formatos de moneda y porcentaje contable
            c_cant.number_format = fmt_qty
            c_prom.number_format = fmt_currency
            c_act.number_format = fmt_currency
            c_cap.number_format = fmt_currency
            c_val.number_format = fmt_currency
            c_pnl.number_format = fmt_currency
            c_rend.number_format = fmt_pct
            c_peso.number_format = fmt_pct

            # Color condicional para PnL individual (verde = ganancia, rojo = pérdida)
            c_pnl.font = font_green if pos["pnl"] >= 0 else font_red

        # Fila de Totales del Portafolio
        row_tot_inv = len(posiciones) + 5
        ws3.cell(row=row_tot_inv, column=1, value="TOTAL")
        ws3.cell(row=row_tot_inv, column=2, value="")
        ws3.cell(row=row_tot_inv, column=3, value="")
        ws3.cell(row=row_tot_inv, column=4, value="")
        ws3.cell(row=row_tot_inv, column=5, value="")
        c_tot_cap = ws3.cell(row=row_tot_inv, column=6, value=total_inv_invertido)
        c_tot_val = ws3.cell(row=row_tot_inv, column=7, value=total_inv_valor)
        c_tot_pnl = ws3.cell(row=row_tot_inv, column=8, value=total_inv_pnl)
        c_tot_rend = ws3.cell(row=row_tot_inv, column=9, value=total_inv_pnl_pct)
        c_tot_peso = ws3.cell(row=row_tot_inv, column=10, value=1.0)

        for col in range(1, 11):
            cell = ws3.cell(row=row_tot_inv, column=col)
            cell.border = border_total
            cell.fill = fill_total
            cell.font = font_bold

        c_tot_cap.alignment = align_right
        c_tot_val.alignment = align_right
        c_tot_pnl.alignment = align_right
        c_tot_rend.alignment = align_center
        c_tot_peso.alignment = align_center

        c_tot_cap.number_format = fmt_currency
        c_tot_val.number_format = fmt_currency
        c_tot_pnl.number_format = fmt_currency
        c_tot_rend.number_format = fmt_pct
        c_tot_peso.number_format = fmt_pct
        c_tot_pnl.font = font_green if total_inv_pnl >= 0 else font_red

        # =====================================================================
        # GRÁFICA DE PASTEL NATIVA EN EXCEL (PIECHART DE ASSET ALLOCATION)
        # =====================================================================
        pie = PieChart()
        pie.title = f"Asset Allocation (% Peso) - {moneda_base}"

        # Categorías: Tickers (Columna 1, filas 5 a len+4)
        labels = Reference(ws3, min_col=1, min_row=5, max_row=len(posiciones) + 4)
        # Serie de Datos: Valor de Mercado (Columna 7, encabezado en fila 4, datos filas 5 a len+4)
        data = Reference(ws3, min_col=7, min_row=4, max_row=len(posiciones) + 4)

        pie.add_data(data, titles_from_data=True)
        pie.set_categories(labels)

        # Mostrar etiquetas de porcentaje directamente en cada sector del gráfico
        pie.dataLabels = DataLabelList()
        pie.dataLabels.showPercent = True
        pie.dataLabels.showVal = False
        pie.dataLabels.showCatName = False

        # Dimensiones visuales ideales para no solapar la tabla
        pie.width = 17
        pie.height = 11.5

        # Posicionar el gráfico a la derecha de la tabla de posiciones (en la celda L4)
        ws3.add_chart(pie, "L4")

    # =========================================================================
    # PASO 6: AUTOAJUSTE INTELIGENTE DEL ANCHO DE COLUMNAS
    # =========================================================================
    for ws in [ws1, ws2, ws3]:
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                # Omitir títulos principales y celdas informativas largas para no ensanchar artificialmente
                if cell.row in [1, 2, 4]:
                    continue
                val_str = str(cell.value or "")
                if cell.number_format and "%" in cell.number_format:
                    val_str += "%"
                if len(val_str) > max_len:
                    max_len = len(val_str)
            ws.column_dimensions[col_letter].width = max(max_len + 4, 13)

    # =========================================================================
    # PASO 6B: HOJAS DE FIJOS Y MSI, Y CUENTAS CON RENDIMIENTO
    # =========================================================================
    def _encabezado(ws, titulo, cols):
        ws['A1'] = titulo
        ws['A1'].font = font_title
        ws['A2'] = f"Reporte al {fecha_hora_emision} | Denominado en {moneda_base}"
        ws['A2'].font = font_subtitle
        for col_idx, h in enumerate(cols, start=1):
            cell = ws.cell(row=4, column=col_idx, value=h)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = align_center
            cell.border = border_thin

    def _celda(ws, row, col, valor, fmt=None):
        c = ws.cell(row=row, column=col, value=valor)
        if isinstance(valor, str) and valor[:1] in ("=", "+", "-", "@"):
            c.data_type = "s"  # texto del usuario: nunca una fórmula
        c.border = border_thin
        c.font = font_regular
        if fmt:
            c.number_format = fmt
        return c

    hoy_excel = ahora_local().date()

    ws4 = wb.create_sheet(title="Fijos y MSI")
    _encabezado(ws4, "FIJOS Y COMPRAS A MESES", [
        "Descripción", "Tipo", "Frecuencia", f"Monto por pago ({moneda_base})", "Próxima fecha",
        "Cuotas", f"Deuda pendiente ({moneda_base})", "Estado"
    ])
    if not recurrentes_db:
        ws4.cell(row=5, column=1, value="No hay fijos ni compras a meses registrados.").font = font_subtitle
    for idx, r in enumerate(recurrentes_db, start=5):
        es_msi = r["total_cuotas"] is not None
        terminado = es_msi and r["cuotas_generadas"] >= r["total_cuotas"]
        estado_txt = "Terminado" if terminado else ("Activo" if r["activo"] else "Pausado")
        _celda(ws4, idx, 1, r["descripcion"])
        _celda(ws4, idx, 2, "Gasto" if r["tipo"] == "gasto" else "Ingreso")
        _celda(ws4, idx, 3, r["frecuencia"].capitalize())
        _celda(ws4, idx, 4, round(convertir(float(r["monto"]), "MXN", moneda_base, tasa_usd_mxn), 2), '#,##0.00')
        _celda(ws4, idx, 5, r["proxima_fecha"])
        _celda(ws4, idx, 6, f"{r['cuotas_generadas']}/{r['total_cuotas']}" if es_msi else "Indefinido")
        _celda(ws4, idx, 7, round(convertir(deuda_pendiente(r), "MXN", moneda_base, tasa_usd_mxn), 2), '#,##0.00')
        _celda(ws4, idx, 8, estado_txt)
    for col_letter, ancho in zip("ABCDEFGH", [30, 11, 12, 22, 16, 11, 22, 12]):
        ws4.column_dimensions[col_letter].width = ancho

    ws5 = wb.create_sheet(title="Cuentas con rendimiento")
    _encabezado(ws5, "CUENTAS CON RENDIMIENTO", [
        "Cuenta", "Tasa anual (%)", f"Capital ({moneda_base})", f"Valor actual ({moneda_base})",
        f"Interés generado ({moneda_base})", f"Ganancia mensual estimada ({moneda_base})"
    ])
    if not cuentas_rend_db:
        ws5.cell(row=5, column=1, value="No hay cuentas con rendimiento registradas.").font = font_subtitle
    total_valor = 0.0
    for idx, c in enumerate(cuentas_rend_db, start=5):
        capital = float(c["capital"])
        tasa = float(c["tasa_anual"])
        valor = valor_rendimiento(capital, tasa, datetime.strptime(c["fecha_base"], "%Y-%m-%d").date(), hoy_excel)
        valor_b = convertir(valor, "MXN", moneda_base, tasa_usd_mxn)
        total_valor += valor_b
        _celda(ws5, idx, 1, c["nombre"])
        _celda(ws5, idx, 2, tasa, '0.00')
        _celda(ws5, idx, 3, round(convertir(capital, "MXN", moneda_base, tasa_usd_mxn), 2), '#,##0.00')
        _celda(ws5, idx, 4, round(valor_b, 2), '#,##0.00')
        _celda(ws5, idx, 5, round(convertir(valor - capital, "MXN", moneda_base, tasa_usd_mxn), 2), '#,##0.00')
        _celda(ws5, idx, 6, round(convertir(valor * tasa / 100 / 12, "MXN", moneda_base, tasa_usd_mxn), 2), '#,##0.00')
    for col_letter, ancho in zip("ABCDEF", [30, 15, 20, 22, 24, 30]):
        ws5.column_dimensions[col_letter].width = ancho

    # =========================================================================
    # PASO 7: RETORNO DEL BUFFER EN MEMORIA (BYTESIO)
    # =========================================================================
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output
