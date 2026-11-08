"""tests/auditoria/escritorio.py - Capas B (escritorio, 1280×800) y C (celular, 390×844, táctil) de la auditoría.

Cada episodio abre la app en un navegador (1280×800), entra como Laura y hace acciones con clics, igual que una
persona: registrar un gasto o ingreso, quitar un movimiento (cancelar o eliminar), filtrar la tabla, mirar el
resumen, recargar y cambiar de día. Después de cada acción compara lo que muestra la pantalla con el oráculo
(tests/auditoria/oraculo.py), y registra errores de JavaScript y respuestas 5xx.

Lo que falla queda en fallos/<episodio>-<paso>-<semilla>/ con la base de la persona y una captura de pantalla.

Corre dentro del contenedor de Playwright (tests/auditoria/run.sh --capa escritorio). No se llama directamente.
"""

import datetime
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import acciones  # noqa: E402
import personas  # noqa: E402
from bitacora import Bitacora  # noqa: E402
from oraculo import Oraculo  # noqa: E402

HOY_INICIAL = datetime.date(2026, 10, 1)
PASOS_MIN, PASOS_MAX = 5, 30
PESOS_UI = (
    ("gasto", 30), ("ingreso", 10), ("cancelar", 12), ("eliminar", 8),
    ("filtrar", 6), ("dashboard", 8), ("recargar", 5), ("avanzar_dia", 5),
)
FILTROS = ("", "gasto", "ingreso", "cancelado")
# Escritorio (capa B) y celular (capa C): mismo recorrido, otro dispositivo
DISPOSITIVOS = {
    "escritorio": {"viewport": {"width": 1280, "height": 800}},
    "celular": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True},
}
IGNORAR_EN_COPIA = shutil.ignore_patterns(
    "data", ".git", "tests", "auditoria", "__pycache__", "*.db", "backups", "venv", ".venv",
    "node_modules", "graphify-out", "docs",
)
# Mismo lanzador que las pruebas de navegador: cotizaciones fijas, sin salir a internet.
LANZADOR = """
import sys
import mercado
COTIZACIONES = {"USDMXN=X": 19.5, "EURUSD=X": 1.08}
mercado.consultar_precio_y_moneda_mercado = lambda t: (COTIZACIONES.get(t.strip().upper()), "USD")
import uvicorn
uvicorn.run("main:app", host="127.0.0.1", port=int(sys.argv[1]))
"""


class Servidor:
    """Un servidor de la app en una carpeta temporal, con reloj fijo. Cambiar de día lo reinicia en el mismo puerto."""

    def __init__(self, tmp, logs):
        self.tmp = tmp
        self.logs = logs
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.puerto = s.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.puerto}"
        self.proc = None

    def arrancar(self, hoy):
        env = {
            **os.environ, "FINANZAS_MODO_PRUEBA": "1", "FINANZAS_LOG_DIR": self.logs,
            "FINANZAS_HOY": f"{hoy.isoformat()}T10:00:00",
        }
        self.proc = subprocess.Popen(
            [sys.executable, "-c", LANZADOR, str(self.puerto)], cwd=self.tmp,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env,
        )
        for _ in range(120):
            try:
                urllib.request.urlopen(self.base + "/health", timeout=1)
                return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("el servidor no arrancó")

    def parar(self):
        if self.proc is not None:
            self.proc.terminate()
            self.proc.wait(timeout=20)
            self.proc = None


def _cargar_modulos(tmp):
    os.environ.setdefault("JWT_SECRET_KEY", "k" * 48)
    os.environ["DEFAULT_ADMIN_PASSWORD"] = "AdminAuditoria#12345"
    sys.path.insert(0, tmp)
    import database
    import master_db
    import security
    return types.SimpleNamespace(database=database, master_db=master_db, security=security)


def _login(page, base, usuario, clave=None):
    page.goto(base + "/login")
    # Con una sesión guardada, /login pasa a la app: entonces ya no hay nada que llenar
    page.wait_for_selector("#vista-app:not(.hidden), #login-username:visible", timeout=60000)
    if page.query_selector("#vista-app:not(.hidden)"):
        return
    _esperar_foco(page, "login-username")
    page.fill("#login-username", usuario)
    page.fill("#login-password", clave or personas.CLAVE)
    page.click("#form-login [type=submit]")
    page.wait_for_selector("#vista-app:not(.hidden)", timeout=20000)


