"""tests/auditoria/escritorio_fijos.py - Capas B y C de Fijos: ingresos y gastos fijos, y compras a meses en el navegador.

Mismo recorrido que escritorio.py (1280×800 o 390×844 táctil), pero con las acciones de Fijos: dar de alta un
ingreso fijo, un gasto fijo o una compra a meses con el botón «+», saltar un cobro, eliminar un fijo, y cambiar de día.
Después de cada acción compara lo que se ve con la base, con las mismas reglas que tests/auditoria/fijos.py.

Corre dentro del contenedor de Playwright: tests/auditoria/run.sh --seccion fijos --capa escritorio|celular.
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
from bitacora import Bitacora  # noqa: E402
from oraculo import Oraculo  # noqa: E402

HOY_INICIAL = datetime.date(2026, 10, 1)
PASOS_MIN, PASOS_MAX = 5, 30
PESOS_UI = (
    ("alta_ingreso_fijo", 10), ("alta_gasto_fijo", 12), ("alta_msi", 8), ("saltar", 8),
    ("eliminar", 6), ("recargar", 5), ("avanzar_dia", 6), ("dashboard", 6),
)
FRECUENCIAS = ("semanal", "quincenal", "mensual")
MESES = ("3", "6", "9", "12", "18", "24")
CATEGORIA_INGRESO = "12"
CATEGORIAS_GASTO = ("1", "2", "4", "7", "8")
IGNORAR_EN_COPIA = escritorio.IGNORAR_EN_COPIA


def _ir_fijos(page):
    """Abre Movimientos en la subpestaña de Fijos y compras a meses."""
    escritorio._ir(page, "tab-movimientos")
    page.evaluate("document.getElementById('btn-subtab-fijos').click()")
    page.wait_for_selector("#subtab-content-fijos.active", state="attached", timeout=15000)


def _ingresos_activos(oraculo):
    return sum(1 for r in oraculo.recurrentes() if r[1] == "ingreso" and r[7] == 1)


def _alta(page, rng, oraculo, modo, hoy):
    """Abre el «+» de fijos, llena el formulario y lo envía. Devuelve lo que pasó."""
    fecha = (hoy + datetime.timedelta(days=rng.randint(-20, 20))).isoformat()
    descripcion = f"Fijo {rng.randint(1, 999)}"
    ingresos_pre = _ingresos_activos(oraculo)
    antes = len(oraculo.recurrentes())
    page.evaluate("document.getElementById('btn-nuevo-fijo').click()")
    page.wait_for_selector("#modal-nuevo-fijo:not(.hidden)")
    page.evaluate(f"document.querySelector('input[name=\"rec-modo\"][value=\"{modo}\"]').click()")
    page.fill("#rec-desc", descripcion)
    page.fill("#rec-fecha", fecha)
    if modo == "msi":
        total = round(rng.uniform(1000, 60000), 2)
        page.fill("#rec-total", f"{total:.2f}")
        page.select_option("#rec-meses", value=rng.choice(MESES))
        entrada = {"modo": modo, "descripcion": descripcion, "total": total, "fecha": fecha}
    else:
        monto = round(rng.uniform(50, 5000) if modo == "gasto" else rng.uniform(1000, 20000), 2)
        page.fill("#rec-monto", f"{monto:.2f}")
        page.select_option("#rec-frecuencia", value=rng.choice(FRECUENCIAS))
        page.select_option("#rec-categoria", value=CATEGORIA_INGRESO if modo == "ingreso" else rng.choice(CATEGORIAS_GASTO))
        entrada = {"modo": modo, "descripcion": descripcion, "monto": monto, "fecha": fecha}
    page.evaluate("document.getElementById('form-recurrente').requestSubmit()")
    # Una compra que supera lo libre pide confirmación: la persona decide
    limite = time.perf_counter() + 15
    while time.perf_counter() < limite:
        if page.is_visible("#modal-confirmar"):
            aceptar = rng.random() < 0.5
            entrada["confirmar"] = aceptar
            page.evaluate(f"document.querySelector('{'#btn-confirmar-aceptar' if aceptar else '#btn-confirmar-cancelar'}').click()")
            page.wait_for_selector("#modal-confirmar.hidden", state="attached", timeout=15000)
        if not page.is_visible("#modal-nuevo-fijo") or page.inner_text("#rec-mensaje").strip():
            break
        page.wait_for_timeout(150)
    aceptado = not page.is_visible("#modal-nuevo-fijo")
    mensaje = "" if aceptado else page.inner_text("#rec-mensaje").strip()
    if not aceptado:
        page.evaluate("document.querySelector('#modal-nuevo-fijo .modal-actions .btn-secondary').click()")
        page.wait_for_selector("#modal-nuevo-fijo.hidden", state="attached")
    return {"entrada": entrada, "aceptado": aceptado, "mensaje": mensaje, "modo": modo,
            "ingresos_pre": ingresos_pre, "antes": antes}


def _accion_en_fila(page, rng, oraculo, accion):
    """Pulsa «Saltar» o «Eliminar» de un fijo visible, confirma y espera a que la base lo refleje."""
    _ir_fijos(page)
    botones = page.query_selector_all(f"#subtab-content-fijos button[data-accion='{accion}']")
    if not botones:
        return None
    indice = rng.randrange(len(botones))
    id_rec = int(botones[indice].get_attribute("data-id"))
    if accion == "saltar" and any(r[0] == id_rec and r[4] is not None for r in oraculo.recurrentes()):
        return None  # las compras a meses se saltan desde su propio cuadro: aquí solo fijos simples
    proxima_antes = next(r[8] for r in oraculo.recurrentes() if r[0] == id_rec)
    # La lista se repinta a veces: el botón se busca por el id del fijo al momento de tocarlo
    page.locator(f"#subtab-content-fijos button[data-accion='{accion}'][data-id='{id_rec}']").click(timeout=10000)
    page.wait_for_selector("#modal-confirmar:not(.hidden)", timeout=10000)
    page.evaluate("document.getElementById('btn-confirmar-aceptar').click()")
    page.wait_for_selector("#modal-confirmar.hidden", state="attached", timeout=15000)
    # La app puede rechazar: el aviso aparece en pantalla y la base no cambia. Se espera a ver el efecto o el aviso.
    limite = time.perf_counter() + 6
    mensaje = ""
    while time.perf_counter() < limite:
        existe = any(r[0] == id_rec for r in oraculo.recurrentes())
        if accion == "eliminar" and not existe:
            break
        if accion == "saltar" and existe and next(r[8] for r in oraculo.recurrentes() if r[0] == id_rec) != proxima_antes:
            break
        if page.locator("text=No se pudo").count() > 0:
            mensaje = page.locator("text=No se pudo").first.inner_text().strip()
            break
        page.wait_for_timeout(150)
    existe = any(r[0] == id_rec for r in oraculo.recurrentes())
    cambio = (not existe) if accion == "eliminar" else (existe and next(r[8] for r in oraculo.recurrentes() if r[0] == id_rec) != proxima_antes)
    return {"entrada": {"id": id_rec, "accion": accion}, "aceptado": bool(cambio), "mensaje": mensaje,
            "objetivo": id_rec if cambio else None, "proxima_antes": proxima_antes}


def _verificar(nombre, datos, oraculo, pre, hoy):
    problemas = []
    dup = [f for f in oraculo.filas_ligadas() if f[2] > 1]
    if dup:
        problemas.append(f"un fijo se materializó dos veces en la misma fecha: {dup[0][:2]}")
    futuras = oraculo.filas_ligadas_despues(hoy)
    if futuras:
        problemas.append(f"hay movimientos de fijos con fecha futura: {futuras[0]}")
    for r in oraculo.recurrentes():
        if r[4] is not None and oraculo.cuotas_de(r[0]) > r[5]:
            problemas.append(f"compra {r[0]}: hay más cuotas ligadas que cuotas contadas")

    if nombre in ("alta_ingreso_fijo", "alta_gasto_fijo", "alta_msi"):
        datos_modo = datos["modo"]
        if datos["aceptado"]:
            if len(oraculo.recurrentes()) != pre["antes"] + 1:
                problemas.append("el alta aceptada no creó exactamente un fijo")
            if datos_modo == "gasto" and datos["ingresos_pre"] == 0:
                problemas.append("se aceptó un gasto fijo sin ingreso fijo activo")
        else:
            if len(oraculo.recurrentes()) != pre["antes"]:
                problemas.append("el formulario rechazó el alta y la base cambió")
            if datos_modo == "gasto" and datos["ingresos_pre"] == 0 and "ingreso fijo" not in datos["mensaje"]:
                problemas.append("sin ingreso fijo el gasto se rechazó sin decir por qué")
    if nombre == "eliminar" and datos and datos.get("objetivo") is not None:
        if any(r[0] == datos["objetivo"] for r in oraculo.recurrentes()):
            problemas.append("el fijo eliminado sigue en la base")
    if nombre == "saltar" and datos and datos.get("objetivo") is not None:
        nueva = next((r[8] for r in oraculo.recurrentes() if r[0] == datos["objetivo"]), None)
        if nueva is not None and nueva == datos["proxima_antes"]:
            problemas.append("saltar no avanzó la próxima fecha")
    return problemas


def _episodio(semilla, numero, navegador, servidor, mods, bit, fallos, dispositivo):
    rng = random.Random(semilla)
    usuario = f"aud{semilla}"
    hoy = HOY_INICIAL
    servidor.arrancar(hoy)
    import httpx
    with httpx.Client(base_url=servidor.base, timeout=30) as cliente:
        cabecera, ruta_db = personas.crear_laura(cliente, usuario, mods)
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
        pre = {"antes": len(oraculo.recurrentes())}
        inicio = time.perf_counter()
        datos = None
        try:
            if nombre == "avanzar_dia":
                hoy = hoy + datetime.timedelta(days=rng.choice((1, 1, 2, 7, 15)))
                servidor.parar()
                servidor.arrancar(hoy)
                page.goto(servidor.base + "/")
                if not page.query_selector("#vista-app:not(.hidden)"):
                    escritorio._login(page, servidor.base, usuario)
                datos = {"entrada": {"hoy": hoy.isoformat()}, "aceptado": None, "mensaje": ""}
            elif nombre == "recargar":
                page.reload()
                page.wait_for_selector("#vista-app:not(.hidden)", timeout=20000)
                datos = {"entrada": {}, "aceptado": None, "mensaje": ""}
            elif nombre == "dashboard":
                escritorio._ir(page, "tab-dashboard")
                datos = {"entrada": {}, "aceptado": None, "mensaje": ""}
            elif nombre in ("alta_ingreso_fijo", "alta_gasto_fijo", "alta_msi"):
                modo = {"alta_ingreso_fijo": "ingreso", "alta_gasto_fijo": "gasto", "alta_msi": "msi"}[nombre]
                _ir_fijos(page)
                datos = _alta(page, rng, oraculo, modo, hoy)
            else:
                datos = _accion_en_fila(page, rng, oraculo, nombre)
        except Exception as error:
            fallo_accion = escritorio._primera_linea(error)
            datos = {"entrada": {}, "aceptado": None, "mensaje": ""}
        duracion_ms = round((time.perf_counter() - inicio) * 1000)

        nota = None
        if datos is None:
            nota = "omitida: no había fijos que tocar"
            datos = {"entrada": {}, "aceptado": None, "mensaje": ""}
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
            if nota is None:
                problemas += _verificar(nombre, datos, oraculo, pre, hoy.isoformat())
            if nombre in ("dashboard", "avanzar_dia", "alta_ingreso_fijo", "alta_gasto_fijo", "alta_msi", "eliminar", "saltar"):
                try:
                    problemas += escritorio._verificar_resumen(page, oraculo)
                except Exception as error:
                    problemas.append(f"el resumen no se pudo leer: {escritorio._primera_linea(error)}")

        registro = {
            "episodio": numero, "semilla": semilla, "persona": usuario, "paso": paso,
            "fecha_simulada": hoy.isoformat(), "accion": nombre, "entrada": datos["entrada"],
            "aceptado": datos.get("aceptado"), "mensaje": datos.get("mensaje") or None,
            "saldo_base_c": oraculo.saldo_c(), "recurrentes_base": len(oraculo.recurrentes()),
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
    servidor.parar()
    return pasos


def correr(semilla, episodios, fuente, carpeta, dispositivo):
    from playwright.sync_api import sync_playwright

    shutil.rmtree(carpeta, ignore_errors=True)
    os.makedirs(os.path.join(carpeta, "logs"), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="auditoria_fijos_ui_")
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

    print(f"Corrida fijos {dispositivo} s{semilla} n{episodios}: {episodios} episodios, {pasos_totales} acciones, carpeta {carpeta}")
    if fallos:
        print(f"FALLO: {len(fallos)} acciones con invariantes rotas")
        for numero, paso, nombre, problemas in fallos[:20]:
            print(f"  episodio {numero} paso {paso} ({nombre}): {'; '.join(problemas)}")
        return 1
    print("OK: todas las invariantes se cumplieron")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Capas B y C de Fijos (navegador).")
    parser.add_argument("--semilla", type=int, default=1)
    parser.add_argument("--episodios", type=int, default=100)
    parser.add_argument("--fuente", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    parser.add_argument("--carpeta", required=True)
    parser.add_argument("--dispositivo", choices=sorted(escritorio.DISPOSITIVOS), default="escritorio")
    args = parser.parse_args()
    sys.exit(correr(args.semilla, args.episodios, os.path.abspath(args.fuente), os.path.abspath(args.carpeta), args.dispositivo))
