#!/usr/bin/env bash
# Ejecuta toda la batería de pruebas en un contenedor efímero (no toca el contenedor en producción ni data/).
# Uso: ./tests/run_tests.sh
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGEN="${TEST_IMAGE:-python:3.11-slim}"
docker run --rm -v "$PWD":/src:ro -e JWT_SECRET_KEY="$(printf 'k%.0s' {1..48})" "$IMAGEN" bash -c '
  set -e
  pip install -q --no-cache-dir -r /src/requirements.txt httpx
  cp -r /src /tmp/app && cd /tmp/app
  echo "== reglas =="; ./scripts/verificar_reglas.sh
  echo "== test_finanzas.py ==";        python3 test_finanzas.py
  echo "== test_migraciones.py ==";      python3 tests/test_migraciones.py
  echo "== test_respaldo_automatico.py =="; python3 tests/test_respaldo_automatico.py
  echo "== test_api_aislamiento.py =="; python3 tests/test_api_aislamiento.py
'
