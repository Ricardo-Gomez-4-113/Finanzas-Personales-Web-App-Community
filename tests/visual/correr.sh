#!/usr/bin/env bash
# Recorrido visual sistemático (capturas + comprobaciones) en el contenedor de Playwright. No toca data/ ni producción.
# Uso: tests/visual/correr.sh   -> auditoria/visual/<cuenta>/*.png y auditoria/visual/findings.json
set -euo pipefail
cd "$(dirname "$0")/../.."
IMAGEN="${PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright/python:v1.48.0-jammy}"
[ -n "${CONSERVAR:-}" ] || docker run --rm -v "$PWD/auditoria":/a alpine rm -rf /a/visual; mkdir -p auditoria/visual
docker run --rm --ipc=host -e ANCHOS="${ANCHOS:-360,390,768,1024,1440}" -e TEMAS="${TEMAS:-light,dark}" -e VISTAS="${VISTAS:-}" -e CUENTAS="${CUENTAS:-}" -e SIN_REDIMENSIONAR="${SIN_REDIMENSIONAR:-}" -v "$PWD":/src:ro -v "$PWD/auditoria/visual":/salida "$IMAGEN" bash -c '
  set -e
  pip install -q --no-cache-dir -r /src/requirements.txt playwright==1.48.0 2>&1 | grep -v "notice\|WARNING: Running pip" || true
  cp -r /src /tmp/app && cd /tmp/app
  python3 tests/visual/capturas.py
'