def _reentrar(page, base, usuario, clave=None):
    """Abre la app tras reiniciar el servidor: espera a que la página decida entre la app y el login, y solo entra si hace falta."""
    page.goto(base + "/")
    page.wait_for_selector("#vista-app:not(.hidden), #login-username:visible", timeout=60000)
    if not page.query_selector("#vista-app:not(.hidden)"):
        _login(page, base, usuario, clave)


def _esperar_foco(page, id_campo, segundos=2.0):
    """Los cuadros mueven el foco a su primer campo un momento después de abrirse; escribir antes manda el texto allí."""
    limite = time.perf_counter() + segundos
    while time.perf_counter() < limite:
        if page.evaluate("document.activeElement && document.activeElement.id") == id_campo:
            return
        page.wait_for_timeout(50)


def _ir(page, pestana):
    page.evaluate(f"document.querySelector('[data-tab=\"{pestana}\"]').click()")
    page.wait_for_selector(f"#{pestana}.active")


def _primera_linea(error):
    """Primera línea del mensaje de un error; si el error no trae texto, su tipo."""
    lineas = str(error).splitlines()
    return (lineas[0] if lineas else type(error).__name__)[:200]


def _monto_visto(texto):
    """Convierte «$1,234.56» o «−$1,234.56» a pesos."""
    numero = re.search(r"[\d,]+\.\d{2}", texto)
    if not numero:
        return None
    valor = float(numero.group(0).replace(",", ""))
    return -valor if ("−" in texto or "-" in texto.split("$")[0]) else valor


def _ir_movimientos(page):
    """Abre Movimientos con la ventana en «Todo el historial», para contar contra toda la base."""
    _ir(page, "tab-movimientos")
    page.select_option("#filtro-ventana", value="todo")


def _tabla_visible(page):
    """(filas vigentes, filas canceladas) que muestra la tabla ahora."""
    vigentes = page.locator("#tabla-movimientos tr:has(button[title='Quitar movimiento'])").count()
    canceladas = page.locator("#tabla-movimientos tr.fila-cancelada").count()
    return vigentes, canceladas


def _esperar_tabla(page, oraculo, segundos=5.0):
    """Espera a que la tabla muestre lo que dice la base con el filtro actual. Devuelve (filtro, visto, esperado).
    Compara vigentes y cancelados por separado: un total igual puede esconder una fila que aún no se repintó."""
    limite = time.perf_counter() + segundos
    while True:
        filtro = page.eval_on_selector("#filtro-tipo", "e => e.value")
        esperado = _esperado_tabla(filtro, oraculo.movimientos(), oraculo.cancelados())
        visto = _tabla_visible(page)
        if visto == esperado or time.perf_counter() >= limite:
            return filtro, visto, esperado
        page.wait_for_timeout(200)


def _botones_quitar(page):
    return page.query_selector_all("#tabla-movimientos button[title='Quitar movimiento']")


def _esperado_tabla(filtro, mov, can):
    """(filas vigentes, filas canceladas) que debe mostrar la tabla con ese filtro."""
    if filtro == "cancelado":
        return 0, len(can)
    if filtro == "gasto":
        return sum(1 for f in mov if f[3] == "gasto"), 0
    if filtro == "ingreso":
        return sum(1 for f in mov if f[3] == "ingreso"), 0
    return len(mov), len(can)


