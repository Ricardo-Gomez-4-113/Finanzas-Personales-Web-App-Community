"""tests/auditoria/escritorio_presupuesto.py - Capas B y C de Presupuesto en el navegador.

Mismo recorrido que escritorio.py (1280×800 o 390×844 táctil), con las acciones de Presupuesto: cambiar el ingreso
base y la frecuencia, agregar, editar y eliminar tarjetas en cada pago, y exportar a fijos (dos veces, para comprobar
que no se duplica). Después de cada acción compara lo que se ve con la base, con las mismas reglas que
tests/auditoria/presupuesto.py.

Corre dentro del contenedor de Playwright: tests/auditoria/run.sh --seccion presupuesto --capa escritorio|celular.
"""

import datetime
import os
import random
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import escritorio  # noqa: E402
import personas  # noqa: E402
import presupuesto  # noqa: E402
from bitacora import Bitacora  # noqa: E402
from oraculo import Oraculo  # noqa: E402

HOY_INICIAL = datetime.date(2026, 10, 1)
PASOS_MIN, PASOS_MAX = 5, 30
PESOS_UI = (
    ("config", 8), ("crear_tarjeta", 14), ("editar_tarjeta", 10), ("eliminar_tarjeta", 6),
    ("exportar", 14), ("recargar", 5), ("avanzar_dia", 8), ("dashboard", 6),
)
FRECUENCIAS = ("semanal", "quincenal", "mensual")
PAGOS_ACTIVOS = presupuesto.PAGOS_ACTIVOS
IGNORAR_EN_COPIA = escritorio.IGNORAR_EN_COPIA


def _ir_presupuesto(page):
    escritorio._ir(page, "tab-presupuesto")
    page.wait_for_selector("#pres-ingreso-base", state="attached", timeout=15000)


def _cerrar_si_abierto(page, modal):
    if page.is_visible(f"#{modal}"):
        page.evaluate(f"document.querySelector('#{modal} .modal-actions .btn-secondary').click()")
        page.wait_for_selector(f"#{modal}.hidden", state="attached", timeout=15000)


def _config(page, rng, oraculo):
    base = 0 if rng.random() < 0.1 else round(rng.uniform(3000, 30000), 2)
    frecuencia = rng.choice(FRECUENCIAS)
    _ir_presupuesto(page)
    page.fill("#pres-ingreso-base", f"{base:.2f}")
    page.select_option("#pres-frecuencia", value=frecuencia)
    page.evaluate("document.getElementById('form-presupuesto-config').requestSubmit()")
    limite = time.perf_counter() + 6
    while time.perf_counter() < limite:
        cfg = oraculo.config_presupuesto()
        if cfg and cfg[0] == round(base * 100) and cfg[1] == frecuencia:
            break
        page.wait_for_timeout(150)
    cfg = oraculo.config_presupuesto()
    aceptado = bool(cfg and cfg[0] == round(base * 100) and cfg[1] == frecuencia)
    return {"entrada": {"ingreso_base": base, "frecuencia": frecuencia}, "aceptado": aceptado, "mensaje": "",
            "modo": "config"}


def _crear_tarjeta(page, rng, oraculo):
    activos = PAGOS_ACTIVOS.get((oraculo.config_presupuesto() or (0, "mensual"))[1], 1)
    orden = rng.randint(1, activos) if rng.random() < 0.85 else rng.randint(1, 4)
    botones = page.query_selector_all("#pres-pagos-lista .pres-pago-add")
    if not botones:
        return None
    boton = botones[min(orden, len(botones)) - 1]
    tope = (oraculo.config_presupuesto() or (0,))[0] / 100 * 0.6
    monto = round(rng.uniform(50, max(60, tope)), 2)
    nombre = f"Tarjeta {rng.randint(1, 99)}"
    antes = len(oraculo.tarjetas_presupuesto())
    boton.evaluate("b => b.click()")
    page.wait_for_selector("#modal-tarjeta-presupuesto:not(.hidden)")
    page.fill("#tarjeta-pres-nombre", nombre)
    page.fill("#tarjeta-pres-monto", f"{monto:.2f}")
    page.evaluate("document.getElementById('form-tarjeta-presupuesto').requestSubmit()")
    limite = time.perf_counter() + 6
    while time.perf_counter() < limite:
        if not page.is_visible("#modal-tarjeta-presupuesto") or len(oraculo.tarjetas_presupuesto()) != antes:
            break
        page.wait_for_timeout(150)
    aceptado = len(oraculo.tarjetas_presupuesto()) == antes + 1
    mensaje = "" if aceptado else page.inner_text("#modal-tarjeta-presupuesto").strip()[:200]
    _cerrar_si_abierto(page, "modal-tarjeta-presupuesto")
    return {"entrada": {"pago": orden, "nombre": nombre, "monto": monto}, "aceptado": aceptado, "mensaje": mensaje,
            "modo": "crear_tarjeta", "pago": orden, "antes": antes}


