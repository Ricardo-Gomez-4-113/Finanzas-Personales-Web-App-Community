"""tests/auditoria/portafolio.py - Sección Inversiones por API: posiciones bursátiles y cuentas con rendimiento.

Mismo esquema que acciones.py, fijos.py y metas.py. Lo que se comprueba con cálculo propio (no con las funciones de la app):
- Cada posición se valúa en cada moneda: cantidad × precio, convertido con las tasas que la app publica.
- El patrimonio del Resumen es lo guardado en metas más lo invertido, y «inversiones» coincide con la lista.
- Una cuenta con rendimiento vale su capital más el interés diario compuesto desde su fecha base.
- Retirar de una cuenta solo se acepta hasta lo que vale hoy; aportar y retirar fijan el nuevo capital y la fecha de hoy.
- Cambiar la tasa capitaliza antes de cambiarla.
- Ninguna de estas operaciones toca el dinero disponible (inversiones y cuentas son una calculadora, no mueven el flujo).
- Un rechazo no cambia nada.
"""

import datetime
import os

import acciones

PESOS_ACCION = (
    ("crear_posicion", 12), ("editar_posicion", 8), ("eliminar_posicion", 6), ("crear_cuenta", 10),
    ("mov_cuenta", 18), ("editar_cuenta", 8), ("eliminar_cuenta", 5), ("avanzar_dia", 14),
)
TICKERS = ("AAPL", "MSFT", "VOO", "WALMEX.MX", "SAP.DE", "AMZN", "TSLA", "NVDA")
MONEDA_NATURAL = {"WALMEX.MX": "MXN", "SAP.DE": "EUR"}
TOLERANCIA = acciones.TOLERANCIA
TOLERANCIA_PESOS = 0.011

Contexto = acciones.Contexto


def _hoy():
    return os.environ["FINANZAS_HOY"][:10]


def _convertir(monto, origen, base, tasa_mxn, tasa_eur):
    """Conversión por USD, escrita aparte de la de la app. Las tasas son unidades de moneda por USD."""
    if origen == base:
        return monto
    usd = monto if origen == "USD" else monto / (tasa_mxn if origen == "MXN" else tasa_eur)
    return usd if base == "USD" else usd * (tasa_mxn if base == "MXN" else tasa_eur)


def _valor_cuenta(capital_c, tasa, fecha_base, hoy):
    dias = max(0, (datetime.date.fromisoformat(hoy) - datetime.date.fromisoformat(fecha_base)).days)
    return round(capital_c / 100 * (1 + tasa / 100 / 365) ** dias, 2)


def _estado(oraculo):
    return {"pos": oraculo.posiciones(), "cuentas": oraculo.cuentas_rendimiento(), "saldo": oraculo.saldo_c(),
            "mov": oraculo.movimientos()}


def _vistas(ctx):
    """Lo que la app muestra después de la acción: portafolio, cuentas, tasas y resumen, todo en pesos."""
    return {
        "portafolio": ctx.api("GET", "/api/inversiones?moneda_base=MXN").json(),
        "cuentas": ctx.api("GET", "/api/rendimiento?moneda_base=MXN").json(),
        "tasas": ctx.api("GET", "/api/tipo-cambio").json().get("tasas", {}),
        "resumen": ctx.api("GET", "/api/resumen?moneda_base=MXN").json(),
    }


