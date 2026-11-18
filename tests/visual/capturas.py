"""tests/visual/capturas.py - Recorrido visual sistemático con comprobaciones automáticas.

Cada vista y cada modal, en claro y oscuro, a cinco anchos y con tres cuentas (escenario con historia, vacía y de
valores extremos). Guarda una captura por combinación y un findings.json con lo que las comprobaciones detectan:
desborde horizontal, elementos fuera del ancho, texto cortado sin puntos suspensivos, áreas táctiles menores de 44 px
(solo hasta 768 px), contraste de texto menor que AA y errores de consola.

Corre en el contenedor de Playwright: tests/visual/correr.sh. Nunca toca data/.
Salida: /salida (montada desde auditoria/visual/): <cuenta>/<vista>-<tema>-<ancho>.png y findings.json.
"""
import json
import os
import sys
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(RAIZ, "tests"))

import test_frontend as tf  # noqa: E402  (copia el repo a un temporal y deja el reloj de prueba)
from playwright.sync_api import sync_playwright  # noqa: E402

escenario, master_db, security = tf.escenario, tf.master_db, tf.security
SALIDA = os.environ.get("SALIDA", "/salida")
ANCHOS = [int(a) for a in os.environ.get("ANCHOS", "360,390,768,1024,1440").split(",")]
ALTOS = {360: 740, 390: 844, 768: 1024, 1024: 768, 1440: 900}
TEMAS = os.environ.get("TEMAS", "light,dark").split(",")
PESTANAS = ["tab-dashboard", "tab-movimientos", "tab-presupuesto", "tab-apartados", "tab-inversiones", "tab-calculadora-rf"]
MODALES = ["modal-nuevo-movimiento", "modal-nuevo-fijo", "modal-nueva-posicion", "modal-crear-apartado",
           "modal-tarjeta-presupuesto", "modal-editar-inversion", "modal-editar-transaccion", "modal-editar-recurrente"]
MODALES_ADMIN = ["modal-crear-usuario", "modal-editar-usuario", "modal-eliminar-usuario"]
CLAVE = "Passw0rd#Visual1"

