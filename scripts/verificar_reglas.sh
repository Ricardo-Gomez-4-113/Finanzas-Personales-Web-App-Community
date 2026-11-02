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

# 4. Sin sombras ni brillos neutros con color literal fuera de tokens.css: se usa rgb(var(--sombra-rgb) / alfa)
#    o rgb(var(--luz-rgb) / alfa). La paleta vive en tokens.css; la landing (solo SaaS) tiene su propia hoja.
CSS_APP=$(ls static/css/*.css | grep -v -e landing -e tokens)
if grep -nE "rgba\(\s*(0\s*,\s*0\s*,\s*0|255\s*,\s*255\s*,\s*255)\s*," $CSS_APP; then
  echo "FALLO: rgba neutro literal en static/css (usar --sombra-rgb o --luz-rgb)"; fallos=1
fi

# 5. Los !important (declaraciones, no comentarios) tienen tope. Cada uno de los actuales lleva su motivo escrito
#    junto a la regla. Para uno nuevo: selector más específico con :root; si de verdad hace falta, justificarlo y subir el tope.
MAX_IMPORTANT=15
n_important=$(grep -hE "![i]mportant\s*[;}]" $CSS_APP | wc -l)
if (( n_important > MAX_IMPORTANT )); then
  echo "FALLO: hay $n_important declaraciones !important en static/css (tope $MAX_IMPORTANT)"; fallos=1
fi

# 6. El dinero vive en centavos enteros (dinero.py). Un total calculado en SQL sobre una columna de dinero
#    sale en centavos: se divide entre 100.0 en la propia consulta. Excepción: HAVING compara contra centavos.
CODIGO_SQL="finanzas_core.py routers/*.py"
if grep -nE "SUM\((CASE|[a-z]\.)?[^)]*(monto|monto_actual|capital|ingreso_base)" $CODIGO_SQL | grep -v "100" | grep -v "HAVING"; then
  echo "FALLO: SUM sobre una columna de dinero sin dividir entre 100.0 (los montos se guardan en centavos)"; fallos=1
fi

[[ $fallos -eq 0 ]] && echo "Reglas OK"
exit $fallos
