"""tests/auditoria/escritorio_metas.py - Capas B y C de Metas en el navegador.

Mismo recorrido que escritorio.py (1280×800 o 390×844 táctil), con las acciones de Metas: crear una meta o un sobre,
depositar, retirar y eliminar desde sus tarjetas, y cambiar de día. Lo que pasó se juzga con las mismas reglas que la
capa API (tests/auditoria/metas.py): lo que la app acepta o rechaza en pantalla tiene que coincidir con lo que dice
la base, y el saldo guardado de cada meta tiene que cuadrar con sus movimientos.

Corre dentro del contenedor de Playwright: tests/auditoria/run.sh --seccion metas --capa escritorio|celular.
"""

import datetime
import json
import os
import random
import shutil
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import acciones  # noqa: E402
import escritorio  # noqa: E402
import metas  # noqa: E402
import personas  # noqa: E402
from bitacora import Bitacora  # noqa: E402
from oraculo import Oraculo  # noqa: E402

HOY_INICIAL = datetime.date(2026, 10, 1)
PASOS_MIN, PASOS_MAX = 5, 30
PESOS_UI = (
    ("crear_meta", 10), ("depositar", 25), ("retirar", 15), ("eliminar_meta", 8),
    ("ingreso", 12), ("recargar", 5), ("avanzar_dia", 8), ("dashboard", 6),
)
IGNORAR_EN_COPIA = escritorio.IGNORAR_EN_COPIA


def _quitar_avisos(page):
    """Los avisos duran 3.5 s: uno del paso anterior se confundiría con el de la acción que sigue."""
    page.evaluate("document.querySelectorAll('.toast').forEach(t => t.remove())")


def _ir_metas(page):
    escritorio._ir(page, "tab-apartados")
    _quitar_avisos(page)
    page.wait_for_selector("#contenedor-apartados", state="attached", timeout=15000)


def _instantanea(oraculo):
    return (oraculo.apartados(), oraculo.neto_por_apartado(), oraculo.movimientos())


def _ids_de_tarjetas(page):
    """Ids de las metas que muestra la pantalla, leídos de los botones de eliminar."""
    return [json.loads(b.get_attribute("data-on-click"))[0][1]
            for b in page.query_selector_all("#contenedor-apartados .btn-eliminar-apartado")]


def _esperar_efecto(page, oraculo, antes, segundos=6.0):
    """Espera a que la base cambie o a que la pantalla muestre un aviso de error (.toast-error).
    Devuelve (la base cambió, texto del aviso). Si hay aviso, se da un momento más por si el cambio llega a la par."""
    limite = time.perf_counter() + segundos
    while time.perf_counter() < limite:
        if _instantanea(oraculo) != antes:
            return True, ""
        avisos = page.locator(".toast-error")
        if avisos.count() > 0:
            texto = avisos.first.inner_text().strip()[:160]
            page.wait_for_timeout(500)
            return _instantanea(oraculo) != antes, texto
        page.wait_for_timeout(150)
    return _instantanea(oraculo) != antes, ""


def _crear_meta(page, rng, oraculo, hoy):
    nombre = f"Meta {rng.randint(1, 40)}"
    tipo = rng.choice(("meta", "meta", "libre"))
    pre = metas._estado(oraculo, hoy.isoformat())
    page.evaluate("document.querySelector('#tab-apartados [data-on-click*=\"abrirModalCrearApartado\"]').click()")
    page.wait_for_selector("#modal-crear-apartado:not(.hidden)")
    page.fill("#nombre-apartado", nombre)
    page.select_option("#tipo-apartado", value=tipo)
    if tipo == "meta":
        page.fill("#meta-monto", f"{round(rng.uniform(500, 80000), 2):.2f}")
    page.evaluate("document.getElementById('form-apartado').requestSubmit()")
    cambio, aviso = _esperar_efecto(page, oraculo, (pre["apartados"], pre["neto"], pre["mov"]))
    if page.is_visible("#modal-crear-apartado"):
        page.evaluate("document.querySelector('#modal-crear-apartado .modal-actions .btn-secondary').click()")
        page.wait_for_selector("#modal-crear-apartado.hidden", state="attached", timeout=15000)
    return {"entrada": {"nombre": nombre, "tipo": tipo}, "aceptada": cambio, "mensaje": aviso, "pre": pre,
            "accion": "crear_meta", "nombre": nombre}


