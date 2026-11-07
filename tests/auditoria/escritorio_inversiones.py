"""tests/auditoria/escritorio_inversiones.py - Capas B y C de Inversiones en el navegador.

Mismo recorrido que escritorio_metas.py (1280×800 o 390×844 táctil), con las acciones del portafolio: registrar una
posición, editarla (cantidad y precio de compra) y eliminarla desde la tabla. Las cuentas con rendimiento no tienen
pantalla en la app, así que se quedan en la capa API. Después de cada acción se compara la tabla y las tarjetas de la
pantalla con la base y con un cálculo propio de la conversión de monedas.

Corre dentro del contenedor de Playwright: tests/auditoria/run.sh --seccion inversiones --capa escritorio|celular.
"""

import datetime
import json
import os
import random
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import escritorio  # noqa: E402
import personas  # noqa: E402
import portafolio  # noqa: E402
from bitacora import Bitacora  # noqa: E402
from oraculo import Oraculo  # noqa: E402

HOY_INICIAL = datetime.date(2026, 10, 1)
PASOS_MIN, PASOS_MAX = 5, 30
PESOS_UI = (
    ("crear_posicion", 30), ("editar_posicion", 20), ("eliminar_posicion", 12), ("recargar", 6),
    ("avanzar_dia", 6), ("dashboard", 8),
)
IGNORAR_EN_COPIA = escritorio.IGNORAR_EN_COPIA


def _quitar_avisos(page):
    page.evaluate("document.querySelectorAll('.toast').forEach(t => t.remove())")


def _ir_inversiones(page):
    escritorio._ir(page, "tab-inversiones")
    _quitar_avisos(page)
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#tabla-inversiones", state="attached", timeout=15000)


def _esperar_efecto(page, oraculo, antes, segundos=6.0):
    """Espera a que cambien las posiciones o a que aparezca un error (aviso del formulario o .toast-error)."""
    limite = time.perf_counter() + segundos
    while time.perf_counter() < limite:
        if oraculo.posiciones() != antes:
            return True, ""
        texto = ""
        if page.locator(".toast-error").count() > 0:
            texto = page.locator(".toast-error").first.inner_text().strip()[:160]
        elif page.is_visible("#inv-mensaje") and page.inner_text("#inv-mensaje").strip():
            texto = page.inner_text("#inv-mensaje").strip()[:160]
        if texto:
            page.wait_for_timeout(500)
            return oraculo.posiciones() != antes, texto
        page.wait_for_timeout(150)
    return oraculo.posiciones() != antes, ""


def _ids_de_tabla(page):
    return [json.loads(b.get_attribute("data-on-click"))[0][1] for b in page.query_selector_all("#tabla-inversiones .btn-eliminar")]


def _crear_posicion(page, rng, oraculo):
    ticker = rng.choice(portafolio.TICKERS)
    moneda = portafolio.MONEDA_NATURAL.get(ticker, "USD") if rng.random() < 0.8 else rng.choice(("USD", "MXN", "EUR"))
    cantidad, promedio = round(rng.uniform(0.01, 500), 4), round(rng.uniform(1, 400), 2)
    actual = round(rng.uniform(1, 400), 2)
    pre = oraculo.posiciones()
    page.evaluate("document.getElementById('btn-nueva-posicion').click()")
    page.wait_for_selector("#modal-nueva-posicion:not(.hidden)")
    page.fill("#inv-ticker", ticker)
    page.select_option("#inv-moneda", value=moneda)
    page.fill("#inv-cantidad", f"{cantidad}")
    page.fill("#inv-precio-promedio", f"{promedio:.2f}")
    page.fill("#inv-precio-actual", f"{actual:.2f}")
    page.evaluate("document.getElementById('form-inversion').requestSubmit()")
    cambio, aviso = _esperar_efecto(page, oraculo, pre)
    if page.is_visible("#modal-nueva-posicion"):
        page.evaluate("document.querySelector('#modal-nueva-posicion .modal-actions .btn-secondary').click()")
        page.wait_for_selector("#modal-nueva-posicion.hidden", state="attached", timeout=15000)
    existia = any(p[1] == ticker for p in pre)
    return {"entrada": {"ticker": ticker, "moneda": moneda, "cantidad": cantidad, "promedio": promedio},
            "aceptada": cambio, "mensaje": aviso, "pre": pre, "existia": existia, "ticker": ticker}


