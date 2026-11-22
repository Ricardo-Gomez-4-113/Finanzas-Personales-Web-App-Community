#!/usr/bin/env bash
# Pruebas de mutación de finanzas_core.py en un contenedor efímero (ver scripts/mutar_core.py).
# Uso: ./scripts/mutar_core.sh [--n 300] [--semilla 1] [--funcion REGEX]
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p auditoria/mutacion
docker run --rm -v "$PWD":/src:ro -v "$PWD/auditoria/mutacion":/out -e JWT_SECRET_KEY="$(printf 'k%.0s' {1..48})" "${TEST_IMAGE:-python:3.11-slim}" bash -c '
  pip install -q --no-cache-dir -r /src/requirements.txt httpx hypothesis >/tmp/pip.log 2>&1 || { tail -5 /tmp/pip.log; exit 1; }
  cd /src && python3 scripts/mutar_core.py --salida /out/informe.md "$@"
' _ "$@"
