"""tests/auditoria/copias.py - Sección Respaldo y Excel por API: descargar un respaldo, restaurarlo, rechazar archivos dañados
y exportar el reporte en Excel.

Mismo esquema que acciones.py y los demás módulos. Lo que se comprueba con cálculo propio:
- El respaldo que se descarga contiene lo mismo que la base viva.
- Restaurar un respaldo deja la base exactamente como estaba cuando se respaldó, aunque en medio se hayan hecho cambios.
- Un archivo que no es un respaldo (extensión mala, bytes al azar, cabecera falsa, una base ajena, un respaldo cortado) se
  rechaza con un 400 y no cambia nada.
- El Excel tiene los mismos movimientos, el mismo balance y el mismo número de fijos, cuentas y cancelados que la base, en
  la moneda que dice su nombre; y ninguna descripción del usuario se vuelve fórmula.
"""

import datetime
import io
import os
import sqlite3
import tempfile

import openpyxl

import acciones
import portafolio

PESOS_ACCION = (
    ("ingreso", 12), ("gasto", 10), ("cancelar", 8), ("crear_fijo", 6), ("crear_cuenta", 4), ("crear_posicion", 4),
    ("respaldar", 10), ("restaurar", 10), ("restaurar_danado", 10), ("excel", 20), ("avanzar_dia", 6),
)
DESCRIPCIONES_PELIGROSAS = ("=1+1", "+52 55 1234 5678", "-5 y más", "@usuario", '=HYPERLINK("http://x","a")', "Súper", None)
MONEDAS_EXCEL = ("MXN", "USD", "EUR")
TOLERANCIA = acciones.TOLERANCIA
CABECERA_SQLITE = b"SQLite format 3\x00"

Contexto = acciones.Contexto


def _hoy():
    return os.environ["FINANZAS_HOY"][:10]


def _volcado_de_bytes(datos):
    """Contenido de las tablas de una base dada como bytes (un respaldo descargado)."""
    from oraculo import Oraculo
    fd, ruta = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(ruta, "wb") as archivo:
            archivo.write(datos)
        return Oraculo(ruta).volcado()
    finally:
        os.unlink(ruta)


def _subir(ctx, nombre, contenido):
    return ctx.cliente.post("/api/restore", files={"file": (nombre, contenido, "application/octet-stream")},
                            headers=ctx.cabecera)


def _base(ctx, accion, respuesta=None, **extra):
    return {"accion": accion, "respuesta": respuesta, **extra}


def ingreso(ctx):
    cuerpo = {"fecha": ctx.fecha_cercana(15), "monto": round(ctx.rng.uniform(500, 15000), 2), "tipo": "ingreso",
              "categoria_id": 12, "descripcion": ctx.rng.choice(DESCRIPCIONES_PELIGROSAS)}
    return _base(ctx, "ingreso", ctx.api("POST", "/api/transacciones", cuerpo), entrada=cuerpo)


def gasto(ctx):
    cuerpo = {"fecha": ctx.fecha_cercana(10), "monto": round(ctx.rng.uniform(10, 2000), 2), "tipo": "gasto",
              "categoria_id": ctx.rng.choice(acciones.CATEGORIAS_GASTO), "descripcion": ctx.rng.choice(DESCRIPCIONES_PELIGROSAS)}
    return _base(ctx, "gasto", ctx.api("POST", "/api/transacciones", cuerpo), entrada=cuerpo)


def cancelar(ctx):
    lista = ctx.api("GET", "/api/transacciones").json()
    if not lista:
        return None
    mov = ctx.rng.choice(lista)
    return _base(ctx, "cancelar", ctx.api("DELETE", f"/api/transacciones/{mov['id']}?modo=cancelar"), entrada={"id": mov["id"]})


def crear_fijo(ctx):
    cuerpo = {"descripcion": f"Fijo {ctx.rng.randint(1, 99)}", "tipo": "ingreso", "frecuencia": "mensual",
              "monto": round(ctx.rng.uniform(1000, 10000), 2), "fecha_inicio": ctx.fecha_cercana(20), "categoria_id": 12}
    return _base(ctx, "crear_fijo", ctx.api("POST", "/api/recurrentes", cuerpo), entrada=cuerpo)


def crear_cuenta(ctx):
    cuerpo = {"nombre": f"Cuenta {ctx.rng.randint(1, 25)}", "capital": round(ctx.rng.uniform(1000, 50000), 2),
              "tasa_anual": round(ctx.rng.uniform(0, 12), 2), "fecha_inicio": ctx.fecha_cercana(30)}
    return _base(ctx, "crear_cuenta", ctx.api("POST", "/api/rendimiento", cuerpo), entrada=cuerpo)


def crear_posicion(ctx):
    cuerpo = {"ticker": ctx.rng.choice(portafolio.TICKERS), "cantidad": round(ctx.rng.uniform(1, 100), 2),
              "precio_promedio": round(ctx.rng.uniform(1, 300), 2), "precio_actual": round(ctx.rng.uniform(1, 300), 2),
              "moneda": "USD"}
    return _base(ctx, "crear_posicion", ctx.api("POST", "/api/inversiones", cuerpo), entrada=cuerpo)


