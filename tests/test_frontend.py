"""tests/test_frontend.py - Smoke test del navegador con Playwright contra un servidor temporal.

Login, registrar un ingreso, ver cada pestaña sin errores de JS ni desbordamiento horizontal
(1366x768 y móvil), y foco de teclado en el modal de edición.
Requiere playwright (se ejecuta en Docker, ver tests/run_frontend.sh). Nunca toca 'data/' del proyecto.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
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

import escenario  # noqa: E402
import master_db  # noqa: E402
import security  # noqa: E402

USUARIO, CLAVE = "front_user", "Passw0rd#Front1"
PESTANAS = ["tab-dashboard", "tab-movimientos", "tab-apartados", "tab-inversiones", "tab-presupuesto", "tab-calculadora-rf"]
VIEWPORTS = {"laptop": {"width": 1366, "height": 768}, "movil": {"width": 390, "height": 844}}


def _puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _arrancar_servidor():
    puerto = _puerto_libre()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(puerto)],
        cwd=TMP, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
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
        page.fill("#fecha", time.strftime("%Y-%m-%d"))
        page.select_option("#tipo", "ingreso")
        page.fill("#monto", "1234.56")
        page.fill("#descripcion", "ingreso-desde-playwright")
        opciones = page.locator("#categoria option").evaluate_all("os => os.map(o => o.value).filter(v => v)")
        if opciones:
            page.select_option("#categoria", opciones[0])
        page.click("#transaccion-form [type=submit]")
        page.wait_for_selector("text=ingreso-desde-playwright", timeout=10000)

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
        historial = page.locator("#lista-resumen-mensual .monthly-card")
        assert historial.count() == 3, historial.count()
        primera = historial.nth(0).inner_text()
        assert "oct 2026" in primera.lower() and "+$14,000.00" in primera and "-$14,799.00" in primera, primera
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
        page.wait_for_timeout(2000)
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
        assert not errores, errores
    finally:
        ctx.close()


def test_fijos_y_msi_aparecen_con_su_avance(navegador, base):
    ctx, page, errores = _flujo_escenario(navegador, base)
    try:
        _ir(page, "tab-movimientos")
        page.click("#btn-subtab-fijos")
        page.wait_for_selector("#lista-msi .rec-item")
        msi = page.inner_text("#lista-msi")
        assert "Laptop a 12 MSI" in msi, msi
        assert page.inner_text("#lbl-fijos-deuda") != "$0.00", "la deuda MSI debe mostrarse en Fijos y MSI"
        fijos = page.inner_text("#lista-fijos")
        assert "2026-11-15" in fijos, fijos          # próximo cargo de Netflix tras el 15 de octubre
        assert "2026-10-31" in fijos, fijos          # próxima nómina
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

        page.once("dialog", lambda d: d.accept())
        page.locator("#tabla-movimientos tr", has_text="e2e-cafe").locator(".btn-eliminar").click()
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


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    from ejecutor import ejecutar, verificar_cobertura
    escenario.activar_modo_prueba()  # el servidor hereda el reloj fijo del escenario
    proc, base = _arrancar_servidor()
    try:
        # Tras el arranque: el servidor ya creó la base maestra en TMP/data.
        master_db.create_user_entry(USUARIO, security.hash_password(CLAVE), email=f"{USUARIO}@test.local")
        _sembrar_escenario(base)
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            registradas = [
                (test_login_incorrecto_muestra_error, lambda: test_login_incorrecto_muestra_error(navegador, base)),
                (test_recorrido_pestanas_laptop, lambda: test_recorrido_pestanas_laptop(navegador, base)),
                (test_recorrido_pestanas_movil, lambda: test_recorrido_pestanas_movil(navegador, base)),
                (test_registrar_ingreso_y_modal_accesible, lambda: test_registrar_ingreso_y_modal_accesible(navegador, base)),
                (test_resumen_muestra_las_cifras_del_escenario, lambda: test_resumen_muestra_las_cifras_del_escenario(navegador, base)),
                (test_movimientos_y_resumen_muestran_el_mismo_periodo, lambda: test_movimientos_y_resumen_muestran_el_mismo_periodo(navegador, base)),
                (test_lista_de_movimientos_sigue_el_periodo_de_cobro, lambda: test_lista_de_movimientos_sigue_el_periodo_de_cobro(navegador, base)),
                (test_doble_clic_al_guardar_tarjeta_no_duplica, lambda: test_doble_clic_al_guardar_tarjeta_no_duplica(navegador, base)),
                (test_pagos_se_adaptan_a_la_frecuencia, lambda: test_pagos_se_adaptan_a_la_frecuencia(navegador, base)),
                (test_presupuesto_muestra_totales_en_una_linea, lambda: test_presupuesto_muestra_totales_en_una_linea(navegador, base)),
                (test_fijos_y_msi_aparecen_con_su_avance, lambda: test_fijos_y_msi_aparecen_con_su_avance(navegador, base)),
                (test_tema_y_modo_discrecion_cambian_sin_perder_datos, lambda: test_tema_y_modo_discrecion_cambian_sin_perder_datos(navegador, base)),
                (test_movimiento_crear_editar_y_borrar, lambda: test_movimiento_crear_editar_y_borrar(navegador, base)),
                (test_meta_depositar_y_retirar, lambda: test_meta_depositar_y_retirar(navegador, base)),
            ]
            codigo = ejecutar(verificar_cobertura(sys.modules[__name__], registradas), "test_frontend")
            navegador.close()
    finally:
        proc.terminate()
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)
