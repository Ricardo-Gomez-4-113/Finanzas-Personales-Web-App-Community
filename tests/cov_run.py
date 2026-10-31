"""tests/cov_run.py - Envoltorio para medir cobertura de las suites que copian el código a una carpeta temporal.

Las suites de API copian el proyecto a una carpeta temporal y la borran al terminar. Aquí esa carpeta
tiene una ruta fija (/tmp/app_run) que no se borra, y run_cobertura.sh la mapea de vuelta al código.
Uso: coverage run -a cov_run.py tests/test_xxx.py
"""

import os
import runpy
import shutil
import sys
import tempfile

CARPETA_FIJA = "/tmp/app_run"
_mkdtemp_original, _rmtree_original = tempfile.mkdtemp, shutil.rmtree


def _mkdtemp(suffix=None, prefix=None, dir=None):
    if prefix and prefix.startswith("finanzas_"):
        # Cada suite empieza en limpio: la base de una anterior no debe contar como usuarios existentes.
        _rmtree_original(CARPETA_FIJA, ignore_errors=True)
        os.makedirs(CARPETA_FIJA, exist_ok=True)
        return CARPETA_FIJA
    return _mkdtemp_original(suffix=suffix, prefix=prefix, dir=dir)


def _rmtree(path, *args, **kwargs):
    if str(path) == CARPETA_FIJA:
        return None
    return _rmtree_original(path, *args, **kwargs)


tempfile.mkdtemp, shutil.rmtree = _mkdtemp, _rmtree
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[1])))
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