def _tarjeta_en_pantalla(page, rng, boton):
    tarjetas = page.query_selector_all(f"#pres-pagos-lista button.{boton}")
    if not tarjetas:
        return None
    return tarjetas[rng.randrange(len(tarjetas))]


def _editar_tarjeta(page, rng, oraculo):
    boton = _tarjeta_en_pantalla(page, rng, "btn-icon-edit")
    if boton is None:
        return None
    monto = round(rng.uniform(50, 6000), 2)
    antes = oraculo.tarjetas_presupuesto()
    boton.evaluate("b => b.click()")
    page.wait_for_selector("#modal-tarjeta-presupuesto:not(.hidden)")
    page.fill("#tarjeta-pres-monto", f"{monto:.2f}")
    page.evaluate("document.getElementById('form-tarjeta-presupuesto').requestSubmit()")
    limite = time.perf_counter() + 6
    while time.perf_counter() < limite:
        if not page.is_visible("#modal-tarjeta-presupuesto") or oraculo.tarjetas_presupuesto() != antes:
            break
        page.wait_for_timeout(150)
    despues = oraculo.tarjetas_presupuesto()
    aceptado = despues != antes
    mensaje = "" if aceptado else page.inner_text("#modal-tarjeta-presupuesto").strip()[:200]
    _cerrar_si_abierto(page, "modal-tarjeta-presupuesto")
    return {"entrada": {"monto": monto}, "aceptado": aceptado, "mensaje": mensaje, "modo": "editar_tarjeta",
            "antes": antes}


def _eliminar_tarjeta(page, rng, oraculo):
    boton = _tarjeta_en_pantalla(page, rng, "btn-icon-del")
    if boton is None:
        return None
    antes = len(oraculo.tarjetas_presupuesto())
    boton.evaluate("b => b.click()")
    page.wait_for_selector("#modal-confirmar:not(.hidden)", timeout=10000)
    page.evaluate("document.getElementById('btn-confirmar-aceptar').click()")
    page.wait_for_selector("#modal-confirmar.hidden", state="attached", timeout=15000)
    limite = time.perf_counter() + 6
    mensaje = ""
    while time.perf_counter() < limite:
        if len(oraculo.tarjetas_presupuesto()) != antes:
            break
        if page.locator("text=No se pudo eliminar la tarjeta").count() > 0:
            mensaje = "No se pudo eliminar la tarjeta."
            break
        page.wait_for_timeout(150)
    aceptado = len(oraculo.tarjetas_presupuesto()) == antes - 1
    return {"entrada": {"accion": "eliminar"}, "aceptado": aceptado, "mensaje": mensaje, "modo": "eliminar_tarjeta",
            "antes": antes}


