"""tests/test_acciones.py - Toda acción declarada en el DOM (data-on-*) apunta a una función expuesta en window.

El despachador de static/js/acciones.js ignora en silencio un nombre que no existe en window, así que
este chequeo estático es lo que evita un botón muerto tras quitar los atributos on* inline.
"""

import glob
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(RAIZ, "static")


def _archivos(patron):
    return glob.glob(os.path.join(STATIC, patron), recursive=True)


def _nombres_declarados():
    """Nombres de función usados en data-on-* (HTML) y en plantillas JS (JSON dentro de atributos)."""
    nombres = set()
    for ruta in _archivos("*.html") + _archivos("js/*.js"):
        with open(ruta, encoding="utf-8") as f:
            texto = f.read()
        nombres.update(re.findall(r'\[\[\s*(?:"|&quot;)([A-Za-z_]\w*)', texto))
        nombres.update(re.findall(r'\],\s*\[\s*(?:"|&quot;)([A-Za-z_]\w*)', texto))
    return nombres


def _nombres_expuestos():
    """Nombres asignados a window: bloques Object.assign(window, {...}) y asignaciones window.nombre = ..."""
    expuestos = set()
    for ruta in _archivos("js/*.js"):
        with open(ruta, encoding="utf-8") as f:
            texto = f.read()
        for m in re.finditer(r"Object\.assign\(window,\s*\{", texto):
            bloque = texto[m.end():]
            bloque = bloque[: bloque.find("\n});")] if "\n});" in bloque else bloque
            expuestos.update(re.findall(r"^\s*([A-Za-z_]\w*)\s*(?:,|\(|:|$)", bloque, re.M))
        expuestos.update(re.findall(r"window\.([A-Za-z_]\w*)\s*=", texto))
    return expuestos


# Funciones que solo define landing.js (exclusivo del SaaS). Community no las tiene y su index.html
# no las invoca: el modal de solicitud de acceso no tiene apertura allí, y mostrarToast es un guard opcional.
SOLO_EN_LANDING = {"cerrarModalSolicitarAcceso", "mostrarToast"}


def test_toda_accion_declarada_existe_en_window():
    declarados = _nombres_declarados()
    expuestos = _nombres_expuestos()
    assert declarados, "no se encontró ninguna acción declarada: revisa el patrón del test"
    faltan = declarados - expuestos
    if not os.path.exists(os.path.join(STATIC, "js", "landing.js")):
        faltan -= SOLO_EN_LANDING
    assert not faltan, f"acciones declaradas sin función en window: {sorted(faltan)}"


def test_el_patron_encuentra_acciones_conocidas():
    declarados = _nombres_declarados()
    for esperado in ("toggleSidebar", "iniciarSesion", "abrirModalCrearTarjeta", "realizarMovimiento", "cambiarMonedaGlobal"):
        assert esperado in declarados, esperado


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import ejecutar_modulo
    sys.exit(ejecutar_modulo(sys.modules[__name__], "test_acciones"))
