"""tests/auditoria/conciliacion.py - Sección Conciliación por API: lo que dicen todas las vistas de la app contra el SQL de la base.

Mezcla acciones de Movimientos, Fijos y Metas con el tiempo corriendo (hasta 60 días de golpe) y, después de CADA paso, pide el resumen, el
historial por periodos, los gastos por categoría y los fijos por periodo, y los compara con cálculos propios en SQL (no con las funciones
de la app). Lo que otras secciones comprueban una acción a la vez, aquí se comprueba entre pantallas: dos vistas del mismo dinero no pueden
contradecirse.

Reglas que se comprueban:
- Resumen: ingresos, gastos y balance suman los movimientos; «apartados» suma lo guardado en las metas; patrimonio = metas + inversiones.
- Cada periodo del resumen (los ya cerrados, sin proyección) cuadra con sus movimientos por rubro (ingreso, normales, fijos, MSI, metas),
  el saldo inicial es lo acumulado antes de su inicio y el saldo final de uno es el saldo inicial del siguiente.
- El historial por periodos suma, sin las metas, los mismos ingresos y gastos que el SQL en cada intervalo, y los periodos van seguidos.
- Gastos por categoría: el total y la suma de sus categorías son los gastos sin metas del rango que dice la respuesta.
- Fijos por periodo: cada fijo aplicado es un movimiento real con su fecha y monto, y cada movimiento de un fijo aparece.
- Un fijo mensual cae en su día (o en el último del mes si es más corto); ninguna fecha se materializa dos veces ni en el futuro; una compra
  a meses con todas sus cuotas suma exactamente su total.
- Un movimiento ligado a un fijo o a una meta lleva el rubro que le toca.
"""

import calendar
import datetime
import os

import acciones
import fijos
import fijos_metas
import metas

TOLERANCIA = acciones.TOLERANCIA
PESOS_ACCION = (
    ("gasto", 14), ("ingreso", 8), ("cancelar", 6), ("eliminar", 4), ("editar", 5), ("depositar", 5), ("retirar", 3),
    ("alta_ingreso_fijo", 5), ("alta_gasto_fijo", 8), ("alta_gasto_fin_de_mes", 4), ("alta_msi", 6), ("alta_aporte", 6),
    ("saltar", 4), ("editar_fecha_fijo", 5), ("editar_frecuencia_fijo", 4), ("cancelar_cuota", 5), ("eliminar_fijo", 3),
    ("crear_meta", 3), ("avanzar_dia", 20),
)

Contexto = acciones.Contexto
_vistas = {}   # cliente y cabecera del episodio en curso: verificar() solo recibe el oráculo


def _fecha_hoy():
    return os.environ["FINANZAS_HOY"][:10]


def preparar(cliente, cabecera):
    # `editados`: los fijos a los que el episodio les movió la fecha o la frecuencia. La app reancla su día a propósito (ancla_al_editar_proximo_cargo),
    # así que sus movimientos viejos caen en el día anterior y la regla «cae en su día» ya no les aplica.
    _vistas.update(cliente=cliente, cabecera=cabecera, editados=set())
    fijos_metas.preparar(cliente, cabecera)


def _api(ruta):
    return _vistas["cliente"].get(ruta, headers=_vistas["cabecera"])


# ------------------------------------------------------------------ acciones (las de las otras secciones, con su propio «antes»)

def _con(nombre, funcion):
    def hacer(ctx):
        res = funcion(ctx)
        return res if res is None else res | {"accion": nombre}
    return hacer