# Comprobaciones: se ejecutan en la página y devuelven una lista de hallazgos
JS_COMPROBAR = r"""
(ancho) => {
  const hall = [];
  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const sel = (el) => { let s = el.tagName.toLowerCase(); if (el.id) s += '#' + el.id; else if (el.className && typeof el.className === 'string') s += '.' + el.className.trim().split(/\s+/).slice(0,2).join('.'); return s; };
  const texto = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\s+/g,' ').slice(0,40);
  const enScroll = (el) => { for (let p = el.parentElement; p; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden' && p.scrollWidth > p.clientWidth) return true; } return false; };
  const modal = [...document.querySelectorAll('.modal-overlay')].find(m => !m.classList.contains('hidden') && vis(m));
  const raiz = modal || document.body;
  const enPantalla = (el) => { const r = el.getBoundingClientRect(); return r.right > 0 && r.left < innerWidth && r.bottom > 0; };
  const el_all = [...raiz.querySelectorAll('*')].filter(vis).filter(el => !el.closest('.sr-only') && !el.classList.contains('sr-only'))
    .filter(el => { const sb = el.closest('.app-sidebar'); return !sb || enPantalla(sb); });
  const de = document.documentElement;
  if (de.scrollWidth > innerWidth) hall.push({tipo: 'desborde-horizontal', detalle: `${de.scrollWidth}px > ${innerWidth}px`});
  const fuera = new Set();
  for (const el of el_all) {
    const r = el.getBoundingClientRect();
    if ((r.right > innerWidth + 1 || r.left < -1) && !enScroll(el) && getComputedStyle(el).position !== 'fixed') fuera.add(sel(el) + ' "' + texto(el) + '"');
  }
  [...fuera].slice(0, 8).forEach(d => hall.push({tipo: 'fuera-del-ancho', detalle: d}));
  const cortados = new Set();
  for (const el of el_all) {
    const s = getComputedStyle(el);
    if (!el.childNodes.length || ![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    if ((s.overflow === 'hidden' || s.overflowX === 'hidden') && el.scrollWidth > el.clientWidth + 2 && s.textOverflow !== 'ellipsis')
      cortados.add(sel(el) + ' "' + texto(el) + '"');
  }
  [...cortados].slice(0, 8).forEach(d => hall.push({tipo: 'texto-cortado', detalle: d}));
  if (ancho <= 768) {
    const chicos = new Set();
    for (const el of raiz.querySelectorAll('button, a[href], [role=button]')) {
      if (!vis(el) || el.disabled || !enPantalla(el) || el.closest('.sr-only')) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 43.9 || r.height < 43.9) chicos.add(`${sel(el)} "${texto(el)}" ${Math.round(r.width)}x${Math.round(r.height)}`);
    }
    [...chicos].slice(0, 12).forEach(d => hall.push({tipo: 'area-tactil-menor-44', detalle: d}));
  }
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map(x => parseFloat(x)); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
  const lum = (c) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const fondo = (el) => { let acum = null; for (let p = el; p; p = p.parentElement) { const s = getComputedStyle(p); if (s.backgroundImage !== 'none') return null;
      const c = parse(s.backgroundColor); if (!c || c.a === 0) continue; if (c.a >= 1) return acum ? {r: acum.r * acum.a + c.r * (1 - acum.a), g: acum.g * acum.a + c.g * (1 - acum.a), b: acum.b * acum.a + c.b * (1 - acum.a), a: 1} : c; acum = acum || c; }
    return null; };
  const bajos = new Set();
  for (const el of el_all) {
    if (el.disabled || !([...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))) continue;
    const s = getComputedStyle(el); const fg = parse(s.color); const bg = fondo(el); if (!fg || !bg) continue;
    const a = fg.a; const mezcla = {r: fg.r * a + bg.r * (1 - a), g: fg.g * a + bg.g * (1 - a), b: fg.b * a + bg.b * (1 - a)};
    const L1 = lum(mezcla), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const px = parseFloat(s.fontSize), grueso = parseInt(s.fontWeight) >= 700; const grande = px >= 24 || (px >= 18.66 && grueso);
    if (ratio < (grande ? 3 : 4.5)) bajos.add(`${sel(el)} "${texto(el)}" ${ratio.toFixed(2)}:1`);
  }
  [...bajos].slice(0, 12).forEach(d => hall.push({tipo: 'contraste-bajo', detalle: d}));
  return hall;
}
"""


def _post(base, token, ruta, cuerpo):
    req = urllib.request.Request(base + ruta, method="POST", data=json.dumps(cuerpo).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="ignore")


