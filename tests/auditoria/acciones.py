"""tests/auditoria/acciones.py - Acciones de Movimientos (capa API) y lo que debe cumplirse después de cada una.

Todo lo aleatorio sale del generador de la semilla: la misma semilla repite la misma secuencia de acciones.
Lo que la base debe tener después se compara con el oráculo, no con las funciones de la app.
"""

import datetime
import os

PESOS_ACCION = (
    ("gasto", 30), ("ingreso", 10), ("cancelar", 12), ("eliminar", 8), ("editar", 8),
    ("borrar_cancelado", 5), ("depositar", 6), ("retirar", 4), ("avanzar_dia", 8),
)
CATEGORIAS_GASTO = (1, 2, 4, 7, 8)
DESCRIPCIONES = ("Súper", "Gasolina", "🍕 Pizza", "<script>alert(1)</script>", "", None, "x" * 250)
TOLERANCIA = 0.005


def _firmado(tipo, monto_c):
    """Efecto de un movimiento en el disponible, en centavos: el ingreso suma, el gasto resta."""
    return monto_c if tipo == "ingreso" else -monto_c


def _a_centavos(pesos):
    return round(pesos * 100)


class Contexto:
    def __init__(self, rng, cliente, cabecera, oraculo, hoy, meta_id):
        self.rng = rng
        self.cliente = cliente
        self.cabecera = cabecera
        self.oraculo = oraculo
        self.hoy = hoy
        self.meta_id = meta_id

    def api(self, metodo, ruta, cuerpo=None):
        return self.cliente.request(metodo, ruta, json=cuerpo, headers=self.cabecera)

    def fijar_hoy(self):
        os.environ["FINANZAS_HOY"] = f"{self.hoy.isoformat()}T10:00:00"

    def fecha_cercana(self, rango):
        return (self.hoy + datetime.timedelta(days=self.rng.randint(-rango, rango))).isoformat()


def _monto(rng):
    tope = rng.choice((1, 100, 1500, 8000, 30000))
    return round(rng.uniform(0.01, tope), 2)


def _estado(oraculo, fecha):
    return {
        "mov": oraculo.movimientos(),
        "can": oraculo.cancelados(),
        "saldo": oraculo.saldo_c(),
        "minimo": oraculo.minimo_desde(fecha),
    }


def _elegir_movimiento(ctx):
    lista = ctx.api("GET", "/api/transacciones").json()
    return ctx.rng.choice(lista) if lista else None


def _crear(ctx, tipo, fecha, monto, categoria_id, descripcion, apartado=None):
    """Alta de movimiento (o de depósito/retiro de meta). Devuelve el resultado con el estado antes y después."""
    monto_c = _a_centavos(monto)
    cuerpo = {"fecha": fecha, "monto": monto, "tipo": tipo, "categoria_id": categoria_id, "descripcion": descripcion}
    pre = _estado(ctx.oraculo, fecha)
    candidato = ctx.oraculo.minimo_desde(fecha, delta=_firmado(tipo, monto_c)) if tipo == "gasto" else None
    if apartado is None:
        respuesta = ctx.api("POST", "/api/transacciones", cuerpo)
    else:
        respuesta = ctx.api("POST", f"/api/apartados/{apartado[0]}/movimiento", {"monto": monto, "accion": apartado[1]})
    return {"entrada": cuerpo if apartado is None else {"meta": apartado[0], "accion": apartado[1], "monto": monto},
            "respuesta": respuesta, "pre": pre, "fecha_ref": fecha, "monto_c": monto_c, "tipo": tipo,
            "candidato": candidato, "apartado": apartado is not None}


def gasto(ctx):
    return _crear(ctx, "gasto", ctx.fecha_cercana(20), _monto(ctx.rng), ctx.rng.choice(CATEGORIAS_GASTO),
                  ctx.rng.choice(DESCRIPCIONES))


