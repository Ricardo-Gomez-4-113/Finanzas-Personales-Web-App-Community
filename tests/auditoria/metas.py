"""tests/auditoria/metas.py - Sección Metas por API: crear, depositar, retirar, eliminar y quitar movimientos de una meta.

Mismo esquema que acciones.py y fijos.py: las acciones eligen sus datos con la semilla, y verificar() compara lo que
quedó en la base con el oráculo (SQL propio), no con las funciones de la app.

Reglas que se comprueban además del saldo:
- Depositar solo cabe en lo que hay disponible hoy; si cabe, se acepta (y si no cabe, se rechaza).
- Retirar solo llega hasta lo que tiene la meta.
- Una meta con saldo no se elimina; una vacía sí.
- No hay dos metas con el mismo nombre.
- El saldo guardado de cada meta es su ahorro propio (el que nunca pasó por la cuenta) más los depósitos menos
  los retiros registrados como movimientos. Esa identidad se comprueba en cada paso.
- Un rechazo no cambia nada.
"""

import datetime
import os

import acciones

PESOS_ACCION = (
    ("crear_meta", 10), ("depositar", 25), ("retirar", 15), ("eliminar_meta", 8),
    ("cancelar_mov_meta", 10), ("ingreso", 14), ("avanzar_dia", 10),
)
AHORRO_PROPIO_CENTAVOS = 5_000_000  # lo que sembró personas.crear_laura en «Fondo propio»
TOLERANCIA = acciones.TOLERANCIA

Contexto = acciones.Contexto


def _estado(oraculo, hoy):
    return {
        "apartados": oraculo.apartados(),
        "neto": oraculo.neto_por_apartado(),
        "mov": oraculo.movimientos(),
        "saldo": oraculo.saldo_c(),
        "saldo_hoy": oraculo.saldo_hasta_c(hoy),
    }


def _metas_api(ctx):
    return ctx.api("GET", "/api/apartados").json()


def _elegir_meta(ctx):
    lista = _metas_api(ctx)
    return ctx.rng.choice(lista) if lista else None