def _exportar(page, rng, oraculo):
    """Exporta dos veces seguidas desde el modal: la segunda no debe cambiar nada. Sin tarjetas ni ingreso
    marcado no hay nada que exportar: la app deja el modal abierto y se cancela (no cuenta como acción)."""
    incluir = rng.random() < 0.7
    if not incluir and not oraculo.tarjetas_presupuesto():
        return None
    pre = oraculo.recurrentes_contenido()
    resultados = []
    mensaje = ""
    for _ in range(2):
        page.evaluate("document.getElementById('btn-exportar-pres-fijos').click()")
        page.wait_for_selector("#modal-exportar-presupuesto:not(.hidden)")
        chk = page.query_selector("#chk-exportar-ingreso")
        if chk is not None and chk.is_checked() != incluir:
            page.evaluate("document.getElementById('chk-exportar-ingreso').click()")
        page.evaluate("document.getElementById('btn-confirmar-exportar-pres').click()")
        # La app muestra antes una vista previa en un diálogo «Esto es lo que cambiará»: se acepta para aplicar
        limite = time.perf_counter() + 8
        while time.perf_counter() < limite and not page.is_visible("#modal-confirmar") and page.is_visible("#modal-exportar-presupuesto"):
            page.wait_for_timeout(150)
        if page.is_visible("#modal-confirmar"):
            page.evaluate("document.getElementById('btn-confirmar-aceptar').click()")
            page.wait_for_selector("#modal-confirmar.hidden", state="attached", timeout=15000)
        try:
            page.wait_for_selector("#modal-exportar-presupuesto.hidden", state="attached", timeout=10000)
        except Exception:
            # La app rechazó la exportación y dejó el modal abierto: se anota su aviso y se cierra
            mensaje = page.inner_text("#modal-exportar-presupuesto").strip()[:200]
            _cerrar_si_abierto(page, "modal-exportar-presupuesto")
            return {"entrada": {"incluir_ingreso": incluir}, "aceptado": False, "mensaje": mensaje, "modo": "exportar",
                    "pre": pre, "primera": pre, "segunda": pre, "duplicadas": [f for f in oraculo.filas_ligadas() if f[2] > 1]}
        resultados.append(oraculo.recurrentes_contenido())
    fijos_dup = [f for f in oraculo.filas_ligadas() if f[2] > 1]
    return {"entrada": {"incluir_ingreso": incluir}, "aceptado": True, "mensaje": "", "modo": "exportar",
            "pre": pre, "primera": resultados[0], "segunda": resultados[1], "duplicadas": fijos_dup}


def _verificar(nombre, datos, oraculo, pre, hoy):
    problemas = []
    if nombre == "crear_tarjeta" and datos:
        pago = datos.get("pago")
        if datos["aceptado"]:
            if len(oraculo.tarjetas_presupuesto()) != datos["antes"] + 1:
                problemas.append("el alta aceptada no creó exactamente una tarjeta")
            suma, ingreso = presupuesto._capacidad_del_pago(oraculo, pago)
            if suma > ingreso + 0.005:
                problemas.append(f"el pago {pago} suma {suma:,.2f} con ingreso {ingreso:,.2f} tras el alta")
        elif len(oraculo.tarjetas_presupuesto()) != datos["antes"]:
            problemas.append("el formulario rechazó la tarjeta y la base cambió")
    if nombre == "exportar" and datos:
        if datos["duplicadas"]:
            problemas.append(f"un fijo ligado aparece dos veces: {datos['duplicadas'][0][:2]}")
        if datos["primera"] != datos["segunda"]:
            problemas.append("exportar dos veces seguidas cambió los fijos: no es idempotente")
    if nombre == "editar_tarjeta" and datos and datos["aceptado"] is False and datos.get("antes") is not None:
        if oraculo.tarjetas_presupuesto() != datos["antes"]:
            problemas.append("un rechazo al editar cambió la base")
    futuras = oraculo.filas_ligadas_despues(hoy)
    if futuras:
        problemas.append(f"hay movimientos de fijos con fecha futura: {futuras[0]}")
    return problemas


def _resumen_tras_accion(page, oraculo, nombre, servidor, usuario):
    """Compara el resumen con la base. Si no coincide, recarga la página: si al recargar sí coincide, el resumen
    se quedó viejo después de la acción (la app no lo refresca tras exportar); si no, la pantalla y la base no cuadran."""
    problemas = escritorio._verificar_resumen(page, oraculo)
    if not problemas:
        return []
    page.reload()
    page.wait_for_selector("#vista-app:not(.hidden)", timeout=20000)
    if not escritorio._verificar_resumen(page, oraculo):
        return [f"el resumen no se actualizó tras «{nombre}»: hizo falta recargar la página"]
    return problemas


