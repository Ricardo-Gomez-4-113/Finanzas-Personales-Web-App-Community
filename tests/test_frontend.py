"""tests/test_frontend.py - Smoke test del navegador con Playwright contra un servidor temporal.

Login, registrar un ingreso, ver cada pestaña sin errores de JS ni desbordamiento horizontal
(1366x768 y móvil), y foco de teclado en el modal de edición.
Requiere playwright (se ejecuta en Docker, ver tests/run_frontend.sh). Nunca toca 'data/' del proyecto.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP: requiere playwright (ejecutar con tests/run_frontend.sh)")
    sys.exit(0)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="finanzas_front_")
shutil.copytree(
    RAIZ, TMP, dirs_exist_ok=True,
    ignore=shutil.ignore_patterns("data", ".git", "tests", "__pycache__", "*.db", "backups", "venv", ".venv", "node_modules"),
)
os.chdir(TMP)
sys.path.insert(0, TMP)
os.environ.setdefault("JWT_SECRET_KEY", "k" * 48)
os.environ["DEFAULT_ADMIN_PASSWORD"] = "AdminTest#12345"
os.environ["TELEGRAM_BOT_USERNAME"] = "finanzas_demo_bot"   # el servidor de pruebas lo hereda: el menú del asistente de Telegram queda disponible

import escenario  # noqa: E402
import master_db  # noqa: E402
import security  # noqa: E402

USUARIO, CLAVE = "front_user", "Passw0rd#Front1"
PESTANAS = ["tab-dashboard", "tab-movimientos", "tab-apartados", "tab-inversiones", "tab-presupuesto", "tab-calculadora-rf"]
VIEWPORTS = {"laptop": {"width": 1366, "height": 768}, "movil": {"width": 390, "height": 844}}
LOGS = tempfile.mkdtemp(prefix="finanzas_logs_front_")  # el servidor de la prueba escribe aquí su registro


def _puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# El servidor de las pruebas no sale a internet: sin cotizaciones fijas, cada consulta a Yahoo
# desde un contenedor sin red consume los 5 s de espera y las pruebas dependen de la latencia.
LANZADOR = """
import sys
import mercado
COTIZACIONES = {"USDMXN=X": 19.5, "EURUSD=X": 1.08}
mercado.consultar_precio_y_moneda_mercado = lambda t: (COTIZACIONES.get(t.strip().upper()), "USD")
import uvicorn
uvicorn.run("main:app", host="127.0.0.1", port=int(sys.argv[1]))
"""


def _arrancar_servidor(extra_env=None):
    puerto = _puerto_libre()
    proc = subprocess.Popen(
        [sys.executable, "-c", LANZADOR, str(puerto)],
        cwd=TMP, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        env={**os.environ, "FINANZAS_LOG_DIR": LOGS, **(extra_env or {})},
    )
    base = f"http://127.0.0.1:{puerto}"
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/health", timeout=1)
            return proc, base
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("el servidor no arrancó")


def _sin_desbordamiento(page, donde):
    ancho = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    assert ancho[0] <= ancho[1], f"desbordamiento horizontal en {donde}: {ancho[0]}px > {ancho[1]}px"


def _login(page, base):
    page.goto(base + "/login")
    page.fill("#login-username", USUARIO)
    page.fill("#login-password", CLAVE)
    page.click("#form-login [type=submit]")
    page.wait_for_selector("#vista-app:not(.hidden)", timeout=15000)


def test_login_incorrecto_muestra_error(navegador, base):
    page = navegador.new_page()
    page.goto(base + "/login")
    page.fill("#login-username", USUARIO)
    page.fill("#login-password", "clave-equivocada")
    page.click("#form-login [type=submit]")
    page.wait_for_selector("#login-error", state="visible", timeout=10000)
    assert page.inner_text("#login-error").strip(), "el mensaje de error debe tener texto"
    page.close()


def test_login_sin_fab_con_texto_fijo_y_ojo_de_contrasena(navegador, base):
    """En móvil el login no muestra el botón «+»; el botón conserva su texto tras un error;
    el botón de ojo alterna el tipo del campo y su estado accesible; el error no mueve la tarjeta."""
    ctx, page, errores = _contexto(navegador, VIEWPORTS["movil"])
    try:
        page.goto(base + "/login")
        page.wait_for_selector("#vista-login:not(.hidden)")
        assert page.locator("#btn-fab, .fab").count() == 0, "ya no hay botón «+» flotante"
        page.fill("#login-username", "nadie")
        page.fill("#login-password", "clave-equivocada")
        arriba_antes = page.evaluate("document.querySelector('.login-card').getBoundingClientRect().top")
        page.click("#form-login [type=submit]")
        page.wait_for_selector("#login-error:not(.hidden)", timeout=10000)
        assert page.inner_text("#btn-login-submit") == "Entrar", page.inner_text("#btn-login-submit")
        arriba_despues = page.evaluate("document.querySelector('.login-card').getBoundingClientRect().top")
        assert abs(arriba_despues - arriba_antes) < 2, f"la tarjeta se movió {arriba_antes} -> {arriba_despues}"
        assert page.get_attribute("#login-error", "role") == "alert"
        assert page.get_attribute("#login-password", "type") == "password"
        page.click(".btn-ver-clave")
        assert page.get_attribute("#login-password", "type") == "text"
        assert page.get_attribute(".btn-ver-clave", "aria-pressed") == "true"
        page.click(".btn-ver-clave")
        assert page.get_attribute("#login-password", "type") == "password"
        assert page.get_attribute(".btn-ver-clave", "aria-pressed") == "false"
        assert not errores, "errores de JavaScript en el login:\n" + "\n".join(errores)
    finally:
        ctx.close()


def test_login_pie_legal_a_13px_y_en_tipo_oracion(navegador, base):
    """La línea legal del login se lee: al menos 13 px, y «Términos» y «Aviso de privacidad» en tipo oración."""
    for viewport in VIEWPORTS.values():
        ctx, page, errores = _contexto(navegador, viewport)
        try:
            page.goto(base + "/login")
            page.wait_for_selector("#vista-login:not(.hidden)")
            tamano = page.evaluate("parseFloat(getComputedStyle(document.querySelector('.login-legal')).fontSize)")
            assert tamano >= 13, f"la línea legal mide {tamano} px"
            assert page.inner_text(".login-legal").count("Aviso de privacidad") == 1
            assert not errores, "errores de JavaScript en el login:\n" + "\n".join(errores)
        finally:
            ctx.close()


def test_inversiones_pagina_sin_borde_un_solo_nivel_de_tarjeta(navegador, base):
    """En Inversiones la página no lleva borde ni fondo: cada bloque es el único nivel de tarjeta."""
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base)
        _ir(page, "tab-inversiones")
        estilo = page.evaluate("""(() => {
            const s = getComputedStyle(document.querySelector('.inversiones-section'));
            return [s.borderTopWidth, s.backgroundColor];
        })()""")
        assert estilo[0] == "0px", f"la página de Inversiones tiene borde {estilo[0]}"
        assert estilo[1] == "rgba(0, 0, 0, 0)", f"la página de Inversiones tiene fondo {estilo[1]}"
        assert not errores, "errores de JavaScript en Inversiones:\n" + "\n".join(errores)
    finally:
        ctx.close()


def test_avisos_sin_barra_de_color_y_botones_sin_brillo_dorado(navegador, base):
    """Los avisos no llevan barra de color a un lado (el estado va en el texto) y los botones de acción no tienen halo dorado."""
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base)
        page.evaluate("""document.body.insertAdjacentHTML('beforeend', "<div class='toast toast-error' id='aviso-prueba'>x</div>")""")
        ancho, color = page.evaluate("""(() => {
            const s = getComputedStyle(document.getElementById('aviso-prueba'));
            return [s.borderLeftWidth, s.color];
        })()""")
        assert ancho == "1px", f"el aviso tiene barra lateral de {ancho}"
        assert color != "rgb(0, 0, 0)", "el aviso de error debe llevar el tono de texto de peligro"
        _ir(page, "tab-calculadora-rf")
        sombra = page.evaluate("getComputedStyle(document.querySelector('.btn-calc-action')).boxShadow")
        assert "201, 179, 126" not in sombra and "156, 134, 83" not in sombra, f"el botón de cálculo tiene halo dorado: {sombra}"
        assert not errores, "errores de JavaScript:\n" + "\n".join(errores)
    finally:
        ctx.close()


def test_boton_de_texto_dorado_no_queda_dorado_sobre_dorado_al_pasar_el_cursor(navegador, base):
    """Los botones con texto dorado no toman el fondo de acento al pasar el cursor: el texto seguiría ilegible."""
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base)
        fondo_acento = page.evaluate("""(() => {
            const p = document.createElement('div');
            p.style.backgroundColor = 'var(--primary-hover)';
            document.body.appendChild(p);
            const c = getComputedStyle(p).backgroundColor;
            p.remove();
            return c;
        })()""")
        page.hover(".btn-topbar-privacy")
        fondo = page.evaluate("getComputedStyle(document.querySelector('.btn-topbar-privacy')).backgroundColor")
        assert fondo != fondo_acento, f"el botón del ojo toma el fondo de acento al pasar el cursor: {fondo}"
        assert not errores, "errores de JavaScript:\n" + "\n".join(errores)
    finally:
        ctx.close()


def test_enlace_de_accion_sin_fondo_dorado_al_pasar_el_cursor(navegador, base):
    """«Ir a fijos y MSI» es un enlace dorado: al pasar el cursor no toma el fondo de acento (texto dorado sobre dorado)."""
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base)
        _ir(page, "tab-movimientos")
        _abrir_formulario(page, "movimiento")
        page.hover(".link-btn")
        fondo = page.evaluate("getComputedStyle(document.querySelector('.link-btn')).backgroundColor")
        assert fondo == "rgba(0, 0, 0, 0)", f"el enlace toma fondo al pasar el cursor: {fondo}"
        assert not errores, "errores de JavaScript:\n" + "\n".join(errores)
    finally:
        ctx.close()


def test_login_movil_centrado_y_sin_saltar_al_fallar(navegador, base):
    """En móvil la tarjeta del login queda centrada en la pantalla y no se mueve cuando aparece el error."""
    ctx, page, errores = _contexto(navegador, VIEWPORTS["movil"])
    try:
        page.goto(base + "/login")
        page.wait_for_selector("#vista-login:not(.hidden)")
        centro = page.evaluate("(() => { const r = document.querySelector('.login-card').getBoundingClientRect(); return (r.top + r.bottom) / 2; })()")
        alto_vista = VIEWPORTS["movil"]["height"]
        assert abs(centro - alto_vista / 2) < 40, f"la tarjeta no está centrada: centro {centro}, pantalla {alto_vista / 2}"
        arriba_antes = page.evaluate("document.querySelector('.login-card').getBoundingClientRect().top")
        page.fill("#login-username", "nadie")
        page.fill("#login-password", "clave-equivocada")
        page.click("#form-login [type=submit]")
        page.wait_for_selector("#login-error:not(.hidden)", timeout=10000)
        arriba_despues = page.evaluate("document.querySelector('.login-card').getBoundingClientRect().top")
        assert abs(arriba_despues - arriba_antes) < 2, f"la tarjeta se movió {arriba_antes} -> {arriba_despues}"
        assert not errores, "errores de JavaScript en el login:\n" + "\n".join(errores)
    finally:
        ctx.close()


def test_error_de_fondos_insuficientes_se_lee_a_la_izquierda_y_se_anuncia(navegador, base):
    """El aviso de fondos insuficientes va alineado a la izquierda, sin negrita, se anuncia (role=alert) y no trae «$-» ni fechas ISO."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-movimientos")
        _abrir_formulario(page, "movimiento")
        page.fill("#fecha", escenario.HOY)
        page.select_option("#tipo", "gasto")
        page.fill("#monto", "9999999")
        page.fill("#descripcion", "gasto-que-no-alcanza")
        page.click("#transaccion-form [type=submit]")
        page.wait_for_selector("#mensaje.msg-error", timeout=10000)
        texto = page.inner_text("#mensaje")
        assert texto.startswith("Fondos insuficientes:"), texto
        assert "$-" not in texto and escenario.HOY not in texto, texto
        estilo = page.evaluate("(() => { const s = getComputedStyle(document.getElementById('mensaje')); return [s.textAlign, parseInt(s.fontWeight)]; })()")
        assert estilo[0] in ("left", "start"), f"el aviso está alineado a {estilo[0]}"
        assert estilo[1] < 600, f"el aviso va en negrita ({estilo[1]})"
        assert page.get_attribute("#mensaje", "role") == "alert", "el aviso debe anunciarse"
        assert page.is_visible("#modal-nuevo-movimiento #mensaje"), "el aviso aparece dentro del modal"
        assert not page.is_hidden("#modal-nuevo-movimiento"), "con un error el modal sigue abierto"
        assert not errores, "errores de JavaScript:\n" + "\n".join(errores)
    finally:
        ctx.close()