def crear_meta(ctx):
    nombre = f"Meta {ctx.rng.randint(1, 40)}"
    tipo = ctx.rng.choice(("meta", "meta", "libre"))
    cuerpo = {"nombre": nombre, "tipo": tipo}
    if tipo == "meta":
        cuerpo["meta_monto"] = round(ctx.rng.uniform(500, 80000), 2)
    pre = _estado(ctx.oraculo, ctx.hoy.isoformat())
    respuesta = ctx.api("POST", "/api/apartados", cuerpo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": pre, "accion": "crear_meta", "nombre": nombre}


def _movimiento(ctx, accion):
    meta = _elegir_meta(ctx)
    if meta is None:
        return None
    # Montos que caen a veces dentro y a veces fuera de lo que hay, para ver los dos lados de la regla
    pre = _estado(ctx.oraculo, ctx.hoy.isoformat())
    if accion == "depositar":
        tope = max(10.0, pre["saldo_hoy"] / 100 * 1.3)
    else:
        tope = max(10.0, next((a[3] for a in pre["apartados"] if a[0] == meta["id"]), 0) / 100 * 1.3)
    monto = round(ctx.rng.uniform(0.01, tope), 2)
    respuesta = ctx.api("POST", f"/api/apartados/{meta['id']}/movimiento", {"monto": monto, "accion": accion})
    return {"entrada": {"meta": meta["id"], "monto": monto, "accion": accion}, "respuesta": respuesta, "pre": pre,
            "accion": accion, "objetivo": meta["id"], "monto_c": acciones._a_centavos(monto)}


def depositar(ctx):
    return _movimiento(ctx, "depositar")


def retirar(ctx):
    return _movimiento(ctx, "retirar")


def eliminar_meta(ctx):
    meta = _elegir_meta(ctx)
    if meta is None:
        return None
    pre = _estado(ctx.oraculo, ctx.hoy.isoformat())
    respuesta = ctx.api("DELETE", f"/api/apartados/{meta['id']}")
    return {"entrada": {"id": meta["id"], "nombre": meta["nombre"]}, "respuesta": respuesta, "pre": pre,
            "accion": "eliminar_meta", "objetivo": meta["id"]}


def cancelar_mov_meta(ctx):
    """Cancela un movimiento que viene de una meta: el saldo de la meta debe seguirlo."""
    de_meta = [m for m in ctx.api("GET", "/api/transacciones").json() if m.get("apartado_id")]
    if not de_meta:
        return None
    mov = ctx.rng.choice(de_meta)
    pre = _estado(ctx.oraculo, ctx.hoy.isoformat())
    respuesta = ctx.api("DELETE", f"/api/transacciones/{mov['id']}?modo=cancelar")
    return {"entrada": {"id": mov["id"], "apartado": mov["apartado_id"], "tipo": mov["tipo"], "monto": mov["monto"]},
            "respuesta": respuesta, "pre": pre, "accion": "cancelar_mov_meta", "objetivo": mov["id"]}


def ingreso(ctx):
    """Dinero en la cuenta para poder depositar."""
    cuerpo = {"fecha": ctx.hoy.isoformat(), "monto": round(ctx.rng.uniform(500, 15000), 2), "tipo": "ingreso",
              "categoria_id": 12, "descripcion": "Ingreso de prueba"}
    pre = _estado(ctx.oraculo, ctx.hoy.isoformat())
    respuesta = ctx.api("POST", "/api/transacciones", cuerpo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": pre, "accion": "ingreso"}


def avanzar_dia(ctx):
    ctx.hoy += datetime.timedelta(days=ctx.rng.choice((1, 1, 2, 7, 15)))
    ctx.fijar_hoy()
    return {"entrada": {"hoy": ctx.hoy.isoformat()}, "respuesta": None, "pre": None, "accion": "avanzar_dia"}


FUNCIONES = {
    "crear_meta": crear_meta, "depositar": depositar, "retirar": retirar, "eliminar_meta": eliminar_meta,
    "cancelar_mov_meta": cancelar_mov_meta, "ingreso": ingreso, "avanzar_dia": avanzar_dia,
}


def elegir(rng):
    nombres = [n for n, _ in PESOS_ACCION]
    pesos = [p for _, p in PESOS_ACCION]
    return rng.choices(nombres, weights=pesos)[0]


def _saldo_guardado_de(post, apartado):
    return next((a[3] for a in post["apartados"] if a[0] == apartado), None)


def verificar(nombre, res, oraculo, saldo_api):
    """Devuelve la lista de invariantes rotas en este paso (vacía si todo cuadra)."""
    problemas = []
    respuesta = res["respuesta"]
    if respuesta is not None and respuesta.status_code >= 500:
        problemas.append("error del servidor (5xx)")

    saldo_base = oraculo.saldo_c()
    if abs(saldo_api - saldo_base / 100) > TOLERANCIA:
        problemas.append(f"resumen dice {saldo_api} y la base suma {saldo_base / 100}")

    # Identidad de cada meta: saldo guardado = ahorro propio + depósitos − retiros
    neto = oraculo.neto_por_apartado()
    for id_, nombre_meta, _, guardado, _ in oraculo.apartados():
        propio = getattr(oraculo, "offsets", {}).get(id_, 0)  # ahorro propio de esa meta al empezar el episodio
        esperado = propio + neto.get(id_, 0)
        if guardado != esperado:
            problemas.append(f"la meta «{nombre_meta}» guarda {guardado / 100} y sus movimientos suman {esperado / 100}")

    if respuesta is None:
        return problemas

    codigo = respuesta.status_code
    pre = res["pre"]
    post = _estado(oraculo, _fecha_hoy())
    aceptada = 200 <= codigo < 300

    if nombre == "crear_meta":
        existia = any(a[1] == res["nombre"] for a in pre["apartados"])
        if aceptada and existia:
            problemas.append("se aceptó una meta con un nombre que ya existía")
        if not aceptada and not existia and codigo < 500:
            problemas.append(f"se rechazó una meta con nombre nuevo y datos válidos ({codigo})")
        if aceptada and len(post["apartados"]) != len(pre["apartados"]) + 1:
            problemas.append("crear una meta no añadió exactamente una")
    elif nombre == "depositar":
        cabe = res["monto_c"] <= pre["saldo_hoy"]
        if aceptada and not cabe:
            problemas.append(f"se aceptó guardar {res['monto_c'] / 100} con {pre['saldo_hoy'] / 100} disponibles hoy")
        if not aceptada and cabe and codigo < 500:
            problemas.append(f"se rechazó guardar {res['monto_c'] / 100} aunque había {pre['saldo_hoy'] / 100} disponibles hoy")
        if aceptada and post["saldo"] - pre["saldo"] != -res["monto_c"]:
            problemas.append("depositar no restó el monto del disponible")
    elif nombre == "retirar":
        guardado = _saldo_guardado_de(pre, res["objetivo"]) or 0
        cabe = res["monto_c"] <= guardado
        if aceptada and not cabe:
            problemas.append(f"se aceptó retirar {res['monto_c'] / 100} de una meta con {guardado / 100}")
        if not aceptada and cabe and codigo < 500:
            problemas.append(f"se rechazó retirar {res['monto_c'] / 100} de una meta con {guardado / 100}")
        if aceptada and post["saldo"] - pre["saldo"] != res["monto_c"]:
            problemas.append("retirar no sumó el monto al disponible")
    elif nombre == "eliminar_meta":
        guardado = _saldo_guardado_de(pre, res["objetivo"]) or 0
        if aceptada and guardado > 0:
            problemas.append(f"se eliminó una meta con {guardado / 100} guardados")
        if not aceptada and guardado <= 0 and codigo < 500 and codigo != 404:
            problemas.append("se rechazó eliminar una meta vacía")
        if aceptada and post["saldo"] != pre["saldo"]:
            problemas.append("eliminar una meta cambió el disponible")
    elif nombre == "cancelar_mov_meta":
        if aceptada and any(f[0] == res["objetivo"] for f in post["mov"]):
            problemas.append("el movimiento cancelado de la meta sigue en la base")

    if not aceptada and codigo != 404:
        if post["apartados"] != pre["apartados"] or post["mov"] != pre["mov"] or post["saldo"] != pre["saldo"]:
            problemas.append("un rechazo cambió la base")
    if codigo == 404 and nombre in ("depositar", "retirar", "eliminar_meta", "cancelar_mov_meta"):
        problemas.append("la lista muestra un elemento que el servidor no encuentra (404)")
    return problemas


def _fecha_hoy():
    return os.environ["FINANZAS_HOY"][:10]