def _alta(page, rng, oraculo, tipo, hoy):
    fecha = (hoy + datetime.timedelta(days=rng.randint(-20, 20))).isoformat()
    monto = acciones._monto(rng) if tipo == "gasto" else round(acciones._monto(rng) * 10, 2)
    categoria = rng.choice(acciones.CATEGORIAS_GASTO) if tipo == "gasto" else 12
    descripcion = rng.choice(acciones.DESCRIPCIONES)
    candidato = oraculo.minimo_desde(fecha, delta=-acciones._a_centavos(monto)) if tipo == "gasto" else None
    antes = len(oraculo.movimientos())
    page.evaluate("document.getElementById('btn-nuevo-movimiento').click()")
    page.wait_for_selector("#modal-nuevo-movimiento:not(.hidden)")
    page.fill("#fecha", fecha)
    page.select_option("#tipo", tipo)
    page.fill("#monto", f"{monto:.2f}")
    page.select_option("#categoria", value=str(categoria))
    if descripcion is not None:
        page.fill("#descripcion", descripcion)
    page.evaluate("document.querySelector('#modal-nuevo-movimiento #mensaje').textContent = ''")
    page.click("#modal-nuevo-movimiento button[type=submit]")
    # La página no admite evaluar texto como código (CSP): se espera por selector, no por función
    page.wait_for_selector(
        "#modal-nuevo-movimiento.hidden, #modal-nuevo-movimiento #mensaje:not(:empty)",
        state="attached", timeout=15000,
    )
    aceptado = not page.is_visible("#modal-nuevo-movimiento")
    mensaje = "" if aceptado else page.inner_text("#modal-nuevo-movimiento #mensaje").strip()
    if not aceptado:
        page.evaluate("document.querySelector('#modal-nuevo-movimiento .modal-actions .btn-secondary').click()")
        page.wait_for_selector("#modal-nuevo-movimiento.hidden", state="attached")
    return {
        "entrada": {"tipo": tipo, "fecha": fecha, "monto": monto, "categoria_id": categoria, "descripcion": descripcion},
        "aceptado": aceptado, "mensaje": mensaje, "candidato": candidato, "antes": antes,
        "monto_c": acciones._a_centavos(monto), "fecha_ref": fecha, "tipo_mov": tipo,
    }


def _resuelto(oraculo, modo, id_mov):
    """¿La base ya refleja quitar ese movimiento (cancelado o eliminado)?"""
    if modo == "cancelar":
        return any(c[1] == id_mov for c in oraculo.cancelados())
    return not any(f[0] == id_mov for f in oraculo.movimientos())


def _quitar(page, rng, oraculo, modo):
    _esperar_tabla(page, oraculo)  # una papelera tomada de una tabla sin repintar apunta a un movimiento viejo
    botones = _botones_quitar(page)
    if not botones:
        return None
    indice = rng.randrange(len(botones))
    id_mov = json.loads(botones[indice].get_attribute("data-on-click"))[0][1]
    fila = next((f for f in oraculo.movimientos() if f[0] == id_mov), None)
    if fila is None:  # la tabla muestra un movimiento que la base ya no tiene: se anota, no se intenta
        return {"entrada": {"id": id_mov, "modo": modo}, "objetivo": id_mov, "fantasma": True,
                "aceptado": False, "mensaje": "", "candidato": None, "fecha_ref": None, "tipo_mov": None,
                "monto_c": None}
    # Toque real, como una persona: se desplaza hasta la papelera y la toca con el puntero. Un clic por
    # código sobre un botón fuera de la pantalla no pasa por el mismo camino que un dedo.
    # Se busca la papelera por el id del movimiento al momento de tocarla: la tabla se repinta a veces
    # y una referencia tomada antes queda desconectada del DOM
    papelera = f"#tabla-movimientos button[data-on-click='[[\"eliminarTransaccion\",{id_mov}]]']"
    page.locator(papelera).click(timeout=10000)
    try:
        page.wait_for_selector("#modal-quitar-movimiento:not(.hidden)", timeout=5000)
    except Exception as error:
        # Diagnóstico: qué había en pantalla cuando el clic no abrió el modal
        diagnostico = page.evaluate("""(i) => {
            const b = document.querySelectorAll("#tabla-movimientos button[title='Quitar movimiento']")[i];
            const r = b ? b.getBoundingClientRect() : null;
            const modal = document.getElementById('modal-quitar-movimiento');
            return {botones: document.querySelectorAll("#tabla-movimientos button[title='Quitar movimiento']").length,
                    indice: i, conectado: !!(b && b.isConnected), en_tabla: !!(b && b.closest('#tabla-movimientos')),
                    caja: r ? [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] : null,
                    fila: b ? b.closest('tr').innerText.slice(0, 120) : null,
                    modal_clase: modal ? modal.className : null, filtro: document.getElementById('filtro-tipo').value,
                    ancho_ventana: window.innerWidth};
        }""", indice)
        raise RuntimeError(f"el clic en quitar no abrió el cuadro: {diagnostico}") from error
    page.evaluate(f"document.getElementById('btn-quitar-{'cancelar' if modo == 'cancelar' else 'eliminar'}').click()")
    page.wait_for_selector("#modal-quitar-movimiento.hidden", state="attached", timeout=15000)
    # Quitar un ingreso que deja el saldo en negativo abre «Forzar operación» cuando el servidor responde:
    # se espera a ver el diálogo o a que la base refleje la operación, lo que llegue primero
    limite = time.perf_counter() + 6.0
    while time.perf_counter() < limite and not page.is_visible("#modal-confirmar") and not _resuelto(oraculo, modo, id_mov):
        page.wait_for_timeout(150)
    forzado = None
    if page.is_visible("#modal-confirmar"):
        forzado = rng.random() < 0.3
        boton = "#btn-confirmar-aceptar" if forzado else "#btn-confirmar-cancelar"
        page.evaluate(f"document.querySelector('{boton}').click()")
        page.wait_for_selector("#modal-confirmar.hidden", state="attached", timeout=15000)
    limite = time.perf_counter() + 6.0
    while time.perf_counter() < limite and not _resuelto(oraculo, modo, id_mov):
        page.wait_for_timeout(150)
    return {
        "entrada": {"id": id_mov, "modo": modo, "fecha": fila[1], "monto": fila[2] / 100, "tipo": fila[3], "forzar": forzado},
        "objetivo": id_mov, "tipo_mov": fila[3], "monto_c": fila[2], "fecha_ref": fila[1],
        "aceptado": _resuelto(oraculo, modo, id_mov), "mensaje": "", "candidato": None,
    }


