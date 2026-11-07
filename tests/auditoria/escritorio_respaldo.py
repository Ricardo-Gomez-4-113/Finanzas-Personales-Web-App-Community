"""tests/auditoria/escritorio_respaldo.py - Capas B y C de Respaldo y Excel en el navegador.

Mismo recorrido que escritorio_metas.py (1280×800 o 390×844 táctil), con las acciones del menú de usuario: descargar el
respaldo, restaurarlo (con su aviso de confirmación y el selector de archivo), intentar restaurar archivos dañados y
exportar el reporte de Excel en cada moneda. Se juzga con las mismas reglas que la capa API (tests/auditoria/copias.py):
el respaldo trae lo que tiene la base, restaurar la deja como estaba, un archivo dañado se rechaza sin cambiar nada
y el Excel coincide con la base en la moneda elegida. Los movimientos de apoyo se registran por la API.

Corre dentro del contenedor de Playwright: tests/auditoria/run.sh --seccion respaldo --capa escritorio|celular.
"""

import datetime
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import copias  # noqa: E402
import escritorio  # noqa: E402
import personas  # noqa: E402
from bitacora import Bitacora  # noqa: E402
from oraculo import Oraculo  # noqa: E402

HOY_INICIAL = datetime.date(2026, 10, 1)
PASOS_MIN, PASOS_MAX = 5, 30
PESOS_UI = (
    ("ingreso", 12), ("gasto", 12), ("respaldar", 15), ("restaurar", 15), ("restaurar_danado", 14),
    ("excel", 20), ("recargar", 4), ("avanzar_dia", 6),
)
IGNORAR_EN_COPIA = escritorio.IGNORAR_EN_COPIA


class Estado:
    """Lo que la pantalla puede tener guardado entre acciones: el último respaldo descargado."""
    respaldo = None
    retener = False


def _quitar_avisos(page):
    page.evaluate("document.querySelectorAll('.toast').forEach(t => t.remove())")


def _esperar_aviso(page, segundos=20.0):
    """Texto del primer aviso que aparece (éxito o error) y su clase."""
    page.wait_for_selector(".toast", timeout=segundos * 1000)
    aviso = page.locator(".toast").first
    return aviso.inner_text().strip()[:200], (aviso.get_attribute("class") or "")


def _apoyo(cliente, oraculo, hoy, rng, tipo):
    cuerpo = {"fecha": hoy.isoformat(), "monto": round(rng.uniform(10, 5000), 2), "tipo": tipo,
              "categoria_id": 12 if tipo == "ingreso" else rng.choice((1, 2, 7)),
              "descripcion": rng.choice(copias.DESCRIPCIONES_PELIGROSAS)}
    cliente.post("/api/transacciones", json=cuerpo)
    return {"entrada": cuerpo, "aceptada": None, "mensaje": ""}


def _respaldar(page, oraculo, estado):
    with page.expect_download(timeout=30000) as info:
        page.evaluate("document.querySelector('.btn-backup').click()")
    ruta = info.value.path()
    with open(ruta, "rb") as archivo:
        contenido = archivo.read()
    vivo = oraculo.volcado()
    estado.respaldo = {"bytes": contenido, "volcado": vivo}
    return {"entrada": {"bytes": len(contenido), "archivo": info.value.suggested_filename}, "aceptada": True, "mensaje": "",
            "res": {"respuesta": types.SimpleNamespace(status_code=200, content=contenido), "volcado_vivo": vivo,
                    "contenido": contenido}}


def _subir(page, estado, nombre, contenido, oraculo):
    """Restaurar desde el menú: botón, confirmación y selector de archivo. Devuelve (texto del aviso, clase, volcado al instante)."""
    _quitar_avisos(page)
    page.evaluate("document.querySelector('.btn-restore').click()")
    page.wait_for_selector("#modal-confirmar:not(.hidden)", timeout=10000)
    estado.retener = True  # las lecturas de la app esperan: así la base se mira tal como la dejó la restauración
    try:
        with page.expect_file_chooser(timeout=10000) as info:
            page.evaluate("document.getElementById('btn-confirmar-aceptar').click()")
        info.value.set_files({"name": nombre, "mimeType": "application/octet-stream", "buffer": contenido})
        texto, clase = _esperar_aviso(page)
        return texto, clase, oraculo.volcado()
    finally:
        estado.retener = False


