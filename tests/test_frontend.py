"""tests/test_frontend.py - Smoke test del navegador con Playwright contra un servidor temporal.

Login, registrar un ingreso, ver cada pestaña sin errores de JS ni desbordamiento horizontal
(1366x768 y móvil), y foco de teclado en el modal de edición.
Requiere playwright (se ejecuta en Docker, ver tests/run_frontend.sh). Nunca toca 'data/' del proyecto.
"""

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


def test_flujo_completo(navegador, base, nombre_vp, viewport):
    ctx = navegador.new_context(viewport=viewport)
    page = ctx.new_page()
    errores = []
    page.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
    # Solo errores propios: recursos externos (fuentes, CDN) pueden fallar sin internet.
    page.on("console", lambda m: errores.append(f"console: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)

    _login(page, base)
    _sin_desbordamiento(page, f"{nombre_vp}/inicio")

    for tab in PESTANAS:
        # Clic por JS: en móvil el botón vive en el sidebar colapsado, fuera de la pantalla.
        page.evaluate(f"document.querySelector('[data-tab=\"{tab}\"]').click()")
        page.wait_for_timeout(400)
        _sin_desbordamiento(page, f"{nombre_vp}/{tab}")

    assert not errores, f"{nombre_vp}: errores de JS\n" + "\n".join(errores)
    return ctx, page


def test_registrar_ingreso_y_modal_accesible(page, base):
    page.evaluate("document.querySelector('[data-tab=\"tab-movimientos\"]').click()")
    page.fill("#fecha", time.strftime("%Y-%m-%d"))
    page.select_option("#tipo", "ingreso")
    page.fill("#monto", "1234.56")
    page.fill("#descripcion", "ingreso-desde-playwright")
    opciones = page.locator("#categoria option").evaluate_all("os => os.map(o => o.value).filter(v => v)")
    if opciones:
        page.select_option("#categoria", opciones[0])
    page.click("#transaccion-form [type=submit]")
    page.wait_for_selector("text=ingreso-desde-playwright", timeout=10000)

    editar = page.locator(".btn-editar").first
    editar.click()
    page.wait_for_selector("#modal-editar-transaccion:not(.hidden)")
    page.wait_for_timeout(300)
    assert page.evaluate("document.getElementById('modal-editar-transaccion').contains(document.activeElement)"), \
        "al abrir el modal el foco debe quedar dentro"
    for _ in range(30):
        page.keyboard.press("Tab")
    assert page.evaluate("document.getElementById('modal-editar-transaccion').contains(document.activeElement)"), \
        "Tab no debe sacar el foco del modal"
    page.keyboard.press("Escape")
    page.wait_for_selector("#modal-editar-transaccion.hidden", state="attached")
    assert page.evaluate("document.activeElement && document.activeElement.classList.contains('btn-editar')"), \
        "al cerrar, el foco debe volver al botón que abrió el modal"


if __name__ == "__main__":
    proc, base = _arrancar_servidor()
    try:
        # Tras el arranque: el servidor ya creó la base maestra en TMP/data.
        master_db.create_user_entry(USUARIO, security.hash_password(CLAVE), email=f"{USUARIO}@test.local")
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            test_login_incorrecto_muestra_error(navegador, base)
            for nombre_vp, vp in VIEWPORTS.items():
                ctx, page = test_flujo_completo(navegador, base, nombre_vp, vp)
                if nombre_vp == "laptop":
                    test_registrar_ingreso_y_modal_accesible(page, base)
                ctx.close()
            navegador.close()
    finally:
        proc.terminate()
        shutil.rmtree(TMP, ignore_errors=True)
    print("OK")