def ingreso(ctx):
    return _crear(ctx, "ingreso", ctx.fecha_cercana(20), _monto(ctx.rng) * 10, 12, "Ingreso de prueba")


def depositar(ctx):
    return _crear(ctx, "gasto", ctx.hoy.isoformat(), _monto(ctx.rng), None, None, apartado=(ctx.meta_id, "depositar"))


def retirar(ctx):
    return _crear(ctx, "ingreso", ctx.hoy.isoformat(), _monto(ctx.rng), None, None, apartado=(ctx.meta_id, "retirar"))


def _quitar(ctx, modo):
    mov = _elegir_movimiento(ctx)
    if mov is None:
        return None
    pre = _estado(ctx.oraculo, mov["fecha"])
    respuesta = ctx.api("DELETE", f"/api/transacciones/{mov['id']}?modo={modo}")
    return {"entrada": {"id": mov["id"], "modo": modo, "fecha": mov["fecha"], "monto": mov["monto"], "tipo": mov["tipo"]},
            "respuesta": respuesta, "pre": pre, "fecha_ref": mov["fecha"], "monto_c": _a_centavos(mov["monto"]),
            "tipo": mov["tipo"], "objetivo": mov["id"], "candidato": None, "apartado": False}


def cancelar(ctx):
    return _quitar(ctx, "cancelar")


def eliminar(ctx):
    return _quitar(ctx, "eliminar")


def editar(ctx):
    mov = _elegir_movimiento(ctx)
    if mov is None:
        return None
    nuevo = round(_monto(ctx.rng), 2)
    fecha_ref = min(mov["fecha"], ctx.fecha_cercana(20))
    pre = _estado(ctx.oraculo, fecha_ref)
    cuerpo = {"monto": nuevo}
    respuesta = ctx.api("PUT", f"/api/transacciones/{mov['id']}", cuerpo)
    return {"entrada": {"id": mov["id"], "monto_anterior": mov["monto"], "monto_nuevo": nuevo},
            "respuesta": respuesta, "pre": pre, "fecha_ref": fecha_ref, "objetivo": mov["id"],
            "tipo": mov["tipo"], "monto_c": _a_centavos(mov["monto"]), "monto_nuevo_c": _a_centavos(nuevo),
            "candidato": None, "apartado": mov.get("apartado_id") is not None}


def borrar_cancelado(ctx):
    cancelados = ctx.api("GET", "/api/transacciones/canceladas").json()
    if not cancelados:
        return None
    elegido = ctx.rng.choice(cancelados)
    pre = _estado(ctx.oraculo, elegido["fecha"])
    respuesta = ctx.api("DELETE", f"/api/transacciones/canceladas/{elegido['id']}")
    return {"entrada": {"id_cancelada": elegido["id"]}, "respuesta": respuesta, "pre": pre,
            "fecha_ref": elegido["fecha"], "objetivo_cancelado": elegido["id"], "candidato": None, "apartado": False}


def avanzar_dia(ctx):
    ctx.hoy += datetime.timedelta(days=ctx.rng.choice((1, 1, 2, 7, 15)))
    ctx.fijar_hoy()
    return {"entrada": {"hoy": ctx.hoy.isoformat()}, "respuesta": None, "pre": None, "fecha_ref": None,
            "candidato": None, "apartado": False}


FUNCIONES = {
    "gasto": gasto, "ingreso": ingreso, "cancelar": cancelar, "eliminar": eliminar, "editar": editar,
    "borrar_cancelado": borrar_cancelado, "depositar": depositar, "retirar": retirar, "avanzar_dia": avanzar_dia,
}


def elegir(rng):
    nombres = [n for n, _ in PESOS_ACCION]
    pesos = [p for _, p in PESOS_ACCION]
    return rng.choices(nombres, weights=pesos)[0]