def _accion(nombre, page, rng, oraculo, hoy, servidor, cabecera_base):
    """Ejecuta una acción de la persona en pantalla. Devuelve (datos, invariantes que dependen de la acción)."""
    if nombre in ("gasto", "ingreso"):
        _ir_movimientos(page)
        return _alta(page, rng, oraculo, nombre, hoy)
    if nombre in ("cancelar", "eliminar"):
        _ir_movimientos(page)
        return _quitar(page, rng, oraculo, "cancelar" if nombre == "cancelar" else "eliminar")
    if nombre == "filtrar":
        _ir_movimientos(page)
        filtro = rng.choice(FILTROS)
        page.select_option("#filtro-tipo", value=filtro)
        page.wait_for_timeout(300)
        return {"entrada": {"filtro": filtro}, "aceptado": None, "mensaje": "", "candidato": None}
    if nombre == "dashboard":
        _ir(page, "tab-dashboard")
        page.wait_for_timeout(300)
        return {"entrada": {}, "aceptado": None, "mensaje": "", "candidato": None}
    if nombre == "recargar":
        page.reload()
        page.wait_for_selector("#vista-app:not(.hidden)", timeout=20000)
        _ir_movimientos(page)
        return {"entrada": {}, "aceptado": None, "mensaje": "", "candidato": None}
    raise ValueError(nombre)


def _esperar_base(oraculo, nombre, datos, pre, segundos=5.0):
    """Espera a que la base tenga el cambio que la operación aceptada debía dejar."""
    limite = time.perf_counter() + segundos
    while time.perf_counter() < limite:
        mov, can = oraculo.movimientos(), oraculo.cancelados()
        if nombre in ("gasto", "ingreso") and len(mov) == pre["antes"] + 1:
            return
        if nombre == "cancelar" and any(c[1] == datos["objetivo"] for c in can):
            return
        if nombre == "eliminar" and not any(f[0] == datos["objetivo"] for f in mov):
            return
        time.sleep(0.2)