def respaldar(ctx):
    respuesta = ctx.api("GET", "/api/backup")
    vivo = ctx.oraculo.volcado()
    ctx.respaldo = {"bytes": respuesta.content, "volcado": vivo}
    return _base(ctx, "respaldar", respuesta, entrada={"bytes": len(respuesta.content)}, volcado_vivo=vivo,
                 contenido=respuesta.content)


def restaurar(ctx):
    guardado = getattr(ctx, "respaldo", None)
    if not guardado:
        return None
    respuesta = _subir(ctx, "copia.db", guardado["bytes"])
    # Se mira la base justo al restaurar: la siguiente lectura del Resumen materializa los cobros que ya llegaron
    # (el reloj pudo avanzar desde el respaldo), y eso es lo normal, no parte de restaurar
    tras = ctx.oraculo.volcado()
    return _base(ctx, "restaurar", respuesta, entrada={"bytes": len(guardado["bytes"])},
                 esperado=guardado["volcado"], tras=tras)


def restaurar_danado(ctx):
    antes = ctx.oraculo.volcado()
    variante = ctx.rng.choice(("extension", "azar", "cabecera_falsa", "base_ajena", "cortada"))
    if variante == "extension":
        nombre, contenido = "notas.txt", b"hola, esto no es una base de datos"
    elif variante == "azar":
        nombre, contenido = "falsa.db", bytes(ctx.rng.randrange(256) for _ in range(400))
    elif variante == "cabecera_falsa":
        nombre, contenido = "trunca.db", CABECERA_SQLITE + bytes(ctx.rng.randrange(256) for _ in range(400))
    elif variante == "base_ajena":
        fd, ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            con = sqlite3.connect(ruta)
            con.execute("CREATE TABLE ajena (a INTEGER)")
            con.executemany("INSERT INTO ajena VALUES (?)", [(i,) for i in range(200)])
            con.commit()
            con.close()
            with open(ruta, "rb") as archivo:
                nombre, contenido = "ajena.db", archivo.read()
        finally:
            os.unlink(ruta)
    else:
        guardado = getattr(ctx, "respaldo", None)
        datos = guardado["bytes"] if guardado else ctx.api("GET", "/api/backup").content
        nombre, contenido = "cortada.db", datos[: max(150, len(datos) // 2)]
    return _base(ctx, "restaurar_danado", _subir(ctx, nombre, contenido), entrada={"variante": variante, "bytes": len(contenido)},
                 antes=antes)


def excel(ctx):
    moneda = ctx.rng.choice(MONEDAS_EXCEL)
    respuesta = ctx.api("GET", f"/api/exportar-excel?moneda_base={moneda}")
    tasas = ctx.api("GET", "/api/tipo-cambio").json().get("tasas", {})
    return _base(ctx, "excel", respuesta, entrada={"moneda": moneda}, moneda=moneda, tasas=tasas, contenido=respuesta.content)


def avanzar_dia(ctx):
    ctx.hoy += datetime.timedelta(days=ctx.rng.choice((1, 1, 2, 7, 15)))
    ctx.fijar_hoy()
    return _base(ctx, "avanzar_dia", None, entrada={"hoy": ctx.hoy.isoformat()})


FUNCIONES = {
    "ingreso": ingreso, "gasto": gasto, "cancelar": cancelar, "crear_fijo": crear_fijo, "crear_cuenta": crear_cuenta,
    "crear_posicion": crear_posicion, "respaldar": respaldar, "restaurar": restaurar,
    "restaurar_danado": restaurar_danado, "excel": excel, "avanzar_dia": avanzar_dia,
}


def elegir(rng):
    nombres = [n for n, _ in PESOS_ACCION]
    pesos = [p for _, p in PESOS_ACCION]
    return rng.choices(nombres, weights=pesos)[0]


def _filas_con_datos(hoja, primera=5):
    """Filas de datos de una hoja: desde la 5 hasta la primera vacía, sin contar el aviso «No hay…»."""
    filas = []
    fila = primera
    while hoja.cell(row=fila, column=1).value not in (None, ""):
        valor = hoja.cell(row=fila, column=1).value
        if isinstance(valor, str) and valor.startswith("No hay"):
            break
        filas.append(fila)
        fila += 1
    return filas


def _verificar_excel(res, oraculo):
    problemas = []
    respuesta = res["respuesta"]
    if respuesta.status_code != 200:
        return [f"el reporte de Excel respondió {respuesta.status_code}"]
    moneda = res["moneda"]
    nombre_archivo = respuesta.headers.get("content-disposition", "")
    if f"_{moneda}_" not in nombre_archivo:
        problemas.append(f"el reporte pide {moneda} y el archivo se llama {nombre_archivo.split('filename=')[-1]}")
    libro = openpyxl.load_workbook(io.BytesIO(res["contenido"]))
    tasas = res["tasas"]
    convertir = lambda pesos: round(portafolio._convertir(pesos, "MXN", moneda, tasas["MXN"], tasas["EUR"]), 2)

    hoja = libro["Movimientos"]
    encabezado = hoja.cell(row=4, column=5).value
    if encabezado != f"Monto ({moneda})":
        problemas.append(f"la columna de montos dice «{encabezado}» y el reporte es en {moneda}")
    esperados = {fila[0]: fila for fila in oraculo.movimientos()}
    vistos = {}
    fila = 5
    while hoja.cell(row=fila, column=3).value not in (None, "BALANCE"):
        id_ = hoja.cell(row=fila, column=1).value
        vistos[id_] = fila
        fila += 1
    if set(vistos) != set(esperados):
        problemas.append(f"el Excel tiene {len(vistos)} movimientos y la base {len(esperados)}")
    for id_, fila_excel in vistos.items():
        base = esperados.get(id_)
        if base is None:
            continue
        monto = hoja.cell(row=fila_excel, column=5).value
        if hoja.cell(row=fila_excel, column=2).value != base[1] or hoja.cell(row=fila_excel, column=3).value != base[3].upper():
            problemas.append(f"el movimiento {id_} tiene otra fecha o tipo en el Excel")
        if abs(monto - convertir(base[2] / 100)) > 0.011:
            problemas.append(f"el movimiento {id_} suma {monto} en el Excel y {convertir(base[2] / 100)} en la base")
        celda = hoja.cell(row=fila_excel, column=4)
        if celda.data_type == "f":
            problemas.append(f"la descripción del movimiento {id_} quedó como fórmula en el Excel: {str(celda.value)[:40]}")
    if fila >= 5 and hoja.cell(row=fila, column=3).value == "BALANCE":
        balance = hoja.cell(row=fila, column=5).value
        if abs(balance - convertir(oraculo.saldo_c() / 100)) > 0.02:
            problemas.append(f"el balance del Excel es {balance} y la base suma {convertir(oraculo.saldo_c() / 100)}")

    for titulo, esperado in (("Fijos y MSI", len(oraculo.recurrentes())), ("Cuentas con rendimiento", len(oraculo.cuentas_rendimiento())),
                             ("Cancelados", len(oraculo.cancelados()))):
        encontrados = len(_filas_con_datos(libro[titulo]))
        if encontrados != esperado:
            problemas.append(f"la hoja «{titulo}» tiene {encontrados} filas y la base {esperado}")
    return problemas


def _diferencias(esperado, actual):
    """Tablas que no coinciden y la primera fila que sobra o falta en cada una."""
    partes = []
    for tabla in sorted(set(esperado) | set(actual)):
        antes, despues = set(esperado.get(tabla, ())), set(actual.get(tabla, ()))
        if antes != despues:
            faltan, sobran = sorted(antes - despues, key=str)[:1], sorted(despues - antes, key=str)[:1]
            partes.append(f"{tabla}: falta {faltan}, sobra {sobran} (esperadas {len(esperado.get(tabla, ()))}, hay {len(actual.get(tabla, ()))})")
    return "; ".join(partes)[:600]


def verificar(nombre, res, oraculo, saldo_api):
    """Devuelve la lista de invariantes rotas en este paso (vacía si todo cuadra)."""
    problemas = []
    respuesta = res["respuesta"]
    if respuesta is not None and respuesta.status_code >= 500:
        problemas.append("error del servidor (5xx)")

    saldo_base = oraculo.saldo_c()
    if abs(saldo_api - saldo_base / 100) > TOLERANCIA:
        problemas.append(f"resumen dice {saldo_api} y la base suma {saldo_base / 100}")

    if respuesta is None:
        return problemas

    if nombre == "respaldar":
        if respuesta.status_code != 200 or respuesta.content[:16] != CABECERA_SQLITE:
            problemas.append("el respaldo descargado no es un archivo SQLite")
        elif _volcado_de_bytes(res["contenido"]) != res["volcado_vivo"]:
            problemas.append("el respaldo descargado no tiene lo mismo que la base viva")
    elif nombre == "restaurar":
        if respuesta.status_code != 200:
            problemas.append(f"restaurar un respaldo propio respondió {respuesta.status_code}")
        else:
            diferencias = _diferencias(res["esperado"], res["tras"])
            if diferencias:
                problemas.append(f"tras restaurar, la base no quedó como cuando se respaldó: {diferencias}")
    elif nombre == "restaurar_danado":
        if respuesta.status_code == 200:
            problemas.append(f"se aceptó restaurar un archivo dañado ({res['entrada']['variante']})")
        elif respuesta.status_code not in (400, 413, 422):
            problemas.append(f"un archivo dañado ({res['entrada']['variante']}) respondió {respuesta.status_code}, no un rechazo")
        if oraculo.volcado() != res["antes"]:
            problemas.append(f"restaurar un archivo dañado ({res['entrada']['variante']}) cambió la base")
    elif nombre == "excel":
        problemas += _verificar_excel(res, oraculo)
    return problemas
