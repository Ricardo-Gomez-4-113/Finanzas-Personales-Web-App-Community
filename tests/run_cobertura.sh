#!/usr/bin/env bash
# Cobertura del backend sobre todas las suites, en un contenedor efímero. Falla si routers/ baja del mínimo.
# El mínimo solo debe subir: al añadir pruebas de inversiones, admin o presupuesto, súbelo aquí.
# Uso: ./tests/run_cobertura.sh
set -euo pipefail
cd "$(dirname "$0")/.."

MINIMO_ROUTERS=80
IMAGEN="${TEST_IMAGE:-python:3.11-slim}"
docker run --rm -v "$PWD":/src:ro -e JWT_SECRET_KEY="$(printf 'k%.0s' {1..48})" -e MINIMO_ROUTERS="$MINIMO_ROUTERS" "$IMAGEN" bash -c '
  if ! pip install -q --no-cache-dir -r /src/requirements.txt httpx coverage >/tmp/pip.log 2>&1; then
    echo "ERROR: no se pudieron instalar las dependencias (pip)"; tail -5 /tmp/pip.log; exit 1
  fi
  cp -r /src /tmp/app && cd /tmp/app
  printf "[run]\nsource =\n    /tmp/app\n    /tmp/app_run\n[paths]\ncodigo =\n    /tmp/app/\n    /tmp/app_run/\n" > .coveragerc
  for suite in test_finanzas.py tests/test_migraciones.py tests/test_mercado.py \
               tests/test_acciones.py tests/test_api_aislamiento.py tests/test_escenario_api.py tests/test_durabilidad.py tests/test_api_rutas.py tests/test_observabilidad.py tests/test_seguridad_login.py tests/test_limite_cuerpo.py; do
    if ! coverage run -a --rcfile=.coveragerc tests/cov_run.py "$suite" >/tmp/suite.log 2>&1; then
      echo "FALLO: $suite"; tail -5 /tmp/suite.log; exit 1
    fi
  done
  coverage combine --rcfile=.coveragerc >/dev/null
  coverage report --rcfile=.coveragerc --include="routers/*" | tail -1
  PCT=$(coverage report --rcfile=.coveragerc --include="routers/*" --format=total)
  echo "Cobertura de routers/: ${PCT}% (mínimo ${MINIMO_ROUTERS}%)"
  if [ "${PCT%%.*}" -lt "$MINIMO_ROUTERS" ]; then echo "RESULTADO: cobertura por debajo del mínimo"; exit 1; fi
  echo "RESULTADO: cobertura OK"
'
