"""tests/auditoria/fijos.py - Sección Fijos por API: ingresos y gastos fijos, y compras a meses (MSI).

Mismo esquema que acciones.py: cada acción elige sus datos con la semilla, y verificar() compara lo que quedó
en la base con el oráculo (SQL propio), no con las funciones de la app.

Reglas que se comprueban además del saldo:
- Un gasto fijo solo puede aceptarse si hay un ingreso fijo activo (regla deliberada, no se suaviza).
- Ninguna fecha de un fijo se materializa dos veces ni en el futuro.
- Las filas ligadas a una compra a meses nunca pasan de las cuotas contadas (saltar con «contabilizar» cuenta
  una cuota pagada por fuera, sin movimiento).
- Cancelar una cuota la descuenta del contador de cuotas.
- Saltar un ciclo avanza la próxima fecha.
- Un rechazo no cambia nada.
"""

import datetime
import os

import acciones

PESOS_ACCION = (
    ("alta_ingreso_fijo", 10), ("alta_gasto_fijo", 14), ("alta_msi", 8), ("saltar", 8),
    ("editar", 8), ("eliminar", 6), ("cancelar_cuota", 10), ("avanzar_dia", 10),
)
FRECUENCIAS = ("semanal", "quincenal", "mensual")
CATEGORIA_INGRESO = 12
TOLERANCIA = acciones.TOLERANCIA

Contexto = acciones.Contexto


def _fecha_hoy():
    return os.environ["FINANZAS_HOY"][:10]


def _estado(oraculo):
    return {
        "rec": oraculo.recurrentes(),
        "mov": oraculo.movimientos(),
        "can": oraculo.cancelados(),
        "saldo": oraculo.saldo_c(),
    }


def _ingresos_activos(oraculo):
    return sum(1 for r in oraculo.recurrentes() if r[1] == "ingreso" and r[7] == 1)


def _elegir_recurrente(ctx):
    lista = ctx.api("GET", "/api/recurrentes").json()
    lista = [r for r in lista if r.get("activo")]
    return ctx.rng.choice(lista) if lista else None