def _movimiento(page, rng, oraculo, hoy, accion):
    ids = _ids_de_tarjetas(page)
    if not ids:
        return None
    objetivo = rng.choice(ids)
    pre = metas._estado(oraculo, hoy.isoformat())
    if accion == "depositar":
        tope = max(10.0, pre["saldo_hoy"] / 100 * 1.3)
    else:
        tope = max(10.0, next((a[3] for a in pre["apartados"] if a[0] == objetivo), 0) / 100 * 1.3)
    monto = round(rng.uniform(0.01, tope), 2)
    boton = f"#contenedor-apartados button[data-on-click='[[\"realizarMovimiento\",{objetivo},\"{accion}\"]]']"
    antes = (pre["apartados"], pre["neto"], pre["mov"])
    # La lista de tarjetas se repinta sola tras la acción anterior: se espera a que la pantalla se asiente y, si el
    # monto escrito se borró antes del clic, se escribe otra vez (una persona esperaría a ver la pantalla quieta)
    for intento in range(3):
        page.wait_for_load_state("networkidle")
        _quitar_avisos(page)
        page.fill(f"#monto-acc-{objetivo}", f"{monto:.2f}")
        page.locator(boton).click(timeout=10000)
        cambio, aviso = _esperar_efecto(page, oraculo, antes)
        if cambio or not aviso.startswith("Ingresa un monto"):
            break
    return {"entrada": {"meta": objetivo, "monto": monto, "accion": accion}, "aceptada": cambio, "mensaje": aviso,
            "pre": pre, "accion": accion, "objetivo": objetivo, "monto_c": acciones._a_centavos(monto)}


def _eliminar_meta(page, rng, oraculo, hoy):
    ids = _ids_de_tarjetas(page)
    if not ids:
        return None
    objetivo = rng.choice(ids)
    pre = metas._estado(oraculo, hoy.isoformat())
    boton = f"#contenedor-apartados button[data-on-click='[[\"eliminarApartado\",{objetivo}]]']"
    page.locator(boton).click(timeout=10000)
    page.wait_for_selector("#modal-confirmar:not(.hidden)", timeout=10000)
    page.evaluate("document.getElementById('btn-confirmar-aceptar').click()")
    page.wait_for_selector("#modal-confirmar.hidden", state="attached", timeout=15000)
    cambio, aviso = _esperar_efecto(page, oraculo, (pre["apartados"], pre["neto"], pre["mov"]))
    return {"entrada": {"id": objetivo}, "aceptada": cambio, "mensaje": aviso, "pre": pre,
            "accion": "eliminar_meta", "objetivo": objetivo}


def _ingreso(oraculo, cliente_api, hoy, rng):
    """Dinero en la cuenta para poder depositar: se registra por la API (no es lo que se audita aquí)."""
    cuerpo = {"fecha": hoy.isoformat(), "monto": round(rng.uniform(500, 15000), 2), "tipo": "ingreso",
              "categoria_id": 12, "descripcion": "Ingreso de prueba"}
    cliente_api.post("/api/transacciones", json=cuerpo)
    return {"entrada": cuerpo, "aceptada": None, "mensaje": ""}


def _verificar(nombre, datos, oraculo, hoy):
    """Reutiliza las reglas de la capa API: la pantalla se juzga como si fuera la respuesta de la API."""
    if datos.get("pre") is None or nombre not in ("crear_meta", "depositar", "retirar", "eliminar_meta"):
        return []
    res = {"respuesta": types.SimpleNamespace(status_code=200 if datos["aceptada"] else 400), "pre": datos["pre"],
           "accion": nombre, "objetivo": datos.get("objetivo"), "monto_c": datos.get("monto_c"),
           "nombre": datos.get("nombre")}
    saldo_api = oraculo.saldo_c() / 100  # el resumen se compara aparte, en pantalla
    return metas.verificar(nombre, res, oraculo, saldo_api)