def test_formularios_se_abren_con_el_mas_y_la_tabla_ocupa_todo_el_ancho(navegador, base):
    """Movimientos, Fijos e Inversiones: el formulario vive detrás de un «+», se abre con el foco en su primer campo,
    se cierra con Escape devolviendo el foco al botón, y la tabla o la gráfica ocupan todo el ancho."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        casos = [
            ("movimiento", "tab-movimientos", None, "btn-nuevo-movimiento", "modal-nuevo-movimiento", "monto",
             "#subtab-content-historial .dashboard-grid-movimientos"),
            ("fijo", "tab-movimientos", "btn-subtab-fijos", "btn-nuevo-fijo", "modal-nuevo-fijo", "rec-desc",
             "#subtab-content-fijos .dashboard-grid-movimientos"),
            ("posicion", "tab-inversiones", None, "btn-nueva-posicion", "modal-nueva-posicion", "inv-ticker",
             ".inversiones-top-grid"),
        ]
        for nombre, tab, subtab, boton, modal, campo, rejilla in casos:
            _ir(page, tab)
            if subtab:
                page.evaluate(f"document.getElementById('{subtab}').click()")
            assert page.is_hidden(f"#{modal}"), f"{nombre}: el modal no debe verse hasta pulsar el «+»"
            hijos = page.evaluate(f"""(() => {{
                const g = document.querySelector('{rejilla}');
                const w = g.getBoundingClientRect().width;
                return [...g.children].filter(h => getComputedStyle(h).position !== 'fixed')
                    .map(h => Math.abs(h.getBoundingClientRect().width - w) < 2);
            }})()""")
            assert hijos and all(hijos), f"{nombre}: los bloques deben ocupar todo el ancho de su rejilla: {hijos}"
            page.evaluate(f"document.getElementById('{boton}').scrollIntoView({{block: 'center'}})")
            page.click(f"#{boton}")
            page.wait_for_selector(f"#{modal}:not(.hidden)")
            _esperar(page, f"document.activeElement && document.activeElement.id === '{campo}'")
            separacion = page.evaluate(f"""(() => {{
                const barra = document.querySelector('#{modal} .modal-actions');
                return barra.getBoundingClientRect().top - barra.previousElementSibling.getBoundingClientRect().bottom;
            }})()""")
            assert separacion >= 12, f"{nombre}: la barra de botones toca el último campo ({separacion:.0f} px de separación)"
            page.keyboard.press("Escape")
            page.wait_for_selector(f"#{modal}.hidden", state="attached")
            _esperar(page, f"document.activeElement && document.activeElement.id === '{boton}'")
        assert not errores, "errores de JavaScript:\n" + "\n".join(errores)
    finally:
        ctx.close()


def test_fijo_con_error_muestra_el_aviso_dentro_del_modal(navegador, base):
    """Un gasto fijo sin ingreso fijo lo rechaza el servidor a propósito: el aviso sale dentro del modal, que sigue abierto,
    y no como un toast que quedaría detrás del velo."""
    usuario, clave = _usuario_nuevo("recerr")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        _ir(page, "tab-movimientos")
        page.evaluate("document.getElementById('btn-subtab-fijos').click()")
        _abrir_formulario(page, "fijo")
        page.fill("#rec-desc", "Renta")
        page.fill("#rec-monto", "5000")
        page.fill("#rec-fecha", "2026-10-01")
        page.click("#btn-guardar-recurrente")
        page.wait_for_selector("#rec-mensaje.msg-error", timeout=10000)
        assert "ingreso fijo" in page.inner_text("#rec-mensaje").lower(), page.inner_text("#rec-mensaje")
        assert page.get_attribute("#rec-mensaje", "role") == "alert"
        assert not page.is_hidden("#modal-nuevo-fijo"), "con un error el modal sigue abierto"
        assert page.is_visible("#modal-nuevo-fijo #rec-mensaje")
        assert not errores, "errores de JavaScript:\n" + "\n".join(errores)
    finally:
        ctx.close()


def test_cancelar_o_eliminar_devuelven_el_dinero_y_solo_cancelar_deja_constancia(navegador, base):
    """La papelera abre un cuadro: «Eliminar» borra sin rastro y «Cancelar operación» deja el movimiento como cancelado,
    tachado y sin casilla; en las dos el dinero vuelve. Un cancelado se puede borrar del todo sin tocar el saldo."""
    usuario, clave = _usuario_nuevo("quitar")
    api = _api(base, usuario, clave)
    api.post("/api/transacciones", json={"fecha": "2026-10-01", "monto": 1000, "tipo": "ingreso", "categoria_id": 12, "descripcion": "sueldo"})
    api.post("/api/transacciones", json={"fecha": "2026-10-02", "monto": 100, "tipo": "gasto", "categoria_id": 2, "descripcion": "gasto-eliminar"})
    api.post("/api/transacciones", json={"fecha": "2026-10-03", "monto": 200, "tipo": "gasto", "categoria_id": 2, "descripcion": "gasto-cancelar"})
    saldo = lambda: api.get("/api/resumen?moneda_base=MXN").json()["balance"]
    fila = lambda texto: page.locator("#tabla-movimientos tr", has_text=texto)
    assert saldo() == 700.0

    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        _ir(page, "tab-movimientos")
        page.wait_for_selector("#tabla-movimientos tr .btn-eliminar")

        # Volver y Escape no cambian nada
        fila("gasto-eliminar").locator(".btn-eliminar").click()
        visto = _responder_quitar(page, None)
        assert visto["titulo"] == "Quitar movimiento", visto
        fila("gasto-eliminar").locator(".btn-eliminar").click()
        page.wait_for_selector("#modal-quitar-movimiento:not(.hidden)")
        page.keyboard.press("Escape")
        page.wait_for_selector("#modal-quitar-movimiento.hidden", state="attached")
        assert fila("gasto-eliminar").count() == 1 and saldo() == 700.0

        # Eliminar: el dinero vuelve y la fila desaparece sin rastro
        fila("gasto-eliminar").locator(".btn-eliminar").click()
        _responder_quitar(page, "eliminar")
        _esperar(page, "![...document.querySelectorAll('#tabla-movimientos tr')].some(tr => tr.textContent.includes('gasto-eliminar'))")
        assert saldo() == 800.0
        assert api.get("/api/transacciones/canceladas").json() == []

        # Cancelar operación: el dinero vuelve, la fila se queda como cancelada
        fila("gasto-cancelar").locator(".btn-eliminar").click()
        _responder_quitar(page, "cancelar")
        _esperar(page, "document.querySelector('#tabla-movimientos tr.fila-cancelada') !== null")
        assert saldo() == 1000.0
        cancelada = fila("gasto-cancelar")
        assert cancelada.count() == 1 and "Cancelado" in cancelada.inner_text()
        assert cancelada.locator(".chk-mov-item").count() == 0, "un cancelado no se selecciona para el lote"
        assert cancelada.locator(".btn-editar").count() == 0, "un cancelado no se edita"
        assert "line-through" in cancelada.locator(".td-monto").evaluate("e => getComputedStyle(e).textDecorationLine")

        # El filtro «Cancelados» solo los muestra a ellos; «Gastos» no los incluye
        page.select_option("#filtro-tipo", "cancelado")
        assert page.locator("#tabla-movimientos tr.fila-cancelada").count() == 1
        assert fila("sueldo").count() == 0
        page.select_option("#filtro-tipo", "gasto")
        assert page.locator("#tabla-movimientos tr.fila-cancelada").count() == 0
        page.select_option("#filtro-tipo", "")

        # Borrar un cancelado del todo no cambia el saldo
        cancelada.locator(".btn-eliminar").click()
        _responder_confirmacion(page)
        _esperar(page, "document.querySelector('#tabla-movimientos tr.fila-cancelada') === null")
        assert saldo() == 1000.0 and api.get("/api/transacciones/canceladas").json() == []
        assert not errores, errores
    finally:
        ctx.close()


def test_cuadro_de_quitar_en_movil_con_botones_de_44px(navegador, base):
    usuario, clave = _usuario_nuevo("quitarmovil")
    _api(base, usuario, clave).post("/api/transacciones", json={"fecha": "2026-10-02", "monto": 100, "tipo": "ingreso", "categoria_id": 12, "descripcion": "cobro-movil"})
    ctx, page, errores = _contexto(navegador, VIEWPORTS["movil"])
    try:
        _entrar(page, base, usuario, clave)
        _ir(page, "tab-movimientos")
        page.wait_for_selector("#tabla-movimientos tr .btn-eliminar")
        page.evaluate("document.querySelector('#tabla-movimientos .btn-eliminar').click()")
        page.wait_for_selector("#modal-quitar-movimiento:not(.hidden)")
        assert "ingreso" in page.inner_text("#quitar-mensaje").lower(), "el aviso de un ingreso dice que su dinero sale del saldo"
        for boton in ("#btn-quitar-cancelar", "#btn-quitar-eliminar", "#btn-quitar-volver"):
            alto = page.evaluate(f"document.querySelector('{boton}').getBoundingClientRect().height")
            assert alto >= 44, f"{boton} mide {alto} px"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "el cuadro desborda el ancho"
        page.keyboard.press("Escape")
        assert not errores, errores
    finally:
        ctx.close()


def _contexto(navegador, viewport):
    """Contexto nuevo con registro de errores de JavaScript. Cada prueba abre el suyo: no comparten estado."""
    ctx = navegador.new_context(viewport=viewport)
    page = ctx.new_page()
    errores = []
    page.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
    # Solo errores propios: recursos externos (fuentes, CDN) pueden fallar sin internet.
    page.on("console", lambda m: errores.append(f"console: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
    return ctx, page, errores


def _recorrer_app(navegador, base, nombre_vp, viewport):
    ctx, page, errores = _contexto(navegador, viewport)
    try:
        _login(page, base)
        _sin_desbordamiento(page, f"{nombre_vp}/inicio")
        for tab in PESTANAS:
            # Clic por JS: en móvil el botón vive en el sidebar colapsado, fuera de la pantalla.
            page.evaluate(f"document.querySelector('[data-tab=\"{tab}\"]').click()")
            page.wait_for_selector(f"#{tab}.active")
            _sin_desbordamiento(page, f"{nombre_vp}/{tab}")
        assert not errores, f"{nombre_vp}: errores de JS\n" + "\n".join(errores)
    finally:
        ctx.close()


def test_recorrido_pestanas_laptop(navegador, base):
    _recorrer_app(navegador, base, "laptop", VIEWPORTS["laptop"])


def test_recorrido_pestanas_movil(navegador, base):
    _recorrer_app(navegador, base, "movil", VIEWPORTS["movil"])


def test_registrar_ingreso_y_modal_accesible(navegador, base):
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _login(page, base)
        page.evaluate("document.querySelector('[data-tab=\"tab-movimientos\"]').click()")
        page.wait_for_selector("#tab-movimientos.active")
        _abrir_formulario(page, "movimiento")
        page.fill("#fecha", escenario.HOY)  # el servidor de pruebas tiene el reloj fijo: la fecha real caería fuera del periodo
        page.select_option("#tipo", "ingreso")
        page.fill("#monto", "1234.56")
        page.fill("#descripcion", "ingreso-desde-playwright")
        opciones = page.locator("#categoria option").evaluate_all("os => os.map(o => o.value).filter(v => v)")
        if opciones:
            page.select_option("#categoria", opciones[0])
        page.click("#transaccion-form [type=submit]")
        page.wait_for_selector("text=ingreso-desde-playwright", timeout=10000)
        assert page.is_hidden("#modal-nuevo-movimiento"), "al guardar bien, el modal se cierra"

        page.locator(".btn-editar").first.click()
        page.wait_for_selector("#modal-editar-transaccion:not(.hidden)")
        # El foco entra al modal de forma asíncrona: esperamos a que quede dentro, no un tiempo fijo.
        _esperar(page, "document.getElementById('modal-editar-transaccion').contains(document.activeElement)")
        for _ in range(30):
            page.keyboard.press("Tab")
            assert page.evaluate("document.getElementById('modal-editar-transaccion').contains(document.activeElement)"), \
                "Tab no debe sacar el foco del modal"
        page.keyboard.press("Escape")
        page.wait_for_selector("#modal-editar-transaccion.hidden", state="attached")
        _esperar(page, "document.activeElement && document.activeElement.classList.contains('btn-editar')")
        assert not errores, "errores de JS:\n" + "\n".join(errores)
    finally:
        ctx.close()


class _ClienteHttp:
    """Adaptador mínimo para sembrar el escenario por HTTP real contra el servidor de la prueba."""

    def __init__(self, base, token):
        self.base, self.token = base, token

    def _req(self, metodo, ruta, body=None, headers=None):
        import json as _json
        import urllib.error
        datos = _json.dumps(body).encode() if body is not None else None
        cab = {"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"}
        req = urllib.request.Request(self.base + ruta, method=metodo, data=datos, headers=cab)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return _Respuesta(r.status, r.read())
        except urllib.error.HTTPError as e:
            return _Respuesta(e.code, e.read())

    def post(self, ruta, json=None, headers=None):
        return self._req("POST", ruta, json)

    def get(self, ruta, headers=None):
        return self._req("GET", ruta)

    def put(self, ruta, json=None, headers=None):
        return self._req("PUT", ruta, json)


class _Respuesta:
    def __init__(self, status_code, cuerpo):
        self.status_code = status_code
        self.content = cuerpo
        self.text = cuerpo.decode(errors="ignore")

    def json(self):
        import json as _json
        return _json.loads(self.content or b"null")


def _sembrar_escenario(base):
    """Crea el usuario de referencia y su historia, por la API, con el reloj fijo del servidor."""
    master_db.create_user_entry(escenario.USUARIO, security.hash_password(escenario.CLAVE), email="escenario@test.local")
    body = json.dumps({"username": escenario.USUARIO, "password": escenario.CLAVE}).encode()
    req = urllib.request.Request(base + "/auth/login", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        token = json.loads(r.read())["access_token"]
    escenario.sembrar(_ClienteHttp(base, token), {"Authorization": f"Bearer {token}"})


def _entrar(page, base, usuario=USUARIO, clave=CLAVE):
    page.goto(base + "/login")
    page.fill("#login-username", usuario)
    page.fill("#login-password", clave)
    page.click("#form-login [type=submit]")
    page.wait_for_selector("#vista-app:not(.hidden)", timeout=15000)


def _esperar(page, expresion, timeout=10.0):
    """Espera a que `expresion` (JavaScript) sea verdadera. Sondeo con evaluate: wait_for_function
    con cadenas usa eval, que la política CSP de la app bloquea."""
    limite = time.time() + timeout
    while time.time() < limite:
        if page.evaluate(expresion):
            return
        page.wait_for_timeout(100)
    estado = page.evaluate("[...document.querySelectorAll('.card-apartado')].map(c => c.innerText.replace(/\\n/g,' | ')).join(' || ')")
    raise AssertionError(f"no se cumplió en {timeout}s: {expresion}\nTarjetas ahora: {estado}")


def _responder_confirmacion(page, aceptar=True):
    """Responde al modal de confirmación y devuelve lo que mostraba (título, mensaje y detalle)."""
    page.wait_for_selector("#modal-confirmar:not(.hidden)")
    visto = {campo: page.inner_text(f"#confirmar-{campo}") for campo in ("titulo", "mensaje", "detalle")}
    page.click("#btn-confirmar-aceptar" if aceptar else "#btn-confirmar-cancelar")
    page.wait_for_selector("#modal-confirmar.hidden", state="attached")
    return visto


def _responder_quitar(page, opcion):
    """Responde al cuadro «Quitar movimiento»: «cancelar» (Cancelar operación), «eliminar» o None (Volver).
    Devuelve lo que mostraba (título y mensaje)."""
    page.wait_for_selector("#modal-quitar-movimiento:not(.hidden)")
    visto = {campo: page.inner_text(f"#quitar-{campo}") for campo in ("titulo", "mensaje")}
    page.click({"cancelar": "#btn-quitar-cancelar", "eliminar": "#btn-quitar-eliminar", None: "#btn-quitar-volver"}[opcion])
    page.wait_for_selector("#modal-quitar-movimiento.hidden", state="attached")
    return visto


def _abrir_formulario(page, que):
    """Abre el modal del botón «+» de Movimientos («movimiento»), Fijos («fijo») o Inversiones («posicion»)."""
    boton, modal = {
        "movimiento": ("btn-nuevo-movimiento", "modal-nuevo-movimiento"),
        "fijo": ("btn-nuevo-fijo", "modal-nuevo-fijo"),
        "posicion": ("btn-nueva-posicion", "modal-nueva-posicion"),
    }[que]
    page.evaluate(f"document.getElementById('{boton}').click()")  # clic por JS: la barra superior puede tapar el botón
    page.wait_for_selector(f"#{modal}:not(.hidden)")


def _ir(page, tab):
    page.evaluate(f"document.querySelector('[data-tab=\"{tab}\"]').click()")
    page.wait_for_selector(f"#{tab}.active")


def _flujo_escenario(navegador, base, viewport=None):
    """Contexto con sesión del escenario y registro de errores de JavaScript."""
    ctx, page, errores = _contexto(navegador, viewport or VIEWPORTS["laptop"])
    _entrar(page, base, escenario.USUARIO, escenario.CLAVE)
    return ctx, page, errores


def test_resumen_muestra_las_cifras_del_escenario(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        page.wait_for_selector("#periodo-libre:not(:empty)")
        assert page.inner_text("#periodo-libre") == "$8,103.00", page.inner_text("#periodo-libre")
        assert page.inner_text("#periodo-disponible") == "$8,103.00"
        assert "31 de octubre" in page.inner_text("#periodo-titulo"), page.inner_text("#periodo-titulo")
        assert "7,000.00" in page.inner_text("#lbl-dash-patrimonio"), page.inner_text("#lbl-dash-patrimonio")
        # La nómina es quincenal: el historial va por quincena, del cobro al día anterior al siguiente
        assert page.inner_text("#titulo-historial-periodos") == "Historial por quincena"
        historial = page.locator("#lista-historial-periodos .monthly-card")
        assert historial.count() == 5, historial.count()
        primera = historial.nth(0).inner_text()
        assert "15 oct – 30 oct" in primera.lower() and "en curso" in primera and "+$14,000.00" in primera and "−$299.00" in primera, primera
        segunda = historial.nth(1).inner_text()
        assert "30 sep – 14 oct" in segunda.lower() and "+$14,000.00" in segunda and "−$14,500.00" in segunda, segunda
        assert not errores, errores
    finally:
        ctx.close()


def test_movimientos_y_resumen_muestran_el_mismo_periodo(navegador, base):
    """Resumen y Movimientos pintan el periodo en curso, con fijos y compras a meses en tarjetas separadas."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        page.wait_for_selector("#periodo-libre:not(:empty)")
        assert page.locator("#lbl-dash-mes-ingresos").count() == 0, "Ingresos del periodo debe vivir solo en Movimientos"
        _ir(page, "tab-movimientos")
        page.wait_for_selector("#lbl-rec-ingreso:not(:empty)")
        tarjetas = {i: page.inner_text(f"#{i}") for i in ("lbl-rec-ingreso", "lbl-rec-gastos-normales", "lbl-rec-fijos", "lbl-rec-msi")}
        assert tarjetas["lbl-rec-ingreso"] == "$14,000.00" and tarjetas["lbl-rec-gastos-normales"] == "$0.00", tarjetas
        assert tarjetas["lbl-rec-fijos"] == "$299.00", tarjetas
        assert page.locator("#lbl-rec-libre").count() == 0, "el remanente se muestra solo como Libre en el Resumen"
        assert page.locator("#tabla-periodos-body").count() == 0, "la tabla de periodos se quitó de Movimientos"
        assert page.locator("#lbl-rec-periodo").count() == 0
        _sin_desbordamiento(page, "laptop/movimientos-periodos")
        assert not errores, errores
    finally:
        ctx.close()