def _verificar(nombre, datos, page, oraculo, pre, errores, respuestas_5xx):
    """Invariantes de un paso: lo que se ve coincide con la base y no hay errores ni 5xx."""
    problemas = []
    # Al recargar o cambiar de día, el navegador corta las peticiones en vuelo: no es un error de la app
    if nombre in ("recargar", "avanzar_dia"):
        errores = [e for e in errores if "Failed to fetch" not in e]
    if errores:
        problemas.append(f"errores de JavaScript: {errores[0][:160]}")
    if respuestas_5xx:
        problemas.append(f"respuesta del servidor {respuestas_5xx[0]}")

    # La operación termina en el servidor después de cerrar el modal: se espera a que la base la refleje
    if nombre in ("gasto", "ingreso", "cancelar", "eliminar") and datos.get("aceptado"):
        _esperar_base(oraculo, nombre, datos, pre)
    mov, can = oraculo.movimientos(), oraculo.cancelados()
    if nombre == "gasto" or nombre == "ingreso":
        if datos["aceptado"] and len(mov) != pre["antes"] + 1:
            problemas.append("se aceptó el alta pero la base no tiene un movimiento más")
        if not datos["aceptado"]:
            if len(mov) != pre["antes"]:
                problemas.append("el formulario rechazó el alta y la base cambió")
            if "Fondos insuficientes" in datos["mensaje"] and datos["candidato"] is not None and datos["candidato"] >= 0:
                problemas.append("rechazo falso: el gasto no deja ninguna fecha bajo cero y aun así se rechazó por fondos")
    if datos.get("fantasma"):
        problemas.append(f"la tabla muestra el movimiento {datos['objetivo']} y la base ya no lo tiene")
    if nombre in ("cancelar", "eliminar") and datos is not None and datos.get("aceptado") and not datos.get("fantasma"):
        if any(f[0] == datos["objetivo"] for f in mov):
            problemas.append("el movimiento quitado sigue en la tabla de la base")
        if nombre == "cancelar" and not any(c[1] == datos["objetivo"] for c in can):
            problemas.append("cancelar no dejó constancia en la base")
        if nombre == "eliminar" and any(c[1] == datos["objetivo"] for c in can):
            problemas.append("eliminar dejó constancia en la base")

    if page.is_visible("#tab-movimientos"):
        filtro, visto, esperado = _esperar_tabla(page, oraculo)  # la tabla se repinta tras la operación: se da tiempo
        if visto != esperado:
            problemas.append(f"la tabla muestra {visto[0]} vigentes y {visto[1]} cancelados con filtro «{filtro}»; "
                             f"la base tiene {esperado[0]} y {esperado[1]}")

    return problemas


def _verificar_resumen(page, oraculo):
    """Entra al resumen y compara su «Disponible hoy» con la base."""
    _ir(page, "tab-dashboard")
    page.wait_for_selector("#periodo-disponible")
    texto = page.inner_text("#periodo-disponible")
    visto = _monto_visto(texto)
    esperado = oraculo.saldo_c() / 100
    if visto is None or abs(visto - esperado) > 0.005:
        return [f"el resumen muestra {texto} y la base suma {esperado:.2f}"]
    return []


