#!/usr/bin/env bash
# Corre la auditoría por uso real en un contenedor efímero (no toca data/ ni el contenedor de producción).
# Uso: tests/auditoria/run.sh [--capa api|escritorio] [--semilla N] [--episodios N] [--fuente RUTA] [--base-dueno COPIA.db]
#   La salida queda en auditoria/corridas/<nombre>/ (bitacora.jsonl, tiempos.jsonl, logs/, fallos/).
#   api: capa A (API, sin navegador). escritorio: capa B (Chromium a 1280×800, imagen de Playwright).
#   celular: capa C (mismo recorrido a 390×844, táctil).
set -euo pipefail
cd "$(dirname "$0")/../.."

IMAGEN="${TEST_IMAGE:-python:3.11-slim}"
SEMILLA=1; EPISODIOS=100; FUENTE=""; CAPA="api"; SECCION="movimientos"; PERSONA="laura"; BASE_DUENO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --capa) CAPA="$2"; shift 2 ;;
    --seccion) SECCION="$2"; shift 2 ;;
    --persona) PERSONA="$2"; shift 2 ;;
    --base-dueno) BASE_DUENO="$2"; shift 2 ;;
    --semilla) SEMILLA="$2"; shift 2 ;;
    --episodios) EPISODIOS="$2"; shift 2 ;;
    --fuente) FUENTE="$2"; shift 2 ;;
    *) echo "opción desconocida: $1"; exit 2 ;;
  esac
done
SUFIJO_PERSONA=""; [ "$PERSONA" = "laura" ] || SUFIJO_PERSONA="-${PERSONA}"
NOMBRE="${SECCION}-${CAPA}${SUFIJO_PERSONA}-s${SEMILLA}-n${EPISODIOS}"
DISPOSITIVO=""
if [ "$CAPA" = "escritorio" ] || [ "$CAPA" = "celular" ]; then
  DISPOSITIVO="$CAPA"
  IMAGEN="${PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright/python:v1.48.0-jammy}"
  SCRIPT="tests/auditoria/escritorio.py"
  if [ "$SECCION" = "fijos" ]; then SCRIPT="tests/auditoria/escritorio_fijos.py"; fi
  if [ "$SECCION" = "presupuesto" ]; then SCRIPT="tests/auditoria/escritorio_presupuesto.py"; fi
  if [ "$SECCION" = "metas" ]; then SCRIPT="tests/auditoria/escritorio_metas.py"; fi
  if [ "$SECCION" = "inversiones" ]; then SCRIPT="tests/auditoria/escritorio_inversiones.py"; fi
  if [ "$SECCION" = "respaldo" ]; then SCRIPT="tests/auditoria/escritorio_respaldo.py"; fi
  if [ "$SECCION" = "admin" ]; then SCRIPT="tests/auditoria/escritorio_admin.py"; fi
  if [ "$SECCION" = "login" ]; then SCRIPT="tests/auditoria/escritorio_login.py"; fi
  INSTALAR="pip install -q --no-cache-dir -r /src/requirements.txt playwright==1.48.0 httpx"
else
  SCRIPT="tests/auditoria/run_api.py --seccion ${SECCION} --persona ${PERSONA}"
  # La base del dueño para las personas dueno_*: una copia (sqlite3 .backup en solo lectura), nunca la base viva
  if [ -n "$BASE_DUENO" ]; then SCRIPT="$SCRIPT --base-dueno /base_dueno.db"; fi
  INSTALAR="pip install -q --no-cache-dir -r /src/requirements.txt httpx"
fi
mkdir -p "$PWD/auditoria/corridas"

# La carpeta de salida se monta escribible; el código, de solo lectura. Con --fuente, esa carpeta se monta
# en /fuente y la auditoría prueba ese código en vez del repo (sirve para comprobar que un error viejo se detecta).
MONTAJE_FUENTE=()
RUTA_FUENTE="/src"
if [ -n "$FUENTE" ]; then
  MONTAJE_FUENTE=(-v "$(realpath "$FUENTE")":/fuente:ro)
  RUTA_FUENTE="/fuente"
fi

MONTAJE_BASE=()
if [ -n "$BASE_DUENO" ]; then MONTAJE_BASE=(-v "$(realpath "$BASE_DUENO")":/base_dueno.db:ro); fi

docker run --rm -v "$PWD":/src:ro -v "$PWD/auditoria":/salida "${MONTAJE_FUENTE[@]}" "${MONTAJE_BASE[@]}" \
  -e JWT_SECRET_KEY="$(printf 'k%.0s' {1..48})" "$IMAGEN" bash -c "
  set -e
  $INSTALAR >/tmp/pip.log 2>&1 || { tail -5 /tmp/pip.log; exit 1; }
  cd /src
  python3 ${SCRIPT} --semilla ${SEMILLA} --episodios ${EPISODIOS} \
    --carpeta /salida/corridas/${NOMBRE} --fuente ${RUTA_FUENTE} ${DISPOSITIVO:+--dispositivo ${DISPOSITIVO}}
"