def alta_gasto_fin_de_mes(ctx):
    """Un fijo que cae el 29, 30 o 31: febrero y los meses cortos deben moverlo al último día."""
    anio, mes = ctx.hoy.year, ctx.hoy.month
    inicio = datetime.date(anio, mes, calendar.monthrange(anio, mes)[1])
    if ctx.rng.random() < 0.5:
        inicio = inicio.replace(day=ctx.rng.choice((29, 30, 31)) if calendar.monthrange(anio, mes)[1] >= 31 else calendar.monthrange(anio, mes)[1])
    cuerpo = {"descripcion": f"Fijo fin de mes {ctx.rng.randint(1, 99)}", "tipo": "gasto", "frecuencia": "mensual",
              "monto": round(ctx.rng.uniform(50, 800), 2), "fecha_inicio": inicio.isoformat(),
              "categoria_id": ctx.rng.choice(acciones.CATEGORIAS_GASTO)}
    respuesta = ctx.api("POST", "/api/recurrentes", cuerpo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": None, "accion": "alta_gasto_fin_de_mes"}


def editar_fecha_fijo(ctx):
    """Cambiar el próximo cargo: no debe duplicar ni perder ciclos."""
    lista = [r for r in ctx.api("GET", "/api/recurrentes").json() if r.get("activo") and not r.get("total_cuotas")]
    if not lista:
        return None
    elegido = ctx.rng.choice(lista)
    cuerpo = {"proxima_fecha": ctx.fecha_cercana(40)}
    respuesta = ctx.api("PATCH", f"/api/recurrentes/{elegido['id']}", cuerpo)
    _vistas["editados"].add(elegido["id"])
    return {"entrada": {"id": elegido["id"], **cuerpo}, "respuesta": respuesta, "pre": None, "accion": "editar_fecha_fijo"}


def editar_frecuencia_fijo(ctx):
    lista = [r for r in ctx.api("GET", "/api/recurrentes").json() if r.get("activo") and not r.get("total_cuotas")]
    if not lista:
        return None
    elegido = ctx.rng.choice(lista)
    cuerpo = {"frecuencia": ctx.rng.choice(fijos.FRECUENCIAS)}
    respuesta = ctx.api("PATCH", f"/api/recurrentes/{elegido['id']}", cuerpo)
    _vistas["editados"].add(elegido["id"])
    return {"entrada": {"id": elegido["id"], **cuerpo}, "respuesta": respuesta, "pre": None, "accion": "editar_frecuencia_fijo"}


FUNCIONES = {
    "gasto": _con("gasto", acciones.gasto), "ingreso": _con("ingreso", acciones.ingreso), "cancelar": _con("cancelar", acciones.cancelar),
    "eliminar": _con("eliminar", acciones.eliminar), "editar": _con("editar", acciones.editar),
    "depositar": _con("depositar", acciones.depositar), "retirar": _con("retirar", acciones.retirar),
    "alta_ingreso_fijo": _con("alta_ingreso_fijo", fijos.alta_ingreso_fijo), "alta_gasto_fijo": _con("alta_gasto_fijo", fijos.alta_gasto_fijo),
    "alta_gasto_fin_de_mes": alta_gasto_fin_de_mes, "alta_msi": _con("alta_msi", fijos.alta_msi),
    "alta_aporte": _con("alta_aporte", fijos_metas.alta_aporte), "saltar": _con("saltar", fijos.saltar),
    "editar_fecha_fijo": editar_fecha_fijo, "editar_frecuencia_fijo": editar_frecuencia_fijo,
    "cancelar_cuota": _con("cancelar_cuota", fijos.cancelar_cuota), "eliminar_fijo": _con("eliminar_fijo", fijos.eliminar),
    "crear_meta": _con("crear_meta", metas.crear_meta), "avanzar_dia": fijos_metas.avanzar_dia,
}


def elegir(rng):
    nombres = [n for n, _ in PESOS_ACCION]
    pesos = [p for _, p in PESOS_ACCION]
    return rng.choices(nombres, weights=pesos)[0]


# ------------------------------------------------------------------ oráculo propio (SQL)

def _c(valor):
    """Centavos de la base a pesos."""
    return (valor or 0) / 100


def _suma(oraculo, condicion, parametros=()):
    return _c(oraculo._leer(f"SELECT COALESCE(SUM(monto), 0) FROM transacciones WHERE {condicion}", parametros)[0][0])


def _igual(a, b):
    return abs(a - b) <= TOLERANCIA


def _resumen(oraculo, problemas):
    r = _api("/api/resumen?moneda_base=MXN")
    if r.status_code != 200:
        problemas.append(f"el resumen respondió {r.status_code}")
        return
    j = r.json()
    ingresos, gastos = _suma(oraculo, "tipo = 'ingreso'"), _suma(oraculo, "tipo = 'gasto'")
    for nombre, api, base in (("ingresos", j["ingresos"], ingresos), ("gastos", j["gastos"], gastos), ("balance", j["balance"], ingresos - gastos)):
        if not _igual(api, base):
            problemas.append(f"resumen.{nombre} dice {api} y la base suma {base}")
    guardado = _c(oraculo._leer("SELECT COALESCE(SUM(monto_actual), 0) FROM apartados")[0][0])
    if not _igual(j["apartados"], guardado):
        problemas.append(f"resumen.apartados dice {j['apartados']} y las metas guardan {guardado}")
    if not _igual(j["patrimonio_neto"], j["apartados"] + j["inversiones"]):
        problemas.append(f"patrimonio {j['patrimonio_neto']} no es metas {j['apartados']} + inversiones {j['inversiones']}")

    # Periodos ya cerrados (o el en curso en lo registrado): sin proyección, deben cuadrar con los movimientos por rubro
    hoy = _fecha_hoy()
    anterior_final = None
    for p in j["periodos"]:
        ini, fin = p["inicio"], p["fin"]
        rango = "substr(fecha, 1, 10) BETWEEN ? AND ?"
        por_rubro = {rubro: _suma(oraculo, f"{rango} AND rubro = ? AND tipo = ?", (ini, fin, rubro, tipo))
                     for rubro, tipo in (("ingreso", "ingreso"), ("normales", "gasto"), ("fijos", "gasto"), ("msi", "gasto"))}
        depositos = _suma(oraculo, f"{rango} AND rubro = 'metas' AND tipo = 'gasto'", (ini, fin))
        retiros = _suma(oraculo, f"{rango} AND rubro = 'metas' AND tipo = 'ingreso'", (ini, fin))
        if not _igual(p["ingresos"]["recibido"], por_rubro["ingreso"]):
            problemas.append(f"periodo {ini}: ingresos recibidos {p['ingresos']['recibido']} y la base suma {por_rubro['ingreso']}")
        pagado = por_rubro["normales"] + por_rubro["fijos"] + por_rubro["msi"]
        if not _igual(p["gastos"]["pagado"], pagado):
            problemas.append(f"periodo {ini}: gastos pagados {p['gastos']['pagado']} y la base suma {pagado}")
        if fin < hoy:   # cerrado: nada por recibir, por pagar ni proyectado
            if p["ingresos"]["por_recibir"] or p["gastos"]["por_pagar"]:
                problemas.append(f"periodo cerrado {ini}..{fin} aún tiene por recibir {p['ingresos']['por_recibir']} o por pagar {p['gastos']['por_pagar']}")
            if not _igual(p["metas"], depositos - retiros):
                problemas.append(f"periodo {ini}: metas {p['metas']} y la base suma {depositos - retiros}")
            esperado = p["ingresos"]["total"] - p["gastos"]["total"] - p["metas"]
            if not _igual(p["remanente"], esperado):
                problemas.append(f"periodo {ini}: remanente {p['remanente']} y ingresos − gastos − metas da {esperado}")
        inicial = _c(oraculo._leer(
            "SELECT COALESCE(SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END), 0) FROM transacciones WHERE substr(fecha, 1, 10) < ?", (ini,))[0][0])
        if anterior_final is None and not _igual(p["saldo_inicial"], inicial):
            problemas.append(f"periodo {ini}: saldo inicial {p['saldo_inicial']} y lo acumulado antes da {inicial}")
        if anterior_final is not None and not _igual(p["saldo_inicial"], anterior_final):
            problemas.append(f"periodo {ini}: saldo inicial {p['saldo_inicial']} no es el saldo final del anterior {anterior_final}")
        anterior_final = p["saldo_final"]


def _historial(oraculo, problemas):
    r = _api("/api/historial-periodos")
    if r.status_code != 200:
        problemas.append(f"el historial respondió {r.status_code}")
        return
    periodos = r.json()["periodos"]
    for p in periodos:
        for clave, tipo in (("ingresos", "ingreso"), ("gastos", "gasto")):
            base = _suma(oraculo, "apartado_id IS NULL AND tipo = ? AND substr(fecha, 1, 10) BETWEEN ? AND ?", (tipo, p["inicio"], p["fin"]))
            if not _igual(p[clave], base):
                problemas.append(f"historial {p['inicio']}..{p['fin']}: {clave} dice {p[clave]} y la base suma {base}")
    for nuevo, viejo in zip(periodos, periodos[1:]):   # del más reciente al más antiguo
        siguiente = datetime.date.fromisoformat(viejo["fin"]) + datetime.timedelta(days=1)
        if nuevo["inicio"] != siguiente.isoformat():
            problemas.append(f"historial con hueco o traslape: {viejo['fin']} y luego {nuevo['inicio']}")


def _por_categoria(oraculo, problemas):
    r = _api("/api/gastos-por-categoria")
    if r.status_code != 200:
        problemas.append(f"gastos por categoría respondió {r.status_code}")
        return
    j = r.json()
    base = _suma(oraculo, "apartado_id IS NULL AND tipo = 'gasto' AND substr(fecha, 1, 10) BETWEEN ? AND ?", (j["desde"], j["hasta"]))
    if not _igual(j["total"], base):
        problemas.append(f"gastos por categoría {j['desde']}..{j['hasta']}: total {j['total']} y la base suma {base}")
    suma = sum(c["total"] for c in j["categorias"])
    if not _igual(suma, j["total"]):
        problemas.append(f"las categorías suman {suma} y el total dice {j['total']}")


def _fijos_por_periodo(oraculo, problemas):
    r = _api("/api/recurrentes/periodos")
    if r.status_code != 200:
        problemas.append(f"fijos por periodo respondió {r.status_code}")
        return
    for p in r.json()["periodos"]:
        aplicados = {(i["id"], i["fecha"], round(i["monto"], 2)) for i in p["items"] if i["estado"] == "aplicado"}
        reales = {(f[0], f[1][:10], round(_c(f[2]), 2)) for f in oraculo._leer(
            "SELECT recurrente_id, fecha, monto FROM transacciones WHERE recurrente_id IS NOT NULL AND substr(fecha, 1, 10) BETWEEN ? AND ?",
            (p["inicio"], p["fin"]))}
        if aplicados - reales:
            problemas.append(f"fijos {p['inicio']}: la pantalla muestra como aplicado {sorted(aplicados - reales)[0]} y no es un movimiento")
        if reales - aplicados:
            problemas.append(f"fijos {p['inicio']}: el movimiento {sorted(reales - aplicados)[0]} no aparece como aplicado")


def _fijos_en_si(oraculo, problemas):
    hoy = _fecha_hoy()
    duplicadas = [f for f in oraculo.filas_ligadas() if f[2] > 1]
    if duplicadas:
        problemas.append(f"un fijo se materializó dos veces en la misma fecha: {duplicadas[0][:2]}")
    futuras = oraculo.filas_ligadas_despues(hoy)
    if futuras:
        problemas.append(f"hay movimientos de fijos con fecha futura: {futuras[0]}")
    # Un fijo mensual cae en su día, o en el último del mes si es más corto
    for id_, dia in oraculo._leer("SELECT id, dia FROM recurrentes WHERE frecuencia = 'mensual' AND presupuesto_tarjeta_id IS NULL AND dia IS NOT NULL"):
        if id_ in _vistas.get("editados", ()):
            continue
        for (fecha,) in oraculo._leer("SELECT fecha FROM transacciones WHERE recurrente_id = ?", (id_,)):
            anio, mes = int(fecha[:4]), int(fecha[5:7])
            esperado = min(dia, calendar.monthrange(anio, mes)[1])
            if int(fecha[8:10]) != esperado:
                problemas.append(f"el fijo mensual {id_} (día {dia}) cayó el {fecha[:10]} y debía caer el día {esperado}")
                break
    # Compra a meses con todas sus cuotas: suma exacta de su total
    for id_, total, monto_total in oraculo._leer("SELECT id, total_cuotas, monto_total FROM recurrentes WHERE total_cuotas IS NOT NULL AND monto_total IS NOT NULL"):
        cuotas, suma = oraculo._leer("SELECT COUNT(*), COALESCE(SUM(monto), 0) FROM transacciones WHERE recurrente_id = ?", (id_,))[0]
        if cuotas == total and suma != monto_total:
            problemas.append(f"la compra a meses {id_} tiene sus {total} cuotas y suman {_c(suma)} en vez de {_c(monto_total)}")
    # Rubro de cada movimiento ligado
    for id_, rubro, apartado, total, tipo in oraculo._leer(
            "SELECT t.id, t.rubro, t.apartado_id, r.total_cuotas, t.tipo FROM transacciones t LEFT JOIN recurrentes r ON r.id = t.recurrente_id "
            "WHERE t.apartado_id IS NOT NULL OR t.recurrente_id IS NOT NULL"):
        esperado = "metas" if apartado is not None else ("ingreso" if tipo == "ingreso" else ("msi" if total is not None else "fijos"))
        if rubro != esperado:
            problemas.append(f"el movimiento {id_} tiene rubro «{rubro}» y le toca «{esperado}»")
            break


def verificar(nombre, res, oraculo, saldo_api):
    """Devuelve la lista de invariantes rotas en este paso (vacía si todo cuadra)."""
    problemas = []
    respuesta = res["respuesta"]
    if respuesta is not None and respuesta.status_code >= 500:
        problemas.append("error del servidor (5xx)")
    if abs(saldo_api - oraculo.saldo_c() / 100) > TOLERANCIA:
        problemas.append(f"resumen dice {saldo_api} y la base suma {oraculo.saldo_c() / 100}")
    _resumen(oraculo, problemas)
    _historial(oraculo, problemas)
    _por_categoria(oraculo, problemas)
    _fijos_por_periodo(oraculo, problemas)
    _fijos_en_si(oraculo, problemas)
    # Un estado roto dura varios pasos: se anota la primera vez que aparece en el episodio, no en cada paso que sigue
    visto = oraculo.__dict__.setdefault("avisados_conciliacion", set())
    nuevos = [t for t in problemas if (oraculo.ruta_db, t) not in visto]
    visto.update((oraculo.ruta_db, t) for t in nuevos)
    return nuevos