def _episodio(semilla, numero, navegador, servidor, mods, tmp, bit, fallos, dispositivo):
    rng = random.Random(semilla)
    usuario = f"aud{semilla}"
    hoy = HOY_INICIAL
    servidor.arrancar(hoy)
    import httpx
    with httpx.Client(base_url=servidor.base, timeout=30) as cliente:
        cabecera, ruta_db = personas.crear_laura(cliente, usuario, mods)
    oraculo = Oraculo(ruta_db)

    ctx = navegador.new_context(**DISPOSITIVOS[dispositivo])
    page = ctx.new_page()
    errores, respuestas_5xx = [], []
    page.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
    page.on("console", lambda m: errores.append(f"console: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.on("response", lambda r: respuestas_5xx.append(f"{r.status} {r.url.split('?')[0]}") if r.status >= 500 else None)
    _login(page, servidor.base, usuario)

    pasos = rng.randint(PASOS_MIN, PASOS_MAX)
    for paso in range(1, pasos + 1):
        nombre = rng.choices([n for n, _ in PESOS_UI], weights=[p for _, p in PESOS_UI])[0]
        fallo_accion = None
        errores.clear()
        respuestas_5xx.clear()
        pre = {"antes": len(oraculo.movimientos())}
        inicio = time.perf_counter()
        if nombre == "avanzar_dia":
            hoy = hoy + datetime.timedelta(days=rng.choice((1, 1, 2, 7, 15)))
            servidor.parar()
            servidor.arrancar(hoy)
            _reentrar(page, servidor.base, usuario)
            if page.is_visible("#tab-movimientos"):
                page.select_option("#filtro-ventana", value="todo")  # al recargar, la página vuelve al periodo actual
            datos = {"entrada": {"hoy": hoy.isoformat()}, "aceptado": None, "mensaje": "", "candidato": None}
        else:
            try:
                datos = _accion(nombre, page, rng, oraculo, hoy, servidor, cabecera)
            except Exception as error:  # una acción que no se puede completar es un hallazgo, no un corte del run
                datos = {"entrada": {}, "aceptado": None, "mensaje": "", "candidato": None}
                fallo_accion = _primera_linea(error)
        duracion_ms = round((time.perf_counter() - inicio) * 1000)

        if datos is None:
            nota = "omitida: no había movimientos que quitar"
            datos = {"entrada": {}, "aceptado": None, "mensaje": "", "candidato": None}
        else:
            nota = None
        problemas = []
        if fallo_accion:
            problemas.append(f"la acción no se pudo completar en pantalla: {fallo_accion}")
        elif nota is None:
            problemas += _verificar(nombre, datos, page, oraculo, pre, errores, respuestas_5xx)
        if not fallo_accion and (nombre == "dashboard" or nombre == "avanzar_dia"):
            try:
                problemas += _verificar_resumen(page, oraculo)
            except Exception as error:
                problemas.append(f"el resumen no se pudo leer: {_primera_linea(error)}")

        registro = {
            "episodio": numero, "semilla": semilla, "persona": usuario, "paso": paso,
            "fecha_simulada": hoy.isoformat(), "accion": nombre, "entrada": datos["entrada"],
            "aceptado": datos.get("aceptado"), "mensaje": datos.get("mensaje") or None,
            "saldo_base_c": oraculo.saldo_c(), "filas_base": len(oraculo.movimientos()) + len(oraculo.cancelados()),
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


def correr(semilla, episodios, fuente, carpeta, dispositivo="escritorio"):
    from playwright.sync_api import sync_playwright

    shutil.rmtree(carpeta, ignore_errors=True)
    os.makedirs(os.path.join(carpeta, "logs"), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="auditoria_ui_")
    shutil.copytree(fuente, tmp, dirs_exist_ok=True, ignore=IGNORAR_EN_COPIA)
    os.chdir(tmp)
    os.environ["JWT_SECRET_KEY"] = "k" * 48
    mods = _cargar_modulos(tmp)

    servidor = Servidor(tmp, os.path.join(carpeta, "logs"))
    bit = Bitacora(carpeta)
    fallos, pasos_totales = [], 0
    try:
        with sync_playwright() as pw:
            navegador = pw.chromium.launch(args=["--no-sandbox"])
            try:
                for numero in range(1, episodios + 1):
                    pasos_totales += _episodio(semilla + numero - 1, numero, navegador, servidor, mods, tmp, bit, fallos, dispositivo)
            finally:
                navegador.close()
    finally:
        servidor.parar()
        bit.cerrar()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"Corrida {dispositivo} s{semilla} n{episodios}: {episodios} episodios, {pasos_totales} acciones, carpeta {carpeta}")
    if fallos:
        print(f"FALLO: {len(fallos)} acciones con invariantes rotas")
        for numero, paso, nombre, problemas in fallos[:20]:
            print(f"  episodio {numero} paso {paso} ({nombre}): {'; '.join(problemas)}")
        return 1
    print("OK: todas las invariantes se cumplieron")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Capa B de la auditoría (navegador de escritorio).")
    parser.add_argument("--semilla", type=int, default=1)
    parser.add_argument("--episodios", type=int, default=100)
    parser.add_argument("--fuente", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    parser.add_argument("--carpeta", required=True)
    parser.add_argument("--dispositivo", choices=sorted(DISPOSITIVOS), default="escritorio")
    args = parser.parse_args()
    sys.exit(correr(args.semilla, args.episodios, os.path.abspath(args.fuente), os.path.abspath(args.carpeta), args.dispositivo))
