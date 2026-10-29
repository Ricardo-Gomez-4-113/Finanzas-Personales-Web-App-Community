#!/usr/bin/env bash
# Verificación de solo lectura de los contenedores en ejecución (no crea usuarios ni toca datos).
# Uso: ./scripts/smoke_produccion.sh            (SaaS 8001 y Community 8002)
#      ./scripts/smoke_produccion.sh 8002       (solo el puerto indicado)
# Sale con 1 si algo crítico falla. Las advertencias (por ejemplo, respaldo vencido) no fallan.
set -u
PUERTOS=("$@")
[[ ${#PUERTOS[@]} -eq 0 ]] && PUERTOS=(8001 8002)
fallos=0; avisos=0

ok()    { echo "  ok      $1"; }
aviso() { echo "  AVISO   $1"; avisos=$((avisos+1)); }
falla() { echo "  FALLO   $1"; fallos=$((fallos+1)); }

# Código HTTP de una URL (000 si no responde)
codigo() { curl -s -o /dev/null -w "%{http_code}" -m 10 "$1"; }

for p in "${PUERTOS[@]}"; do
  base="http://127.0.0.1:$p"
  echo "== puerto $p"

  # 1. Salud: la base maestra responde; "degradado" es aviso (p. ej. respaldo vencido)
  salud=$(curl -s -m 10 "$base/health")
  case "$salud" in
    *'"base_maestra":"ok"'*) ok "/health base maestra";;
    *) falla "/health no responde o la base maestra falla: ${salud:-sin respuesta}";;
  esac
  if echo "$salud" | grep -q '"status":"degradado"'; then
    aviso "/health degradado: $(echo "$salud" | grep -o '"respaldo":"[a-z]*"')"
  fi

  # 2. Páginas principales
  for ruta in / /login /app; do
    c=$(codigo "$base$ruta")
    [[ "$c" == "200" ]] && ok "GET $ruta -> $c" || falla "GET $ruta -> $c"
  done

  # 3. Recursos con cache-busting: cada CSS/JS referenciado responde 200
  html=$(curl -s -m 10 "$base/app")
  refs=$(echo "$html" | grep -oP '(?:href|src)="\K/static/(?:css|js)/[^"]+' | sort -u)
  malos=0
  for ref in $refs; do
    c=$(codigo "$base$ref")
    [[ "$c" == "200" ]] || { falla "recurso $ref -> $c"; malos=$((malos+1)); }
  done
  [[ $malos -eq 0 ]] && ok "$(echo "$refs" | wc -l) recursos CSS/JS responden 200"

  # 4. Los recursos llevan ?v= (sin él el navegador podría servir una versión vieja)
  sin_v=$(echo "$html" | grep -oP '(?:href|src)="/static/(?:css|js)/[^"]+"' | grep -vc '?v=')
  [[ "$sin_v" == "0" ]] && ok "todos los recursos llevan ?v=" || falla "$sin_v recursos sin ?v="

  # 5. Autenticación: la API rechaza peticiones sin token
  c=$(codigo "$base/api/resumen")
  [[ "$c" == "401" ]] && ok "/api/resumen sin token -> 401" || falla "/api/resumen sin token -> $c (debe ser 401)"

  # 6. Cabeceras de seguridad
  cab=$(curl -s -D - -o /dev/null -m 10 "$base/login")
  for cabecera in "Content-Security-Policy" "Strict-Transport-Security" "X-Content-Type-Options"; do
    echo "$cab" | grep -qi "^$cabecera:" && ok "cabecera $cabecera" || falla "falta cabecera $cabecera"
  done
  echo "$cab" | grep -qi "^Cache-Control: no-" && ok "/login sin caché" || aviso "/login sin Cache-Control no-cache"

  # 7. Edición Community: no debe servir la landing (solo existe en el SaaS)
  if [[ "$p" == "8002" ]]; then
    c=$(codigo "$base/static/landing.html")
    [[ "$c" == "404" ]] && ok "Community sin landing" || falla "Community expone landing.html -> $c"
  fi

  # 8. La API responde en tiempo razonable (medición simple)
  t=$(curl -s -o /dev/null -w "%{time_total}" -m 10 "$base/api/public/tipo-cambio")
  awk -v t="$t" 'BEGIN{exit !(t<3)}' && ok "tipo de cambio responde en ${t}s" || aviso "tipo de cambio lento: ${t}s"
done

echo
if [[ $fallos -eq 0 ]]; then
  echo "RESULTADO: OK (avisos: $avisos)"; exit 0
else
  echo "RESULTADO: $fallos fallo(s), avisos: $avisos"; exit 1
fi