def _restaurar(page, oraculo, estado):
    if not estado.respaldo:
        return None
    texto, clase, tras = _subir(page, estado, "copia.db", estado.respaldo["bytes"], oraculo)
    ok = "restaurada" in texto.lower() and "error" not in clase
    return {"entrada": {"bytes": len(estado.respaldo["bytes"])}, "aceptada": ok, "mensaje": texto,
            "res": {"respuesta": types.SimpleNamespace(status_code=200 if ok else 400), "esperado": estado.respaldo["volcado"], "tras": tras}}


def _restaurar_danado(page, rng, oraculo, estado):
    antes = oraculo.volcado()
    variante = rng.choice(("extension", "azar", "cabecera_falsa", "base_ajena", "cortada"))
    if variante == "extension":
        nombre, contenido = "notas.txt", b"hola, esto no es una base de datos"
    elif variante == "azar":
        nombre, contenido = "falsa.db", bytes(rng.randrange(256) for _ in range(400))
    elif variante == "cabecera_falsa":
        nombre, contenido = "trunca.db", copias.CABECERA_SQLITE + bytes(rng.randrange(256) for _ in range(400))
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
        base = estado.respaldo["bytes"] if estado.respaldo else None
        if base is None:
            return None
        nombre, contenido = "cortada.db", base[: max(150, len(base) // 2)]
    texto, clase, _ = _subir(page, estado, nombre, contenido, oraculo)
    rechazada = "restaurada con" not in texto.lower()
    return {"entrada": {"variante": variante, "bytes": len(contenido)}, "aceptada": not rechazada, "mensaje": texto,
            "res": {"respuesta": types.SimpleNamespace(status_code=400 if rechazada else 200), "antes": antes,
                    "entrada": {"variante": variante}}}


def _excel(page, rng, oraculo, tasas):
    moneda = rng.choice(copias.MONEDAS_EXCEL)
    page.select_option("#select-moneda-global", value=moneda)
    page.wait_for_load_state("networkidle")
    with page.expect_download(timeout=30000) as info:
        page.evaluate("document.querySelector('.btn-export').click()")
    ruta = info.value.path()
    nombre = info.value.suggested_filename
    with open(ruta, "rb") as archivo:
        contenido = archivo.read()
    # La pantalla le pone el nombre al archivo: debe terminar en la moneda elegida
    cabecera = f"filename=x_{moneda}_x" if nombre.endswith(f"_{moneda}.xlsx") else f"filename={nombre}"
    res = {"respuesta": types.SimpleNamespace(status_code=200, headers={"content-disposition": cabecera}),
           "moneda": moneda, "tasas": tasas, "contenido": contenido}
    return {"entrada": {"moneda": moneda, "archivo": nombre}, "aceptada": True, "mensaje": "", "res": res}


def _verificar(nombre, datos, oraculo):
    res = datos.get("res")
    if res is None:
        return []
    if nombre == "excel":
        return copias._verificar_excel(res, oraculo)
    saldo_api = oraculo.saldo_c() / 100  # el resumen se compara aparte, en pantalla
    return copias.verificar(nombre, res, oraculo, saldo_api)


def _resumen_asentado(page, oraculo):
    page.wait_for_load_state("networkidle")
    if page.input_value("#select-moneda-global") != "MXN":  # el Resumen se compara en pesos, como la base
        page.select_option("#select-moneda-global", value="MXN")
        page.wait_for_load_state("networkidle")
    return escritorio._verificar_resumen(page, oraculo)


def _episodio(semilla, numero, navegador, servidor, mods, bit, fallos, dispositivo):
    rng = random.Random(semilla)
    usuario = f"aud{semilla}"
    hoy = HOY_INICIAL
    os.environ["FINANZAS_HOY"] = f"{hoy.isoformat()}T10:00:00"  # las reglas leen el día simulado de aquí
    servidor.arrancar(hoy)
    import httpx
    cliente = httpx.Client(base_url=servidor.base, timeout=30)
    cabecera, ruta_db = personas.crear_laura(cliente, usuario, mods)
    cliente.headers.update(cabecera)
    oraculo = Oraculo(ruta_db)
    tasas = cliente.get('/api/tipo-cambio').json()['tasas']
    estado = Estado()

    ctx = navegador.new_context(**escritorio.DISPOSITIVOS[dispositivo])
    page = ctx.new_page()
    errores, respuestas_5xx = [], []
    page.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
    page.on("console", lambda m: errores.append(f"console: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.on("response", lambda r: respuestas_5xx.append(f"{r.status} {r.url.split('?')[0]}") if r.status >= 500 else None)
    def _retener(ruta):
        # Mientras estado.retener sea cierto, las lecturas de la app esperan (el POST de restaurar pasa)
        while estado.retener and ruta.request.method == "GET" and "/api/" in ruta.request.url:
            page.wait_for_timeout(50)
        ruta.continue_()
    page.route("**/api/**", _retener)
    escritorio._login(page, servidor.base, usuario)

    pasos = rng.randint(PASOS_MIN, PASOS_MAX)
    for paso in range(1, pasos + 1):
        nombre = rng.choices([n for n, _ in PESOS_UI], weights=[p for _, p in PESOS_UI])[0]
        fallo_accion = None
        errores.clear()
        respuestas_5xx.clear()
        inicio = time.perf_counter()
        datos = None
        try:
            if nombre == "avanzar_dia":
                hoy = hoy + datetime.timedelta(days=rng.choice((1, 1, 2, 7, 15)))
                os.environ["FINANZAS_HOY"] = f"{hoy.isoformat()}T10:00:00"
                servidor.parar()
                servidor.arrancar(hoy)
                escritorio._reentrar(page, servidor.base, usuario)
                datos = {"entrada": {"hoy": hoy.isoformat()}, "aceptada": None, "mensaje": ""}
            elif nombre == "recargar":
                page.reload()
                page.wait_for_selector("#vista-app:not(.hidden)", timeout=20000)
                datos = {"entrada": {}, "aceptada": None, "mensaje": ""}
            elif nombre == "dashboard":
                escritorio._ir(page, "tab-dashboard")
                datos = {"entrada": {}, "aceptada": None, "mensaje": ""}
            elif nombre in ("ingreso", "gasto"):
                datos = _apoyo(cliente, oraculo, hoy, rng, nombre)
                page.reload()
                page.wait_for_selector("#vista-app:not(.hidden)", timeout=20000)
            elif nombre == "respaldar":
                datos = _respaldar(page, oraculo, estado)
            elif nombre == "restaurar":
                datos = _restaurar(page, oraculo, estado)
            elif nombre == "restaurar_danado":
                datos = _restaurar_danado(page, rng, oraculo, estado)
            elif nombre == "excel":
                datos = _excel(page, rng, oraculo, tasas)
        except Exception as error:
            fallo_accion = escritorio._primera_linea(error)
            datos = {"entrada": {}, "aceptada": None, "mensaje": ""}
        duracion_ms = round((time.perf_counter() - inicio) * 1000)

        nota = None
        if datos is None:
            nota = "omitida: todavía no había un respaldo descargado"
            datos = {"entrada": {}, "aceptada": None, "mensaje": ""}
        problemas = []
        if fallo_accion:
            problemas.append(f"la acción no se pudo completar en pantalla: {fallo_accion}")
        else:
            if errores and nombre not in ("recargar", "avanzar_dia", "ingreso", "gasto", "restaurar", "restaurar_danado"):
                problemas.append(f"errores de JavaScript: {errores[0][:160]}")
            elif errores:
                errores = [e for e in errores if "Failed to fetch" not in e and "restaurar" not in e.lower()]
                if errores:
                    problemas.append(f"errores de JavaScript: {errores[0][:160]}")
            if respuestas_5xx:
                problemas.append(f"respuesta del servidor {respuestas_5xx[0]}")
            if nota is None:
                problemas += _verificar(nombre, datos, oraculo)
            if nombre in ("restaurar", "restaurar_danado", "avanzar_dia", "ingreso", "gasto"):
                try:
                    problemas += _resumen_asentado(page, oraculo)
                except Exception as error:
                    problemas.append(f"el resumen no se pudo leer: {escritorio._primera_linea(error)}")

        registro = {
            "episodio": numero, "semilla": semilla, "persona": usuario, "paso": paso,
            "fecha_simulada": hoy.isoformat(), "accion": nombre, "entrada": datos.get("entrada", {}),
            "aceptada": datos.get("aceptada"), "mensaje": datos.get("mensaje") or None,
            "saldo_base_c": oraculo.saldo_c(), "movimientos_base": len(oraculo.movimientos()),
            "invariantes_rotas": problemas, "nota": nota,
        }
        bit.paso(registro, {"episodio": numero, "paso": paso, "duracion_ms": duracion_ms,
                            "carga_1m": round(os.getloadavg()[0], 2)})
        if problemas:
            fallos.append((numero, paso, nombre, problemas))
            carpeta = bit.fallo(f"{numero:03d}-{paso:02d}-{semilla}", {**registro, "errores_navegador": errores,
                                                                       "respuestas_5xx": respuestas_5xx}, ruta_db)
            try:
                page.screenshot(path=os.path.join(carpeta, "pantalla.png"), full_page=True)
            except Exception:
                pass

    ctx.close()
    cliente.close()
    servidor.parar()
    return pasos


def correr(semilla, episodios, fuente, carpeta, dispositivo):
    from playwright.sync_api import sync_playwright

    shutil.rmtree(carpeta, ignore_errors=True)
    os.makedirs(os.path.join(carpeta, "logs"), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="auditoria_respaldo_ui_")
    shutil.copytree(fuente, tmp, dirs_exist_ok=True, ignore=IGNORAR_EN_COPIA)
    os.chdir(tmp)
    os.environ["JWT_SECRET_KEY"] = "k" * 48
    mods = escritorio._cargar_modulos(tmp)

    servidor = escritorio.Servidor(tmp, os.path.join(carpeta, "logs"))
    bit = Bitacora(carpeta)
    fallos, pasos_totales = [], 0
    try:
        with sync_playwright() as pw:
            navegador = pw.chromium.launch(args=["--no-sandbox"])
            try:
                for numero in range(1, episodios + 1):
                    pasos_totales += _episodio(semilla + numero - 1, numero, navegador, servidor, mods, bit, fallos, dispositivo)
            finally:
                navegador.close()
    finally:
        servidor.parar()
        bit.cerrar()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"Corrida respaldo {dispositivo} s{semilla} n{episodios}: {episodios} episodios, {pasos_totales} acciones, carpeta {carpeta}")
    if fallos:
        print(f"FALLO: {len(fallos)} acciones con invariantes rotas")
        for numero, paso, nombre, problemas in fallos[:20]:
            print(f"  episodio {numero} paso {paso} ({nombre}): {'; '.join(problemas)}")
        return 1
    print("OK: todas las invariantes se cumplieron")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Capas B y C de Respaldo y Excel (navegador).")
    parser.add_argument("--semilla", type=int, default=1)
    parser.add_argument("--episodios", type=int, default=100)
    parser.add_argument("--fuente", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    parser.add_argument("--carpeta", required=True)
    parser.add_argument("--dispositivo", choices=sorted(escritorio.DISPOSITIVOS), default="escritorio")
    args = parser.parse_args()
    sys.exit(correr(args.semilla, args.episodios, os.path.abspath(args.fuente), os.path.abspath(args.carpeta), args.dispositivo))
