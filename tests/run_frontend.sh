#!/usr/bin/env bash
# Smoke test del navegador (Playwright) en un contenedor efímero. No toca producción ni data/.
# Uso: ./tests/run_frontend.sh   (la primera vez descarga la imagen de Playwright, ~2 GB)
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGEN="${PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright/python:v1.48.0-jammy}"
docker run --rm --ipc=host -v "$PWD":/src:ro "$IMAGEN" bash -c '
  set -e
  pip install -q --no-cache-dir -r /src/requirements.txt playwright==1.48.0 2>&1 | grep -v "notice\|WARNING: Running pip" || true
  cp -r /src /tmp/app && cd /tmp/app
  echo "== test_frontend.py =="; python3 tests/test_frontend.py
  if [ -f tests/test_landing.py ]; then echo "== test_landing.py =="; python3 tests/test_landing.py; fi
'
