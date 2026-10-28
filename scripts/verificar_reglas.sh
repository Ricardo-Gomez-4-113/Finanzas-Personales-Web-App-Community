#!/usr/bin/env bash
# Verifica reglas de GEMINI.md que se pueden automatizar. Sale con 1 si alguna falla.
# Uso: ./scripts/verificar_reglas.sh
set -u
cd "$(dirname "$0")/.."
fallos=0

# 1. Sin emojis nativos en la UI (U+1F300-1FAFF, U+FE0F, ⏳⏩ y similares U+23E9-23FA, símbolos U+2600-26FF, ✅ ❌ ⭐; ✓ y ✗ son glifos de texto y se permiten)
if grep -rnP "[\x{1F300}-\x{1FAFF}\x{FE0F}\x{23E9}-\x{23FA}\x{2600}-\x{26FF}\x{2705}\x{274C}\x{2B50}]" static/ --include=*.html --include=*.js --include=*.css; then
  echo "FALLO: emojis nativos en static/ (usar micro-SVG)"; fallos=1
fi

# 2. Todo CSS/JS referenciado por index.html lleva cache-busting (?v=) y existe
while IFS= read -r ref; do
  archivo="${ref%%\?*}"
  [[ "$ref" == *"?v="* ]] || { echo "FALLO: $ref sin ?v= en static/index.html"; fallos=1; }
  [[ -f ".${archivo}" ]] || { echo "FALLO: $archivo referenciado pero no existe"; fallos=1; }
done < <(grep -oP '(?:href|src)="\K/static/(?:css|js)/[^"]+' static/index.html)

# 2b. Los ?v= coinciden con el hash del contenido actual (se arregla con scripts/cache_bust.py)
python3 scripts/cache_bust.py --check || fallos=1

# 3. Solo en repo Community: la landing no debe existir
if [[ "$(basename "$PWD")" == *Community* ]]; then
  for f in static/landing.html static/css/landing.css static/js/landing.js; do
    [[ -e "$f" ]] && { echo "FALLO: $f no debe existir en Community"; fallos=1; }
  done
fi

[[ $fallos -eq 0 ]] && echo "Reglas OK"
exit $fallos
