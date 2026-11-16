#!/usr/bin/env bash
# Prueba de estrés: muchos usuarios a la vez contra una instancia temporal de la app, en un contenedor efímero.
# No toca data/ ni tus contenedores. Salida: auditoria/corridas/estres-<etiqueta>/informe_estres.md
#
# Uso: tests/auditoria/run_estres.sh [--humo] [--fuente RUTA] [--etiqueta NOMBRE] [--escalones 10,25,50,100,200]
#                                    [--duracion SEG] [--usuarios N] [--navegadores N] [--navegador-en 50,100] [--cpus N]
#   --humo     corrida corta de comprobación (escalón de 10, 40 s)
#   --fuente   probar otra copia del código (p. ej. la carpeta del SaaS, que arranca con --reload)
#   --cpus     limitar los núcleos del contenedor (cliente y servidor juntos) para acercarse a un servidor más modesto
set -euo pipefail
cd "$(dirname "$0")/../.."

IMAGEN="${PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright/python:v1.48.0-jammy}"
FUENTE=""; ETIQUETA=""; CPUS=""; PASAR=()
while [ $# -gt 0 ]; do
  case "$1" in
    --fuente) FUENTE="$2"; shift 2 ;;
    --etiqueta) ETIQUETA="$2"; shift 2 ;;
    --cpus) CPUS="$2"; shift 2 ;;
    *) PASAR+=("$1"); shift ;;
  esac
done
MONTAJE_FUENTE=(); RUTA_FUENTE="/src"
if [ -n "$FUENTE" ]; then MONTAJE_FUENTE=(-v "$(realpath "$FUENTE")":/fuente:ro); RUTA_FUENTE="/fuente"; fi
[ -n "$ETIQUETA" ] || ETIQUETA="$(basename "$(realpath "${FUENTE:-.}")")"
NOMBRE="estres-${ETIQUETA}-$(date +%Y%m%d-%H%M)"
mkdir -p "$PWD/auditoria/corridas"

# El código del arnés sale siempre de ESTE repo (/src); lo que se prueba es --fuente
docker run --rm --ipc=host ${CPUS:+--cpus="$CPUS"} -v "$PWD":/src:ro -v "$PWD/auditoria":/salida "${MONTAJE_FUENTE[@]}" "$IMAGEN" bash -c "
  set -e
  pip install -q --no-cache-dir -r ${RUTA_FUENTE}/requirements.txt playwright==1.48.0 httpx >/tmp/pip.log 2>&1 || { tail -5 /tmp/pip.log; exit 1; }
  cd /src
  python3 tests/auditoria/estres.py --fuente ${RUTA_FUENTE} --carpeta /salida/corridas/${NOMBRE} --etiqueta '${ETIQUETA}' ${PASAR[*]:-}
"
echo "Informe: auditoria/corridas/${NOMBRE}/informe_estres.md"