def _editar_posicion(page, rng, oraculo):
    ids = _ids_de_tabla(page)
    if not ids:
        return None
    objetivo = rng.choice(ids)
    cantidad, promedio = round(rng.uniform(0.01, 500), 4), round(rng.uniform(1, 400), 2)
    pre = oraculo.posiciones()
    page.locator(f"#tabla-inversiones button[data-on-click*='abrirModalEditarInversionPorId'][data-on-click*='{objetivo}]']").click(timeout=10000)
    page.wait_for_selector("#modal-editar-inversion:not(.hidden)")
    page.fill("#modal-edit-cantidad", f"{cantidad}")
    page.fill("#modal-edit-precio-promedio", f"{promedio:.2f}")
    page.evaluate("document.getElementById('form-editar-inversion').requestSubmit()")
    cambio, aviso = _esperar_efecto(page, oraculo, pre)
    page.wait_for_load_state("networkidle")
    if page.is_visible("#modal-editar-inversion"):
        page.evaluate("document.querySelector('#modal-editar-inversion .modal-actions .btn-secondary').click()")
        page.wait_for_selector("#modal-editar-inversion.hidden", state="attached", timeout=15000)
    return {"entrada": {"id": objetivo, "cantidad": cantidad, "promedio": promedio}, "aceptada": cambio, "mensaje": aviso,
            "pre": pre, "objetivo": objetivo, "cantidad": cantidad, "promedio": promedio}


def _eliminar_posicion(page, rng, oraculo):
    ids = _ids_de_tabla(page)
    if not ids:
        return None
    objetivo = rng.choice(ids)
    pre = oraculo.posiciones()
    page.locator(f"#tabla-inversiones button[data-on-click*='eliminarInversion'][data-on-click*='{objetivo}]']").click(timeout=10000)
    page.wait_for_selector("#modal-confirmar:not(.hidden)", timeout=10000)
    page.evaluate("document.getElementById('btn-confirmar-aceptar').click()")
    page.wait_for_selector("#modal-confirmar.hidden", state="attached", timeout=15000)
    cambio, aviso = _esperar_efecto(page, oraculo, pre)
    page.wait_for_load_state("networkidle")
    return {"entrada": {"id": objetivo}, "aceptada": cambio, "mensaje": aviso, "pre": pre, "objetivo": objetivo}


def _verificar_accion(nombre, datos, oraculo):
    problemas = []
    pre, post = datos["pre"], oraculo.posiciones()
    aceptada = datos["aceptada"]
    if nombre == "crear_posicion":
        if aceptada and datos["existia"]:
            problemas.append("se aceptó una posición con un ticker que ya existía")
        if not aceptada and not datos["existia"]:
            problemas.append(f"se rechazó una posición con ticker nuevo y datos válidos: {datos['mensaje']}")
        if aceptada and len(post) != len(pre) + 1:
            problemas.append("registrar una posición no añadió exactamente una")
    elif nombre == "editar_posicion" and aceptada:
        fila = next((p for p in post if p[0] == datos["objetivo"]), None)
        if fila is None or abs(fila[2] - datos["cantidad"]) > 1e-9 or abs(fila[3] - datos["promedio"]) > 1e-9:
            problemas.append("la edición de la posición no quedó guardada tal cual")
    elif nombre == "editar_posicion" and not aceptada:
        problemas.append(f"se rechazó una edición válida: {datos['mensaje']}")
    elif nombre == "eliminar_posicion":
        if not aceptada or any(p[0] == datos["objetivo"] for p in post):
            problemas.append("la posición eliminada sigue en la base")
    return problemas


