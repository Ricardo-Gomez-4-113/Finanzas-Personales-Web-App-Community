"""
Medición base de formularios, modales y la tabla de Admin (línea base del rediseño).

No es una prueba del runner: produce un informe por viewport para comparar antes y después.
Uso (en Docker, como run_frontend.sh): python3 tests/medir_formularios.py <carpeta_salida>
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import test_frontend as tf  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ADMIN = "medidor"
CLAVE_ADMIN = "Medidor-base-2026"
VIEWPORTS = {"laptop": {"width": 1366, "height": 768}, "movil": {"width": 390, "height": 844}}

# (id del formulario, pestaña, subpestaña o None)
FORMULARIOS = [
    ("transaccion-form", "tab-movimientos", None),
    ("form-recurrente", "tab-movimientos", "btn-subtab-fijos"),
    ("form-inversion", "tab-inversiones", None),
    ("form-calc-rf", "tab-calculadora-rf", None),
    ("form-presupuesto-config", "tab-presupuesto", None),
    ("form-apartado", "tab-apartados", None),
]

# Modales: se abren quitando la clase hidden y se miden en su tarjeta
MODALES = [
    "modal-editar-inversion", "modal-editar-transaccion", "modal-editar-recurrente",
    "modal-crear-usuario", "modal-editar-usuario", "modal-crear-apartado",
    "modal-tarjeta-presupuesto", "modal-exportar-presupuesto", "modal-saltar-msi",
]

MEDIR_FORMULARIO = """(id) => {
    const el = document.getElementById(id);
    if (!el) return { existe: false };
    const r = el.getBoundingClientRect();
    return {
        existe: true,
        visible: r.height > 0,
        alto: Math.round(r.height),
        ancho: Math.round(r.width),
        campos: [...el.querySelectorAll('input:not([type=hidden]), select, textarea')].filter(c => c.offsetParent !== null).length,
        grupos: el.querySelectorAll('.form-group').length,
        estilos_linea: el.querySelectorAll('[style]').length,
    };
}"""

MEDIR_MODAL = """(id) => {
    const ov = document.getElementById(id);
    if (!ov) return { existe: false };
    ov.classList.remove('hidden');
    const card = ov.querySelector('.modal-card');
    const r = card.getBoundingClientRect();
    const medida = {
        existe: true,
        alto: Math.round(r.height),
        ancho: Math.round(r.width),
        supera_viewport: r.height > window.innerHeight,
        scroll_anidado: card.scrollHeight > card.clientHeight + 1,
        campos: [...card.querySelectorAll('input:not([type=hidden]), select, textarea')].filter(c => c.offsetParent !== null).length,
        grupos: card.querySelectorAll('.form-group').length,
        estilos_linea: card.querySelectorAll('[style]').length,
    };
    ov.classList.add('hidden');
    return medida;
}"""

MEDIR_ADMIN = """() => {
    const tabla = document.getElementById('tabla-usuarios-admin-full');
    const contenedor = document.querySelector('.admin-table-responsive');
    if (!tabla || !contenedor) return { existe: false };
    return {
        existe: true,
        columnas: tabla.querySelectorAll('thead th').length,
        ancho_tabla: Math.round(tabla.getBoundingClientRect().width),
        ancho_contenedor: contenedor.clientWidth,
        scroll_horizontal: contenedor.scrollWidth > contenedor.clientWidth + 1,
        filas: tabla.querySelectorAll('tbody tr').length,
        reglas_columna: [...document.styleSheets].reduce((n, hoja) => {
            try { return n + [...hoja.cssRules].filter(r => r.selectorText && r.selectorText.includes('table-admin-users')).length; }
            catch (e) { return n; }
        }, 0),
    };
}"""


def medir_viewport(navegador, base, nombre, viewport):
    ctx = navegador.new_context(viewport=viewport)
    page = ctx.new_page()
    resultado = {"formularios": {}, "modales": {}, "admin": {}}
    try:
        page.goto(base + "/login")
        resultado["formularios"]["form-login"] = page.evaluate(MEDIR_FORMULARIO, "form-login")
        tf._entrar(page, base, ADMIN, CLAVE_ADMIN)
        for fid, tab, subtab in FORMULARIOS:
            tf._ir(page, tab)
            if subtab:
                page.evaluate(f"document.getElementById('{subtab}').click()")
                page.wait_for_timeout(200)
            resultado["formularios"][fid] = page.evaluate(MEDIR_FORMULARIO, fid)
        for mid in MODALES:
            resultado["modales"][mid] = page.evaluate(MEDIR_MODAL, mid)
        tf._ir(page, "tab-admin")
        page.wait_for_selector("#tabla-usuarios-admin-full tbody tr")
        resultado["admin"] = page.evaluate(MEDIR_ADMIN)
    finally:
        ctx.close()
    return resultado


def tabla(resultado):
    lineas = [f"{'elemento':34} {'alto':>6} {'ancho':>6} {'campos':>7} {'grupos':>7} {'style':>6}  nota"]
    for seccion in ("formularios", "modales"):
        for nombre, m in resultado[seccion].items():
            if not m.get("existe", True):
                lineas.append(f"{nombre:34} no existe")
                continue
            if seccion == "modales":
                nota = ("scroll anidado" if m["scroll_anidado"] else "") + (" supera viewport" if m["supera_viewport"] else "")
                lineas.append(f"{nombre:34} {m['alto']:>6} {m['ancho']:>6} {m['campos']:>7} {m['grupos']:>7} {m['estilos_linea']:>6}  {nota.strip()}")
            else:
                nota = "" if m.get("visible", True) else "oculto"
                lineas.append(f"{nombre:34} {m['alto']:>6} {m['ancho']:>6} {m['campos']:>7} {m['grupos']:>7} {m['estilos_linea']:>6}  {nota}")
    a = resultado["admin"]
    lineas.append(f"admin: {a.get('columnas')} columnas, tabla {a.get('ancho_tabla')} px en contenedor {a.get('ancho_contenedor')} px, "
                  f"scroll horizontal={a.get('scroll_horizontal')}, reglas de columna={a.get('reglas_columna')}")
    return "\n".join(lineas)


def main(carpeta_salida):
    tf.escenario.activar_modo_prueba()
    proc, base = tf._arrancar_servidor()
    try:
        tf.master_db.create_user_entry(ADMIN, tf.security.hash_password(CLAVE_ADMIN), role="admin",
                                       email="medidor@test.local")
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            for nombre, viewport in VIEWPORTS.items():
                resultado = medir_viewport(navegador, base, nombre, viewport)
                with open(os.path.join(carpeta_salida, f"base-{nombre}.json"), "w", encoding="utf-8") as f:
                    json.dump(resultado, f, ensure_ascii=False, indent=2)
                print(f"== {nombre} ({viewport['width']}x{viewport['height']}) ==")
                print(tabla(resultado))
            navegador.close()
    finally:
        proc.terminate()


if __name__ == "__main__":
    main(sys.argv[1])
