"""tests/auditoria/fijos_metas.py - Sección Fijos y metas por API: gastos fijos que aportan solos a una meta, con el tiempo corriendo.

Mismo esquema que fijos.py y metas.py: las acciones eligen sus datos con la semilla y verificar() compara lo que quedó en la base con el
oráculo (SQL propio). El tiempo avanza hasta 60 días de golpe: las aportaciones se crean al abrir la app (cualquier petición), todas las
atrasadas juntas.

Reglas que se comprueban además de las de Fijos y Metas:
- El saldo guardado de cada meta es su ahorro propio más lo que sus movimientos suman (también las aportaciones automáticas).
- Ningún fijo aporta dos veces en la misma fecha ni con fecha futura; saltar un ciclo avanza la fecha; cancelar una aportación la saca de la meta.
- El saldo de la cuenta no baja de cero en ninguna fecha. [F1: la aportación automática no valida saldo; decidido: sin saldo no se aporta]
- Un fijo que aporta a una meta ya alcanzada deja de existir. [F2: hoy sigue aportando; decidido: se quita al cumplirse]
- Borrar una meta con un fijo ligado exige confirmar. [F3: hoy se borra y el fijo sigue como gasto normal]
Los tres últimos se marcan con su etiqueta (F1, F2, F3) del plan-auditoria-asistente-y-core.md y siguen fallando hasta que se arreglen.
- Un rechazo no cambia nada.
"""

import datetime
import os

import acciones
import fijos
import metas

PESOS_ACCION = (
    ("alta_ingreso_fijo", 6), ("alta_aporte", 16), ("crear_meta", 6), ("ingreso", 10), ("avanzar_dia", 24),
    ("saltar_aporte", 7), ("cancelar_aportacion", 9), ("eliminar_meta", 8), ("eliminar_fijo", 6), ("depositar", 8),
    ("gastar_casi_todo", 6),
)
SALTOS_DE_DIAS = (1, 2, 7, 15, 30, 60)
TOLERANCIA = acciones.TOLERANCIA

Contexto = acciones.Contexto


def _fecha_hoy():
    return os.environ["FINANZAS_HOY"][:10]


def _estado(oraculo):
    return {
        "rec": oraculo.recurrentes(), "mov": oraculo.movimientos(), "apartados": oraculo.apartados(),
        "ligados": oraculo.fijos_a_meta(), "saldo": oraculo.saldo_c(),
    }


def preparar(cliente, cabecera):
    """Un ingreso fijo desde el inicio: sin él la app no deja crear ningún gasto fijo, y esta sección es sobre esos."""
    lista = cliente.get("/api/recurrentes", headers=cabecera).json()
    if any(r["tipo"] == "ingreso" and r.get("activo") and not r.get("total_cuotas") for r in lista):
        return
    cliente.post("/api/recurrentes", headers=cabecera, json={
        "descripcion": "Nómina de prueba", "tipo": "ingreso", "frecuencia": "mensual", "monto": 30000,
        "fecha_inicio": _fecha_hoy(), "categoria_id": fijos.CATEGORIA_INGRESO})


def alta_ingreso_fijo(ctx):
    return fijos.alta_ingreso_fijo(ctx) | {"accion": "alta_ingreso_fijo"}


def alta_aporte(ctx):
    lista = metas._metas_api(ctx)
    if not lista:
        return None
    meta = ctx.rng.choice(lista)
    cuerpo = {
        "descripcion": f"Aporte {ctx.rng.randint(1, 99)}", "tipo": "gasto", "frecuencia": ctx.rng.choice(fijos.FRECUENCIAS),
        "monto": round(ctx.rng.uniform(20, 900), 2), "fecha_inicio": ctx.fecha_cercana(20),
        "categoria_id": ctx.rng.choice(acciones.CATEGORIAS_GASTO), "apartado_id": meta["id"],
    }
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("POST", "/api/recurrentes", cuerpo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": pre, "accion": "alta_aporte", "objetivo": meta["id"],
            "ingresos_activos_pre": fijos._ingresos_activos(ctx.oraculo)}


def crear_meta(ctx):
    return metas.crear_meta(ctx) | {"accion": "crear_meta"}


def ingreso(ctx):
    return metas.ingreso(ctx) | {"accion": "ingreso"}


def depositar(ctx):
    res = metas.depositar(ctx)
    return res if res is None else res | {"accion": "depositar"}


