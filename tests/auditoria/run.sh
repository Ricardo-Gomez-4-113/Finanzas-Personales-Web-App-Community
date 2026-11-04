#!/usr/bin/env bash
# Corre la auditoría por uso real en un contenedor efímero (no toca data/ ni el contenedor de producción).
# Uso: tests/auditoria/run.sh [--semilla N] [--episodios N] [--fuente RUTA]
#   La salida queda en auditoria/corridas/<nombre>/ (bitacora.jsonl, tiempos.jsonl, logs/, fallos/).
set -euo pipefail
cd "$(dirname "$0")/../.."

IMAGEN="${TEST_IMAGE:-python:3.11-slim}"
SEMILLA=1; EPISODIOS=100; FUENTE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --semilla) SEMILLA="$2"; shift 2 ;;
    --episodios) EPISODIOS="$2"; shift 2 ;;
    --fuente) FUENTE="$2"; shift 2 ;;
    *) echo "opción desconocida: $1"; exit 2 ;;
  esac
done
NOMBRE="movimientos-api-s${SEMILLA}-n${EPISODIOS}"
mkdir -p "$PWD/auditoria/corridas"

# La carpeta de salida se monta escribible; el código, de solo lectura. Con --fuente, esa carpeta se monta
# en /fuente y la auditoría prueba ese código en vez del repo (sirve para comprobar que un error viejo se detecta).
MONTAJE_FUENTE=()
RUTA_FUENTE="/src"
if [ -n "$FUENTE" ]; then
  MONTAJE_FUENTE=(-v "$(realpath "$FUENTE")":/fuente:ro)
  RUTA_FUENTE="/fuente"
fi

docker run --rm -v "$PWD":/src:ro -v "$PWD/auditoria":/salida "${MONTAJE_FUENTE[@]}" \
  -e JWT_SECRET_KEY="$(printf 'k%.0s' {1..48})" "$IMAGEN" bash -c "
  set -e
  pip install -q --no-cache-dir -r /src/requirements.txt httpx >/tmp/pip.log 2>&1 || { tail -5 /tmp/pip.log; exit 1; }
  cd /src
  python3 tests/auditoria/run_api.py --semilla ${SEMILLA} --episodios ${EPISODIOS} \
    --carpeta /salida/corridas/${NOMBRE} --fuente ${RUTA_FUENTE}
"