def alta_ingreso_fijo(ctx):
    cuerpo = {
        "descripcion": f"Ingreso fijo {ctx.rng.randint(1, 99)}", "tipo": "ingreso",
        "frecuencia": ctx.rng.choice(FRECUENCIAS), "monto": round(ctx.rng.uniform(1000, 20000), 2),
        "fecha_inicio": ctx.fecha_cercana(20), "categoria_id": CATEGORIA_INGRESO,
    }
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("POST", "/api/recurrentes", cuerpo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": pre, "accion": "alta_ingreso_fijo"}


def alta_gasto_fijo(ctx):
    cuerpo = {
        "descripcion": f"Gasto fijo {ctx.rng.randint(1, 99)}", "tipo": "gasto",
        "frecuencia": ctx.rng.choice(FRECUENCIAS), "monto": round(ctx.rng.uniform(50, 5000), 2),
        "fecha_inicio": ctx.fecha_cercana(20), "categoria_id": ctx.rng.choice(acciones.CATEGORIAS_GASTO),
    }
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("POST", "/api/recurrentes", cuerpo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": pre, "accion": "alta_gasto_fijo",
            "ingresos_activos_pre": _ingresos_activos(ctx.oraculo)}


def alta_msi(ctx):
    cuerpo = {
        "descripcion": f"Compra MSI {ctx.rng.randint(1, 99)}", "monto_total": round(ctx.rng.uniform(1000, 60000), 2),
        "meses": ctx.rng.randint(2, 24), "fecha_inicio": ctx.fecha_cercana(20),
        "categoria_id": ctx.rng.choice(acciones.CATEGORIAS_GASTO),
    }
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("POST", "/api/recurrentes", cuerpo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": pre, "accion": "alta_msi"}


def saltar(ctx):
    elegido = _elegir_recurrente(ctx)
    if elegido is None:
        return None
    contabilizar = ctx.rng.random() < 0.5
    pre = _estado(ctx.oraculo)
    proxima_antes = elegido["proxima_fecha"]
    respuesta = ctx.api("POST", f"/api/recurrentes/{elegido['id']}/saltar", {"contabilizar_cuota": contabilizar})
    return {"entrada": {"id": elegido["id"], "contabilizar_cuota": contabilizar}, "respuesta": respuesta, "pre": pre,
            "accion": "saltar", "objetivo": elegido["id"], "proxima_antes": proxima_antes}


def editar(ctx):
    elegido = _elegir_recurrente(ctx)
    if elegido is None:
        return None
    if elegido.get("total_cuotas"):
        cuerpo = {"descripcion": f"Compra MSI {ctx.rng.randint(1, 99)}"}
    else:
        cuerpo = {"monto": round(ctx.rng.uniform(50, 5000), 2)}
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("PATCH", f"/api/recurrentes/{elegido['id']}", cuerpo)
    return {"entrada": {"id": elegido["id"], **cuerpo}, "respuesta": respuesta, "pre": pre,
            "accion": "editar", "objetivo": elegido["id"]}


def eliminar(ctx):
    elegido = _elegir_recurrente(ctx)
    if elegido is None:
        return None
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("DELETE", f"/api/recurrentes/{elegido['id']}")
    return {"entrada": {"id": elegido["id"], "descripcion": elegido["descripcion"]}, "respuesta": respuesta,
            "pre": pre, "accion": "eliminar", "objetivo": elegido["id"]}


def cancelar_cuota(ctx):
    ligados = [m for m in ctx.api("GET", "/api/transacciones").json() if m.get("recurrente_id")]
    if not ligados:
        return None
    mov = ctx.rng.choice(ligados)
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("DELETE", f"/api/transacciones/{mov['id']}?modo=cancelar")
    return {"entrada": {"id": mov["id"], "recurrente": mov["recurrente_id"], "monto": mov["monto"], "tipo": mov["tipo"]},
            "respuesta": respuesta, "pre": pre, "accion": "cancelar_cuota", "objetivo": mov["id"],
            "recurrente": mov["recurrente_id"], "tipo_mov": mov["tipo"], "monto_c": acciones._a_centavos(mov["monto"])}


def avanzar_dia(ctx):
    ctx.hoy += datetime.timedelta(days=ctx.rng.choice((1, 1, 2, 7, 15)))
    ctx.fijar_hoy()
    return {"entrada": {"hoy": ctx.hoy.isoformat()}, "respuesta": None, "pre": None, "accion": "avanzar_dia"}


FUNCIONES = {
    "alta_ingreso_fijo": alta_ingreso_fijo, "alta_gasto_fijo": alta_gasto_fijo, "alta_msi": alta_msi,
    "saltar": saltar, "editar": editar, "eliminar": eliminar, "cancelar_cuota": cancelar_cuota,
    "avanzar_dia": avanzar_dia,
}


def elegir(rng):
    nombres = [n for n, _ in PESOS_ACCION]
    pesos = [p for _, p in PESOS_ACCION]
    return rng.choices(nombres, weights=pesos)[0]


def verificar(nombre, res, oraculo, saldo_api):
    """Devuelve la lista de invariantes rotas en este paso (vacía si todo cuadra)."""
    problemas = []
    respuesta = res["respuesta"]
    if respuesta is not None and respuesta.status_code >= 500:
        problemas.append("error del servidor (5xx)")

    saldo_base = oraculo.saldo_c()
    if abs(saldo_api - saldo_base / 100) > TOLERANCIA:
        problemas.append(f"resumen dice {saldo_api} y la base suma {saldo_base / 100}")

    hoy = _fecha_hoy()
    filas_ligadas = oraculo.filas_ligadas()
    duplicadas = [f for f in filas_ligadas if f[2] > 1]
    if duplicadas:
        problemas.append(f"un fijo se materializó dos veces en la misma fecha: {duplicadas[0][:2]}")
    futuras = oraculo.filas_ligadas_despues(hoy)
    if futuras:
        problemas.append(f"hay movimientos de fijos con fecha futura: {futuras[0]}")
    for r in oraculo.recurrentes():
        if r[4] is not None:  # compra a meses
            _, _, monto, _, total, generadas, monto_total, activo, _ = r[:9]
            pagadas = oraculo.cuotas_de(r[0])
            if pagadas > generadas:
                problemas.append(f"compra {r[0]}: hay {pagadas} cuotas ligadas y solo {generadas} contadas")
            if generadas > total:
                problemas.append(f"compra {r[0]}: {generadas} cuotas contadas de {total}")

    if respuesta is None:
        return problemas

    codigo = respuesta.status_code
    pre = res["pre"]
    post = _estado(oraculo)

    if 200 <= codigo < 300:
        if nombre == "alta_ingreso_fijo" and len(post["rec"]) != len(pre["rec"]) + 1:
            problemas.append("el alta de ingreso fijo no creó exactamente un fijo")
        if nombre == "alta_gasto_fijo":
            if res.get("ingresos_activos_pre", 0) == 0:
                problemas.append("se aceptó un gasto fijo sin ingreso fijo activo")
            if len(post["rec"]) != len(pre["rec"]) + 1:
                problemas.append("el alta de gasto fijo no creó exactamente un fijo")
        if nombre == "alta_msi" and len(post["rec"]) != len(pre["rec"]) + 1:
            problemas.append("el alta de compra a meses no creó exactamente un fijo")
        if nombre == "saltar":
            nueva = next(r for r in post["rec"] if r[0] == res["objetivo"])[8]
            if nueva == res["proxima_antes"]:
                problemas.append("saltar un ciclo no avanzó la próxima fecha")
        if nombre == "eliminar" and any(r[0] == res["objetivo"] for r in post["rec"]):
            problemas.append("el fijo eliminado sigue en la base")
        if nombre == "eliminar" and post["saldo"] != pre["saldo"]:
            problemas.append("eliminar un fijo cambió el saldo; las filas ya materializadas deben quedarse")
        if nombre == "cancelar_cuota":
            if any(f[0] == res["objetivo"] for f in post["mov"]):
                problemas.append("la cuota cancelada sigue en la base")
            antes = next((r for r in pre["rec"] if r[0] == res["recurrente"]), None)
            despues = next((r for r in post["rec"] if r[0] == res["recurrente"]), None)
            if antes and despues and antes[4] is not None and despues[5] != antes[5] - 1:
                problemas.append(f"cancelar una cuota debía bajar el contador de {antes[5]} a {antes[5] - 1} y quedó en {despues[5]}")
            esperado = -acciones._firmado(res["tipo_mov"], res["monto_c"])  # quitar un gasto devuelve dinero
            if post["saldo"] - pre["saldo"] != esperado:
                problemas.append(f"cancelar la cuota cambió el saldo {(post['saldo'] - pre['saldo']) / 100} y debía cambiar {esperado / 100}")
        return problemas

    # Rechazo: nada debe cambiar en la base
    if codigo == 404 and nombre in ("saltar", "editar", "eliminar", "cancelar_cuota"):
        problemas.append("la lista muestra un elemento que el servidor no encuentra (404)")
    if post["rec"] != pre["rec"] or post["mov"] != pre["mov"] or post["saldo"] != pre["saldo"]:
        problemas.append("un rechazo cambió la base")
    return problemas