def test_lista_de_movimientos_sigue_el_periodo_de_cobro(navegador, base):
    """La lista de movimientos muestra por defecto el periodo en curso y 'Todo el historial' lo amplía."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        page.wait_for_selector("#periodo-libre:not(:empty)")
        _ir(page, "tab-movimientos")
        page.wait_for_function("document.getElementById('lbl-ventana-movimientos').textContent.startsWith('Periodo del')", timeout=10000)
        inicio, fin = page.evaluate("""() => fetch('/api/resumen?moneda_base=MXN', {headers: {Authorization: 'Bearer ' + localStorage.getItem('jwt_token')}})
            .then(r => r.json()).then(d => { const p = d.periodos.find(x => x.en_curso); return [p.inicio, p.fin]; })""")
        celdas = page.locator("#tabla-movimientos td.td-fecha")
        fechas_periodo = [celdas.nth(i).get_attribute("title")[:10] for i in range(celdas.count())]
        assert fechas_periodo, "el periodo en curso no tiene movimientos en el escenario"
        assert all(inicio <= f <= fin for f in fechas_periodo), (inicio, fin, fechas_periodo)
        page.select_option("#filtro-ventana", "todo")
        page.wait_for_function("document.getElementById('lbl-ventana-movimientos').textContent === 'Todo el historial'", timeout=10000)
        assert page.locator("#tabla-movimientos td.td-fecha").count() >= len(fechas_periodo)
        assert not errores, errores
    finally:
        ctx.close()


def test_doble_clic_al_guardar_tarjeta_no_duplica(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-presupuesto")
        # Cada tarjeta cabe en el ingreso de su pago: el escenario necesita un ingreso base
        estado_cfg = page.evaluate("""() => fetch('/api/presupuesto/config', {method: 'PUT',
            headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('jwt_token')},
            body: JSON.stringify({ingreso_base: 10000, frecuencia: 'mensual', subdivision_tipo: 'ninguna'})}).then(async r => r.status + ' ' + await r.text())""")
        assert estado_cfg.startswith('200'), estado_cfg
        page.evaluate("document.querySelector('#pres-pagos-lista .pres-pago-add').click()")
        page.wait_for_selector("#modal-tarjeta-presupuesto:not(.hidden)")
        page.fill("#tarjeta-pres-nombre", "Doble prueba")
        page.fill("#tarjeta-pres-monto", "500")
        page.dispatch_event("#tarjeta-pres-monto", "input")
        page.dblclick("#form-tarjeta-presupuesto [type=submit]")
        _esperar(page, "[...document.querySelectorAll('#pres-pagos-lista .pres-card')].some(c => c.innerText.includes('Doble prueba'))", timeout=8)
        page.wait_for_timeout(1000)  # un segundo envío duplicado tendría tiempo de llegar antes de contar
        nombres = page.evaluate("[...document.querySelectorAll('#pres-pagos-lista .pres-card')].map(c => c.innerText)")
        creadas = [n for n in nombres if "Doble prueba" in n]
        assert len(creadas) == 1, nombres
        page.evaluate("""fetch('/api/presupuesto', {headers: {Authorization: 'Bearer ' + localStorage.getItem('jwt_token')}})
            .then(r => r.json()).then(d => d.tarjetas.filter(t => t.nombre === 'Doble prueba').forEach(t =>
                fetch('/api/presupuesto/tarjetas/' + t.id, {method: 'DELETE', headers: {Authorization: 'Bearer ' + localStorage.getItem('jwt_token')}})))""")
        assert not errores, errores
    finally:
        ctx.close()


def test_pagos_se_adaptan_a_la_frecuencia(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-presupuesto")
        original = page.evaluate("""() => fetch('/api/presupuesto', {headers: {Authorization: 'Bearer ' + localStorage.getItem('jwt_token')}})
            .then(r => r.json()).then(d => d.config)""")
        def fijar_config(frecuencia):
            page.evaluate("""(frecuencia) => fetch('/api/presupuesto/config', {method: 'PUT',
                headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('jwt_token')},
                body: JSON.stringify({ingreso_base: 9000, frecuencia: frecuencia, subdivision_tipo: 'ninguna'})})""", frecuencia)
            page.evaluate("document.dispatchEvent(new Event('recargar'))")
        try:
            # Semanal: cuatro pagos independientes; mensual: uno solo
            fijar_config("semanal")
            _esperar(page, "document.querySelectorAll('#pres-pagos-lista .pres-pago').length === 4")
            fijar_config("mensual")
            _esperar(page, "document.querySelectorAll('#pres-pagos-lista .pres-pago').length === 1")
            # El modal de un pago no pide día ni subdivisión: el día es el del pago
            page.evaluate("document.querySelector('#pres-pagos-lista .pres-pago-add').click()")
            page.wait_for_selector("#modal-tarjeta-presupuesto:not(.hidden)")
            assert "Pago mensual" in page.inner_text("#modal-tarjeta-pres-title"), page.inner_text("#modal-tarjeta-pres-title")
            assert not page.is_visible("#tarjeta-pres-dia"), "el día no debe pedirse en una tarjeta de pago"
            page.keyboard.press("Escape")
        finally:
            page.evaluate("""(cfg) => fetch('/api/presupuesto/config', {method: 'PUT',
                headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('jwt_token')},
                body: JSON.stringify({ingreso_base: cfg.ingreso_base, frecuencia: cfg.frecuencia, dia_pago: cfg.dia_pago, subdivision_tipo: 'ninguna'})})""", original)
        assert not errores, errores
    finally:
        ctx.close()


def test_presupuesto_muestra_totales_en_una_linea(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-presupuesto")
        # Las cuatro tarjetas KPI se quitaron: los totales van en una sola línea del encabezado de pagos
        assert page.locator(".pres-kpi-card").count() == 0
        page.wait_for_function("document.querySelector('#lbl-pres-totales').textContent.includes('Sin asignar')")
        texto = page.inner_text("#lbl-pres-totales")
        assert "Ingreso" in texto and "Asignado" in texto and "$" in texto, texto
        # El día de cobro se fija en cada pago: la configuración del ingreso ya no tiene ese campo
        assert page.locator("#pres-ingreso-dia").count() == 0
        assert not errores, errores
    finally:
        ctx.close()


def test_selector_de_periodo_cambia_las_tarjetas(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-movimientos")
        page.wait_for_selector("#cinta-movimientos .cinta-periodo")
        celdas = page.locator("#cinta-movimientos .cinta-periodo")
        assert celdas.count() == 4, celdas.count()
        # Se elige el periodo siguiente al en curso: un periodo futuro, sin movimientos reales todavía
        periodos = page.evaluate("""() => fetch('/api/resumen?moneda_base=MXN', {headers: {Authorization: 'Bearer ' + localStorage.getItem('jwt_token')}})
            .then(r => r.json()).then(d => d.periodos)""")
        k = next(i for i, p in enumerate(periodos) if p["en_curso"])
        periodo = periodos[k + 1]
        celdas.nth(1).click()
        assert page.inner_text("#lbl-rec-ingreso") == f"${periodo['ingresos']['total']:,.2f}", page.inner_text("#lbl-rec-ingreso")
        assert page.inner_text("#lbl-rec-fijos") == f"${periodo['gastos']['fijos']:,.2f}", page.inner_text("#lbl-rec-fijos")
        assert celdas.nth(1).get_attribute("aria-pressed") == "true", "la celda elegida queda presionada"
        assert "Aún no hay movimientos" in page.inner_text("#tabla-movimientos"), page.inner_text("#tabla-movimientos")
        assert page.locator("#fijos-periodos .fijos-periodo-elegido").count() == 1, "Fijos marca el mismo periodo"
        assert not errores, errores
    finally:
        ctx.close()


def test_fijos_y_msi_aparecen_con_su_avance(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-movimientos")
        page.click("#btn-subtab-fijos")
        page.wait_for_selector("#fijos-periodos .fijos-periodo")
        texto = page.inner_text("#fijos-periodos")
        assert "Laptop a 12 MSI" in texto, texto     # la compra a meses cae en su periodo
        assert "En curso" in texto, texto            # el periodo actual va marcado
        assert page.inner_text("#lbl-fijos-deuda") != "$0.00", "la deuda MSI debe mostrarse en Fijos y MSI"
        assert "2026-10-31" in texto, texto          # próxima nómina
        assert page.locator("#fijos-periodos .fijo-fila").count() >= 3, texto
        assert not errores, errores
    finally:
        ctx.close()


def test_cinta_periodos_pinta_libre_y_selecciona(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        page.wait_for_selector("#periodo-libre:not(:empty)")
        resultado = page.evaluate("""async () => {
            const { renderCintaPeriodos } = await import('/static/js/cinta_periodos.js');
            const raiz = document.createElement('div');
            document.body.appendChild(raiz);
            const periodos = [
                { inicio: '2026-10-15', fin: '2026-10-30', en_curso: true,
                  totales: { ingresos: 14000, fijos: 299, msi: 500, libre: 13201, saldo_al_cierre: 20000 } },
                { inicio: '2026-10-31', fin: '2026-11-14', en_curso: false,
                  totales: { ingresos: 0, fijos: 14000, msi: 0, libre: -14000, saldo_al_cierre: 5000 } },
            ];
            let elegido = -1;
            renderCintaPeriodos(raiz, periodos, { onSeleccionar: i => { elegido = i; } });
            const botones = [...raiz.querySelectorAll('.cinta-periodo')];
            botones[1].click();
            return {
                n: botones.length,
                actual: botones[0].classList.contains('cinta-periodo-actual'),
                libre: botones[0].querySelector('.cinta-periodo-libre').textContent,
                riesgo: botones[1].classList.contains('cinta-periodo-riesgo'),
                sinIngreso: !!botones[1].querySelector('.cinta-periodo-sin-ingreso'),
                elegido,
                dias: botones[0].style.getPropertyValue('--dias'),
                presionado: botones[0].getAttribute('aria-pressed'),
            };
        }""")
        assert resultado["n"] == 2, resultado
        assert resultado["actual"] and resultado["riesgo"] and resultado["sinIngreso"], resultado
        # Cada periodo muestra lo suyo (libre), no el saldo acumulado; y es riesgo si el periodo gasta más de lo que ingresa
        assert resultado["libre"] == "$13,201.00", resultado
        assert resultado["elegido"] == 1, resultado          # el clic avisa el índice elegido
        assert resultado["dias"] == "16" and resultado["presionado"] == "true", resultado
        assert not errores, errores
    finally:
        ctx.close()


def test_inicio_cinta_muestra_el_flujo_de_cada_periodo_y_el_hero_el_efectivo(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        page.wait_for_selector("#cinta-periodos .cinta-periodo")
        celdas = page.locator("#cinta-periodos .cinta-periodo")
        assert celdas.count() == 4, celdas.count()
        actual = page.locator("#cinta-periodos .cinta-periodo-actual")
        assert actual.count() == 1 and "En curso" in actual.inner_text(), actual.inner_text()
        # La cinta muestra lo que queda en cada periodo (ingresos - gastos - metas), sin arrastrar el efectivo ni los
        # periodos anteriores. El hero, "Libre hasta el cobro", sí cuenta el efectivo disponible: es otra pregunta.
        esperados = [f"${p['remanente']:,.2f}" for p in escenario.ESPERADO["periodos"]]
        mostrados = page.locator("#cinta-periodos .cinta-periodo-libre").all_inner_texts()[:len(esperados)]
        assert mostrados == esperados, (mostrados, esperados)
        assert page.inner_text("#periodo-libre") == f"${escenario.ESPERADO['libre_hasta_cobro']:,.2f}"
        assert "Libre del periodo" in actual.inner_text(), actual.inner_text()
        assert not errores, errores
    finally:
        ctx.close()


def test_admin_busca_filtra_y_pide_el_nombre_para_eliminar(navegador, base):
    admin, otro = "admprueba", "anaprueba"
    master_db.create_user_entry(admin, security.hash_password(CLAVE), role="admin", email=f"{admin}@test.local")
    master_db.create_user_entry(otro, security.hash_password(CLAVE), role="user", email=f"{otro}@test.local")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, admin, CLAVE)
        _ir(page, "tab-admin")
        page.wait_for_selector("#tabla-usuarios-admin tr[data-usuario]")
        filas = page.locator("#tabla-usuarios-admin tr[data-usuario]")
        assert filas.count() >= 3, filas.count()

        page.fill("#admin-buscar", "zz-no-existe")
        assert "Ningún usuario coincide" in page.inner_text("#tabla-usuarios-admin"), page.inner_text("#tabla-usuarios-admin")
        page.fill("#admin-buscar", otro)
        assert filas.count() == 1 and otro in filas.first.inner_text(), filas.count()
        page.fill("#admin-buscar", "")

        page.click(".admin-filtro:has-text('Suspendidos')")
        assert "Ningún usuario tiene este estado" in page.inner_text("#tabla-usuarios-admin")
        page.click(".admin-filtro:has-text('Todos')")
        assert filas.count() >= 3

        fila = page.locator("#tabla-usuarios-admin tr[data-usuario]", has_text=otro)
        fila.locator("button[aria-haspopup='menu']").click()
        page.locator(".admin-menu:not([hidden]) .admin-menu-peligro").click()
        page.wait_for_selector("#modal-eliminar-usuario:not(.hidden)")
        assert page.is_disabled("#btn-confirmar-eliminar"), "sin el nombre exacto no se puede eliminar"
        page.fill("#eliminar-usuario-confirmar", otro[:-1])
        assert page.is_disabled("#btn-confirmar-eliminar"), "un nombre parcial no habilita el botón"
        page.fill("#eliminar-usuario-confirmar", otro)
        assert page.is_enabled("#btn-confirmar-eliminar")
        # Con el nombre correcto pero una contraseña de administrador incorrecta no se borra y el cuadro sigue abierto
        page.fill("#eliminar-usuario-contrasena", "Incorrecta#123")
        page.click("#btn-confirmar-eliminar")
        page.wait_for_selector(".toast-error", timeout=10000)
        assert page.is_visible("#modal-eliminar-usuario"), "con contraseña incorrecta el cuadro sigue abierto"
        assert page.locator("#tabla-usuarios-admin tr[data-usuario]", has_text=otro).count() == 1, "contraseña incorrecta no borra"
        page.click("#btn-cancelar-eliminar")
        page.wait_for_selector("#modal-eliminar-usuario.hidden", state="attached")
        assert page.input_value("#eliminar-usuario-contrasena") == "", "al cerrar, la contraseña no se queda en el campo"
        assert page.locator("#tabla-usuarios-admin tr[data-usuario]", has_text=otro).count() == 1, "cancelar no borra"
        # Con la contraseña correcta sí se borra
        fila.locator("button[aria-haspopup='menu']").click()
        page.locator(".admin-menu:not([hidden]) .admin-menu-peligro").click()
        page.wait_for_selector("#modal-eliminar-usuario:not(.hidden)")
        assert page.input_value("#eliminar-usuario-contrasena") == "", "la contraseña no se queda escrita al reabrir"
        page.fill("#eliminar-usuario-confirmar", otro)
        page.fill("#eliminar-usuario-contrasena", CLAVE)  # la del administrador que está en sesión
        page.click("#btn-confirmar-eliminar")
        page.wait_for_selector("#modal-eliminar-usuario.hidden", state="attached")
        _esperar(page, f"!document.getElementById('tabla-usuarios-admin').innerText.includes({json.dumps(otro)})")
        assert not errores, errores
    finally:
        ctx.close()


def test_admin_apaga_y_enciende_el_asistente_de_una_cuenta_desde_el_menu(navegador, base):
    admin, otro = "admasist", "anaasist"
    master_db.create_user_entry(admin, security.hash_password(CLAVE), role="admin", email=f"{admin}@test.local")
    fila_otro = master_db.create_user_entry(otro, security.hash_password(CLAVE), role="user", email=f"{otro}@test.local")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, admin, CLAVE)
        _ir(page, "tab-admin")
        page.wait_for_selector("#tabla-usuarios-admin tr[data-usuario]")
        fila = page.locator("#tabla-usuarios-admin tr[data-usuario]", has_text=otro)
        assert fila.locator(".admin-sin-asistente").count() == 0, "con el asistente encendido no hay marca"
        fila.locator("button[aria-haspopup='menu']").click()
        page.locator(".admin-menu:not([hidden]) .admin-menu-item", has_text="Desactivar asistente").click()
        fila.locator(".admin-sin-asistente").wait_for(timeout=10000)
        assert master_db.get_user_by_id(fila_otro["id"])["asistente_activo"] == 0
        fila.locator("button[aria-haspopup='menu']").click()
        page.locator(".admin-menu:not([hidden]) .admin-menu-item", has_text="Activar asistente").click()
        _esperar(page, "document.querySelectorAll('.admin-sin-asistente').length === 0")
        assert master_db.get_user_by_id(fila_otro["id"])["asistente_activo"] == 1
        # Tu propia fila no lleva el menú: el asistente de tu cuenta no lo apagas tú aquí
        propia = page.locator("#tabla-usuarios-admin tr[data-usuario]", has_text=admin)
        assert propia.locator("button[aria-haspopup='menu']").count() == 0
        assert not errores, errores
    finally:
        ctx.close()


def test_tema_y_modo_discrecion_cambian_sin_perder_datos(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        page.wait_for_selector("#periodo-libre:not(:empty)")
        tema_antes = page.get_attribute("html", "data-theme")
        page.click("#btn-user-menu-trigger")
        page.click("#btn-modo")
        assert page.get_attribute("html", "data-theme") != tema_antes
        page.click("#btn-toggle-privacidad")
        assert page.evaluate("document.body.classList.contains('privacy-mode')")
        page.click("#btn-toggle-privacidad")
        page.keyboard.press("Escape")
        assert page.inner_text("#periodo-libre") == "$8,103.00", "cambiar de tema no debe alterar cifras"
        assert not errores, errores
    finally:
        ctx.close()


def test_movimiento_crear_editar_y_borrar(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-movimientos")
        _abrir_formulario(page, "movimiento")
        page.fill("#fecha", "2026-10-21")
        page.select_option("#tipo", "gasto")
        page.fill("#monto", "123.45")
        page.fill("#descripcion", "e2e-cafe")
        page.click("#transaccion-form [type=submit]")
        fila = page.locator("#tabla-movimientos tr", has_text="e2e-cafe")
        fila.wait_for()
        assert "$123.45" in fila.inner_text()

        fila.locator(".btn-editar").click()
        page.wait_for_selector("#modal-editar-transaccion:not(.hidden)")
        page.fill("#modal-edit-trans-monto", "200")
        page.click("#form-editar-transaccion [type=submit]")
        page.wait_for_selector("#modal-editar-transaccion.hidden", state="attached")
        _esperar(page, "[...document.querySelectorAll('#tabla-movimientos tr')].some(tr => tr.textContent.includes('e2e-cafe') && tr.textContent.includes('$200.00'))")

        # La confirmación es un modal propio, no el diálogo del navegador: cancelar conserva la fila y aceptar la borra
        page.on("dialog", lambda d: (errores.append(f"diálogo nativo: {d.message}"), d.dismiss()))
        boton = page.locator("#tabla-movimientos tr", has_text="e2e-cafe").locator(".btn-eliminar")
        boton.click()
        assert _responder_quitar(page, None)["titulo"] == "Quitar movimiento"
        assert page.locator("#tabla-movimientos tr", has_text="e2e-cafe").count() == 1
        boton.click()
        _responder_quitar(page, "eliminar")
        _esperar(page, "![...document.querySelectorAll('#tabla-movimientos tr')].some(tr => tr.textContent.includes('e2e-cafe'))")
        assert not errores, errores
    finally:
        ctx.close()


def _movimiento_en_meta(page, nombre, cantidad, boton):
    """Escribe el monto y pulsa el botón de la meta en una sola instrucción del navegador.
    La lista se repinta tras cada operación y borraría un monto escrito en dos pasos."""
    for _ in range(20):
        hecho = page.evaluate("""([nombre, cantidad, boton]) => {
            const card = [...document.querySelectorAll('.card-apartado')].find(c => c.textContent.includes(nombre));
            if (!card) return false;
            const input = card.querySelector('input[type=number]');
            const btn = [...card.querySelectorAll('button')].find(b => b.textContent.trim() === boton);
            if (!input || !btn || btn.disabled) return false;
            input.value = cantidad;
            btn.click();
            return true;
        }""", [nombre, cantidad, boton])
        if hecho:
            return
        page.wait_for_timeout(200)
    raise AssertionError(f"no se pudo usar el botón {boton} de {nombre}")


def test_meta_depositar_y_retirar(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-apartados")
        page.locator(".card-apartado", has_text="Fondo de emergencia").wait_for()
        _movimiento_en_meta(page, "Fondo de emergencia", "500", "Depositar")
        _esperar(page, "[...document.querySelectorAll('.card-apartado')].some(c => c.textContent.includes('Fondo de emergencia') && c.textContent.includes('$2,500.00'))")
        _movimiento_en_meta(page, "Fondo de emergencia", "500", "Retirar")
        page.wait_for_timeout(1500)
        page.wait_for_timeout(1500)
        _esperar(page, "[...document.querySelectorAll('.card-apartado')].some(c => c.textContent.includes('Fondo de emergencia') && c.textContent.includes('$2,000.00'))")
        assert not errores, errores
    finally:
        ctx.close()


def test_fallo_de_red_avisa_al_usuario(navegador, base):
    ctx, page, _errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _login(page, base)
        page.route("**/api/**", lambda route: route.abort())
        page.evaluate("document.dispatchEvent(new Event('recargar'))")
        page.wait_for_selector(".toast-error", timeout=10000)
        texto = page.inner_text(".toast-error")
        assert "Sin conexión" in texto, texto
        page.unroute("**/api/**")
    finally:
        ctx.close()


def test_403_de_permisos_no_cierra_la_sesion(navegador, base):
    ctx, page, _errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _login(page, base)
        page.route("**/api/resumen**", lambda route: route.fulfill(
            status=403, content_type="application/json",
            body='{"detail": "Acceso restringido exclusivamente a administradores."}'))
        page.evaluate("document.dispatchEvent(new Event('recargar'))")
        page.wait_for_timeout(1500)
        assert page.is_visible("#vista-app"), "un 403 de permisos no debe cerrar la sesión"
        page.unroute("**/api/resumen**")
    finally:
        ctx.close()


def test_403_de_cuenta_desactivada_cierra_la_sesion(navegador, base):
    ctx, page, _errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _login(page, base)
        page.route("**/api/**", lambda route: route.fulfill(
            status=403, content_type="application/json",
            body='{"detail": "Tu cuenta ha sido desactivada por el administrador."}'))
        page.evaluate("document.dispatchEvent(new Event('recargar'))")
        page.wait_for_selector("#form-login", state="visible", timeout=10000)
        page.unroute("**/api/**")
    finally:
        ctx.close()


def test_cerrar_sesion_con_una_carga_en_curso_no_muestra_sesion_expirada(navegador, base):
    """Si se cierra la sesión mientras los movimientos siguen cargando, la petición que sale después ya no lleva token y
    recibe 401: eso no es una sesión vencida, y no debe aparecer en la pantalla de entrada como si lo fuera."""
    ctx, page, _errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _login(page, base)

        def _retener(route):
            page.wait_for_timeout(2500)  # la lista de movimientos tarda: da tiempo a cerrar la sesión antes de que siga
            route.continue_()

        page.route("**/api/transacciones", _retener)
        page.evaluate("document.dispatchEvent(new Event('recargar'))")
        page.evaluate("document.querySelector('.dropdown-btn-logout').click()")
        _responder_confirmacion(page)
        page.wait_for_selector("#form-login", state="visible", timeout=10000)
        page.wait_for_timeout(4000)  # llega la lista retenida y sale la petición de cancelados, ya sin token
        page.unroute("**/api/transacciones")
        assert page.is_hidden("#login-error") and not page.inner_text("#login-error").strip(), \
            f"el login muestra un aviso falso: «{page.inner_text('#login-error')}»"
    finally:
        ctx.close()


def test_recargar_con_un_token_vencido_pide_entrar_y_lo_explica(navegador, base):
    ctx, page, _errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        page.goto(base + "/login")
        page.evaluate("localStorage.setItem('jwt_token', 'token.vencido.o.falso')")
        page.goto(base + "/")
        page.wait_for_selector("#login-username", state="visible", timeout=15000)
        assert "expirado" in page.inner_text("#login-error"), page.inner_text("#login-error")
        assert page.evaluate("localStorage.getItem('jwt_token')") is None, "el token vencido no se queda guardado"
    finally:
        ctx.close()


def test_un_fallo_del_servidor_al_abrir_la_pagina_no_tira_el_token(navegador, base):
    ctx, page, _errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _login(page, base)
        token = page.evaluate("localStorage.getItem('jwt_token')")
        page.route("**/auth/me", lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail": "reiniciando"}'))
        page.goto(base + "/")
        page.wait_for_selector("#login-username", state="visible", timeout=15000)
        assert "verificar la sesión" in page.inner_text("#login-error"), page.inner_text("#login-error")
        assert page.evaluate("localStorage.getItem('jwt_token')") == token, "un 503 no debe borrar un token válido"
        page.unroute("**/auth/me")
    finally:
        ctx.close()


def test_respuesta_tardia_de_la_sesion_anterior_no_cierra_la_nueva(navegador, base):
    """Cierras sesión y entras de nuevo antes de que llegue la respuesta (un 401) de una carga de la sesión anterior."""
    ctx, page, _errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _login(page, base)

        retenida = {"primera": True, "liberar": False}  # solo la carga de la sesión anterior se retiene y responde 401

        def _tardia(route):
            if not retenida["primera"]:
                route.continue_()
                return
            retenida["primera"] = False
            while not retenida["liberar"]:  # espera a que la sesión nueva ya esté dentro: sin tiempos fijos
                page.wait_for_timeout(100)
            route.fulfill(status=401, content_type="application/json", body='{"detail": "token de la sesión anterior"}')

        page.route("**/api/transacciones", _tardia)
        page.evaluate("document.dispatchEvent(new Event('recargar'))")
        page.evaluate("document.querySelector('.dropdown-btn-logout').click()")
        _responder_confirmacion(page)
        page.wait_for_selector("#form-login", state="visible", timeout=10000)
        page.fill("#login-username", USUARIO)
        page.fill("#login-password", CLAVE)
        page.click("#form-login [type=submit]")
        page.wait_for_selector("#vista-app:not(.hidden)", timeout=15000)
        retenida["liberar"] = True  # ahora llega el 401 tardío de la sesión anterior
        page.wait_for_timeout(2500)
        page.unroute("**/api/transacciones")
        assert page.is_visible("#vista-app"), "el 401 tardío de la sesión anterior cerró la sesión nueva"
    finally:
        ctx.close()


def test_cambiar_la_contrasena_con_una_carga_en_vuelo_no_cierra_la_sesion_nueva(navegador, base):
    """El cambio de contraseña rota el token: la carga que salió con el viejo recibe 401, y no es una sesión vencida."""
    usuario, clave = _usuario_nuevo("rota")
    nueva = "Nueva#Clave2026"
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        retenida = {"primera": True, "liberar": False}

        def _tardia(route):
            if not retenida["primera"]:
                route.continue_()
                return
            retenida["primera"] = False
            while not retenida["liberar"]:
                page.wait_for_timeout(100)
            route.fulfill(status=401, content_type="application/json", body='{"detail": "token anterior a la rotación"}')

        page.route("**/api/transacciones", _tardia)
        page.evaluate("document.dispatchEvent(new Event('recargar'))")
        _abrir_mi_perfil(page)
        page.fill("#edit-user-password", nueva)
        page.fill("#edit-user-current-password", clave)
        page.click("#form-editar-usuario [type=submit]")
        page.wait_for_selector("#modal-editar-usuario.hidden", state="attached", timeout=15000)
        retenida["liberar"] = True  # llega el 401 de la carga que salió con el token anterior
        page.wait_for_timeout(2500)
        page.unroute("**/api/transacciones")
        assert page.is_visible("#vista-app"), "el 401 de una carga con el token anterior cerró la sesión recién renovada"
    finally:
        ctx.close()


def test_menu_de_usuario_sin_eliminar_cuenta_y_rol_solo_para_admin(navegador, base):
    ctx, page, _errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _login(page, base)
        page.click("#btn-user-menu-trigger")
        page.wait_for_selector("#user-dropdown-menu.show")
        assert page.locator("#btn-dropdown-eliminar-cuenta").count() == 0, "eliminar cuenta vive solo en Mi perfil"
        assert page.is_hidden("#lbl-user-role"), "el rol solo se muestra a administradores"
        assert page.inner_text("#btn-modo").strip() in ("Modo oscuro", "Modo claro"), page.inner_text("#btn-modo")
        page.keyboard.press("Escape")
    finally:
        ctx.close()


def test_menu_usuario_teclado_y_hoja_movil(navegador, base):
    ctx, page, _errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _login(page, base)
        page.click("#btn-user-menu-trigger")
        page.wait_for_selector("#user-dropdown-menu.show")
        primero = page.evaluate("document.activeElement.textContent.trim()")
        page.keyboard.press("ArrowDown")
        assert page.evaluate("document.activeElement.getAttribute('role')") == "menuitem"
        assert page.evaluate("document.activeElement.textContent.trim()") != primero, "las flechas deben cambiar de opción"
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        assert page.evaluate("document.activeElement.id") == "btn-user-menu-trigger", "Escape devuelve el foco al botón"
    finally:
        ctx.close()
    ctx, page, _errores = _contexto(navegador, VIEWPORTS["movil"])
    try:
        _login(page, base)
        page.click("#btn-user-menu-trigger")
        page.wait_for_selector("#user-dropdown-menu.show")
        assert page.evaluate("document.getElementById('user-dropdown-menu').parentElement === document.body"), "en móvil el menú es una hoja fuera de la barra"
    finally:
        ctx.close()


def _usuario_nuevo(sufijo):
    """Usuario propio de una prueba que modifica datos: el escenario compartido conserva sus cifras fijas."""
    nombre, clave = f"fr_{sufijo}", "Passw0rd#Nuevo1"
    master_db.create_user_entry(nombre, security.hash_password(clave), email=f"{nombre}@test.local")
    return nombre, clave


def _api(base, usuario, clave):
    """Cliente HTTP ya autenticado, para preparar o comprobar datos sin pasar por la pantalla."""
    body = json.dumps({"username": usuario, "password": clave}).encode()
    req = urllib.request.Request(base + "/auth/login", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        token = json.loads(r.read())["access_token"]
    return _ClienteHttp(base, token)


def _abrir_mi_perfil(page):
    page.click("#btn-user-menu-trigger")
    page.wait_for_selector("#user-dropdown-menu.show")
    page.locator("#user-dropdown-menu button", has_text="Mi perfil").click()
    page.wait_for_selector("#modal-editar-usuario:not(.hidden)")


def test_inversion_nueva_aparece_en_el_portafolio(navegador, base):
    usuario, clave = _usuario_nuevo("inv")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        _ir(page, "tab-inversiones")
        _abrir_formulario(page, "posicion")
        page.fill("#inv-ticker", "VOO")
        page.fill("#inv-cantidad", "2")
        page.fill("#inv-precio-promedio", "100")
        page.fill("#inv-precio-actual", "110")
        page.click("#form-inversion [type=submit]")
        _esperar(page, "[...document.querySelectorAll('#tabla-inversiones tr')].some(tr => tr.textContent.includes('VOO'))")
        assert not errores, errores
    finally:
        ctx.close()


def test_fijo_quincenal_y_gasto_con_su_ingreso(navegador, base):
    usuario, clave = _usuario_nuevo("rec")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        _ir(page, "tab-movimientos")
        page.evaluate("document.getElementById('btn-subtab-fijos').click()")  # clic por JS: la barra superior tapa el botón
        _abrir_formulario(page, "fijo")
        page.evaluate("document.querySelector('input[name=rec-modo][value=ingreso]').click()")
        page.fill("#rec-desc", "Nomina")
        page.fill("#rec-monto", "14000")
        page.select_option("#rec-frecuencia", "quincenal")
        page.fill("#rec-fecha", "2026-10-01")
        page.select_option("#rec-categoria", index=0)
        page.click("#btn-guardar-recurrente")
        _esperar(page, "document.getElementById('fijos-periodos').textContent.includes('Nomina')")

        _abrir_formulario(page, "fijo")
        page.evaluate("document.querySelector('input[name=rec-modo][value=gasto]').click()")
        page.fill("#rec-desc", "Gimnasio")
        page.fill("#rec-monto", "450")
        page.select_option("#rec-frecuencia", "quincenal")
        page.fill("#rec-fecha", "2026-10-01")
        page.select_option("#rec-categoria", index=0)
        page.click("#btn-guardar-recurrente")
        _esperar(page, "document.getElementById('fijos-periodos').textContent.includes('Gimnasio')")
        assert not errores, errores
    finally:
        ctx.close()


def test_respaldo_desde_el_menu_se_descarga_y_se_restaura(navegador, base):
    usuario, clave = _usuario_nuevo("resp")
    api = _api(base, usuario, clave)
    r = api.post("/api/transacciones", json={"fecha": "2026-10-01", "monto": 1000, "tipo": "ingreso", "categoria_id": 12, "descripcion": "sueldo"})
    assert r.status_code in (200, 201), r.text
    r = api.post("/api/transacciones", json={"fecha": "2026-10-05", "monto": 100, "tipo": "gasto", "categoria_id": 2, "descripcion": "antes-del-respaldo"})
    assert r.status_code in (200, 201), r.text
    carpeta = tempfile.mkdtemp(dir=TMP)
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        page.click("#btn-user-menu-trigger")
        page.wait_for_selector("#user-dropdown-menu.show")
        with page.expect_download() as descarga:
            page.click(".btn-backup")
        copia = os.path.join(carpeta, "copia.db")
        descarga.value.save_as(copia)
        with open(copia, "rb") as f:
            assert f.read(16) == b"SQLite format 3\x00", "la descarga debe ser una base SQLite"

        r = api.post("/api/transacciones", json={"fecha": "2026-10-06", "monto": 50, "tipo": "gasto", "categoria_id": 2, "descripcion": "despues-del-respaldo"})
        assert r.status_code in (200, 201), r.text
        page.click("#btn-user-menu-trigger")
        page.wait_for_selector("#user-dropdown-menu.show")
        with page.expect_file_chooser() as eleccion:
            page.click(".btn-restore")
            _responder_confirmacion(page)   # el selector de archivo se abre al aceptar
        eleccion.value.set_files(copia)

        descripciones = lambda: [t["descripcion"] for t in api.get("/api/transacciones").json()]
        for _ in range(100):
            if "despues-del-respaldo" not in descripciones():
                break
            page.wait_for_timeout(100)
        assert "despues-del-respaldo" not in descripciones(), "la restauración debe quitar lo hecho después del respaldo"
        assert "antes-del-respaldo" in descripciones()

        # Un respaldo de una versión más nueva se rechaza con su motivo y no toca los datos.
        import sqlite3 as _sqlite
        futura = os.path.join(carpeta, "futura.db")
        shutil.copy(copia, futura)
        con = _sqlite.connect(futura)
        con.execute("PRAGMA user_version = 99")
        con.commit()
        con.close()
        page.click("#btn-user-menu-trigger")
        page.wait_for_selector("#user-dropdown-menu.show")
        with page.expect_file_chooser() as eleccion:
            page.click(".btn-restore")
            _responder_confirmacion(page)   # el selector de archivo se abre al aceptar
        eleccion.value.set_files(futura)
        page.wait_for_selector(".toast-error", timeout=10000)
        assert "más nueva" in page.inner_text(".toast-error"), page.inner_text(".toast-error")
        assert "antes-del-respaldo" in descripciones()
        assert not errores, errores
    finally:
        ctx.close()
        shutil.rmtree(carpeta, ignore_errors=True)


def test_mi_perfil_exige_contrasena_actual_para_cambiarla(navegador, base):
    usuario, clave = _usuario_nuevo("pwd")
    nueva = "Nueva#Clave2026"
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        _abrir_mi_perfil(page)
        page.fill("#edit-user-password", nueva)
        page.click("#form-editar-usuario [type=submit]")
        page.wait_for_selector(".toast-error", timeout=10000)
        assert "contraseña actual" in page.inner_text(".toast-error"), page.inner_text(".toast-error")
        page.wait_for_timeout(3500)  # el toast tapa al siguiente: esperamos a que se vaya

        page.fill("#edit-user-current-password", "Incorrecta#1")
        page.click("#form-editar-usuario [type=submit]")
        page.wait_for_selector(".toast-error", timeout=10000)
        assert "no es correcta" in page.inner_text(".toast-error"), page.inner_text(".toast-error")
        page.wait_for_timeout(3500)

        page.fill("#edit-user-current-password", clave)
        page.click("#form-editar-usuario [type=submit]")
        page.wait_for_selector("#modal-editar-usuario.hidden", state="attached", timeout=10000)
        assert not errores, errores
    finally:
        ctx.close()
    _api(base, usuario, nueva)  # la nueva contraseña entra
    try:
        _api(base, usuario, clave)
        raise AssertionError("la contraseña anterior debe dejar de funcionar")
    except urllib.error.HTTPError as e:
        assert e.code == 401, e.code


def test_login_recorta_igual_que_el_alta_y_una_clave_larga_entra(navegador, base):
    """El alta recorta la contraseña a 72; el login debe recortar igual, o quien la creó con una más larga nunca
    puede entrar (el login manda las 83 y la API rechaza más de 72)."""
    nombre = "fr_larga"
    larga = "Larga#" + "x" * 77  # 83 caracteres; el campo del alta solo guarda 72
    master_db.create_user_entry(nombre, security.hash_password(larga[:72]), email=f"{nombre}@test.local")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        page.goto(base + "/login")
        assert page.get_attribute("#login-username", "maxlength") == "50"
        assert page.get_attribute("#login-password", "maxlength") == "72"
        # Las demás puertas de credenciales, con los mismos límites que su API
        assert page.get_attribute("#edit-user-username", "maxlength") == "50"
        assert page.get_attribute("#recuperar-identifier", "maxlength") == "120"
        _entrar(page, base, nombre, larga)  # teclea las 83: el campo se queda con 72 y entra
        assert not errores, errores
    finally:
        ctx.close()


def test_eliminar_cuenta_exige_escribir_el_usuario(navegador, base):
    usuario, clave = _usuario_nuevo("del")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        _abrir_mi_perfil(page)
        page.wait_for_selector("#danger-zone-eliminar-cuenta:not(.hidden)")
        page.click("#danger-zone-eliminar-cuenta summary")
        boton = "#btn-eliminar-cuenta-confirmar"
        assert page.is_disabled(boton), "sin escribir el usuario no se puede eliminar"
        page.fill("#eliminar-cuenta-confirmar", usuario + "x")
        assert page.is_disabled(boton), "un nombre parecido no basta"
        page.fill("#eliminar-cuenta-confirmar", usuario)
        _esperar(page, "!document.getElementById('btn-eliminar-cuenta-confirmar').disabled")
        page.click(boton)  # sin contraseña no se borra: avisa y la cuenta sigue
        page.wait_for_selector(".toast-error", timeout=5000)
        assert page.is_visible("#danger-zone-eliminar-cuenta"), "sin contraseña la cuenta no se elimina"
        page.fill("#eliminar-cuenta-contrasena", clave + "x")
        page.click(boton)  # contraseña incorrecta: el servidor lo rechaza
        _esperar(page, "document.querySelectorAll('.toast-error').length >= 1")
        assert page.is_visible("#danger-zone-eliminar-cuenta"), "con una contraseña incorrecta la cuenta no se elimina"
        page.fill("#eliminar-cuenta-contrasena", clave)
        page.click(boton)
        page.wait_for_selector("#form-login", state="visible", timeout=15000)
        assert not errores, errores
    finally:
        ctx.close()
    try:
        _api(base, usuario, clave)
        raise AssertionError("la cuenta eliminada no debe poder entrar")
    except urllib.error.HTTPError as e:
        assert e.code == 401, e.code


def test_calculadora_rf_calcula_y_bloquea_tasa_fuera_de_rango(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-calculadora-rf")
        page.fill("#calc-rf-monto", "10000")
        page.fill("#calc-rf-tasa", "10")
        page.click("#btn-calcular-rf")
        _esperar(page, "document.getElementById('calc-rf-res-anio').textContent.includes('1,000.00')")
        _esperar(page, "document.getElementById('calc-rf-res-dia').textContent.includes('2.74')")
        page.fill("#calc-rf-tasa", "150")
        assert page.evaluate("document.getElementById('calc-rf-tasa').validity.rangeOverflow"), "una tasa de 150% no es válida"
        assert not errores, errores
    finally:
        ctx.close()


def test_inicio_sin_movimientos_invita_a_empezar_y_se_oculta_al_tener_datos(navegador, base):
    usuario, clave = _usuario_nuevo("vacio")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        page.wait_for_selector("#inicio-vacio:not(.hidden)", timeout=10000)
        page.click("#btn-inicio-vacio")
        page.wait_for_selector("#tab-movimientos.active")
        _api(base, usuario, clave).post("/api/transacciones", json={"fecha": "2026-10-01", "monto": 500, "tipo": "ingreso", "categoria_id": 12, "descripcion": "primer-ingreso"})
        page.evaluate("document.dispatchEvent(new Event('recargar'))")  # recarga los datos sin salir de la sesión
        _esperar(page, "document.getElementById('inicio-vacio').classList.contains('hidden')")
        assert not errores, errores
    finally:
        ctx.close()


def test_importe_grande_no_se_parte_en_movil(navegador, base):
    usuario, clave = _usuario_nuevo("importe")
    _api(base, usuario, clave).post("/api/transacciones", json={"fecha": "2026-10-01", "monto": 5000000, "tipo": "ingreso", "categoria_id": 12, "descripcion": "nomina-grande"})
    ctx, page, errores = _contexto(navegador, VIEWPORTS["movil"])
    try:
        _entrar(page, base, usuario, clave)
        _ir(page, "tab-movimientos")
        medidas = page.evaluate("""() => { const p = document.querySelector('.card.ingresos p');
            const cs = getComputedStyle(p);
            return {alto: p.getBoundingClientRect().height, fuente: parseFloat(cs.fontSize),
                    desborda: p.scrollWidth > p.parentElement.clientWidth + 1}; }""")
        assert medidas["alto"] < medidas["fuente"] * 1.6, f"el importe ocupa dos líneas: {medidas}"
        assert not medidas["desborda"], medidas
        assert not errores, errores
    finally:
        ctx.close()


def test_titulo_de_la_barra_no_se_corta_en_movil(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base, VIEWPORTS["movil"])
    try:
        for tab in ("tab-movimientos", "tab-presupuesto", "tab-inversiones"):
            _ir(page, tab)
            cortado = page.evaluate("() => { const t = document.getElementById('topbar-section-title'); return t.scrollWidth > t.clientWidth + 1; }")
            assert not cortado, f"el título de {tab} se corta en el móvil"
        assert not errores, errores
    finally:
        ctx.close()


def test_componentes_cero_en_tinta_neutra_y_signo_menos_tipografico(navegador, base):
    """Un importe en cero no se pinta en rojo; el historial usa el menos tipográfico pegado a la cifra;
    un panel dentro de otro panel no lleva borde propio."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-movimientos")
        page.wait_for_selector("#lbl-rec-gastos-normales:not(:empty)")
        color_cero = page.evaluate("getComputedStyle(document.getElementById('lbl-rec-gastos-normales')).color")
        color_fijos = page.evaluate("getComputedStyle(document.getElementById('lbl-rec-fijos')).color")
        assert color_cero != color_fijos, f"el cero se ve igual que un gasto: {color_cero}"
        assert page.evaluate("document.getElementById('lbl-rec-gastos-normales').closest('.card').classList.contains('es-cero')")
        anidados = page.evaluate("[...document.querySelectorAll('.panel .panel')].filter(p => getComputedStyle(p).borderTopWidth !== '0px').length")
        assert anidados == 0, f"{anidados} paneles anidados con borde propio"
        assert not errores, errores
    finally:
        ctx.close()

