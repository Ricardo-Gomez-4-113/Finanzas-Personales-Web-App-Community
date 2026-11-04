"""tests/auditoria/presupuesto.py - Sección Presupuesto por API: ingreso base, pagos, tarjetas y exportación a Fijos.

Mismo esquema que acciones.py y fijos.py: las acciones eligen sus datos con la semilla, y verificar() compara lo
que quedó en la base con el oráculo (SQL propio), no con las funciones de la app.

Reglas que se comprueban además del saldo:
- Las tarjetas de un pago no pueden sumar más que el ingreso de ese pago (las altas y ediciones que pasan no la rompen).
- La vista previa de exportar no cambia nada, y lo que promete coincide con lo que la exportación crea.
- Exportar dos veces seguidas deja los mismos fijos: no se duplican.
- Un fijo ligado a una tarjeta aparece una sola vez por tarjeta y descripción.
- Un rechazo no cambia nada.
"""

import datetime

import acciones

PESOS_ACCION = (
    ("config", 8), ("unidad", 8), ("crear_tarjeta", 14), ("editar_tarjeta", 10), ("eliminar_tarjeta", 6),
    ("exportar", 16), ("alta_ingreso_para_presupuesto", 6), ("avanzar_dia", 10),
)
FRECUENCIAS = ("semanal", "quincenal", "mensual")
COLORES = ("gold", "blue", "green")
CATEGORIA_INGRESO = 12
TOLERANCIA = acciones.TOLERANCIA

Contexto = acciones.Contexto


def _estado(oraculo):
    return {
        "config": oraculo.config_presupuesto(),
        "tarjetas": oraculo.tarjetas_presupuesto(),
        "recurrentes": oraculo.recurrentes_contenido(),
        "mov": oraculo.movimientos(),
        "saldo": oraculo.saldo_c(),
    }


PAGOS_ACTIVOS = {"semanal": 4, "quincenal": 2, "mensual": 1}


def preparar(cliente, cabecera):
    """Cada episodio empieza con un ingreso base real: sin él, las tarjetas no caben en ningún pago."""
    r = cliente.put("/api/presupuesto/config", json={"ingreso_base": 25000, "frecuencia": "quincenal", "dia_pago": None},
                    headers=cabecera)
    assert r.status_code == 200, r.text


def _pagos_activos(oraculo):
    cfg = oraculo.config_presupuesto()
    return PAGOS_ACTIVOS.get(cfg[1], 1) if cfg else 1


def _tarjetas_api(ctx):
    """Todas las tarjetas, también las de pagos inactivos (la lista «tarjetas» solo trae las de pagos activos)."""
    datos = ctx.api("GET", "/api/presupuesto").json()
    return [t for u in datos.get("unidades", []) for t in u.get("tarjetas", [])]


