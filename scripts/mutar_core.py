#!/usr/bin/env python3
"""Pruebas de mutación de finanzas_core.py: mete errores pequeños y mira si alguna suite los detecta.

Cada mutante cambia UNA cosa (un `<` por `<=`, un `+` por `-`, un `and` por `or`, un entero por su siguiente, una
negación quitada, un `return True` por `return False`). Si ninguna prueba falla, el mutante «sobrevive»: o la regla no
tiene prueba, o el cambio no altera el resultado (mutante equivalente). Cada sobreviviente se revisa a mano.

Debe correr dentro de un contenedor con las dependencias (ver `scripts/mutar_core.sh`). Uso:
    python3 scripts/mutar_core.py --n 300 --semilla 1 [--funcion REGEX] [--trabajadores 14]
Escribe el informe en `auditoria/mutacion/informe.md`.
"""

import argparse
import ast
import copy
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBJETIVO = "finanzas_core.py"
# De la más rápida a la más lenta: el mutante se descarta en cuanto una falla.
SUITES = [
    ["python3", "tests/test_core_casos.py"],
    ["python3", "tests/test_fijos_metas.py"],
    ["python3", "tests/test_propiedades.py"],
    ["python3", "test_finanzas.py"],
]

CAMBIO_COMPARACION = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
CAMBIO_ARITMETICA = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult}


def _docstrings(arbol):
    ids = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and nodo.body:
            primero = nodo.body[0]
            if isinstance(primero, ast.Expr) and isinstance(primero.value, ast.Constant) and isinstance(primero.value.value, str):
                ids.add(id(primero.value))
    return ids


class Mutador(ast.NodeTransformer):
    """Recorre el árbol en orden; si `objetivo` es el índice del sitio actual, lo muta. Con objetivo=None solo cuenta."""

    def __init__(self, objetivo, saltar):
        self.objetivo = objetivo
        self.saltar = saltar
        self.sitios = []
        self.hecho = None
        self.funcion = None
        self.pila_funciones = []

    def visit_FunctionDef(self, nodo):
        self.pila_funciones.append(nodo.name)
        self.generic_visit(nodo)
        self.pila_funciones.pop()
        return nodo

    def visit_JoinedStr(self, nodo):      # el texto de los mensajes no se muta
        return nodo

    def _sitio(self, nodo, descripcion):
        indice = len(self.sitios)
        self.sitios.append((getattr(nodo, "lineno", 0), self.pila_funciones[-1] if self.pila_funciones else "<modulo>", descripcion))
        if self.objetivo == indice:
            self.hecho = (getattr(nodo, "lineno", 0), self.pila_funciones[-1] if self.pila_funciones else "<modulo>", descripcion)
            return True
        return False

    def visit_Compare(self, nodo):
        self.generic_visit(nodo)
        for i, op in enumerate(nodo.ops):
            nuevo = CAMBIO_COMPARACION.get(type(op))
            if nuevo and self._sitio(nodo, f"{type(op).__name__} -> {nuevo.__name__}"):
                nodo.ops[i] = nuevo()
        return nodo

    def visit_BinOp(self, nodo):
        self.generic_visit(nodo)
        nuevo = CAMBIO_ARITMETICA.get(type(nodo.op))
        if nuevo and self._sitio(nodo, f"{type(nodo.op).__name__} -> {nuevo.__name__}"):
            nodo.op = nuevo()
        return nodo

    def visit_BoolOp(self, nodo):
        self.generic_visit(nodo)
        nuevo = ast.Or if isinstance(nodo.op, ast.And) else ast.And
        if self._sitio(nodo, f"{type(nodo.op).__name__} -> {nuevo.__name__}"):
            nodo.op = nuevo()
        return nodo

    def visit_UnaryOp(self, nodo):
        self.generic_visit(nodo)
        if isinstance(nodo.op, ast.Not) and self._sitio(nodo, "quitar not"):
            return nodo.operand
        return nodo

    def visit_Constant(self, nodo):
        if id(nodo) in self.saltar:
            return nodo
        if isinstance(nodo.value, bool) or not isinstance(nodo.value, int):
            return nodo
        if self._sitio(nodo, f"{nodo.value} -> {nodo.value + 1}"):
            return ast.copy_location(ast.Constant(nodo.value + 1), nodo)
        return nodo

    def visit_Return(self, nodo):
        self.generic_visit(nodo)
        if isinstance(nodo.value, ast.Constant) and isinstance(nodo.value.value, bool) and self._sitio(nodo, f"return {nodo.value.value} -> {not nodo.value.value}"):
            nodo.value = ast.copy_location(ast.Constant(not nodo.value.value), nodo.value)
        return nodo


