#!/usr/bin/env python3
"""scripts/cache_bust.py - Pone en cada ?v= un hash del contenido de static/css y static/js.

Uso:
  python3 scripts/cache_bust.py           # reescribe los ?v= desactualizados
  python3 scripts/cache_bust.py --check   # solo verifica; sale con 1 si hay alguno desactualizado

El hash de la app ignora los archivos propios de cada repo (landing*, auth.js) para que SaaS y Community
calculen el mismo valor y index.html siga idéntico al sincronizar. La landing (solo SaaS) usa su propio hash.
"""

import glob
import hashlib
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(RAIZ, "static")
VERSION_RE = re.compile(r"(\.(?:css|js))\?v=[A-Za-z0-9_.-]+")
PROPIOS_DEL_REPO = {"auth.js"}


def _assets():
    return sorted(glob.glob(os.path.join(STATIC, "css", "*.css")) + glob.glob(os.path.join(STATIC, "js", "*.js")))


def _es_landing(ruta):
    return os.path.basename(ruta).startswith("landing")


def _hash(rutas):
    h = hashlib.sha256()
    for ruta in rutas:
        with open(ruta, encoding="utf-8") as f:
            # Sin los ?v= propios: si no, reescribir main.js cambiaría el hash que se acaba de escribir.
            contenido = VERSION_RE.sub(r"\1", f.read())
        h.update(os.path.relpath(ruta, STATIC).encode() + b"\0" + contenido.encode() + b"\0")
    return h.hexdigest()[:10]


def calcular():
    """Devuelve {archivo_a_reescribir: version}."""
    compartidos = [r for r in _assets() if not _es_landing(r) and os.path.basename(r) not in PROPIOS_DEL_REPO]
    v_app = _hash(compartidos)
    destinos = {os.path.join(STATIC, "index.html"): v_app}
    for ruta in _assets():
        if ruta.endswith(".js") and not _es_landing(ruta):
            destinos[ruta] = v_app
    landing = os.path.join(STATIC, "landing.html")
    if os.path.exists(landing):
        v_landing = _hash(compartidos + [r for r in _assets() if _es_landing(r)])
        destinos[landing] = v_landing
        for ruta in _assets():
            if ruta.endswith(".js") and _es_landing(ruta):
                destinos[ruta] = v_landing
    return destinos


def main():
    solo_verificar = "--check" in sys.argv
    pendientes = []
    for ruta, version in calcular().items():
        with open(ruta, encoding="utf-8") as f:
            actual = f.read()
        nuevo = VERSION_RE.sub(lambda m: f"{m.group(1)}?v={version}", actual)
        if nuevo != actual:
            pendientes.append(os.path.relpath(ruta, RAIZ))
            if not solo_verificar:
                with open(ruta, "w", encoding="utf-8") as f:
                    f.write(nuevo)
    if solo_verificar and pendientes:
        print("FALLO: ?v= desactualizado en " + ", ".join(pendientes) + " (ejecuta python3 scripts/cache_bust.py)")
        return 1
    for p in pendientes:
        print(f"actualizado  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
