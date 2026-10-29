#!/usr/bin/env bash
# Ejecuta toda la batería de pruebas en un contenedor efímero (no toca el contenedor en producción ni data/).
# Corre todas las suites aunque una falle, muestra un resumen y sale con 1 si alguna falló.
# Uso: ./tests/run_tests.sh
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGEN="${TEST_IMAGE:-python:3.11-slim}"
docker run --rm -v "$PWD":/src:ro -e JWT_SECRET_KEY="$(printf 'k%.0s' {1..48})" "$IMAGEN" bash -c '
  if ! pip install -q --no-cache-dir -r /src/requirements.txt httpx >/tmp/pip.log 2>&1; then
    echo "ERROR: no se pudieron instalar las dependencias (pip)"; tail -5 /tmp/pip.log; exit 1
  fi
  cp -r /src /tmp/app && cd /tmp/app
  estado=0
  paso() {
    echo "== $1 =="
    shift
    "$@" || estado=1
  }
  paso reglas               ./scripts/verificar_reglas.sh
  paso test_finanzas        python3 test_finanzas.py
  paso test_migraciones     python3 tests/test_migraciones.py
  paso test_respaldo        python3 tests/test_respaldo_automatico.py
  paso test_mercado         python3 tests/test_mercado.py
  paso test_acciones        python3 tests/test_acciones.py
  paso test_api_aislamiento python3 tests/test_api_aislamiento.py
  paso test_escenario_api   python3 tests/test_escenario_api.py
  echo
  if [ "$estado" -eq 0 ]; then echo "RESULTADO: todas las suites OK"; else echo "RESULTADO: hay suites con fallos"; fi
  exit "$estado"
'