def _episodio(semilla, numero, navegador, servidor, mods, bit, fallos, dispositivo):
    rng = random.Random(semilla)
    usuario = f"aud{semilla}"
    hoy = HOY_INICIAL
    os.environ["FINANZAS_HOY"] = f"{hoy.isoformat()}T10:00:00"  # las reglas de metas.py leen el día simulado de aquí
    servidor.arrancar(hoy)
    import httpx
    cliente = httpx.Client(base_url=servidor.base, timeout=30)
    cabecera, ruta_db = personas.crear_laura(cliente, usuario, mods)
    cliente.headers.update(cabecera)
    oraculo = Oraculo(ruta_db)

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
            elif nombre == "ingreso":
                datos = _ingreso(oraculo, cliente, hoy, rng)
                # El ingreso se registró por la API, sin pasar por esta pantalla: se recarga como si lo hubiera
                # hecho la persona desde otra pestaña, para que la pantalla vea lo que ya está en la base
                page.reload()
                page.wait_for_selector("#vista-app:not(.hidden)", timeout=20000)
            elif nombre == "crear_meta":
                _ir_metas(page)
                datos = _crear_meta(page, rng, oraculo, hoy)
            elif nombre in ("depositar", "retirar"):
                _ir_metas(page)
                datos = _movimiento(page, rng, oraculo, hoy, nombre)
            elif nombre == "eliminar_meta":
                _ir_metas(page)
                datos = _eliminar_meta(page, rng, oraculo, hoy)
        except Exception as error:
            fallo_accion = escritorio._primera_linea(error)
            datos = {"entrada": {}, "aceptada": None, "mensaje": ""}
        duracion_ms = round((time.perf_counter() - inicio) * 1000)

        nota = None
        if datos is None:
            nota = "omitida: no había metas que tocar"
            datos = {"entrada": {}, "aceptada": None, "mensaje": ""}
        problemas = []
        if fallo_accion:
            problemas.append(f"la acción no se pudo completar en pantalla: {fallo_accion}")
        else:
            if errores and nombre not in ("recargar", "avanzar_dia", "ingreso"):
                problemas.append(f"errores de JavaScript: {errores[0][:160]}")
            elif errores:
                errores = [e for e in errores if "Failed to fetch" not in e]
                if errores:
                    problemas.append(f"errores de JavaScript: {errores[0][:160]}")
            if respuestas_5xx:
                problemas.append(f"respuesta del servidor {respuestas_5xx[0]}")
            if nota is None:
                problemas += _verificar(nombre, datos, oraculo, hoy)
            if nombre in ("dashboard", "avanzar_dia", "crear_meta", "depositar", "retirar", "eliminar_meta", "ingreso"):
                try:
                    problemas += escritorio._verificar_resumen(page, oraculo)
                except Exception as error:
                    problemas.append(f"el resumen no se pudo leer: {escritorio._primera_linea(error)}")

        registro = {
            "episodio": numero, "semilla": semilla, "persona": usuario, "paso": paso,
            "fecha_simulada": hoy.isoformat(), "accion": nombre, "entrada": datos.get("entrada", {}),
            "aceptada": datos.get("aceptada"), "mensaje": datos.get("mensaje") or None,
            "saldo_base_c": oraculo.saldo_c(), "metas_base": len(oraculo.apartados()),
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
    tmp = tempfile.mkdtemp(prefix="auditoria_metas_ui_")
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

    print(f"Corrida metas {dispositivo} s{semilla} n{episodios}: {episodios} episodios, {pasos_totales} acciones, carpeta {carpeta}")
    if fallos:
        print(f"FALLO: {len(fallos)} acciones con invariantes rotas")
        for numero, paso, nombre, problemas in fallos[:20]:
            print(f"  episodio {numero} paso {paso} ({nombre}): {'; '.join(problemas)}")
        return 1
    print("OK: todas las invariantes se cumplieron")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Capas B y C de Metas (navegador).")
    parser.add_argument("--semilla", type=int, default=1)
    parser.add_argument("--episodios", type=int, default=100)
    parser.add_argument("--fuente", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    parser.add_argument("--carpeta", required=True)
    parser.add_argument("--dispositivo", choices=sorted(escritorio.DISPOSITIVOS), default="escritorio")
    args = parser.parse_args()
    sys.exit(correr(args.semilla, args.episodios, os.path.abspath(args.fuente), os.path.abspath(args.carpeta), args.dispositivo))