def _verificar_pantalla(page, oraculo, tasas):
    """Tabla y tarjetas del portafolio contra la base, con la conversión calculada aparte."""
    _ir_inversiones(page)
    page.wait_for_load_state("networkidle")
    posiciones = oraculo.posiciones()
    limite = time.perf_counter() + 5.0  # la tabla se repinta un momento después de que la base cambia
    while True:
        filas = page.query_selector_all("#tabla-inversiones tr")
        celdas = [[c.inner_text().strip() for c in f.query_selector_all("td")] for f in filas]
        celdas = [c for c in celdas if len(c) >= 9]
        if len(celdas) == len(posiciones) or time.perf_counter() > limite:
            break
        page.wait_for_timeout(200)
    problemas = []
    if len(celdas) != len(posiciones):
        return [f"la tabla muestra {len(celdas)} posiciones y la base tiene {len(posiciones)}"]
    por_ticker = {c[0]: c for c in celdas}
    total = invertido = 0.0
    for _id, ticker, cantidad, promedio, actual, moneda in posiciones:
        moneda = (moneda or "USD").upper()
        c = por_ticker.get(ticker)
        if c is None:
            problemas.append(f"{ticker} está en la base y no en la tabla")
            continue
        if c[1] != moneda or abs(float(c[2]) - cantidad) > 1e-9:
            problemas.append(f"{ticker}: la tabla dice {c[2]} {c[1]} y la base {cantidad} {moneda}")
        if abs(escritorio._monto_visto(c[3]) - promedio) > 0.006 or abs(escritorio._monto_visto(c[4]) - actual) > 0.006:
            problemas.append(f"{ticker}: precios de la tabla ({c[3]} / {c[4]}) distintos de la base ({promedio} / {actual})")
        total += round(portafolio._convertir(round(cantidad * actual, 2), moneda, "MXN", tasas["MXN"], tasas["EUR"]), 2)
        invertido += round(portafolio._convertir(round(cantidad * promedio, 2), moneda, "MXN", tasas["MXN"], tasas["EUR"]), 2)
    tolerancia = 0.02 * max(1, len(posiciones)) + 0.01
    visto = escritorio._monto_visto(page.inner_text("#lbl-inv-valor"))
    if abs(visto - total) > tolerancia:
        problemas.append(f"«Valor de mercado» muestra {visto} y el cálculo da {total:.2f}")
    visto = escritorio._monto_visto(page.inner_text("#lbl-inv-invertido"))
    if abs(visto - invertido) > tolerancia:
        problemas.append(f"«Capital invertido» muestra {visto} y el cálculo da {invertido:.2f}")
    return problemas


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

    ctx = navegador.new_context(**escritorio.DISPOSITIVOS[dispositivo])
    page = ctx.new_page()
    errores, respuestas_5xx = [], []
    page.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
    page.on("console", lambda m: errores.append(f"console: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.on("response", lambda r: respuestas_5xx.append(f"{r.status} {r.url.split('?')[0]}") if r.status >= 500 else None)
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
            elif nombre == "crear_posicion":
                _ir_inversiones(page)
                datos = _crear_posicion(page, rng, oraculo)
            elif nombre == "editar_posicion":
                _ir_inversiones(page)
                datos = _editar_posicion(page, rng, oraculo)
            elif nombre == "eliminar_posicion":
                _ir_inversiones(page)
                datos = _eliminar_posicion(page, rng, oraculo)
        except Exception as error:
            fallo_accion = escritorio._primera_linea(error)
            datos = {"entrada": {}, "aceptada": None, "mensaje": ""}
        duracion_ms = round((time.perf_counter() - inicio) * 1000)

        nota = None
        if datos is None:
            nota = "omitida: no había posiciones que tocar"
            datos = {"entrada": {}, "aceptada": None, "mensaje": ""}
        problemas = []
        if fallo_accion:
            problemas.append(f"la acción no se pudo completar en pantalla: {fallo_accion}")
        else:
            if errores and nombre not in ("recargar", "avanzar_dia"):
                problemas.append(f"errores de JavaScript: {errores[0][:160]}")
            elif errores:
                errores = [e for e in errores if "Failed to fetch" not in e]
                if errores:
                    problemas.append(f"errores de JavaScript: {errores[0][:160]}")
            if respuestas_5xx:
                problemas.append(f"respuesta del servidor {respuestas_5xx[0]}")
            if nota is None and nombre in ("crear_posicion", "editar_posicion", "eliminar_posicion"):
                problemas += _verificar_accion(nombre, datos, oraculo)
            try:
                problemas += _verificar_pantalla(page, oraculo, tasas)
            except Exception as error:
                problemas.append(f"la pantalla de inversiones no se pudo leer: {escritorio._primera_linea(error)}")

        registro = {
            "episodio": numero, "semilla": semilla, "persona": usuario, "paso": paso,
            "fecha_simulada": hoy.isoformat(), "accion": nombre, "entrada": datos.get("entrada", {}),
            "aceptada": datos.get("aceptada"), "mensaje": datos.get("mensaje") or None,
            "saldo_base_c": oraculo.saldo_c(), "posiciones_base": len(oraculo.posiciones()),
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
    tmp = tempfile.mkdtemp(prefix="auditoria_inversiones_ui_")
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

    print(f"Corrida inversiones {dispositivo} s{semilla} n{episodios}: {episodios} episodios, {pasos_totales} acciones, carpeta {carpeta}")
    if fallos:
        print(f"FALLO: {len(fallos)} acciones con invariantes rotas")
        for numero, paso, nombre, problemas in fallos[:20]:
            print(f"  episodio {numero} paso {paso} ({nombre}): {'; '.join(problemas)}")
        return 1
    print("OK: todas las invariantes se cumplieron")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Capas B y C de Inversiones (navegador).")
    parser.add_argument("--semilla", type=int, default=1)
    parser.add_argument("--episodios", type=int, default=100)
    parser.add_argument("--fuente", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    parser.add_argument("--carpeta", required=True)
    parser.add_argument("--dispositivo", choices=sorted(escritorio.DISPOSITIVOS), default="escritorio")
    args = parser.parse_args()
    sys.exit(correr(args.semilla, args.episodios, os.path.abspath(args.fuente), os.path.abspath(args.carpeta), args.dispositivo))