def gastar_casi_todo(ctx):
    """Deja la cuenta casi vacía: así el siguiente ciclo de un aporte se encuentra sin saldo (F1), que con las cuentas holgadas de las personas no pasa."""
    disponible = ctx.oraculo.saldo_hasta_c(ctx.hoy.isoformat()) / 100
    monto = round(disponible - ctx.rng.uniform(0, 150), 2)
    if monto < 1:
        return None
    cuerpo = {"fecha": ctx.hoy.isoformat(), "monto": monto, "tipo": "gasto", "categoria_id": ctx.rng.choice(acciones.CATEGORIAS_GASTO),
              "descripcion": "Gasto grande de prueba"}
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("POST", "/api/transacciones", cuerpo)
    return {"entrada": cuerpo, "respuesta": respuesta, "pre": pre, "accion": "gastar_casi_todo"}


def avanzar_dia(ctx):
    ctx.hoy += datetime.timedelta(days=ctx.rng.choice(SALTOS_DE_DIAS))
    ctx.fijar_hoy()
    return {"entrada": {"hoy": ctx.hoy.isoformat()}, "respuesta": None, "pre": _estado(ctx.oraculo), "accion": "avanzar_dia"}


def _aportes(ctx):
    return [r for r in ctx.api("GET", "/api/recurrentes").json() if r.get("apartado_id") and r.get("activo")]


def saltar_aporte(ctx):
    lista = _aportes(ctx)
    if not lista:
        return None
    elegido = ctx.rng.choice(lista)
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("POST", f"/api/recurrentes/{elegido['id']}/saltar", {"contabilizar_cuota": False})
    return {"entrada": {"id": elegido["id"]}, "respuesta": respuesta, "pre": pre, "accion": "saltar_aporte",
            "objetivo": elegido["id"], "proxima_antes": elegido["proxima_fecha"]}


def cancelar_aportacion(ctx):
    ligados = [m for m in ctx.api("GET", "/api/transacciones").json() if m.get("recurrente_id") and m.get("apartado_id")]
    if not ligados:
        return None
    mov = ctx.rng.choice(ligados)
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("DELETE", f"/api/transacciones/{mov['id']}?modo=cancelar")
    return {"entrada": {"id": mov["id"], "apartado": mov["apartado_id"], "monto": mov["monto"]}, "respuesta": respuesta,
            "pre": pre, "accion": "cancelar_aportacion", "objetivo": mov["id"]}


def eliminar_meta(ctx):
    """Se prefieren las que tienen un fijo ligado: es el caso que interesa (F3). Con saldo la app la rechaza, y eso también se comprueba."""
    lista = metas._metas_api(ctx)
    if not lista:
        return None
    con_fijo = {r[1] for r in ctx.oraculo.fijos_a_meta()}
    candidatas = [m for m in lista if m["id"] in con_fijo] or lista
    meta = ctx.rng.choice(candidatas)
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("DELETE", f"/api/apartados/{meta['id']}")
    return {"entrada": {"id": meta["id"], "nombre": meta["nombre"]}, "respuesta": respuesta, "pre": pre,
            "accion": "eliminar_meta", "objetivo": meta["id"]}


def eliminar_fijo(ctx):
    lista = _aportes(ctx)
    if not lista:
        return None
    elegido = ctx.rng.choice(lista)
    pre = _estado(ctx.oraculo)
    respuesta = ctx.api("DELETE", f"/api/recurrentes/{elegido['id']}")
    return {"entrada": {"id": elegido["id"]}, "respuesta": respuesta, "pre": pre, "accion": "eliminar_fijo", "objetivo": elegido["id"]}


FUNCIONES = {
    "alta_ingreso_fijo": alta_ingreso_fijo, "alta_aporte": alta_aporte, "crear_meta": crear_meta, "ingreso": ingreso,
    "avanzar_dia": avanzar_dia, "gastar_casi_todo": gastar_casi_todo, "saltar_aporte": saltar_aporte, "cancelar_aportacion": cancelar_aportacion,
    "eliminar_meta": eliminar_meta, "eliminar_fijo": eliminar_fijo, "depositar": depositar,
}


def elegir(rng):
    nombres = [n for n, _ in PESOS_ACCION]
    pesos = [p for _, p in PESOS_ACCION]
    return rng.choices(nombres, weights=pesos)[0]


def _una_vez(oraculo, clave, texto, problemas):
    """F1 y F2 describen un estado que dura: se anotan la primera vez que aparecen en el episodio, no en cada paso que sigue."""
    visto = oraculo.__dict__.setdefault("avisados", set())
    if (oraculo.ruta_db, clave) not in visto:
        visto.add((oraculo.ruta_db, clave))
        problemas.append(texto)