def test_moneda_una_vez_y_pestanas_internas_cortas_en_movil(navegador, base):
    """La moneda va una vez, en la etiqueta del bloque (no pegada a cada cifra). En celular, las pestañas
    internas usan textos cortos y caben en una línea."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        page.wait_for_selector("#lbl-dash-patrimonio:not(:empty)")
        assert not page.inner_text("#lbl-dash-patrimonio").endswith("MXN"), page.inner_text("#lbl-dash-patrimonio")
        assert "MXN" in page.inner_text(".networth-header small"), page.inner_text(".networth-header small")
        assert not errores, errores
    finally:
        ctx.close()

    ctx, page, errores = _flujo_escenario(navegador, base, VIEWPORTS["movil"])
    try:
        _ir(page, "tab-movimientos")
        visibles = page.evaluate("""() => [...document.querySelectorAll('.subtab-btn')].map(b => {
            const corto = b.querySelector('.sub-corto'), largo = b.querySelector('.sub-largo');
            return [getComputedStyle(corto).display !== 'none', getComputedStyle(largo).display !== 'none', b.getBoundingClientRect().height];
        })""")
        assert all(c and not l for c, l, _ in visibles), f"en móvil deben verse los textos cortos: {visibles}"
        assert all(h <= 48 for _, _, h in visibles), f"una pestaña interna ocupa dos líneas: {visibles}"
        assert not errores, errores
    finally:
        ctx.close()

def test_resumen_leyenda_hero_plano_y_dona_a_todo_el_ancho(navegador, base):
    """El Resumen explica los colores de la cinta, el hero no lleva degradado y la dona
    de un solo bloque ocupa el ancho del contenido."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        page.wait_for_selector("#cinta-periodos .cinta-periodo")
        assert page.locator("#cinta-leyenda, .cinta-leyenda li").count() == 3, "la leyenda de la cinta debe tener tres elementos"
        fondo = page.evaluate("getComputedStyle(document.getElementById('periodo-hero')).backgroundImage")
        assert fondo == "none", f"el hero aún tiene degradado: {fondo[:60]}"
        ancho = page.evaluate("document.querySelector('.dashboard-grid > .panel:only-child')?.getBoundingClientRect().width || 0")
        contenido = page.evaluate("document.querySelector('#tab-dashboard').getBoundingClientRect().width")
        assert ancho > contenido * 0.9, f"la dona no ocupa el ancho: {ancho} de {contenido}"
        assert not errores, errores
    finally:
        ctx.close()