def _token(base, usuario, clave):
    req = urllib.request.Request(base + "/auth/login", data=json.dumps({"username": usuario, "password": clave}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]


def sembrar_cuentas(base):
    """escenario: historia de varios meses. vacia: recién creada. extremo: cifras enormes y textos largos. admin: panel."""
    tf._sembrar_escenario(base)
    for nombre, rol in (("vacia", "user"), ("extremo", "user"), ("admin_v", "admin")):
        master_db.create_user_entry(nombre, security.hash_password(CLAVE), role=rol, email=f"{nombre}@test.local")
    tok = _token(base, "extremo", CLAVE)
    largo = "Descripción muy larga de un movimiento que no cabe en una sola línea " * 2
    hoy = escenario.HOY
    resultados = [
        _post(base, tok, "/api/transacciones", {"fecha": hoy, "monto": 987654321.99, "tipo": "ingreso", "categoria_id": 12, "descripcion": largo[:120]}),
        _post(base, tok, "/api/transacciones", {"fecha": hoy, "monto": 123456789.55, "tipo": "gasto", "categoria_id": 2, "descripcion": largo[:120]}),
        _post(base, tok, "/api/transacciones", {"fecha": hoy, "monto": 0.01, "tipo": "gasto", "categoria_id": 1, "descripcion": "X"}),
        _post(base, tok, "/api/apartados", {"nombre": "Meta con un nombre larguísimo para ver cómo se parte", "tipo": "meta", "meta_monto": 999999999.99}),
        _post(base, tok, "/api/inversiones", {"ticker": "BRK-B", "cantidad": 123456.789, "precio_promedio": 450.123, "precio_actual": 480.5, "moneda": "USD"}),
        _post(base, tok, "/api/inversiones", {"ticker": "WALMEX.MX", "cantidad": 1, "precio_promedio": 0.01, "precio_actual": 65.2, "moneda": "MXN"}),
    ]
    return [r[0] for r in resultados]


def entrar(page, base, usuario, clave):
    page.goto(base + "/login")
    page.fill("#login-username", usuario)
    page.fill("#login-password", clave)
    page.click("#form-login [type=submit]")
    page.wait_for_selector("#vista-app:not(.hidden)", timeout=15000)
    page.wait_for_timeout(600)


def correr(base, navegador):
    hallazgos, capturas = [], 0
    cuentas = [("escenario", escenario.USUARIO, escenario.CLAVE, PESTANAS + MODALES),
               ("vacia", "vacia", CLAVE, PESTANAS),
               ("extremo", "extremo", CLAVE, PESTANAS),
               ("admin", "admin_v", CLAVE, PESTANAS + ["tab-admin"] + MODALES_ADMIN)]
    solo_vistas = [v for v in os.environ.get("VISTAS", "").split(",") if v]
    solo_cuentas = [c for c in os.environ.get("CUENTAS", "").split(",") if c]
    for nombre, usuario, clave, vistas in cuentas:
        if solo_cuentas and nombre not in solo_cuentas:
            continue
        if solo_vistas:
            vistas = [v for v in vistas if v in solo_vistas]
        os.makedirs(os.path.join(SALIDA, nombre), exist_ok=True)
        for tema in TEMAS:
            for ancho in ANCHOS:
                if nombre in ("vacia", "extremo") and ancho in (768, 1024):
                    continue
                ctx = navegador.new_context(viewport={"width": ancho, "height": ALTOS[ancho]}, color_scheme=tema, has_touch=ancho <= 768)
                ctx.add_init_script(f"localStorage.setItem('theme','{tema}')")
                page = ctx.new_page()
                errores = []
                page.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
                page.on("console", lambda m: errores.append(f"console: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
                try:
                    # Login (una vez por cuenta/tema/ancho)
                    if nombre == "escenario":
                        page.goto(base + "/login")
                        page.wait_for_selector("#vista-login:not(.hidden)")
                        page.wait_for_timeout(500)
                        _registrar(page, f"login-{tema}-{ancho}", nombre, "login", tema, ancho, hallazgos, errores)
                        capturas += 1
                    entrar(page, base, usuario, clave)
                    for vista in vistas:
                        try:
                            if vista.startswith("tab-"):
                                page.evaluate(f"document.querySelector('[data-tab=\"{vista}\"]').click()")
                                page.wait_for_selector(f"#{vista}.active", timeout=8000)
                            else:
                                # Modal: se muestra tal cual (sin pulsar el botón que lo abre) dentro de su pestaña
                                page.evaluate("""(id) => { const m = document.getElementById(id); const t = m.closest('.tab-content');
                                    if (t) document.querySelector('[data-tab="' + t.id + '"]').click();
                                    document.querySelectorAll('.modal-overlay').forEach(x => x.classList.add('hidden'));
                                    m.classList.remove('hidden'); m.classList.add('show'); }""", vista)
                            page.wait_for_timeout(3000 if vista in ("tab-dashboard", "tab-inversiones") else 450)   # las gráficas se animan al entrar
                            _registrar(page, f"{vista}-{tema}-{ancho}", nombre, vista, tema, ancho, hallazgos, errores)
                            capturas += 1
                        except Exception as e:  # una vista que no se deja abrir ya es un hallazgo
                            hallazgos.append({"cuenta": nombre, "vista": vista, "tema": tema, "ancho": ancho, "tipo": "no-se-pudo-abrir", "detalle": str(e)[:200]})
                finally:
                    ctx.close()
    return hallazgos, capturas


def _registrar(page, archivo, cuenta, vista, tema, ancho, hallazgos, errores):
    ruta = os.path.join(SALIDA, cuenta, archivo + ".png")
    if os.environ.get("SIN_REDIMENSIONAR") and vista == "tab-dashboard":
        for cid in ("graficaFinanzas", "graficaCategorias"):
            page.locator("#" + cid).scroll_into_view_if_needed(); page.wait_for_timeout(2500)
            page.locator("#" + cid).locator("xpath=..").screenshot(path=os.path.join(SALIDA, cuenta, f"{cid}-{tema}-{ancho}.png"))
        for nombre_rz, (w, hh) in (("alto", (ancho, 1900)), ("giro", (844, 390)), ("vuelta", (ancho, 844))):
            page.set_viewport_size({"width": w, "height": hh}); page.wait_for_timeout(2500)
            for cid in ("graficaFinanzas", "graficaCategorias"):
                page.locator("#" + cid).scroll_into_view_if_needed()
                lleno = page.evaluate("(id) => { const c = document.getElementById(id); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return [c.width, c.height, n]; }", cid)
                print("redimension", nombre_rz, w, hh, cid, "píxeles pintados:", lleno)
                page.locator("#" + cid).locator("xpath=..").screenshot(path=os.path.join(SALIDA, cuenta, f"{cid}-{nombre_rz}.png"))
        print("graficas", ancho, page.evaluate("[...document.querySelectorAll('canvas')].map(c => [c.id, c.width, c.height, c.getBoundingClientRect().height, !!window.Chart && !!Chart.getChart(c)])"))
    if not vista.startswith("modal-") and not os.environ.get("SIN_REDIMENSIONAR"):
        # El contenido hace scroll dentro de un contenedor: se agranda la ventana al alto del contenido para verlo entero
        alto = page.evaluate("Math.max(...[...document.querySelectorAll('*')].map(e => e.scrollHeight))")
        page.set_viewport_size({"width": ancho, "height": min(max(alto, ALTOS[ancho]), 6000)})
        page.wait_for_timeout(2000 if vista in ("tab-dashboard", "tab-inversiones") else 250)   # tras redimensionar, las gráficas se animan de nuevo
    # La captura a página completa redimensiona por dentro y reiniciaría la animación de las gráficas (saldrían vacías)
    page.evaluate("() => { if (window.Chart) { Chart.defaults.animation = false; Object.values(Chart.instances || {}).forEach(c => { c.options.animation = false; c.update('none'); }); } }")
    page.wait_for_timeout(200)
    page.screenshot(path=ruta, full_page=not vista.startswith("modal-"))
    if not vista.startswith("modal-") and not os.environ.get("SIN_REDIMENSIONAR"):
        page.set_viewport_size({"width": ancho, "height": ALTOS[ancho]})
    for h in page.evaluate(JS_COMPROBAR, ancho):
        hallazgos.append({"cuenta": cuenta, "vista": vista, "tema": tema, "ancho": ancho, **h})
    for e in errores:
        hallazgos.append({"cuenta": cuenta, "vista": vista, "tema": tema, "ancho": ancho, "tipo": "error-consola", "detalle": e[:200]})
    errores.clear()


if __name__ == "__main__":
    escenario.activar_modo_prueba()
    proc, base = tf._arrancar_servidor()
    try:
        master_db.create_user_entry(tf.USUARIO, security.hash_password(tf.CLAVE), email=f"{tf.USUARIO}@test.local")
        print("siembra:", sembrar_cuentas(base))
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            hallazgos, capturas = correr(base, navegador)
            navegador.close()
        with open(os.path.join(SALIDA, "findings.json"), "w", encoding="utf-8") as f:
            json.dump(hallazgos, f, ensure_ascii=False, indent=1)
        print(f"{capturas} capturas, {len(hallazgos)} hallazgos automáticos")
    finally:
        proc.terminate()
