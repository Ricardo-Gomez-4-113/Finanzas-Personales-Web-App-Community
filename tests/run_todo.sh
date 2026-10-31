#!/usr/bin/env bash
# Batería completa: backend, cobertura y navegador (con la landing). Corre todas las partes aunque una falle.
# Uso: ./tests/run_todo.sh
set -uo pipefail
cd "$(dirname "$0")/.."

inicio=$(date +%s)
estado=0
for paso in run_tests.sh run_cobertura.sh run_frontend.sh; do
    echo "== $paso =="
    t0=$(date +%s)
    ./tests/"$paso" || estado=1
    echo "-- $paso: $(( $(date +%s) - t0 )) s"
    echo
done
echo "TOTAL: $(( $(date +%s) - inicio )) s"
if [ "$estado" -eq 0 ]; then echo "RESULTADO: todo en verde"; else echo "RESULTADO: hay partes en rojo"; fi
exit "$estado"