def test_movimientos_en_movil_sin_deslizar_y_con_papelera(navegador, base):
    """En celular, cada movimiento es una fila que cabe en el ancho (sin deslizar la tabla), el monto
    queda a la derecha, la acción de borrar es una papelera con nombre accesible y los textos del
    formulario son los nuevos."""
    ctx, page, errores = _flujo_escenario(navegador, base, VIEWPORTS["movil"])
    try:
        _ir(page, "tab-movimientos")
        page.wait_for_selector("#tabla-movimientos tr .btn-eliminar")
        desbordada = page.evaluate("(() => { const t = document.querySelector('#tabla-movimientos').closest('.table-responsive'); return t.scrollWidth > t.clientWidth + 1; })()")
        assert not desbordada, "la lista de movimientos se desliza de lado en el celular"
        x_fecha, x_monto = page.evaluate("""() => { const f = document.querySelector('#tabla-movimientos td.td-fecha').getBoundingClientRect();
            const m = document.querySelector('#tabla-movimientos td.td-monto').getBoundingClientRect(); return [f.left, m.left]; }""")
        assert x_monto > x_fecha, "el monto debe ir a la derecha de la fecha"
        assert page.get_attribute("#tabla-movimientos .btn-eliminar", "aria-label") == "Quitar movimiento"
        assert page.inner_text("#cinta-movimientos-titulo") == "Periodo"
        _abrir_formulario(page, "movimiento")
        assert page.inner_text("#modal-nuevo-movimiento-titulo").strip().endswith("Registrar movimiento")
        assert not errores, errores
    finally:
        ctx.close()

def test_fijos_msi_con_barra_y_filtro_de_mes_con_marcador(navegador, base):
    """Las compras a meses muestran su avance con una barra (3 de 12 en el escenario); el filtro de mes
    vacío dice «Mes» y deja de decirlo al elegir un mes."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-movimientos")
        assert page.get_attribute("#filtro-mes", "aria-label") == "Filtrar por mes"
        assert page.evaluate("document.getElementById('filtro-mes').parentElement.dataset.vacio") == "true"
        page.fill("#filtro-mes", "2026-10")
        assert page.evaluate("document.getElementById('filtro-mes').parentElement.dataset.vacio") == "false"
        page.click("#btn-subtab-fijos")
        page.wait_for_selector("progress.msi-avance")
        avance = page.evaluate("[...document.querySelectorAll('progress.msi-avance')].map(p => [p.value, p.max])")
        assert [3, 12] in avance, f"la barra de la compra a 12 meses debe marcar 3 de 12: {avance}"
        assert not errores, errores
    finally:
        ctx.close()

def test_presupuesto_panel_sin_borde_y_textos_sencillos(navegador, base):
    """El presupuesto: la página no lleva borde propio, la frecuencia se lee sin paréntesis y la
    descripción no usa símbolos sueltos."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-presupuesto")
        page.wait_for_selector(".pres-panel")
        borde = page.evaluate("getComputedStyle(document.querySelector('.pres-panel')).borderTopWidth")
        assert borde == "0px", f"el panel de presupuesto no debe llevar borde propio: {borde}"
        assert "⇄" not in page.inner_text(".pres-header"), "la descripción no debe usar el símbolo ⇄"
        opciones = page.evaluate("[...document.querySelectorAll('#pres-frecuencia option')].map(o => o.textContent.trim())")
        assert opciones == ["Quincenal", "Mensual", "Semanal"], opciones
        assert not errores, errores
    finally:
        ctx.close()

def test_metas_con_texto_meta_eliminar_visible_y_avance_minimo(navegador, base):
    """Metas: el título y el botón usan «meta», la acción de eliminar dice «Eliminar» (no una ✕),
    el total ahorrado no es una cápsula y una meta con poco avance se ve como una franja."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-apartados")
        page.wait_for_selector(".card-apartado")
        assert "Metas de ahorro" in page.inner_text("#tab-apartados"), "el título debe decir «Metas de ahorro»"
        assert page.locator("#tab-apartados button", has_text="Crear apartado").count() == 0, "el botón no debe decir «Crear apartado»"
        assert page.inner_text(".btn-eliminar-apartado").strip() == "Eliminar"
        borde = page.evaluate("getComputedStyle(document.querySelector('.apartado-total-pill')).borderTopWidth")
        assert borde == "0px", f"el total ahorrado no debe ser una cápsula con borde: {borde}"
        ancho = page.evaluate("[...document.querySelectorAll('.progress-bar-fill')].map(f => f.getBoundingClientRect().width)")
        assert min(ancho) >= 4, f"una barra de avance mínima debe verse: {ancho}"
        assert not errores, errores
    finally:
        ctx.close()

def test_inversiones_moneda_una_vez_sin_tasa_repetida_y_boton_secundario(navegador, base):
    """Inversiones: el título es «Portafolio», la tasa no se repite en el bloque, las cifras no llevan
    la moneda pegada (va en la etiqueta), el botón de cotizaciones es secundario y en celular la
    etiqueta «MXN» no se parte."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-inversiones")
        page.wait_for_selector("#lbl-inv-invertido:not(:empty)")
        assert page.inner_text(".inversiones-header-title-wrap h2").strip() == "Portafolio"
        assert page.locator("#badge-tasa-inversiones").count() == 0, "la tasa no debe repetirse en Inversiones"
        assert not page.inner_text("#lbl-inv-valor").endswith("MXN"), page.inner_text("#lbl-inv-valor")
        fondo = page.evaluate("getComputedStyle(document.getElementById('btn-sync-precios')).backgroundColor")
        verde = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--primary-subtle')")
        assert fondo != verde, "Actualizar cotizaciones no debe llevar el tinte verde"
        assert not errores, errores
    finally:
        ctx.close()

    ctx, page, errores = _flujo_escenario(navegador, base, VIEWPORTS["movil"])
    try:
        _ir(page, "tab-inversiones")
        page.wait_for_selector(".cards-inversiones .inv-card")
        partida = page.evaluate("""() => [...document.querySelectorAll('.inv-card .lbl-moneda-base')].map(s => {
            const r = s.getBoundingClientRect(); return Math.round(r.height); })""")
        assert all(alto < 30 for alto in partida), f"la moneda se parte en dos líneas: {partida}"
        assert not errores, errores
    finally:
        ctx.close()

def test_calculadora_rf_textos_sencillos_y_sin_tarjetas_con_ícono(navegador, base):
    """La calculadora: sin indicador «Modo interactivo», tarjetas de resultado con su periodo como título
    (sin ícono ni insignia), base de días con texto corto y ayuda debajo, y el panel sin borde propio."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-calculadora-rf")
        page.wait_for_selector(".card-calc-dia")
        assert page.locator("#calc-live-indicator").count() == 0, "el indicador «Modo interactivo» debe salir"
        titulos = page.evaluate("[...document.querySelectorAll('.card-calc h3')].map(h => h.textContent.trim())")
        assert titulos == ["Por día", "Por mes (30 días)", "Por año"], titulos
        assert page.locator(".card-calc-icon, .card-calc-badge").count() == 0
        opciones = page.evaluate("[...document.querySelectorAll('#calc-rf-base option')].map(o => o.textContent.trim())")
        assert opciones == ["365 días", "360 días"], opciones
        assert "CETES" in page.inner_text(".calc-ayuda"), "la ayuda debe explicar la base de días"
        borde = page.evaluate("getComputedStyle(document.querySelector('.calc-rf-panel')).borderTopWidth")
        assert borde == "0px", f"el panel de la calculadora no debe llevar borde propio: {borde}"
        assert not errores, errores
    finally:
        ctx.close()

def test_pasada_final_encabezados_franjas_y_deshabilitados(navegador, base):
    """Pasada final: los encabezados no saltan de nivel (sin h4 bajo h2 en Movimientos ni Presupuesto),
    no hay franjas decorativas en el hero ni en la cinta, y un botón deshabilitado se ve apagado."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-movimientos")
        page.click("#btn-subtab-fijos")
        assert page.locator("#tab-movimientos h4").count() == 0, "hay un h4 suelto en Movimientos"
        _ir(page, "tab-presupuesto")
        assert page.locator("#tab-presupuesto h4").count() == 0, "hay un h4 suelto en Presupuesto"
        _ir(page, "tab-dashboard")
        franja = page.evaluate("getComputedStyle(document.querySelector('.hero-networth'), '::before').display")
        assert franja == "none", f"el resumen aún tiene franja decorativa: {franja}"
        opacidad = page.evaluate("""() => { const b = document.createElement('button'); b.className = 'btn-primary'; b.disabled = true;
            document.body.appendChild(b); const o = getComputedStyle(b).opacity; b.remove(); return o; }""")
        assert float(opacidad) < 1, f"un botón deshabilitado debe verse apagado: {opacidad}"
        assert not errores, errores
    finally:
        ctx.close()