def _generar(fuente, indice):
    arbol = ast.parse(fuente)
    m = Mutador(indice, _docstrings(arbol))
    nuevo = m.visit(arbol)
    ast.fix_missing_locations(nuevo)
    return ast.unparse(nuevo), m


def _correr(carpeta, indice_suite_max=None):
    """Devuelve (matado_por, salida) o (None, '') si ninguna suite lo detectó."""
    entorno = {**os.environ, "PROPIEDADES_EJEMPLOS": os.environ.get("PROPIEDADES_EJEMPLOS_MUTACION", "150"), "JWT_SECRET_KEY": "k" * 48}
    for suite in SUITES:
        try:
            r = subprocess.run(suite, cwd=carpeta, env=entorno, capture_output=True, text=True, timeout=240)
        except subprocess.TimeoutExpired:
            return suite[-1] + " (tiempo agotado)", ""
        if r.returncode != 0:
            return suite[-1], (r.stdout + r.stderr)[-300:]
    return None, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--semilla", type=int, default=1)
    ap.add_argument("--funcion", default=None, help="expresión regular: solo mutar funciones con ese nombre")
    ap.add_argument("--trabajadores", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--salida", default=os.path.join(RAIZ, "auditoria", "mutacion", "informe.md"))
    args = ap.parse_args()

    fuente = open(os.path.join(RAIZ, OBJETIVO), encoding="utf-8").read()
    base, contador = _generar(fuente, None)
    candidatos = [i for i, (_, f, _) in enumerate(contador.sitios) if not args.funcion or re.search(args.funcion, f)]
    random.Random(args.semilla).shuffle(candidatos)
    elegidos = sorted(candidatos[: args.n])
    print(f"{len(contador.sitios)} sitios de mutación; se prueban {len(elegidos)} (semilla {args.semilla}).", flush=True)

    tmp = tempfile.mkdtemp(prefix="mutar_")
    carpetas = []
    for i in range(args.trabajadores):
        destino = os.path.join(tmp, f"t{i}")
        shutil.copytree(RAIZ, destino, ignore=shutil.ignore_patterns(".git", "data", "auditoria", "node_modules", "__pycache__", ".logs_pruebas", "graphify-out", ".venv", "venv", ".claude", "docs"))
        carpetas.append(destino)
    # Línea base: el código pasado por ast.unparse debe pasar todas las suites, o los «matados» serían falsos.
    with open(os.path.join(carpetas[0], OBJETIVO), "w", encoding="utf-8") as f:
        f.write(base)
    matado, salida = _correr(carpetas[0])
    if matado:
        sys.exit(f"La línea base falla en {matado}:\n{salida}")
    print("Línea base en verde.", flush=True)

    libres = list(range(len(carpetas)))
    import threading
    candado = threading.Lock()
    resultados = []

    def trabajo(indice):
        with candado:
            t = libres.pop()
        try:
            texto, m = _generar(fuente, indice)
            ruta = os.path.join(carpetas[t], OBJETIVO)
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(texto)
            quien, detalle = _correr(carpetas[t])
            linea, funcion, desc = m.hecho
            resultados.append((indice, linea, funcion, desc, quien))
            marca = "mató " + quien if quien else "SOBREVIVE"
            print(f"  #{indice:<5} L{linea:<5} {funcion:<42} {desc:<28} {marca}", flush=True)
        finally:
            with candado:
                libres.append(t)

    with ThreadPoolExecutor(max_workers=len(carpetas)) as pool:
        list(pool.map(trabajo, elegidos))

    sobreviven = sorted([r for r in resultados if r[4] is None], key=lambda r: r[1])
    total = len(resultados)
    puntaje = 100 * (total - len(sobreviven)) / max(1, total)
    os.makedirs(os.path.dirname(args.salida), exist_ok=True)
    fuente_lineas = fuente.splitlines()
    with open(args.salida, "w", encoding="utf-8") as f:
        f.write(f"# Mutación de {OBJETIVO}\n\n{total} mutantes probados (semilla {args.semilla}): "
                f"{total - len(sobreviven)} detectados, {len(sobreviven)} sobreviven ({puntaje:.1f} % detectado).\n\n")
        f.write("Línea de la fuente original donde se aplicó el cambio (el número es de `ast.unparse`, puede diferir unas líneas).\n\n")
        for indice, linea, funcion, desc, _ in sobreviven:
            f.write(f"- `{funcion}` L{linea}: **{desc}**\n")
    print(f"\n{total - len(sobreviven)}/{total} detectados ({puntaje:.1f} %). Informe: {args.salida}")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