def _episodio(semilla, numero, navegador, servidor, mods, bit, fallos, dispositivo):
    rng = random.Random(semilla)
    usuario = f"aud{semilla}"
    hoy = HOY_INICIAL
    servidor.arrancar(hoy)
    import httpx
    with httpx.Client(base_url=servidor.base, timeout=30) as cliente:
        cabecera, ruta_db = personas.crear_laura(cliente, usuario, mods)
        presupuesto.preparar(cliente, cabecera)
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
        pre = {}
        inicio = time.perf_counter()
        datos = None
        try:
            if nombre == "avanzar_dia":
                hoy = hoy + datetime.timedelta(days=rng.choice((1, 1, 2, 7, 15)))
                servidor.parar()
                servidor.arrancar(hoy)
                escritorio._reentrar(page, servidor.base, usuario)
                datos = {"entrada": {"hoy": hoy.isoformat()}, "aceptado": None, "mensaje": ""}
            elif nombre == "recargar":
                page.reload()
                page.wait_for_selector("#vista-app:not(.hidden)", timeout=20000)
                datos = {"entrada": {}, "aceptado": None, "mensaje": ""}
            elif nombre == "dashboard":
                escritorio._ir(page, "tab-dashboard")
                datos = {"entrada": {}, "aceptado": None, "mensaje": ""}
            elif nombre == "config":
                datos = _config(page, rng, oraculo)
            elif nombre == "crear_tarjeta":
                _ir_presupuesto(page)
                datos = _crear_tarjeta(page, rng, oraculo)
            elif nombre == "editar_tarjeta":
                _ir_presupuesto(page)
                datos = _editar_tarjeta(page, rng, oraculo)
            elif nombre == "eliminar_tarjeta":
                _ir_presupuesto(page)
                datos = _eliminar_tarjeta(page, rng, oraculo)
            elif nombre == "exportar":
                _ir_presupuesto(page)
                datos = _exportar(page, rng, oraculo)
        except Exception as error:
            fallo_accion = escritorio._primera_linea(error)
            datos = {"entrada": {}, "aceptado": None, "mensaje": ""}
        duracion_ms = round((time.perf_counter() - inicio) * 1000)

        nota = None
        if datos is None:
            nota = "omitida: no había tarjetas o botones que tocar"
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
            if nombre in ("dashboard", "avanzar_dia", "config", "crear_tarjeta", "editar_tarjeta", "eliminar_tarjeta", "exportar"):
                try:
                    problemas += _resumen_tras_accion(page, oraculo, nombre, servidor, usuario)
                except Exception as error:
                    problemas.append(f"el resumen no se pudo leer: {escritorio._primera_linea(error)}")

        registro = {
            "episodio": numero, "semilla": semilla, "persona": usuario, "paso": paso,
            "fecha_simulada": hoy.isoformat(), "accion": nombre, "entrada": datos.get("entrada", {}),
            "aceptado": datos.get("aceptado"), "mensaje": datos.get("mensaje") or None,
            "saldo_base_c": oraculo.saldo_c(), "tarjetas_base": len(oraculo.tarjetas_presupuesto()),
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
    tmp = tempfile.mkdtemp(prefix="auditoria_pres_ui_")
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

    print(f"Corrida presupuesto {dispositivo} s{semilla} n{episodios}: {episodios} episodios, {pasos_totales} acciones, carpeta {carpeta}")
    if fallos:
        print(f"FALLO: {len(fallos)} acciones con invariantes rotas")
        for numero, paso, nombre, problemas in fallos[:20]:
            print(f"  episodio {numero} paso {paso} ({nombre}): {'; '.join(problemas)}")
        return 1
    print("OK: todas las invariantes se cumplieron")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Capas B y C de Presupuesto (navegador).")
    parser.add_argument("--semilla", type=int, default=1)
    parser.add_argument("--episodios", type=int, default=100)
    parser.add_argument("--fuente", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    parser.add_argument("--carpeta", required=True)
    parser.add_argument("--dispositivo", choices=sorted(escritorio.DISPOSITIVOS), default="escritorio")
    args = parser.parse_args()
    sys.exit(correr(args.semilla, args.episodios, os.path.abspath(args.fuente), os.path.abspath(args.carpeta), args.dispositivo))