def config(ctx):
    base = 0 if ctx.rng.random() < 0.1 else round(ctx.rng.uniform(3000, 30000), 2)
    cuerpo = {"ingreso_base": base, "frecuencia": ctx.rng.choice(FRECUENCIAS),
              "dia_pago": ctx.rng.choice((None, ctx.rng.randint(1, 28)))}
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("PUT", "/api/presupuesto/config", cuerpo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": pre, "accion": "config"}


def unidad(ctx):
    orden = ctx.rng.randint(1, 4)
    cuerpo = {"dia_pago": ctx.rng.choice((None, ctx.rng.randint(1, 28)))}
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("PUT", f"/api/presupuesto/unidades/{orden}", cuerpo)
    return {"entrada": {"orden": orden, **cuerpo}, "respuesta": respuesta, "pre": pre, "accion": "unidad"}


def crear_tarjeta(ctx):
    # La mayoría cae en un pago activo de la frecuencia actual; alguna cae en uno inactivo (la app debe rechazarla)
    activos = _pagos_activos(ctx.oraculo)
    unidad = ctx.rng.randint(1, activos) if ctx.rng.random() < 0.85 else ctx.rng.randint(1, 4)
    tope = (ctx.oraculo.config_presupuesto() or (0,))[0] / 100 * 0.6
    cuerpo = {
        "nombre": f"Tarjeta {ctx.rng.randint(1, 99)}", "icono": "wallet", "color": ctx.rng.choice(COLORES),
        "monto": round(ctx.rng.uniform(50, max(60, tope)), 2), "notas": "", "unidad_orden": unidad,
        "dia_pago": ctx.rng.choice((None, ctx.rng.randint(1, 28))),
    }
    if ctx.rng.random() < 0.2:
        cuerpo["tipo"] = "msi"
        cuerpo["msi_cuotas"] = ctx.rng.randint(2, 12)
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("POST", "/api/presupuesto/tarjetas", cuerpo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": pre, "accion": "crear_tarjeta",
            "unidad": cuerpo["unidad_orden"]}


def editar_tarjeta(ctx):
    lista = _tarjetas_api(ctx)
    if not lista:
        return None
    elegida = ctx.rng.choice(lista)
    cuerpo = {"monto": round(ctx.rng.uniform(50, 9000), 2)}
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("PATCH", f"/api/presupuesto/tarjetas/{elegida['id']}", cuerpo)
    return {"entrada": {"id": elegida["id"], **cuerpo}, "respuesta": respuesta, "pre": pre,
            "accion": "editar_tarjeta", "objetivo": elegida["id"]}


def eliminar_tarjeta(ctx):
    lista = _tarjetas_api(ctx)
    if not lista:
        return None
    elegida = ctx.rng.choice(lista)
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("DELETE", f"/api/presupuesto/tarjetas/{elegida['id']}")
    return {"entrada": {"id": elegida["id"], "nombre": elegida["nombre"]}, "respuesta": respuesta, "pre": pre,
            "accion": "eliminar_tarjeta", "objetivo": elegida["id"]}


def exportar(ctx):
    """Vista previa y exportación, y después otra exportación igual: la segunda no debe cambiar nada."""
    cuerpo = {"incluir_ingreso": ctx.rng.random() < 0.7}
    pre = _estado(ctx.oraculo)
    vista = ctx.api("POST", "/api/presupuesto/exportar-a-fijos/vista-previa", cuerpo)
    tras_vista = _estado(ctx.oraculo)
    respuesta = ctx.api("POST", "/api/presupuesto/exportar-a-fijos", cuerpo)
    tras_export = _estado(ctx.oraculo)
    segunda = ctx.api("POST", "/api/presupuesto/exportar-a-fijos", cuerpo)
    tras_segunda = _estado(ctx.oraculo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": pre, "accion": "exportar",
            "vista": vista, "tras_vista": tras_vista, "tras_export": tras_export,
            "segunda": segunda, "tras_segunda": tras_segunda}


def alta_ingreso_para_presupuesto(ctx):
    """Un ingreso movimiento: alimenta el saldo sin tocar el presupuesto."""
    cuerpo = {"fecha": ctx.fecha_cercana(20), "monto": round(ctx.rng.uniform(500, 15000), 2), "tipo": "ingreso",
              "categoria_id": CATEGORIA_INGRESO, "descripcion": "Ingreso de prueba"}
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("POST", "/api/transacciones", cuerpo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": pre, "accion": "alta_ingreso_para_presupuesto"}


def avanzar_dia(ctx):
    ctx.hoy += datetime.timedelta(days=ctx.rng.choice((1, 1, 2, 7, 15)))
    ctx.fijar_hoy()
    return {"entrada": {"hoy": ctx.hoy.isoformat()}, "respuesta": None, "pre": None, "accion": "avanzar_dia"}


FUNCIONES = {
    "config": config, "unidad": unidad, "crear_tarjeta": crear_tarjeta, "editar_tarjeta": editar_tarjeta,
    "eliminar_tarjeta": eliminar_tarjeta, "exportar": exportar,
    "alta_ingreso_para_presupuesto": alta_ingreso_para_presupuesto, "avanzar_dia": avanzar_dia,
}


def elegir(rng):
    nombres = [n for n, _ in PESOS_ACCION]
    pesos = [p for _, p in PESOS_ACCION]
    return rng.choices(nombres, weights=pesos)[0]


def _capacidad_del_pago(oraculo, pago):
    """(suma de tarjetas, ingreso) del pago, en pesos. Solo ese pago: un ingreso base bajado después de crear
    las tarjetas deja pagos excedidos a propósito (la app lo marca), y no es culpa de la última alta."""
    cfg = oraculo.config_presupuesto()
    ingreso = cfg[0] if cfg else 0
    suma = sum(monto for _, monto, unidad in oraculo.tarjetas_presupuesto() if unidad == pago)
    return suma / 100, ingreso / 100


def _duplicados(oraculo):
    vistos = {}
    for tipo, descripcion, _, _, tarjeta, _ in oraculo.recurrentes_contenido():
        if tarjeta is not None:
            clave = (tarjeta, descripcion)
            vistos[clave] = vistos.get(clave, 0) + 1
    return [k for k, n in vistos.items() if n > 1]


def verificar(nombre, res, oraculo, saldo_api):
    """Devuelve la lista de invariantes rotas en este paso (vacía si todo cuadra)."""
    problemas = []
    respuesta = res["respuesta"]
    if respuesta is not None and respuesta.status_code >= 500:
        problemas.append("error del servidor (5xx)")

    saldo_base = oraculo.saldo_c()
    if abs(saldo_api - saldo_base / 100) > TOLERANCIA:
        problemas.append(f"resumen dice {saldo_api} y la base suma {saldo_base / 100}")

    if nombre in ("crear_tarjeta", "editar_tarjeta") and respuesta is not None and respuesta.status_code < 300:
        if nombre == "crear_tarjeta":
            pago = res["unidad"]
        else:
            pago = next((u for i, _, u in oraculo.tarjetas_presupuesto() if i == res["objetivo"]), None)
        if pago is not None:
            suma, ingreso = _capacidad_del_pago(oraculo, pago)
            if suma > ingreso + 0.005:
                problemas.append(f"el pago {pago} suma {suma:,.2f} con ingreso {ingreso:,.2f} tras una alta o edición")

    if nombre == "exportar" and res.get("respuesta") is not None and res["respuesta"].status_code < 300:
        dup = _duplicados(oraculo)
        if dup:
            problemas.append(f"un fijo de presupuesto aparece dos veces: {dup[0]}")
        if res["tras_export"]["recurrentes"] != res["tras_segunda"]["recurrentes"]:
            problemas.append("exportar dos veces seguidas cambió los fijos: no es idempotente")
        if res["tras_segunda"]["saldo"] != res["tras_export"]["saldo"]:
            problemas.append("la segunda exportación cambió el saldo")

    if respuesta is None:
        return problemas

    codigo = respuesta.status_code
    pre = res["pre"]
    post = _estado(oraculo)

    if nombre == "exportar":
        vista = res["vista"]
        if vista.status_code < 300:
            if res["tras_vista"] != pre:
                problemas.append("la vista previa cambió la base")
            if vista.status_code < 300 and codigo < 300:
                for clave in ("creados", "actualizados", "eliminados"):
                    a = vista.json().get(clave)
                    b = respuesta.json().get(clave)
                    if a is not None and b is not None and a != b:
                        problemas.append(f"la vista previa promete {clave}={a} y la exportación hizo {b}")
        return problemas

    if 200 <= codigo < 300:
        if nombre == "crear_tarjeta" and len(post["tarjetas"]) != len(pre["tarjetas"]) + 1:
            problemas.append("el alta de tarjeta no creó exactamente una tarjeta")
        if nombre == "eliminar_tarjeta" and any(t[0] == res["objetivo"] for t in post["tarjetas"]):
            problemas.append("la tarjeta eliminada sigue en la base")
        if nombre == "eliminar_tarjeta" and any(r[4] == res["objetivo"] for r in post["recurrentes"]):
            problemas.append("un fijo sigue ligado a una tarjeta que ya no existe")
        if nombre == "alta_ingreso_para_presupuesto" and post["saldo"] - pre["saldo"] != acciones._a_centavos(res["entrada"]["monto"]):
            problemas.append("el ingreso de prueba no sumó su monto al saldo")
        return problemas

    if codigo == 404 and nombre in ("editar_tarjeta", "eliminar_tarjeta", "unidad"):
        problemas.append("la lista muestra un elemento que el servidor no encuentra (404)")
    if post["config"] != pre["config"] or post["tarjetas"] != pre["tarjetas"] or post["recurrentes"] != pre["recurrentes"] \
            or post["mov"] != pre["mov"] or post["saldo"] != pre["saldo"]:
        if nombre != "exportar":
            problemas.append("un rechazo cambió la base")
    return problemas