def test_marco_de_la_app_titulo_menu_y_campo_del_modal_a_la_vista(navegador, base):
    """Escritorio: el título va alineado a la izquierda y «Cerrar sesión» tiene la tipografía de las
    demás opciones del menú. Móvil: el campo enfocado queda por encima del botón «+»."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-movimientos")
        assert page.evaluate("getComputedStyle(document.getElementById('topbar-section-title')).textAlign") == "left"
        page.click("#btn-user-menu-trigger")
        page.wait_for_selector("#user-dropdown-menu.show")
        comparar = page.evaluate("""() => {
            const otra = document.querySelector('#user-dropdown-menu .dropdown-action-btn:not(.dropdown-btn-logout)');
            const salir = document.querySelector('#user-dropdown-menu .dropdown-btn-logout');
            const a = getComputedStyle(otra), b = getComputedStyle(salir);
            return [a.fontSize, b.fontSize, a.fontWeight, b.fontWeight, salir.getBoundingClientRect().height, otra.getBoundingClientRect().height];
        }""")
        assert comparar[0] == comparar[1] and comparar[2] == comparar[3], f"Cerrar sesión no coincide con su vecino: {comparar}"
        assert abs(comparar[4] - comparar[5]) < 1, f"alturas distintas: {comparar}"
        page.keyboard.press("Escape")
        assert not errores, errores
    finally:
        ctx.close()

    ctx, page, errores = _flujo_escenario(navegador, base, VIEWPORTS["movil"])
    try:
        _ir(page, "tab-movimientos")
        assert page.locator("#btn-fab, .fab").count() == 0, "ya no hay botón «+» flotante en el celular"
        _abrir_formulario(page, "movimiento")
        _esperar(page, "document.activeElement && document.activeElement.id === 'monto'")
        tapado = page.evaluate("""() => {
            const campo = document.getElementById('monto').getBoundingClientRect();
            const el = document.elementFromPoint(campo.left + campo.width / 2, campo.top + campo.height / 2);
            return !(el && el.id === 'monto');
        }""")
        assert not tapado, "el campo enfocado del modal queda tapado por otro elemento"
        assert not errores, errores
    finally:
        ctx.close()

def test_botones_de_fila_y_pestanas_tienen_area_tactil_de_44px(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base, VIEWPORTS["movil"])
    try:
        _ir(page, "tab-movimientos")
        page.wait_for_selector("#tabla-movimientos tr .btn-editar")
        area = page.evaluate("""() => { const b = document.querySelector('#tabla-movimientos .btn-editar');
            const cs = getComputedStyle(b, '::after');
            return {w: b.getBoundingClientRect().width + parseFloat(cs.left) * -2, h: b.getBoundingClientRect().height + parseFloat(cs.top) * -2}; }""")
        assert area["w"] >= 44 and area["h"] >= 44, area
        assert not errores, errores
    finally:
        ctx.close()


def test_red_cortada_al_guardar_no_duplica_al_reintentar(navegador, base):
    usuario, clave = _usuario_nuevo("red")
    _api(base, usuario, clave).post("/api/transacciones", json={"fecha": "2026-10-01", "monto": 1000, "tipo": "ingreso", "categoria_id": 12, "descripcion": "sueldo"})
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        _ir(page, "tab-movimientos")
        _abrir_formulario(page, "movimiento")
        page.fill("#fecha", "2026-10-02")
        page.select_option("#tipo", "gasto")
        page.fill("#monto", "75")
        page.fill("#descripcion", "cafe-con-red-caida")
        opciones = page.locator("#categoria option").evaluate_all("os => os.map(o => o.value).filter(v => v)")
        if opciones:
            page.select_option("#categoria", opciones[0])
        page.route("**/api/transacciones", lambda route: route.abort() if route.request.method == "POST" else route.continue_())
        page.click("#transaccion-form [type=submit]")
        page.wait_for_selector(".toast-error", timeout=10000)
        assert "Sin conexión" in page.inner_text(".toast-error"), page.inner_text(".toast-error")
        page.unroute("**/api/transacciones")
        page.click("#transaccion-form [type=submit]")
        _esperar(page, "[...document.querySelectorAll('#tabla-movimientos tr')].some(tr => tr.textContent.includes('cafe-con-red-caida'))")
        page.wait_for_timeout(800)
        repeticiones = _api(base, usuario, clave).get("/api/transacciones").json()
        assert sum(1 for t in repeticiones if t["descripcion"] == "cafe-con-red-caida") == 1, "el reintento no debe duplicar el movimiento"
        assert not errores, errores
    finally:
        ctx.close()


def test_otra_pestana_cambio_el_movimiento_y_no_se_pisa(navegador, base):
    """Pestaña A abre el formulario; pestaña B guarda otro monto; al guardar A, se avisa y queda el monto de B."""
    usuario, clave = _usuario_nuevo("conflicto")
    api = _api(base, usuario, clave)
    api.post("/api/transacciones", json={"fecha": "2026-10-01", "monto": 1000, "tipo": "ingreso", "categoria_id": 12, "descripcion": "base"})
    api.post("/api/transacciones", json={"fecha": "2026-10-02", "monto": 100, "tipo": "gasto", "categoria_id": 2, "descripcion": "conflicto-cafe"})
    mov = next(t for t in api.get("/api/transacciones").json() if t["descripcion"] == "conflicto-cafe")

    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        _ir(page, "tab-movimientos")
        fila = page.locator("#tabla-movimientos tr", has_text="conflicto-cafe")
        fila.wait_for()
        fila.locator(".btn-editar").click()  # la pestaña A abre el formulario con la versión actual
        page.wait_for_selector("#modal-editar-transaccion:not(.hidden)")
        page.fill("#modal-edit-trans-monto", "200")

        # Otra pestaña del mismo usuario guarda primero
        assert api.put(f"/api/transacciones/{mov['id']}", json={"monto": 300}).status_code == 200

        page.click("#form-editar-transaccion [type=submit]")
        page.wait_for_selector("#modal-editar-transaccion.hidden", state="attached")
        page.wait_for_selector("#toast-root :text('cambió desde que lo abriste')", timeout=10000)
        _esperar(page, "[...document.querySelectorAll('#tabla-movimientos tr')].some(tr => tr.textContent.includes('conflicto-cafe') && tr.textContent.includes('$300.00'))")
        assert not [e for e in errores if "Failed to load resource" not in e], errores
    finally:
        ctx.close()


def _esperar_en_registro(texto, evento, tiempo=10.0):
    """Espera a que el registro del servidor tenga una línea de ese evento que contenga el texto."""
    ruta = os.path.join(LOGS, "app.log")
    limite = time.time() + tiempo
    while time.time() < limite:
        if os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as archivo:
                for linea in archivo:
                    registro = json.loads(linea)
                    if registro.get("evento") == evento and texto in json.dumps(registro, ensure_ascii=False):
                        return registro
        time.sleep(0.25)
    # Sin la línea esperada, el fallo muestra lo que sí llegó al registro
    contenido = open(ruta, encoding="utf-8").read()[-1500:] if os.path.exists(ruta) else "(el registro está vacío)"
    raise AssertionError(f"el registro no tiene '{texto}' con evento {evento}. Últimas líneas:\n{contenido}")


def test_error_de_javascript_llega_al_registro(navegador, base):
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base)
        page.evaluate("setTimeout(() => { throw new Error('fallo-js-provocado') }, 0)")
        registro = _esperar_en_registro("fallo-js-provocado", "cliente")
        assert registro["tipo"] == "js" and registro["ruta"], registro
        # El error sí aparece en la consola del navegador: el registro no lo oculta
        assert [e for e in errores if "fallo-js-provocado" not in e] == [], errores
    finally:
        ctx.close()


def test_respuesta_500_de_la_api_se_reporta_con_su_id(navegador, base):
    usuario, clave = _usuario_nuevo("reg500")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        page.wait_for_load_state("networkidle")  # la carga inicial termina antes de interceptar la API
        page.route("**/api/resumen**", lambda route: route.fulfill(
            status=500, content_type="application/json",
            headers={"X-Request-Id": "0123456789abcdef"}, body='{"detail": "Error interno del servidor."}'))
        with page.expect_response(lambda r: "/api/resumen" in r.url, timeout=15000) as respuesta:
            page.evaluate("document.dispatchEvent(new Event('recargar'))")
        assert respuesta.value.status == 500, respuesta.value.status
        registro = _esperar_en_registro("500 GET /api/resumen", "cliente")
        assert registro["tipo"] == "api" and registro["id_peticion"] == "0123456789abcdef", registro
        page.unroute("**/api/resumen**")
        ignorados = ("Failed to load resource", "Error al actualizar datos")
        assert [e for e in errores if not any(i in e for i in ignorados)] == [], errores
    finally:
        ctx.close()


def test_sesion_vencida_a_mitad_de_uso_vuelve_al_login(navegador, base):
    usuario, clave = _usuario_nuevo("expira")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        page.route("**/api/**", lambda route: route.fulfill(
            status=401, content_type="application/json", body='{"detail": "Could not validate credentials"}'))
        page.evaluate("document.dispatchEvent(new Event('recargar'))")
        page.wait_for_selector("#form-login", state="visible", timeout=10000)
        page.unroute("**/api/**")
        # El aviso de consola por la sesión vencida es esperado; cualquier otro error sí cuenta.
        assert not [e for e in errores if "No autorizado" not in e], errores
    finally:
        ctx.close()


def test_icono_del_menu_colapsado_queda_centrado_con_el_cursor(navegador, base):
    """Con el menú colapsado, el icono sigue centrado cuando el cursor está encima (antes se corría y se agrandaba)."""
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _login(page, base)
        page.click("#btn-sidebar-collapse")
        page.wait_for_selector("#app-sidebar.sidebar-collapsed")
        page.hover("[data-tab='tab-dashboard']")
        page.wait_for_timeout(350)  # la transición del icono dura 0,2 s
        centro = page.evaluate("""() => {
            const boton = document.querySelector('[data-tab="tab-dashboard"]').getBoundingClientRect();
            const icono = document.querySelector('[data-tab="tab-dashboard"] .nav-svg-icon').getBoundingClientRect();
            return {boton: boton.x + boton.width / 2, icono: icono.x + icono.width / 2};
        }""")
        assert abs(centro["boton"] - centro["icono"]) < 0.5, centro
        assert not errores, "errores de JS:\n" + "\n".join(errores)
    finally:
        ctx.close()


def test_campos_y_selectores_nativos_siguen_el_tema(navegador, base):
    """El esquema de color sigue al tema (calendarios y listas), hay regla de relleno automático y ningún campo visible queda blanco en oscuro."""
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _login(page, base)
        for tema in ["dark", "light"]:
            page.evaluate(f"document.documentElement.setAttribute('data-theme', '{tema}')")
            esquema = page.evaluate("getComputedStyle(document.documentElement).colorScheme")
            assert esquema == tema, f"color-scheme en {tema}: {esquema}"
        reglas = page.evaluate("""() => {
            const encontradas = [];
            for (const hoja of document.styleSheets) {
                let reglas;
                try { reglas = hoja.cssRules; } catch (e) { continue; }
                for (const r of reglas) {
                    if (r.selectorText && r.selectorText.includes('-webkit-autofill')) encontradas.push(r.selectorText);
                }
            }
            return encontradas;
        }""")
        assert reglas, "falta la regla de relleno automático de los campos"
        page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
        blancos = page.evaluate("""() => [...document.querySelectorAll('input, select, textarea')]
            .filter(el => el.offsetParent !== null && !['hidden', 'checkbox', 'radio', 'file'].includes(el.type))
            .map(el => ({id: el.id, fondo: getComputedStyle(el).backgroundColor}))
            .filter(c => /^rgb\((2[0-5]\d), (2[0-5]\d), (2[0-5]\d)\)$/.test(c.fondo))""")
        assert not blancos, f"campos con fondo blanco en modo oscuro: {blancos}"
        assert not errores, "errores de JS:\n" + "\n".join(errores)
    finally:
        ctx.close()


def test_boton_cotizar_no_sobresale_del_campo(navegador, base):
    """El botón Cotizar cabe en la altura del campo del ticker y el bloque queda a la altura de los demás campos."""
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _login(page, base)
        page.evaluate("document.querySelector('[data-tab=\"tab-inversiones\"]').click()")
        page.wait_for_selector("#tab-inversiones.active")
        _abrir_formulario(page, "posicion")
        medidas = page.evaluate("""() => {
            const alto = (el) => el.getBoundingClientRect().height;
            const boton = document.querySelector('.btn-search-ticker');
            const campo = document.getElementById('inv-ticker');
            const moneda = document.getElementById('inv-moneda');
            return {boton: alto(boton), campo: alto(campo), bloqueTicker: alto(campo.closest('.form-group')), bloqueMoneda: alto(moneda.closest('.form-group'))};
        }""")
        assert medidas["boton"] <= medidas["campo"] + 1, medidas
        assert abs(medidas["bloqueTicker"] - medidas["bloqueMoneda"]) <= 2, medidas
        assert not errores, "errores de JS:\n" + "\n".join(errores)
    finally:
        ctx.close()


def test_etiqueta_del_tipo_de_cambio_avisa_cuando_es_de_respaldo(navegador, base):
    """La etiqueta «1 USD ≈ …» marca como aproximada la tasa que no es una cotización real, y no inventa una sin datos."""
    def etiqueta(en_respaldo):
        ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
        try:
            def servir(route):
                resp = route.fetch()
                datos = resp.json()
                datos["tasas"]["MXN"] = 19.5
                datos["en_respaldo"] = en_respaldo
                route.fulfill(response=resp, json=datos)
            page.route("**/api/tipo-cambio", servir)
            _login(page, base)
            _esperar(page, "document.getElementById('badge-tasa-global').textContent.includes('19.50')")
            assert not errores, "errores de JS:\n" + "\n".join(errores)
            return page.inner_text("#badge-tasa-global"), page.get_attribute("#badge-tasa-global", "title")
        finally:
            ctx.close()

    texto, aviso = etiqueta(["MXN"])
    assert "(aprox.)" in texto and aviso, (texto, aviso)
    texto, aviso = etiqueta([])
    assert "(aprox.)" not in texto and not aviso, (texto, aviso)


def test_exportar_presupuesto_muestra_que_cambia_antes_de_reemplazar(navegador, base):
    """Exportar abre un modal con lo que se crea, actualiza y elimina; cancelar o Escape no tocan nada y aceptar sí aplica."""
    usuario, clave = _usuario_nuevo("expprev")
    api = _api(base, usuario, clave)
    assert api.put("/api/presupuesto/config", json={"ingreso_base": 20000, "frecuencia": "mensual", "dia_pago": 5, "subdivision_tipo": "ninguna"}).status_code == 200
    assert api.post("/api/presupuesto/tarjetas", json={"nombre": "Hogar", "porcentaje": 30, "notas": "", "unidad_orden": 1}).status_code in (200, 201)
    assert api.put("/api/presupuesto/unidades/1", json={"dia_pago": 5}).status_code == 200
    api.post("/api/apartados", json={"nombre": "Viaje", "tipo": "meta", "meta_monto": 9000})
    meta = next(a["id"] for a in api.get("/api/apartados").json() if a["nombre"] == "Viaje")
    fijos = [
        dict(descripcion="Sueldo", tipo="ingreso", frecuencia="mensual", monto=20000, fecha_inicio="2026-12-01", categoria_id=12),
        dict(descripcion="Gimnasio", tipo="gasto", frecuencia="mensual", monto=500, fecha_inicio="2026-12-01", categoria_id=7),
        dict(descripcion="Tablet", tipo="gasto", frecuencia="mensual", monto_total=1200, meses=12, fecha_inicio="2026-12-01"),
        dict(descripcion="Ahorro viaje", tipo="gasto", frecuencia="mensual", monto=300, fecha_inicio="2026-12-01", apartado_id=meta),
    ]
    for fijo in fijos:
        assert api.post("/api/recurrentes", json=fijo).status_code == 201
    nombres = lambda: sorted(r["descripcion"] for r in api.get("/api/recurrentes").json())
    antes = nombres()
    assert "Gimnasio" in antes

    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    page.on("dialog", lambda d: (errores.append(f"diálogo nativo: {d.message}"), d.dismiss()))
    try:
        _entrar(page, base, usuario, clave)
        _ir(page, "tab-presupuesto")
        page.evaluate("document.getElementById('btn-exportar-pres-fijos').click()")
        page.wait_for_selector("#modal-exportar-presupuesto:not(.hidden)")
        page.click("#btn-confirmar-exportar-pres")
        page.wait_for_selector("#modal-confirmar:not(.hidden)")
        detalle = page.inner_text("#confirmar-detalle")
        assert "Se eliminarán (4)" in detalle and "Se crearán" in detalle, detalle
        for esperado in ("Gimnasio", "Fijo hecho a mano", "Tablet", "Compra a meses", "0 de 12 cuotas", "Ahorro viaje", "Aporte a una meta"):
            assert esperado in detalle, (esperado, detalle)
        assert page.inner_text("#btn-confirmar-aceptar") == "Reemplazar fijos"

        # Escape cierra solo la confirmación: el modal de exportar sigue abierto y no se tocó nada
        page.keyboard.press("Escape")
        page.wait_for_selector("#modal-confirmar.hidden", state="attached")
        assert page.locator("#modal-exportar-presupuesto.hidden").count() == 0
        assert nombres() == antes

        page.click("#btn-confirmar-exportar-pres")
        _responder_confirmacion(page, aceptar=False)
        assert nombres() == antes

        page.click("#btn-confirmar-exportar-pres")
        _responder_confirmacion(page)
        page.wait_for_selector("#modal-exportar-presupuesto.hidden", state="attached")
        for _ in range(100):
            if "Gimnasio" not in nombres():
                break
            page.wait_for_timeout(100)
        despues = nombres()
        assert "Gimnasio" not in despues and "Tablet" not in despues and "Hogar" in despues, despues
        assert not errores, errores
    finally:
        ctx.close()


def test_exportar_presupuesto_pide_recargar_el_resumen(navegador, base):
    """Hallazgo H-4: tras exportar, el Resumen y el resto de pantallas se actualizan sin recargar la página.
    La app los refresca con el evento «recargar»; exportar tiene que dispararlo."""
    usuario, clave = _usuario_nuevo("exprecarga")
    api = _api(base, usuario, clave)
    assert api.put("/api/presupuesto/config", json={"ingreso_base": 20000, "frecuencia": "mensual", "dia_pago": 5, "subdivision_tipo": "ninguna"}).status_code == 200
    assert api.post("/api/presupuesto/tarjetas", json={"nombre": "Hogar", "porcentaje": 30, "notas": "", "unidad_orden": 1}).status_code in (200, 201)
    assert api.put("/api/presupuesto/unidades/1", json={"dia_pago": 5}).status_code == 200

    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        _ir(page, "tab-presupuesto")
        page.evaluate("window.__recargas = 0; document.addEventListener('recargar', () => { window.__recargas += 1; })")
        page.evaluate("document.getElementById('btn-exportar-pres-fijos').click()")
        page.wait_for_selector("#modal-exportar-presupuesto:not(.hidden)")
        page.click("#btn-confirmar-exportar-pres")
        _responder_confirmacion(page)
        page.wait_for_selector("#modal-exportar-presupuesto.hidden", state="attached")
        for _ in range(100):
            if page.evaluate("window.__recargas") > 0:
                break
            page.wait_for_timeout(100)
        assert page.evaluate("window.__recargas") > 0, "exportar no pidió recargar los datos: el Resumen quedaría desactualizado"
        assert not errores, errores
    finally:
        ctx.close()


def test_tarjeta_elige_su_meta_en_un_selector_y_la_conserva(navegador, base):
    usuario, clave = _usuario_nuevo("tarjmeta")
    api = _api(base, usuario, clave)
    assert api.put("/api/presupuesto/config", json={"ingreso_base": 20000, "frecuencia": "mensual", "dia_pago": 5, "subdivision_tipo": "ninguna"}).status_code == 200
    assert api.put("/api/presupuesto/unidades/1", json={"dia_pago": 5}).status_code == 200
    api.post("/api/apartados", json={"nombre": "Viaje", "tipo": "meta", "meta_monto": 9000})
    meta = next(a["id"] for a in api.get("/api/apartados").json() if a["nombre"] == "Viaje")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        _ir(page, "tab-presupuesto")
        page.evaluate("document.querySelector('.pres-pago-add').click()")   # una tarjeta siempre pertenece a un pago
        page.wait_for_selector("#modal-tarjeta-presupuesto:not(.hidden)")
        page.fill("#tarjeta-pres-nombre", "Ahorro mensual")
        page.fill("#tarjeta-pres-monto", "1000")
        _esperar(page, "document.getElementById('tarjeta-pres-apartado').dataset.listo === '1'")
        assert [o.inner_text() for o in page.locator("#tarjeta-pres-apartado option").all()] == ["Ninguna: es un gasto", "Viaje"]
        page.select_option("#tarjeta-pres-apartado", str(meta))
        page.click("#form-tarjeta-presupuesto [type=submit]")
        page.wait_for_selector("#modal-tarjeta-presupuesto.hidden", state="attached")
        tarjetas = lambda: [t for u in api.get("/api/presupuesto").json()["unidades"] for t in u["tarjetas"] if t["nombre"] == "Ahorro mensual"]
        for _ in range(50):
            if tarjetas():
                break
            page.wait_for_timeout(100)
        assert tarjetas()[0]["apartado_id"] == meta, tarjetas()
        assert not errores, errores
    finally:
        ctx.close()


def test_grafica_del_historial_etiqueta_los_periodos_segun_el_cobro(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        page.wait_for_selector("#periodo-libre:not(:empty)")
        assert page.inner_text("#titulo-grafica-periodos") == "Ingresos y gastos por quincena"
        etiquetas = page.evaluate("import('/static/js/core.js').then(m => m.estado.graficas.barras.data.labels)")
        # Del más antiguo al más reciente, uno por quincena de cobro
        assert etiquetas == [["15 ago", "30 ago"], ["31 ago", "14 sep"], ["15 sep", "29 sep"], ["30 sep", "14 oct"], ["15 oct", "30 oct"]], etiquetas
        # Un mes calendario completo se rotula «nov 2026»; un periodo que cruza el año lleva el año
        resultado = page.evaluate("""async () => {
            const { actualizarGrafica } = await import('/static/js/dashboard.js');
            const { estado } = await import('/static/js/core.js');
            actualizarGrafica([
                { inicio: '2026-11-01', fin: '2026-11-30', ingresos: 100, gastos: 50 },
                { inicio: '2026-12-15', fin: '2027-01-14', ingresos: 300, gastos: 10 },
            ]);
            return estado.graficas.barras.data.labels;
        }""")
        assert resultado == ["nov 2026", ["15 dic 2026", "14 ene 2027"]], resultado
        assert not errores, errores
    finally:
        ctx.close()


def test_dolares_se_rotulan_us_y_los_titulos_respetan_el_modo_privacidad(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        page.wait_for_selector("#periodo-libre:not(:empty)")
        simbolos = page.evaluate("""async () => {
            const { fmt, conTituloPrivado, sincronizarTitulosPrivados } = await import('/static/js/core.js');
            const pie = document.createElement('div');
            document.body.appendChild(pie);
            conTituloPrivado(pie, 'Hogar: 30%', 'Hogar: 30% ($6,000.00)');
            const normal = pie.title;
            document.body.classList.add('privacy-mode');
            sincronizarTitulosPrivados();
            const privado = pie.title;
            // Un elemento que aparece después (pintado con innerHTML) toma el modo vigente
            const caja = document.createElement('div');
            caja.innerHTML = '<span data-titulo="Sin importe" data-titulo-monto="Con importe $1.00"></span>';
            document.body.appendChild(caja);
            await new Promise(r => setTimeout(r, 50));
            const nuevo = caja.firstChild.title;
            document.body.classList.remove('privacy-mode');
            return { mxn: fmt(5, 'MXN'), usd: fmt(5, 'USD'), eur: fmt(5, 'EUR'), negativo: fmt(-5, 'USD'), normal, privado, nuevo };
        }""")
        assert simbolos["mxn"] == "$5.00" and simbolos["usd"] == "US$5.00" and simbolos["eur"] == "€5.00" and simbolos["negativo"] == "-US$5.00", simbolos
        assert simbolos["normal"] == "Hogar: 30% ($6,000.00)" and simbolos["privado"] == "Hogar: 30%", simbolos
        assert simbolos["nuevo"] == "Sin importe", simbolos
        assert not errores, errores
    finally:
        ctx.close()


def test_barra_de_seleccion_multiple_aparece_al_marcar_un_movimiento(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-movimientos")
        page.wait_for_selector("#tabla-movimientos .chk-mov-item")
        assert page.locator("#barra-lote-movimientos.hidden").count() == 1
        page.evaluate("document.querySelector('#tabla-movimientos .chk-mov-item').click()")
        _esperar(page, "!document.getElementById('barra-lote-movimientos').classList.contains('hidden')")
        assert page.evaluate("getComputedStyle(document.getElementById('barra-lote-movimientos')).display") == "flex"
        page.evaluate("document.querySelector('#tabla-movimientos .chk-mov-item').click()")
        _esperar(page, "document.getElementById('barra-lote-movimientos').classList.contains('hidden')")
        assert not errores, errores
    finally:
        ctx.close()


def test_modo_discrecion_cubre_todas_las_pestanas(navegador, base):
    """Con el modo privacidad activo, toda cifra visible (en cualquier pestaña) queda desenfocada."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        page.wait_for_selector("#periodo-libre:not(:empty)")
        page.click("#btn-toggle-privacidad")
        for tab in ["tab-dashboard", "tab-movimientos", "tab-apartados", "tab-inversiones", "tab-presupuesto"]:
            _ir(page, tab)
            resultado = page.evaluate("""() => {
                const visibles = [...document.querySelectorAll('.dato-sensible')].filter(el => el.offsetParent !== null);
                return {total: visibles.length, sinDesenfocar: visibles.filter(el => getComputedStyle(el).filter === 'none').map(el => el.textContent.slice(0, 30))};
            }""")
            assert resultado["total"] > 0, f"{tab}: no hay cifras marcadas como sensibles"
            assert not resultado["sinDesenfocar"], f"{tab}: cifras sin desenfocar: {resultado['sinDesenfocar']}"
        assert not errores, errores
    finally:
        ctx.close()