def verificar(nombre, res, oraculo, saldo_api):
    """Devuelve la lista de invariantes rotas en este paso (vacía si todo cuadra)."""
    problemas = []
    if res["respuesta"] is not None and res["respuesta"].status_code >= 500:
        problemas.append("error del servidor (5xx)")

    saldo_base = oraculo.saldo_c()
    if abs(saldo_api - saldo_base / 100) > TOLERANCIA:
        problemas.append(f"resumen dice {saldo_api} y la base suma {saldo_base / 100}")

    if res["respuesta"] is None:
        return problemas

    codigo = res["respuesta"].status_code
    post = _estado(oraculo, res["fecha_ref"]) if res["fecha_ref"] else None
    pre = res["pre"]

    if 200 <= codigo < 300:
        mov_ok = len(post["mov"]) - len(pre["mov"])
        delta = post["saldo"] - pre["saldo"]
        if nombre in ("gasto", "ingreso", "depositar", "retirar"):
            if mov_ok != 1:
                problemas.append(f"el alta debía añadir 1 movimiento y añadió {mov_ok}")
            esperado = _firmado(res["tipo"], res["monto_c"])
            if delta != esperado:
                problemas.append(f"el saldo cambió {delta / 100} y debía cambiar {esperado / 100}")
        elif nombre in ("cancelar", "eliminar"):
            ids_post = {fila[0] for fila in post["mov"]}
            if res["objetivo"] in ids_post:
                problemas.append("el movimiento quitado sigue en la lista")
            if delta != -_firmado(res["tipo"], res["monto_c"]):
                problemas.append(f"quitar un {res['tipo']} de {res['monto_c'] / 100} cambió el saldo {delta / 100}")
            cancelados_nuevos = len(post["can"]) - len(pre["can"])
            if nombre == "cancelar" and cancelados_nuevos != 1:
                problemas.append(f"cancelar debía dejar 1 constancia y dejó {cancelados_nuevos}")
            if nombre == "eliminar" and cancelados_nuevos != 0:
                problemas.append(f"eliminar no debía dejar rastro y dejó {cancelados_nuevos} constancia(s)")
        elif nombre == "editar":
            fila = next((f for f in post["mov"] if f[0] == res["objetivo"]), None)
            if fila is None:
                problemas.append("el movimiento editado desapareció")
            elif fila[2] != _a_centavos(res["entrada"]["monto_nuevo"]):
                problemas.append(f"la edición guardó {fila[2] / 100} y se pidió {res['entrada']['monto_nuevo']}")
            esperado = _firmado(res["tipo"], res["monto_nuevo_c"]) - _firmado(res["tipo"], res["monto_c"])
            if delta != esperado:
                problemas.append(f"la edición cambió el saldo {delta / 100} y debía cambiar {esperado / 100}")
        elif nombre == "borrar_cancelado":
            if any(f[0] == res["objetivo_cancelado"] for f in post["can"]):
                problemas.append("el cancelado borrado sigue en la lista")
            if delta != 0 or len(post["mov"]) != len(pre["mov"]):
                problemas.append("borrar un cancelado no debía tocar el saldo ni los movimientos")
        if res["fecha_ref"] and post["minimo"] < min(0, pre["minimo"]) - TOLERANCIA * 100:
            problemas.append(f"el saldo baja a {post['minimo'] / 100} desde esa fecha, antes no bajaba de {min(0, pre['minimo']) / 100}")
        return problemas

    # Rechazo: nada debe cambiar en la base
    if codigo == 404 and nombre != "avanzar_dia":
        problemas.append("la lista muestra un movimiento que el servidor no encuentra (404)")
    if post is not None and (post["mov"] != pre["mov"] or post["can"] != pre["can"] or post["saldo"] != pre["saldo"]):
        problemas.append("un rechazo cambió la base")
    detalle = res["respuesta"].text
    if nombre == "gasto" and "Fondos insuficientes" in detalle and res["candidato"] is not None:
        if res["candidato"] >= 0:
            problemas.append("rechazo falso: el gasto no deja ninguna fecha bajo cero y aun así se rechazó por fondos")
    return problemas