def crear_posicion(ctx):
    ticker = ctx.rng.choice(TICKERS)
    moneda = MONEDA_NATURAL.get(ticker, "USD") if ctx.rng.random() < 0.8 else ctx.rng.choice(("USD", "MXN", "EUR"))
    cuerpo = {"ticker": ticker, "cantidad": round(ctx.rng.uniform(0.01, 500), 4),
              "precio_promedio": round(ctx.rng.uniform(1, 400), 2), "moneda": moneda}
    if ctx.rng.random() < 0.5:
        cuerpo["precio_actual"] = round(ctx.rng.uniform(1, 400), 2)
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("POST", "/api/inversiones", cuerpo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": pre, "accion": "crear_posicion", "ticker": ticker}


def _posicion_al_azar(ctx):
    posiciones = ctx.api("GET", "/api/inversiones?moneda_base=MXN").json().get("posiciones", [])
    return ctx.rng.choice(posiciones) if posiciones else None


def editar_posicion(ctx):
    pos = _posicion_al_azar(ctx)
    if pos is None:
        return None
    cuerpo = {"cantidad": round(ctx.rng.uniform(0.01, 500), 4), "precio_actual": round(ctx.rng.uniform(1, 400), 2)}
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("PUT", f"/api/inversiones/{pos['id']}", cuerpo)
    return {"entrada": {"id": pos["id"], **cuerpo}, "respuesta": respuesta, "pre": pre, "accion": "editar_posicion",
            "objetivo": pos["id"], "cuerpo": cuerpo}


def eliminar_posicion(ctx):
    pos = _posicion_al_azar(ctx)
    if pos is None:
        return None
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("DELETE", f"/api/inversiones/{pos['id']}")
    return {"entrada": {"id": pos["id"], "ticker": pos["ticker"]}, "respuesta": respuesta, "pre": pre,
            "accion": "eliminar_posicion", "objetivo": pos["id"]}


def crear_cuenta(ctx):
    nombre = f"Cuenta {ctx.rng.randint(1, 25)}"
    cuerpo = {"nombre": nombre, "capital": round(ctx.rng.uniform(1000, 100000), 2),
              "tasa_anual": round(ctx.rng.choice((0, ctx.rng.uniform(0.5, 15))), 2), "fecha_inicio": ctx.fecha_cercana(30)}
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("POST", "/api/rendimiento", cuerpo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": pre, "accion": "crear_cuenta", "nombre": nombre,
            "cuerpo": cuerpo}


def _cuenta_al_azar(ctx):
    cuentas = ctx.api("GET", "/api/rendimiento?moneda_base=MXN").json().get("cuentas", [])
    return ctx.rng.choice(cuentas) if cuentas else None


def mov_cuenta(ctx):
    cuenta = _cuenta_al_azar(ctx)
    if cuenta is None:
        return None
    accion = ctx.rng.choice(("aportar", "retirar"))
    pre = _estado(ctx.oraculo)
    fila = next(c for c in pre["cuentas"] if c[0] == cuenta["id"])
    valor = _valor_cuenta(fila[2], fila[3], fila[4], _hoy())
    monto = round(ctx.rng.uniform(0.01, max(1.0, valor * 1.3)), 2)
    respuesta = ctx.api("POST", f"/api/rendimiento/{cuenta['id']}/movimiento", {"monto": monto, "accion": accion})
    return {"entrada": {"id": cuenta["id"], "accion": accion, "monto": monto}, "respuesta": respuesta, "pre": pre,
            "accion": "mov_cuenta", "objetivo": cuenta["id"], "mov": accion, "monto": monto, "valor_pre": valor}


def editar_cuenta(ctx):
    cuenta = _cuenta_al_azar(ctx)
    if cuenta is None:
        return None
    nueva_tasa = round(ctx.rng.uniform(0, 15), 2)
    pre = _estado(ctx.oraculo)
    fila = next(c for c in pre["cuentas"] if c[0] == cuenta["id"])
    valor = _valor_cuenta(fila[2], fila[3], fila[4], _hoy())
    respuesta = ctx.api("PUT", f"/api/rendimiento/{cuenta['id']}", {"tasa_anual": nueva_tasa})
    return {"entrada": {"id": cuenta["id"], "tasa_anual": nueva_tasa}, "respuesta": respuesta, "pre": pre,
            "accion": "editar_cuenta", "objetivo": cuenta["id"], "nueva_tasa": nueva_tasa, "valor_pre": valor,
            "tasa_pre": fila[3]}


def eliminar_cuenta(ctx):
    cuenta = _cuenta_al_azar(ctx)
    if cuenta is None:
        return None
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("DELETE", f"/api/rendimiento/{cuenta['id']}")
    return {"entrada": {"id": cuenta["id"]}, "respuesta": respuesta, "pre": pre, "accion": "eliminar_cuenta",
            "objetivo": cuenta["id"]}


def avanzar_dia(ctx):
    ctx.hoy += datetime.timedelta(days=ctx.rng.choice((1, 1, 2, 7, 30, 90)))
    ctx.fijar_hoy()
    return {"entrada": {"hoy": ctx.hoy.isoformat()}, "respuesta": None, "pre": None, "accion": "avanzar_dia"}


def _con_vistas(funcion):
    def envuelta(ctx):
        res = funcion(ctx)
        if res is not None:
            res["vistas"] = _vistas(ctx)
        return res
    return envuelta


FUNCIONES = {
    "crear_posicion": _con_vistas(crear_posicion), "editar_posicion": _con_vistas(editar_posicion),
    "eliminar_posicion": _con_vistas(eliminar_posicion), "crear_cuenta": _con_vistas(crear_cuenta),
    "mov_cuenta": _con_vistas(mov_cuenta), "editar_cuenta": _con_vistas(editar_cuenta),
    "eliminar_cuenta": _con_vistas(eliminar_cuenta), "avanzar_dia": _con_vistas(avanzar_dia),
}


def elegir(rng):
    nombres = [n for n, _ in PESOS_ACCION]
    pesos = [p for _, p in PESOS_ACCION]
    return rng.choices(nombres, weights=pesos)[0]


def _cerca(a, b, tolerancia=TOLERANCIA_PESOS):
    return abs(a - b) <= tolerancia


def _chequear_vistas(res, oraculo):
    problemas = []
    vistas = res.get("vistas")
    if not vistas:
        return problemas
    tasas = vistas["tasas"]
    tasa_mxn, tasa_eur = tasas.get("MXN"), tasas.get("EUR")
    portafolio, resumen = vistas["portafolio"], vistas["resumen"]
    por_id = {p["id"]: p for p in portafolio.get("posiciones", [])}

    total_esperado = 0.0
    for id_, ticker, cantidad, promedio, actual, moneda in oraculo.posiciones():
        moneda = (moneda or "USD").upper()
        vista = por_id.get(id_)
        if vista is None:
            problemas.append(f"la posición {ticker} está en la base y no en el portafolio")
            continue
        capital_orig, valor_orig = round(cantidad * promedio, 2), round(cantidad * actual, 2)
        capital = round(_convertir(capital_orig, moneda, "MXN", tasa_mxn, tasa_eur), 2)
        valor = round(_convertir(valor_orig, moneda, "MXN", tasa_mxn, tasa_eur), 2)
        total_esperado += valor
        if not _cerca(vista["valor_actual"], valor):
            problemas.append(f"{ticker} vale {vista['valor_actual']} en pesos y el cálculo da {valor}")
        if not _cerca(vista["capital_invertido"], capital):
            problemas.append(f"{ticker} costó {vista['capital_invertido']} en pesos y el cálculo da {capital}")
        if not _cerca(vista["pnl"], round(vista["valor_actual"] - vista["capital_invertido"], 2), 0.02):
            problemas.append(f"{ticker}: la ganancia no es valor menos costo")
    if len(por_id) != len(oraculo.posiciones()):
        problemas.append(f"el portafolio muestra {len(por_id)} posiciones y la base tiene {len(oraculo.posiciones())}")
    if not _cerca(portafolio["resumen"]["total_valor_actual"], total_esperado, 0.02 * max(1, len(por_id))):
        problemas.append(f"el total del portafolio es {portafolio['resumen']['total_valor_actual']} y el cálculo da {total_esperado:.2f}")

    # Resumen: inversiones coincide con el portafolio, y el patrimonio es metas más inversiones
    # El Resumen suma cantidad × precio sin redondear y el portafolio redondea cada posición a centavos de su moneda
    # antes de convertirla: con un euro a unos 21 pesos, medio centavo se vuelve ~0.11. Esa es la diferencia que se admite.
    if not _cerca(resumen["inversiones"], portafolio["resumen"]["total_valor_actual"], 0.12 * max(1, len(por_id))):
        problemas.append("«inversiones» del Resumen no coincide con el total del portafolio")
    if not _cerca(resumen["patrimonio_neto"], round(resumen["apartados"] + resumen["inversiones"], 2), 0.02):
        problemas.append("el patrimonio neto no es metas más inversiones")

    hoy = _hoy()
    cuentas_vista = {c["id"]: c for c in vistas["cuentas"].get("cuentas", [])}
    for id_, nombre, capital_c, tasa, fecha_base in oraculo.cuentas_rendimiento():
        vista = cuentas_vista.get(id_)
        if vista is None:
            problemas.append(f"la cuenta «{nombre}» está en la base y no en la lista")
            continue
        valor = _valor_cuenta(capital_c, tasa, fecha_base, hoy)
        if not _cerca(vista["valor_actual"], valor):
            problemas.append(f"«{nombre}» vale {vista['valor_actual']} y el interés compuesto da {valor}")
        if tasa >= 0 and vista["valor_actual"] < vista["capital"] - 0.011:
            problemas.append(f"«{nombre}» vale menos que su capital con una tasa de {tasa}%")
        if tasa == 0 and not _cerca(vista["valor_actual"], vista["capital"]):
            problemas.append(f"«{nombre}» con tasa 0 no vale su capital")
    if len(cuentas_vista) != len(oraculo.cuentas_rendimiento()):
        problemas.append("la lista de cuentas y la base no tienen las mismas cuentas")
    return problemas


def verificar(nombre, res, oraculo, saldo_api):
    """Devuelve la lista de invariantes rotas en este paso (vacía si todo cuadra)."""
    problemas = []
    respuesta = res["respuesta"]
    if respuesta is not None and respuesta.status_code >= 500:
        problemas.append("error del servidor (5xx)")

    saldo_base = oraculo.saldo_c()
    if abs(saldo_api - saldo_base / 100) > TOLERANCIA:
        problemas.append(f"resumen dice {saldo_api} y la base suma {saldo_base / 100}")

    problemas += _chequear_vistas(res, oraculo)

    if respuesta is None:
        return problemas

    codigo = respuesta.status_code
    pre = res["pre"]
    post = _estado(oraculo)
    aceptada = 200 <= codigo < 300

    # Las inversiones y las cuentas no mueven el dinero disponible
    if post["saldo"] != pre["saldo"] or post["mov"] != pre["mov"]:
        problemas.append("una operación de inversiones cambió el dinero disponible o los movimientos")

    if nombre == "crear_posicion":
        existia = any(p[1] == res["ticker"] for p in pre["pos"])
        if aceptada and existia:
            problemas.append("se aceptó una posición con un ticker que ya existía")
        if not aceptada and not existia and codigo < 500:
            problemas.append(f"se rechazó una posición con ticker nuevo y datos válidos ({codigo})")
        if aceptada and len(post["pos"]) != len(pre["pos"]) + 1:
            problemas.append("crear una posición no añadió exactamente una")
    elif nombre == "editar_posicion" and aceptada:
        fila = next((p for p in post["pos"] if p[0] == res["objetivo"]), None)
        if fila is None or abs(fila[2] - res["cuerpo"]["cantidad"]) > 1e-9 or abs(fila[4] - res["cuerpo"]["precio_actual"]) > 1e-9:
            problemas.append("la edición de la posición no quedó guardada tal cual")
    elif nombre == "eliminar_posicion" and aceptada and any(p[0] == res["objetivo"] for p in post["pos"]):
        problemas.append("la posición eliminada sigue en la base")
    elif nombre == "crear_cuenta":
        existia = any(c[1] == res["nombre"] for c in pre["cuentas"])
        if aceptada and existia:
            problemas.append("se aceptó una cuenta con un nombre que ya existía")
        if not aceptada and not existia and codigo < 500:
            problemas.append(f"se rechazó una cuenta con nombre nuevo y datos válidos ({codigo})")
        if aceptada:
            nueva = next((c for c in post["cuentas"] if c[1] == res["nombre"]), None)
            if nueva is None or nueva[2] != acciones._a_centavos(res["cuerpo"]["capital"]):
                problemas.append("el capital de la cuenta nueva no quedó guardado tal cual")
    elif nombre == "mov_cuenta":
        cabe = res["mov"] == "aportar" or res["monto"] <= res["valor_pre"]
        if aceptada and not cabe:
            problemas.append(f"se aceptó retirar {res['monto']} de una cuenta que vale {res['valor_pre']}")
        if not aceptada and cabe and codigo < 500:
            problemas.append(f"se rechazó {res['mov']} {res['monto']} en una cuenta que vale {res['valor_pre']}")
        if aceptada:
            fila = next(c for c in post["cuentas"] if c[0] == res["objetivo"])
            esperado = res["valor_pre"] + res["monto"] if res["mov"] == "aportar" else res["valor_pre"] - res["monto"]
            if abs(fila[2] / 100 - esperado) > 0.011:
                problemas.append(f"tras {res['mov']} el capital es {fila[2] / 100} y debía ser {esperado:.2f}")
            if fila[4] != _hoy():
                problemas.append("tras el movimiento la fecha base de la cuenta no es hoy")
    elif nombre == "editar_cuenta" and aceptada:
        fila = next(c for c in post["cuentas"] if c[0] == res["objetivo"])
        if fila[3] != res["nueva_tasa"]:
            problemas.append("la tasa nueva no quedó guardada")
        if res["nueva_tasa"] != res["tasa_pre"] and (abs(fila[2] / 100 - res["valor_pre"]) > 0.011 or fila[4] != _hoy()):
            problemas.append("cambiar la tasa no capitalizó el interés ganado hasta hoy")
    elif nombre == "eliminar_cuenta" and aceptada and any(c[0] == res["objetivo"] for c in post["cuentas"]):
        problemas.append("la cuenta eliminada sigue en la base")

    if not aceptada and codigo != 404:
        if post["pos"] != pre["pos"] or post["cuentas"] != pre["cuentas"]:
            problemas.append("un rechazo cambió la base")
    if codigo == 404:
        problemas.append("la lista muestra un elemento que el servidor no encuentra (404)")
    return problemas