def test_moneda_base_euro_se_aplica_y_persiste(navegador, base):
    """Elegir EUR como moneda base cambia las cifras y los rótulos, y sobrevive a recargar la página."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        page.wait_for_selector("#periodo-libre:not(:empty)")
        page.select_option("#select-moneda-global", "EUR")
        _esperar(page, "[...document.querySelectorAll('.lbl-moneda-base')].length > 0 && [...document.querySelectorAll('.lbl-moneda-base')].every(el => el.textContent === 'EUR')")
        _esperar(page, "document.getElementById('periodo-libre').textContent.includes('€')")  # las cifras llegan después de los rótulos
        page.wait_for_load_state("networkidle")  # recargar con peticiones en vuelo las cancela y ensucia el registro de errores
        errores.clear()
        page.goto(base + "/app")  # recargar /login muestra el login en el SaaS aunque haya sesión: se entra a la app por su ruta
        page.wait_for_selector("#periodo-libre:not(:empty)")
        assert page.input_value("#select-moneda-global") == "EUR"
        _esperar(page, "document.getElementById('periodo-libre').textContent.includes('€')")
        assert not errores, errores
    finally:
        ctx.close()


def test_moneda_base_convierte_las_listas_guardadas_en_mxn(navegador, base):
    """Movimientos, historial mensual y metas se guardan en MXN: con base EUR se muestran convertidos, no con otro símbolo."""
    import re
    ctx, page, errores = _flujo_escenario(navegador, base)

    def cifra(texto):
        return float(re.sub(r"[^\d.]", "", texto))

    try:
        page.wait_for_selector("#periodo-libre:not(:empty)")
        page.select_option("#select-moneda-global", "EUR")
        _esperar(page, "document.getElementById('periodo-libre').textContent.includes('€')")
        factor = page.evaluate(
            "fetch('/api/tipo-cambio', {headers: {Authorization: 'Bearer ' + localStorage.getItem('jwt_token')}})"
            ".then(r => r.json()).then(d => d.factores_desde_mxn.EUR)")
        assert 0 < factor < 1, factor

        # Historial: la quincena en curso tuvo 14,000 de ingresos y 299 de gastos (MXN)
        _esperar(page, "document.querySelector('#lista-historial-periodos .monthly-card')?.textContent.includes('€')")
        primera = page.locator("#lista-historial-periodos .monthly-card").nth(0).inner_text()
        ingresos, gastos = [cifra(t) for t in re.findall(r"[+−-]€[\d,]+\.\d\d", primera)]
        assert abs(ingresos - 14000 * factor) < 0.011 and abs(gastos - 299 * factor) < 0.011, (primera, factor)

        # Movimientos del periodo en curso: Netflix, 299 MXN
        _ir(page, "tab-movimientos")
        _esperar(page, "[...document.querySelectorAll('td.td-monto')].some(td => td.textContent.includes('€'))")
        montos = [cifra(t) for t in page.locator("td.td-monto").all_inner_texts()]
        assert any(abs(m - 299 * factor) < 0.011 for m in montos), (montos, factor)
        assert 299.0 not in montos, "el monto guardado en MXN no debe mostrarse sin convertir"

        # Metas: Viaje guarda 5,000 MXN
        _ir(page, "tab-apartados")
        _esperar(page, "[...document.querySelectorAll('.apartado-saldo')].some(p => p.textContent.includes('€'))")
        saldos = [cifra(t) for t in page.locator(".apartado-saldo").all_inner_texts()]
        assert any(abs(s - 5000 * factor) < 0.011 for s in saldos), (saldos, factor)

        # Los formularios siguen en MXN y lo dicen
        assert "MXN" in page.text_content("label[for=meta-monto]")
        assert not errores, errores
    finally:
        ctx.close()


def test_comentario_se_envia_desde_el_menu_y_queda_en_la_base(navegador, base):
    """Desde el menú de usuario se envía un comentario: se confirma, el modal se cierra y la fila queda en la base maestra."""
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        page.wait_for_selector("#periodo-libre:not(:empty)")
        page.click("#btn-user-menu-trigger")
        page.click(".btn-comentarios")
        page.wait_for_selector("#modal-comentarios:not(.hidden)")
        page.select_option("#comentario-tipo", "error")
        texto = "e2e-comentario-no-guarda-el-cambio"
        page.fill("#comentario-mensaje", texto)
        page.click("#btn-enviar-comentario")
        page.wait_for_selector("#modal-comentarios.hidden", state="attached")
        page.wait_for_selector("text=Gracias. Recibimos tu comentario.")
        conn = master_db.get_master_conn()
        try:
            fila = conn.execute("SELECT tipo, contexto FROM comentarios WHERE mensaje = ?", (texto,)).fetchone()
        finally:
            conn.close()
        assert fila is not None and fila[0] == "error" and fila[1] == "tab-dashboard", fila
        assert not errores, errores
    finally:
        ctx.close()


def _abrir_asistente_telegram(page):
    page.click("#btn-user-menu-trigger")
    page.click("#btn-asistente-telegram")
    page.wait_for_selector("#modal-telegram:not(.hidden)")


def test_asistente_telegram_genera_el_qr_detecta_la_vinculacion_y_se_desconecta(navegador, base):
    """Mi menú > Asistente en Telegram: pide la contraseña, muestra el QR de un solo uso y, al escanearlo (aquí lo hace el bot
    simulado), pasa a «vinculado»; Desconectar quita el vínculo."""
    from asistente import vinculos
    usuario, clave = _usuario_nuevo("telegram")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        _abrir_asistente_telegram(page)
        page.wait_for_selector("#telegram-formulario:not(.hidden)")
        page.fill("#telegram-contrasena", "clave-equivocada")
        page.click("#btn-generar-telegram")
        page.wait_for_selector(".toast-error")
        assert page.is_hidden("#telegram-qr-caja") and page.get_attribute("#telegram-qr", "src") is None, "con la contraseña mal no hay QR"
        page.fill("#telegram-contrasena", clave)
        page.click("#btn-generar-telegram")
        page.wait_for_selector("#telegram-qr-caja:not(.hidden)")
        assert page.get_attribute("#telegram-qr", "src").startswith("data:image/svg+xml")
        enlace = page.get_attribute("#telegram-enlace", "href")
        assert enlace.startswith("https://t.me/finanzas_demo_bot?start="), enlace
        assert "Vence en" in page.inner_text("#telegram-vence")
        assert page.input_value("#telegram-contrasena") == "", "la contraseña no se queda en el campo"
        assert page.evaluate("document.getElementById('telegram-qr').naturalWidth") > 0, "la imagen del QR se pinta"
        # El bot simulado recibe «/start <código>»: la pantalla lo detecta sola
        assert vinculos.consumir_codigo(enlace.split("?start=")[1], 8123, "persona_tg")[0] == vinculos.OK
        page.wait_for_selector("#telegram-vinculado:not(.hidden)", timeout=10000)
        assert page.inner_text("#telegram-usuario") == "@persona_tg"
        assert page.get_attribute("#telegram-qr", "src") is None and page.get_attribute("#telegram-enlace", "href") is None, "el código ya no queda en la página"
        page.click("#btn-desconectar-telegram")
        _responder_confirmacion(page)
        page.wait_for_selector("#telegram-formulario:not(.hidden)")
        assert vinculos.usuario_de_chat(8123) is None
        assert not errores, errores
    finally:
        ctx.close()


def test_asistente_telegram_cerrar_el_modal_borra_el_codigo_y_en_movil_no_desborda(navegador, base):
    usuario, clave = _usuario_nuevo("telegram_movil")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["movil"])
    try:
        _entrar(page, base, usuario, clave)
        _abrir_asistente_telegram(page)
        page.fill("#telegram-contrasena", clave)
        page.click("#btn-generar-telegram")
        page.wait_for_selector("#telegram-qr-caja:not(.hidden)")
        ancho = page.evaluate("({pagina: document.documentElement.scrollWidth, ventana: window.innerWidth, qr: document.getElementById('telegram-qr').getBoundingClientRect().width})")
        assert ancho["pagina"] <= ancho["ventana"] and 150 <= ancho["qr"] <= ancho["ventana"], ancho
        page.click("#telegram-qr-caja .btn-secondary")
        page.wait_for_selector("#modal-telegram.hidden", state="attached")
        assert page.get_attribute("#telegram-qr", "src") is None and page.get_attribute("#telegram-enlace", "href") is None
        _abrir_asistente_telegram(page)
        assert page.is_hidden("#telegram-qr-caja") and page.input_value("#telegram-contrasena") == "", "al reabrir no queda el código anterior"
        assert not errores, errores
    finally:
        ctx.close()


def test_asistente_telegram_sin_bot_en_el_servidor_lo_dice(navegador, base):
    usuario, clave = _usuario_nuevo("telegram_sin_bot")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario, clave)
        page.route("**/api/asistente/telegram", lambda r: r.fulfill(status=200, content_type="application/json",
                   body='{"disponible": false, "vinculado": false, "username": null, "desde": null}'))
        _abrir_asistente_telegram(page)
        page.wait_for_selector("#telegram-no-disponible:not(.hidden)")
        assert "no está activado" in page.inner_text("#telegram-no-disponible") and page.is_hidden("#telegram-formulario")
        assert not errores, errores
    finally:
        ctx.close()


# --- La burbuja del asistente: un servicio falso en lugar del de verdad (sin Gemini) y un segundo servidor que lo conoce -----------------
SECRETO_WEB = "w" * 40


def _png_valido(lado=8):
    """PNG real (rojo liso) sin librerías: el navegador tiene que poder decodificarlo para reducirlo."""
    import struct
    import zlib

    def trozo(tipo, datos):
        cuerpo = tipo + datos
        return struct.pack(">I", len(datos)) + cuerpo + struct.pack(">I", zlib.crc32(cuerpo))
    filas = b"".join(b"\x00" + b"\xc8\x32\x32" * lado for _ in range(lado))
    return b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", struct.pack(">IIBBBBB", lado, lado, 8, 2, 0, 0, 0)) + trozo(b"IDAT", zlib.compress(filas)) + trozo(b"IEND", b"")


class _ServicioFalso:
    """Hace de `finanzas-asistente`: responde lo que la prueba espera y, al confirmar, usa el token de escritura que le da la app para
    registrar de verdad el gasto (así la prueba cubre el camino completo: navegador, app, servicio y API)."""

    def __init__(self, base_app, categoria_id):
        import base64
        import http.server
        import threading
        self.recibidos = []
        self.categoria_id = categoria_id
        self.base_app = base_app
        servicio = self

        class Manejador(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _enviar(self, codigo, datos):
                cuerpo = json.dumps(datos).encode()
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)

            def do_GET(self):
                self._enviar(200, {"ok": True})

            def do_POST(self):
                try:
                    self._post()
                except Exception:
                    import traceback
                    traceback.print_exc()
                    raise

            def _post(self):
                if self.headers.get("X-Asistente-Secreto") != SECRETO_WEB:
                    return self._enviar(401, {})
                cuerpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                servicio.recibidos.append((self.path, cuerpo))
                texto = cuerpo.get("texto") or ""
                if self.path == "/web/mensaje":
                    adjunto = cuerpo.get("adjunto")
                    if adjunto:
                        from asistente import medios
                        datos = base64.b64decode(adjunto["datos_b64"])
                        tipo, mime = medios.clasificar(datos)
                        return self._enviar(200, {"estado": "ok", "texto": f"Recibí tu {tipo} ({mime}, {len(datos)} bytes). {texto}", "propuestas": []})
                    if "gasté" in texto:
                        return self._enviar(200, {"estado": "ok", "texto": "Quedó pendiente de confirmación.", "propuestas": [{"id": "prop" + str(len(servicio.recibidos)).zfill(8), "resumen": "Gasto de $31.00 MXN en «Alimentos», el 2026-11-09: «Súper»"}]})
                    if "xss" in texto:
                        return self._enviar(200, {"estado": "ok", "texto": "<img src=x onerror=\"window.__xss=1\"><b>negrita</b>", "propuestas": [{"id": "propxss00001", "resumen": "<img src=x onerror=\"window.__xss=2\">"}]})
                    if "lento" in texto:
                        import time as _t
                        _t.sleep(1.5)
                    return self._enviar(200, {"estado": "ok", "texto": f"Dijiste: {texto}", "propuestas": []})
                if self.path == "/web/confirmar":
                    cuerpo_gasto = json.dumps({"fecha": escenario.HOY_ISO[:10], "monto": 31, "tipo": "gasto", "categoria_id": servicio.categoria_id, "descripcion": "Súper (asistente)"}).encode()
                    r = urllib.request.Request(servicio.base_app + "/api/transacciones", data=cuerpo_gasto, method="POST",
                                               headers={"Content-Type": "application/json", "Authorization": "Bearer " + cuerpo["token_escritura"]})
                    try:
                        urllib.request.urlopen(r, timeout=10)
                        return self._enviar(200, {"resultado": "confirmada", "texto": "Listo: Gasto de $31.00 MXN."})
                    except urllib.error.HTTPError as e:
                        return self._enviar(200, {"resultado": "rechazada", "texto": f"No se pudo: {e.code}"})
                if self.path == "/web/cancelar":
                    return self._enviar(200, {"resultado": "cancelada", "texto": "Cancelada."})
                return self._enviar(200, {"ok": True})

        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def cerrar(self):
        self.http.shutdown()
        self.http.server_close()


def _sesion_api(base, usuario, clave):
    r = urllib.request.Request(base + "/auth/login", data=json.dumps({"username": usuario, "password": clave}).encode(), headers={"Content-Type": "application/json"}, method="POST")
    return {"Authorization": "Bearer " + json.load(urllib.request.urlopen(r, timeout=10))["access_token"]}


def _api_get(base, ruta, cabeceras):
    return json.load(urllib.request.urlopen(urllib.request.Request(base + ruta, headers=cabeceras), timeout=10))


def _montar_servicio_falso(base_normal, usuario):
    """Servicio falso + segundo servidor (con el asistente web configurado) sobre las mismas bases de datos."""
    cab = _sesion_api(base_normal, usuario, CLAVE)
    categorias = [c for c in _api_get(base_normal, "/api/categorias", cab) if c["tipo"] == "gasto"]
    falso = _ServicioFalso("", categorias[0]["id"])
    proc, base = _arrancar_servidor({"ASISTENTE_WEB_URL": falso.url, "ASISTENTE_WEB_SECRETO": SECRETO_WEB})
    falso.base_app = base
    return falso, proc, base


def test_la_burbuja_no_existe_sin_servicio_y_aparece_abajo_a_la_derecha_con_el(navegador, base, base_con_asistente):
    usuario = "burbuja_forma"
    master_db.create_user_entry(usuario, security.hash_password(CLAVE), email=f"{usuario}@test.local")
    # Sin servicio del asistente: ni burbuja ni nada que sobre
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base, usuario)
        page.wait_for_timeout(800)
        assert page.locator("#asistente-web").count() == 0 and "con-asistente" not in (page.get_attribute("body", "class") or "")
        assert not errores, errores
    finally:
        ctx.close()
    for nombre in ("laptop", "movil"):
        ctx, page, errores = _contexto(navegador, VIEWPORTS[nombre])
        try:
            ctx.grant_permissions(["microphone"], origin=base_con_asistente)
            _entrar(page, base_con_asistente, usuario)
            page.wait_for_selector("#asistente-burbuja", timeout=10000)
            caja = page.locator("#asistente-burbuja").bounding_box()
            vp = VIEWPORTS[nombre]
            assert caja["width"] >= 44 and caja["height"] >= 44, caja
            assert abs((vp["width"] - (caja["x"] + caja["width"])) - (16 if nombre == "laptop" else 12)) <= 2, ("a la derecha", caja)
            assert abs((vp["height"] - (caja["y"] + caja["height"])) - (16 if nombre == "laptop" else 12)) <= 2, ("abajo", caja)
            assert page.get_attribute("#asistente-burbuja", "aria-label") == "Abrir el asistente" and page.get_attribute("#asistente-burbuja", "aria-expanded") == "false"
            page.click("#asistente-burbuja")
            page.wait_for_selector("#asistente-panel:not(.hidden)")
            panel = page.locator("#asistente-panel").bounding_box()
            assert panel["x"] >= 0 and panel["y"] >= 0 and panel["x"] + panel["width"] <= vp["width"] + 1 and panel["y"] + panel["height"] <= vp["height"] + 1, ("dentro de la pantalla", panel)
            if nombre == "movil":
                assert panel["width"] >= vp["width"] - 1 and panel["height"] >= vp["height"] - 1, ("a pantalla completa en el móvil", panel)
            assert page.evaluate("document.activeElement.id") == "asistente-texto", "al abrir, el foco va al campo"
            assert "Hola, soy tu asistente" in page.inner_text("#asistente-mensajes")
            ancho = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
            assert ancho[0] <= ancho[1], f"scroll horizontal con el panel abierto en {nombre}"
            for sel in ("#asistente-enviar", "#asistente-mic", ".asistente-cabecera button"):
                for caja in [b.bounding_box() for b in page.locator(sel).all() if b.is_visible()]:
                    assert caja["width"] >= 43.5 and caja["height"] >= 43.5, (sel, caja)
            page.keyboard.press("Escape")
            page.wait_for_selector("#asistente-panel.hidden", state="attached")
            assert page.evaluate("document.activeElement.id") == "asistente-burbuja", "al cerrar, el foco vuelve a la burbuja"
            assert not errores, errores
        finally:
            ctx.close()


def test_el_asistente_web_conversa_muestra_el_texto_sin_interpretarlo_y_confirma_una_sola_vez(navegador, base_con_asistente, servicio_falso):
    usuario = "burbuja_chat"
    fila = master_db.create_user_entry(usuario, security.hash_password(CLAVE), email=f"{usuario}@test.local")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base_con_asistente, usuario)
        page.click("#asistente-burbuja")
        # Enter envía; Shift+Enter hace salto de línea
        page.fill("#asistente-texto", "hola")
        page.press("#asistente-texto", "Shift+Enter")
        assert page.input_value("#asistente-texto") == "hola\n"
        page.fill("#asistente-texto", "lento")
        page.press("#asistente-texto", "Enter")
        page.wait_for_selector(".asistente-escribiendo")
        assert page.is_disabled("#asistente-enviar") and page.is_disabled("#asistente-texto"), "mientras contesta no se envía otra cosa"
        page.wait_for_selector(".asistente-msg-asistente:has-text('Dijiste: lento')", timeout=10000)
        assert page.locator(".asistente-escribiendo").count() == 0 and page.is_enabled("#asistente-enviar")
        assert page.locator(".asistente-msg-persona").last.inner_text() == "lento"
        # Lo que escribe el asistente es texto: ni etiquetas ni scripts
        page.fill("#asistente-texto", "xss")
        page.press("#asistente-texto", "Enter")
        page.wait_for_selector(".asistente-propuesta")
        assert page.evaluate("window.__xss") is None, "no se ejecutó nada"
        assert page.locator("#asistente-mensajes img").count() == 0 and page.locator("#asistente-mensajes b").count() == 0
        assert "<b>negrita</b>" in page.inner_text("#asistente-mensajes") and "<img" in page.inner_text(".asistente-propuesta-resumen")
        page.click(".asistente-propuesta .asistente-btn:has-text('Cancelar')")
        page.wait_for_selector(".asistente-propuesta-resultado:not(.hidden)")
        # Una propuesta de verdad: nada se registra hasta confirmar, y confirmar dos veces seguidas registra una sola
        cab = _sesion_api(base_con_asistente, usuario, CLAVE)
        categorias = _api_get(base_con_asistente, "/api/categorias", cab)
        servicio_falso.categoria_id = next(c["id"] for c in categorias if c["tipo"] == "gasto")   # el servicio falso registra en una categoría de ESTA cuenta
        req = urllib.request.Request(base_con_asistente + "/api/transacciones", method="POST", headers={**cab, "Content-Type": "application/json"},
                                     data=json.dumps({"fecha": "2026-10-01", "monto": 5000, "tipo": "ingreso", "categoria_id": next(c["id"] for c in categorias if c["tipo"] == "ingreso"), "descripcion": "Fondeo"}).encode())
        urllib.request.urlopen(req, timeout=10)   # sin saldo no se puede registrar un gasto
        antes = len([m for m in _api_get(base_con_asistente, "/api/transacciones", cab) if m["tipo"] == "gasto"])
        page.fill("#asistente-texto", "gasté 31 en el súper")
        page.press("#asistente-texto", "Enter")
        page.wait_for_selector(".asistente-propuesta:has-text('Gasto de $31.00')")
        assert len([m for m in _api_get(base_con_asistente, "/api/transacciones", cab) if m["tipo"] == "gasto"]) == antes, "proponer no escribe"
        boton = page.locator(".asistente-propuesta:has-text('Gasto de $31.00') .asistente-btn-principal")
        boton.dblclick()
        page.wait_for_selector(".asistente-propuesta-resultado:has-text('Listo')", timeout=10000)
        assert len([m for m in _api_get(base_con_asistente, "/api/transacciones", cab) if m["tipo"] == "gasto"]) == antes + 1, "una sola vez"
        assert sum(1 for ruta, _ in servicio_falso.recibidos if ruta == "/web/confirmar") == 1
        # La pantalla de movimientos se actualiza sin recargar la página
        _ir(page, "tab-movimientos")
        _esperar(page, "document.getElementById('tabla-movimientos').innerText.includes('Súper (asistente)')")
        # Nueva conversación limpia la lista
        page.click("#asistente-panel [aria-label='Nueva conversación']")
        assert page.locator(".asistente-msg-persona").count() == 0 and "Hola, soy tu asistente" in page.inner_text("#asistente-mensajes")
        assert not errores, errores
    finally:
        ctx.close()


def test_el_asistente_web_lee_fotos_y_notas_de_voz(navegador, base_con_asistente, servicio_falso, tmp_path_str):
    usuario = "burbuja_medios"
    master_db.create_user_entry(usuario, security.hash_password(CLAVE), email=f"{usuario}@test.local")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        ctx.grant_permissions(["microphone"], origin=base_con_asistente)
        _entrar(page, base_con_asistente, usuario)
        page.click("#asistente-burbuja")
        # Foto: se reduce a JPEG en el navegador y viaja como archivo
        ruta = os.path.join(tmp_path_str, "ticket.png")
        with open(ruta, "wb") as f:
            f.write(_png_valido(40))
        page.set_input_files("#asistente-archivo", ruta)
        page.wait_for_selector("#asistente-adjunto:not(.hidden)")
        assert page.inner_text(".asistente-adjunto-texto") == "Foto" and page.get_attribute(".asistente-adjunto-vista", "src").startswith("data:image/")
        page.fill("#asistente-texto", "el súper")
        page.press("#asistente-texto", "Enter")
        page.wait_for_selector(".asistente-msg-asistente:has-text('Recibí tu foto (image/jpeg')", timeout=10000)
        assert page.locator("#asistente-adjunto.hidden").count() == 1, "el adjunto se limpia al enviar"
        assert page.locator(".asistente-msg-persona").last.inner_text() == "[Foto] el súper"
        # Un archivo que no es imagen no se adjunta
        feo = os.path.join(tmp_path_str, "nota.txt")
        with open(feo, "wb") as f:
            f.write(b"esto no es una imagen")
        page.set_input_files("#asistente-archivo", feo)
        page.wait_for_selector(".toast-error")
        assert page.locator("#asistente-adjunto:not(.hidden)").count() == 0
        # Nota de voz: se graba como WAV de 16 kHz en el navegador (micrófono falso de Chromium)
        page.click("#asistente-mic")
        page.wait_for_selector("#asistente-mic.grabando")
        assert page.get_attribute("#asistente-mic", "aria-label") == "Detener la grabación"
        page.wait_for_timeout(1500)
        page.click("#asistente-mic")
        page.wait_for_selector("#asistente-adjunto:not(.hidden)")
        assert page.inner_text(".asistente-adjunto-texto").startswith("Nota de voz")
        page.click("#asistente-adjunto button")   # quitar la nota
        # Cerrar el panel (o Escape) corta la grabación: el micrófono no sigue abierto sin que se vea
        page.click("#asistente-mic")
        page.wait_for_selector("#asistente-mic.grabando")
        page.keyboard.press("Escape")
        page.wait_for_selector("#asistente-panel.hidden", state="attached")
        assert page.locator("#asistente-mic.grabando").count() == 0 and page.locator("#asistente-adjunto:not(.hidden)").count() == 0, "al cerrar no queda grabación ni adjunto"
        page.click("#asistente-burbuja")
        # Dos clics seguidos en el micrófono no abren dos grabaciones
        page.dblclick("#asistente-mic")
        page.wait_for_timeout(1200)
        if page.locator("#asistente-mic.grabando").count():
            page.click("#asistente-mic")
        page.wait_for_selector("#asistente-adjunto:not(.hidden)")
        assert page.inner_text(".asistente-adjunto-texto").startswith("Nota de voz")
        page.click("#asistente-enviar")
        page.wait_for_selector(".asistente-msg-asistente:has-text('Recibí tu audio (audio/wav')", timeout=10000)
        enviado = [c for r, c in servicio_falso.recibidos if r == "/web/mensaje" and c.get("adjunto")][-1]
        import base64
        wav = base64.b64decode(enviado["adjunto"]["datos_b64"])
        assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE" and 16000 * 2 * 1 <= len(wav) <= 16000 * 2 * 4, len(wav)
        assert not errores, errores
    finally:
        ctx.close()


def test_si_el_administrador_apaga_el_asistente_la_burbuja_desaparece(navegador, base_con_asistente, servicio_falso):
    usuario = "burbuja_apagada"
    fila = master_db.create_user_entry(usuario, security.hash_password(CLAVE), email=f"{usuario}@test.local")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base_con_asistente, usuario)
        page.click("#asistente-burbuja")
        master_db.set_asistente_activo(fila["id"], False)
        page.fill("#asistente-texto", "hola")
        page.press("#asistente-texto", "Enter")
        page.wait_for_selector(".toast-error:has-text('desactivó el asistente')", timeout=10000)
        _esperar(page, "document.getElementById('asistente-web') === null")
        assert page.is_visible("#vista-app"), "la app sigue abierta: apagar el asistente no cierra la sesión"
        page.reload()
        page.wait_for_selector("#vista-app:not(.hidden)")
        page.wait_for_timeout(800)
        assert page.locator("#asistente-web").count() == 0, "con el asistente apagado la burbuja no vuelve a salir"
        master_db.set_asistente_activo(fila["id"], True)
        page.reload()
        page.wait_for_selector("#asistente-burbuja", timeout=10000)
        # Al cerrar la sesión la burbuja se va
        page.evaluate("document.querySelector('[data-on-click*=\"logoutUsuario\"]')?.click()")
        page.wait_for_selector("#modal-confirmar:not(.hidden)")
        page.click("#btn-confirmar-aceptar")   # sin esperar a que el modal se oculte: según la edición, al salir se pasa a otra vista
        _esperar(page, "document.getElementById('asistente-web') === null")
        assert not errores, errores
    finally:
        ctx.close()


def test_los_avisos_no_se_tapan_con_la_burbuja(navegador, base_con_asistente, servicio_falso):
    usuario = "burbuja_avisos"
    master_db.create_user_entry(usuario, security.hash_password(CLAVE), email=f"{usuario}@test.local")
    ctx, page, errores = _contexto(navegador, VIEWPORTS["laptop"])
    try:
        _entrar(page, base_con_asistente, usuario)
        page.wait_for_selector("#asistente-burbuja")
        page.evaluate("document.querySelector('#toast-root').innerHTML = '<div class=\"toast toast-ok\" id=\"t-prueba\">Aviso de prueba</div>'")
        aviso = page.locator("#t-prueba").bounding_box()
        burbuja = page.locator("#asistente-burbuja").bounding_box()
        assert aviso["y"] + aviso["height"] <= burbuja["y"] + 1, ("el aviso queda por encima de la burbuja", aviso, burbuja)
        assert not errores, errores
    finally:
        ctx.close()


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    from ejecutor import ejecutar, verificar_cobertura
    escenario.activar_modo_prueba()  # el servidor hereda el reloj fijo del escenario
    proc, base = _arrancar_servidor()
    proc2 = falso = None
    try:
        # Tras el arranque: el servidor ya creó la base maestra en TMP/data.
        master_db.create_user_entry(USUARIO, security.hash_password(CLAVE), email=f"{USUARIO}@test.local")
        _sembrar_escenario(base)
        falso, proc2, base_con_asistente = _montar_servicio_falso(base, USUARIO)
        carpeta_archivos = tempfile.mkdtemp(prefix="finanzas_archivos_")
        with sync_playwright() as p:
            # Micrófono falso: Chromium «graba» un tono sin pedir permiso ni necesitar un dispositivo (la prueba de la nota de voz)
            navegador = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
            registradas = [
                (test_login_incorrecto_muestra_error, lambda: test_login_incorrecto_muestra_error(navegador, base)),
                (test_login_sin_fab_con_texto_fijo_y_ojo_de_contrasena, lambda: test_login_sin_fab_con_texto_fijo_y_ojo_de_contrasena(navegador, base)),
                (test_login_pie_legal_a_13px_y_en_tipo_oracion, lambda: test_login_pie_legal_a_13px_y_en_tipo_oracion(navegador, base)),
                (test_inversiones_pagina_sin_borde_un_solo_nivel_de_tarjeta, lambda: test_inversiones_pagina_sin_borde_un_solo_nivel_de_tarjeta(navegador, base)),
                (test_avisos_sin_barra_de_color_y_botones_sin_brillo_dorado, lambda: test_avisos_sin_barra_de_color_y_botones_sin_brillo_dorado(navegador, base)),
                (test_boton_de_texto_dorado_no_queda_dorado_sobre_dorado_al_pasar_el_cursor, lambda: test_boton_de_texto_dorado_no_queda_dorado_sobre_dorado_al_pasar_el_cursor(navegador, base)),
                (test_enlace_de_accion_sin_fondo_dorado_al_pasar_el_cursor, lambda: test_enlace_de_accion_sin_fondo_dorado_al_pasar_el_cursor(navegador, base)),
                (test_login_movil_centrado_y_sin_saltar_al_fallar, lambda: test_login_movil_centrado_y_sin_saltar_al_fallar(navegador, base)),
                (test_error_de_fondos_insuficientes_se_lee_a_la_izquierda_y_se_anuncia, lambda: test_error_de_fondos_insuficientes_se_lee_a_la_izquierda_y_se_anuncia(navegador, base)),
                (test_formularios_se_abren_con_el_mas_y_la_tabla_ocupa_todo_el_ancho, lambda: test_formularios_se_abren_con_el_mas_y_la_tabla_ocupa_todo_el_ancho(navegador, base)),
                (test_fijo_con_error_muestra_el_aviso_dentro_del_modal, lambda: test_fijo_con_error_muestra_el_aviso_dentro_del_modal(navegador, base)),
                (test_cancelar_o_eliminar_devuelven_el_dinero_y_solo_cancelar_deja_constancia, lambda: test_cancelar_o_eliminar_devuelven_el_dinero_y_solo_cancelar_deja_constancia(navegador, base)),
                (test_cuadro_de_quitar_en_movil_con_botones_de_44px, lambda: test_cuadro_de_quitar_en_movil_con_botones_de_44px(navegador, base)),
                (test_recorrido_pestanas_laptop, lambda: test_recorrido_pestanas_laptop(navegador, base)),
                (test_recorrido_pestanas_movil, lambda: test_recorrido_pestanas_movil(navegador, base)),
                (test_registrar_ingreso_y_modal_accesible, lambda: test_registrar_ingreso_y_modal_accesible(navegador, base)),
                (test_resumen_muestra_las_cifras_del_escenario, lambda: test_resumen_muestra_las_cifras_del_escenario(navegador, base)),
                (test_movimientos_y_resumen_muestran_el_mismo_periodo, lambda: test_movimientos_y_resumen_muestran_el_mismo_periodo(navegador, base)),
                (test_lista_de_movimientos_sigue_el_periodo_de_cobro, lambda: test_lista_de_movimientos_sigue_el_periodo_de_cobro(navegador, base)),
                (test_doble_clic_al_guardar_tarjeta_no_duplica, lambda: test_doble_clic_al_guardar_tarjeta_no_duplica(navegador, base)),
                (test_pagos_se_adaptan_a_la_frecuencia, lambda: test_pagos_se_adaptan_a_la_frecuencia(navegador, base)),
                (test_presupuesto_muestra_totales_en_una_linea, lambda: test_presupuesto_muestra_totales_en_una_linea(navegador, base)),
                (test_selector_de_periodo_cambia_las_tarjetas, lambda: test_selector_de_periodo_cambia_las_tarjetas(navegador, base)),
                (test_fijos_y_msi_aparecen_con_su_avance, lambda: test_fijos_y_msi_aparecen_con_su_avance(navegador, base)),
                (test_cinta_periodos_pinta_libre_y_selecciona, lambda: test_cinta_periodos_pinta_libre_y_selecciona(navegador, base)),
                (test_inicio_cinta_muestra_el_flujo_de_cada_periodo_y_el_hero_el_efectivo, lambda: test_inicio_cinta_muestra_el_flujo_de_cada_periodo_y_el_hero_el_efectivo(navegador, base)),
                (test_admin_busca_filtra_y_pide_el_nombre_para_eliminar, lambda: test_admin_busca_filtra_y_pide_el_nombre_para_eliminar(navegador, base)),
                (test_admin_apaga_y_enciende_el_asistente_de_una_cuenta_desde_el_menu, lambda: test_admin_apaga_y_enciende_el_asistente_de_una_cuenta_desde_el_menu(navegador, base)),
                (test_la_burbuja_no_existe_sin_servicio_y_aparece_abajo_a_la_derecha_con_el, lambda: test_la_burbuja_no_existe_sin_servicio_y_aparece_abajo_a_la_derecha_con_el(navegador, base, base_con_asistente)),
                (test_el_asistente_web_conversa_muestra_el_texto_sin_interpretarlo_y_confirma_una_sola_vez, lambda: test_el_asistente_web_conversa_muestra_el_texto_sin_interpretarlo_y_confirma_una_sola_vez(navegador, base_con_asistente, falso)),
                (test_el_asistente_web_lee_fotos_y_notas_de_voz, lambda: test_el_asistente_web_lee_fotos_y_notas_de_voz(navegador, base_con_asistente, falso, carpeta_archivos)),
                (test_si_el_administrador_apaga_el_asistente_la_burbuja_desaparece, lambda: test_si_el_administrador_apaga_el_asistente_la_burbuja_desaparece(navegador, base_con_asistente, falso)),
                (test_los_avisos_no_se_tapan_con_la_burbuja, lambda: test_los_avisos_no_se_tapan_con_la_burbuja(navegador, base_con_asistente, falso)),
                (test_tema_y_modo_discrecion_cambian_sin_perder_datos, lambda: test_tema_y_modo_discrecion_cambian_sin_perder_datos(navegador, base)),
                (test_movimiento_crear_editar_y_borrar, lambda: test_movimiento_crear_editar_y_borrar(navegador, base)),
                (test_meta_depositar_y_retirar, lambda: test_meta_depositar_y_retirar(navegador, base)),
                (test_fallo_de_red_avisa_al_usuario, lambda: test_fallo_de_red_avisa_al_usuario(navegador, base)),
                (test_403_de_permisos_no_cierra_la_sesion, lambda: test_403_de_permisos_no_cierra_la_sesion(navegador, base)),
                (test_403_de_cuenta_desactivada_cierra_la_sesion, lambda: test_403_de_cuenta_desactivada_cierra_la_sesion(navegador, base)),
                (test_cerrar_sesion_con_una_carga_en_curso_no_muestra_sesion_expirada, lambda: test_cerrar_sesion_con_una_carga_en_curso_no_muestra_sesion_expirada(navegador, base)),
                (test_recargar_con_un_token_vencido_pide_entrar_y_lo_explica, lambda: test_recargar_con_un_token_vencido_pide_entrar_y_lo_explica(navegador, base)),
                (test_un_fallo_del_servidor_al_abrir_la_pagina_no_tira_el_token, lambda: test_un_fallo_del_servidor_al_abrir_la_pagina_no_tira_el_token(navegador, base)),
                (test_respuesta_tardia_de_la_sesion_anterior_no_cierra_la_nueva, lambda: test_respuesta_tardia_de_la_sesion_anterior_no_cierra_la_nueva(navegador, base)),
                (test_cambiar_la_contrasena_con_una_carga_en_vuelo_no_cierra_la_sesion_nueva, lambda: test_cambiar_la_contrasena_con_una_carga_en_vuelo_no_cierra_la_sesion_nueva(navegador, base)),
                (test_menu_de_usuario_sin_eliminar_cuenta_y_rol_solo_para_admin, lambda: test_menu_de_usuario_sin_eliminar_cuenta_y_rol_solo_para_admin(navegador, base)),
                (test_menu_usuario_teclado_y_hoja_movil, lambda: test_menu_usuario_teclado_y_hoja_movil(navegador, base)),
                (test_inversion_nueva_aparece_en_el_portafolio, lambda: test_inversion_nueva_aparece_en_el_portafolio(navegador, base)),
                (test_fijo_quincenal_y_gasto_con_su_ingreso, lambda: test_fijo_quincenal_y_gasto_con_su_ingreso(navegador, base)),
                (test_respaldo_desde_el_menu_se_descarga_y_se_restaura, lambda: test_respaldo_desde_el_menu_se_descarga_y_se_restaura(navegador, base)),
                (test_mi_perfil_exige_contrasena_actual_para_cambiarla, lambda: test_mi_perfil_exige_contrasena_actual_para_cambiarla(navegador, base)),
                (test_login_recorta_igual_que_el_alta_y_una_clave_larga_entra, lambda: test_login_recorta_igual_que_el_alta_y_una_clave_larga_entra(navegador, base)),
                (test_eliminar_cuenta_exige_escribir_el_usuario, lambda: test_eliminar_cuenta_exige_escribir_el_usuario(navegador, base)),
                (test_calculadora_rf_calcula_y_bloquea_tasa_fuera_de_rango, lambda: test_calculadora_rf_calcula_y_bloquea_tasa_fuera_de_rango(navegador, base)),
                (test_inicio_sin_movimientos_invita_a_empezar_y_se_oculta_al_tener_datos, lambda: test_inicio_sin_movimientos_invita_a_empezar_y_se_oculta_al_tener_datos(navegador, base)),
                (test_importe_grande_no_se_parte_en_movil, lambda: test_importe_grande_no_se_parte_en_movil(navegador, base)),
                (test_titulo_de_la_barra_no_se_corta_en_movil, lambda: test_titulo_de_la_barra_no_se_corta_en_movil(navegador, base)),
                (test_componentes_cero_en_tinta_neutra_y_signo_menos_tipografico, lambda: test_componentes_cero_en_tinta_neutra_y_signo_menos_tipografico(navegador, base)),
                (test_moneda_una_vez_y_pestanas_internas_cortas_en_movil, lambda: test_moneda_una_vez_y_pestanas_internas_cortas_en_movil(navegador, base)),
                (test_resumen_leyenda_hero_plano_y_dona_a_todo_el_ancho, lambda: test_resumen_leyenda_hero_plano_y_dona_a_todo_el_ancho(navegador, base)),
                (test_movimientos_en_movil_sin_deslizar_y_con_papelera, lambda: test_movimientos_en_movil_sin_deslizar_y_con_papelera(navegador, base)),
                (test_fijos_msi_con_barra_y_filtro_de_mes_con_marcador, lambda: test_fijos_msi_con_barra_y_filtro_de_mes_con_marcador(navegador, base)),
                (test_presupuesto_panel_sin_borde_y_textos_sencillos, lambda: test_presupuesto_panel_sin_borde_y_textos_sencillos(navegador, base)),
                (test_metas_con_texto_meta_eliminar_visible_y_avance_minimo, lambda: test_metas_con_texto_meta_eliminar_visible_y_avance_minimo(navegador, base)),
                (test_inversiones_moneda_una_vez_sin_tasa_repetida_y_boton_secundario, lambda: test_inversiones_moneda_una_vez_sin_tasa_repetida_y_boton_secundario(navegador, base)),
                (test_calculadora_rf_textos_sencillos_y_sin_tarjetas_con_ícono, lambda: test_calculadora_rf_textos_sencillos_y_sin_tarjetas_con_ícono(navegador, base)),
                (test_pasada_final_encabezados_franjas_y_deshabilitados, lambda: test_pasada_final_encabezados_franjas_y_deshabilitados(navegador, base)),
                (test_marco_de_la_app_titulo_menu_y_campo_del_modal_a_la_vista, lambda: test_marco_de_la_app_titulo_menu_y_campo_del_modal_a_la_vista(navegador, base)),
                (test_botones_de_fila_y_pestanas_tienen_area_tactil_de_44px, lambda: test_botones_de_fila_y_pestanas_tienen_area_tactil_de_44px(navegador, base)),
                (test_red_cortada_al_guardar_no_duplica_al_reintentar, lambda: test_red_cortada_al_guardar_no_duplica_al_reintentar(navegador, base)),
                (test_sesion_vencida_a_mitad_de_uso_vuelve_al_login, lambda: test_sesion_vencida_a_mitad_de_uso_vuelve_al_login(navegador, base)),
                (test_error_de_javascript_llega_al_registro, lambda: test_error_de_javascript_llega_al_registro(navegador, base)),
                (test_respuesta_500_de_la_api_se_reporta_con_su_id, lambda: test_respuesta_500_de_la_api_se_reporta_con_su_id(navegador, base)),
                (test_otra_pestana_cambio_el_movimiento_y_no_se_pisa, lambda: test_otra_pestana_cambio_el_movimiento_y_no_se_pisa(navegador, base)),
                (test_icono_del_menu_colapsado_queda_centrado_con_el_cursor, lambda: test_icono_del_menu_colapsado_queda_centrado_con_el_cursor(navegador, base)),
                (test_campos_y_selectores_nativos_siguen_el_tema, lambda: test_campos_y_selectores_nativos_siguen_el_tema(navegador, base)),
                (test_boton_cotizar_no_sobresale_del_campo, lambda: test_boton_cotizar_no_sobresale_del_campo(navegador, base)),
                (test_etiqueta_del_tipo_de_cambio_avisa_cuando_es_de_respaldo, lambda: test_etiqueta_del_tipo_de_cambio_avisa_cuando_es_de_respaldo(navegador, base)),
                (test_exportar_presupuesto_muestra_que_cambia_antes_de_reemplazar, lambda: test_exportar_presupuesto_muestra_que_cambia_antes_de_reemplazar(navegador, base)),
                (test_exportar_presupuesto_pide_recargar_el_resumen, lambda: test_exportar_presupuesto_pide_recargar_el_resumen(navegador, base)),
                (test_tarjeta_elige_su_meta_en_un_selector_y_la_conserva, lambda: test_tarjeta_elige_su_meta_en_un_selector_y_la_conserva(navegador, base)),
                (test_grafica_del_historial_etiqueta_los_periodos_segun_el_cobro, lambda: test_grafica_del_historial_etiqueta_los_periodos_segun_el_cobro(navegador, base)),
                (test_dolares_se_rotulan_us_y_los_titulos_respetan_el_modo_privacidad, lambda: test_dolares_se_rotulan_us_y_los_titulos_respetan_el_modo_privacidad(navegador, base)),
                (test_barra_de_seleccion_multiple_aparece_al_marcar_un_movimiento, lambda: test_barra_de_seleccion_multiple_aparece_al_marcar_un_movimiento(navegador, base)),
                (test_modo_discrecion_cubre_todas_las_pestanas, lambda: test_modo_discrecion_cubre_todas_las_pestanas(navegador, base)),
                (test_moneda_base_euro_se_aplica_y_persiste, lambda: test_moneda_base_euro_se_aplica_y_persiste(navegador, base)),
                (test_moneda_base_convierte_las_listas_guardadas_en_mxn, lambda: test_moneda_base_convierte_las_listas_guardadas_en_mxn(navegador, base)),
                (test_comentario_se_envia_desde_el_menu_y_queda_en_la_base, lambda: test_comentario_se_envia_desde_el_menu_y_queda_en_la_base(navegador, base)),
                (test_asistente_telegram_genera_el_qr_detecta_la_vinculacion_y_se_desconecta, lambda: test_asistente_telegram_genera_el_qr_detecta_la_vinculacion_y_se_desconecta(navegador, base)),
                (test_asistente_telegram_cerrar_el_modal_borra_el_codigo_y_en_movil_no_desborda, lambda: test_asistente_telegram_cerrar_el_modal_borra_el_codigo_y_en_movil_no_desborda(navegador, base)),
                (test_asistente_telegram_sin_bot_en_el_servidor_lo_dice, lambda: test_asistente_telegram_sin_bot_en_el_servidor_lo_dice(navegador, base)),
            ]
            codigo = ejecutar(verificar_cobertura(sys.modules[__name__], registradas), "test_frontend")
            navegador.close()
    finally:
        proc.terminate()
        if proc2:
            proc2.terminate()
        if falso:
            falso.cerrar()
        shutil.rmtree(TMP, ignore_errors=True)
        shutil.rmtree(LOGS, ignore_errors=True)
    sys.exit(codigo)