def verificar(nombre, res, oraculo, saldo_api):
    """Devuelve la lista de invariantes rotas en este paso (vacía si todo cuadra)."""
    problemas = []
    respuesta = res["respuesta"]
    if respuesta is not None and respuesta.status_code >= 500:
        problemas.append("error del servidor (5xx)")

    saldo_base = oraculo.saldo_c()
    if abs(saldo_api - saldo_base / 100) > TOLERANCIA:
        problemas.append(f"resumen dice {saldo_api} y la base suma {saldo_base / 100}")

    # Identidad de cada meta: saldo guardado = ahorro propio + depósitos − retiros (las aportaciones automáticas son depósitos)
    neto = oraculo.neto_por_apartado()
    for id_, nombre_meta, _, guardado, _ in oraculo.apartados():
        esperado = getattr(oraculo, "offsets", {}).get(id_, 0) + neto.get(id_, 0)
        if guardado != esperado:
            problemas.append(f"la meta «{nombre_meta}» guarda {guardado / 100} y sus movimientos suman {esperado / 100}")

    hoy = _fecha_hoy()
    duplicadas = [f for f in oraculo.filas_ligadas() if f[2] > 1]
    if duplicadas:
        problemas.append(f"un fijo aportó dos veces en la misma fecha: {duplicadas[0][:2]}")
    futuras = oraculo.filas_ligadas_despues(hoy)
    if futuras:
        problemas.append(f"hay aportaciones con fecha futura: {futuras[0]}")

    # F1: el dinero de la cuenta no baja de cero en ninguna fecha (aportes a metas incluidos: salen de la cuenta)
    minimo = oraculo.minimo_desde(hoy)
    if minimo < 0:
        _una_vez(oraculo, "F1", f"F1: el saldo queda en {minimo / 100} en alguna fecha (¿una aportación automática sin saldo?)", problemas)

    # F2: una meta con objetivo ya alcanzada no debería seguir recibiendo un fijo
    objetivos = {a[0]: (a[3], a[4]) for a in oraculo.apartados()}
    for fijo, meta, activo in oraculo.fijos_a_meta():
        guardado, objetivo = objetivos.get(meta, (0, None))
        if activo and objetivo and guardado >= objetivo:
            _una_vez(oraculo, ("F2", fijo), f"F2: el fijo {fijo} sigue aportando a la meta {meta}, que ya llegó a su objetivo ({guardado / 100} de {objetivo / 100})", problemas)

    if respuesta is None:
        return problemas

    codigo = respuesta.status_code
    pre = res["pre"]
    post = _estado(oraculo)
    aceptada = 200 <= codigo < 300

    if aceptada:
        if nombre == "alta_aporte":
            if res.get("ingresos_activos_pre", 0) == 0:
                problemas.append("se aceptó un gasto fijo sin ingreso fijo activo")
            if len(post["rec"]) != len(pre["rec"]) + 1:
                problemas.append("el alta de un aporte no creó exactamente un fijo")
            if (post["ligados"] and res["objetivo"] not in [l[1] for l in post["ligados"]]):
                problemas.append("el aporte no quedó ligado a la meta elegida")
        if nombre == "saltar_aporte":
            nueva = next((r for r in post["rec"] if r[0] == res["objetivo"]), None)
            if nueva is None or nueva[8] == res["proxima_antes"]:
                problemas.append("saltar un ciclo no avanzó la próxima fecha")
        if nombre == "eliminar_fijo" and any(r[0] == res["objetivo"] for r in post["rec"]):
            problemas.append("el fijo eliminado sigue en la base")
        if nombre == "cancelar_aportacion" and any(f[0] == res["objetivo"] for f in post["mov"]):
            problemas.append("la aportación cancelada sigue en la base")
        if nombre == "eliminar_meta":
            guardado = next((a[3] for a in pre["apartados"] if a[0] == res["objetivo"]), 0)
            if guardado > 0:
                problemas.append(f"se eliminó una meta con {guardado / 100} guardados")
            if any(l[1] == res["objetivo"] for l in pre["ligados"]):
                problemas.append("F3: se borró la meta sin confirmar y su fijo siguió como gasto normal")
        return problemas

    # Rechazo: nada debe cambiar en la base
    if codigo == 404 and nombre in ("saltar_aporte", "eliminar_fijo", "cancelar_aportacion", "eliminar_meta"):
        problemas.append("la lista muestra un elemento que el servidor no encuentra (404)")
    # Las acciones tomadas de Fijos y Metas guardan un «antes» con otras llaves: se comparan solo las que ambos tienen
    if nombre != "avanzar_dia" and any(pre[k] != post[k] for k in ("rec", "mov", "saldo", "apartados") if k in pre):
        problemas.append("un rechazo cambió la base")
    return problemas
