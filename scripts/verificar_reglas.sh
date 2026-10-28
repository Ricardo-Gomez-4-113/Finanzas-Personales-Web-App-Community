#!/usr/bin/env bash
# Verifica reglas de GEMINI.md que se pueden automatizar. Sale con 1 si alguna falla.
# Uso: ./scripts/verificar_reglas.sh
set -u
cd "$(dirname "$0")/.."
fallos=0

# 1. Sin emojis nativos en la UI (U+1F300-1FAFF y el selector de emoji U+FE0F; ✓ y ✗ son glifos de texto y se permiten)
if grep -rnP "[\x{1F300}-\x{1FAFF}\x{FE0F}]" static/ --include=*.html --include=*.js --include=*.css; then
  echo "FALLO: emojis nativos en static/ (usar micro-SVG)"; fallos=1
fi

# 2. Todo CSS/JS referenciado por index.html lleva cache-busting (?v=) y existe
while IFS= read -r ref; do
  archivo="${ref%%\?*}"
  [[ "$ref" == *"?v="* ]] || { echo "FALLO: $ref sin ?v= en static/index.html"; fallos=1; }
  [[ -f ".${archivo}" ]] || { echo "FALLO: $archivo referenciado pero no existe"; fallos=1; }
done < <(grep -oP '(?:href|src)="\K/static/(?:css|js)/[^"]+' static/index.html)

# 3. Solo en repo Community: la landing no debe existir
if [[ "$(basename "$PWD")" == *Community* ]]; then
  for f in static/landing.html static/css/landing.css static/js/landing.js; do
    [[ -e "$f" ]] && { echo "FALLO: $f no debe existir en Community"; fallos=1; }
  done
fi

[[ $fallos -eq 0 ]] && echo "Reglas OK"
exit $fallos
