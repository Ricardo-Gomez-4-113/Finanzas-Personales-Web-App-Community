"""tests/auditoria/estres.py - Prueba de estrés: muchos usuarios a la vez contra una instancia temporal de la app.

Dos fases sobre el mismo servidor, que se lanza igual que en producción (con `--reload` si el Dockerfile de esa copia lo lleva):

  A. Carga por API en escalones (10, 25, 50, 100, 200 sesiones simultáneas): mezcla de lecturas, escrituras y descargas
     pesadas, y algunas cuentas abiertas en dos sesiones a la vez (celular y escritorio) para provocar carreras.
  B. Navegador bajo carga: en los escalones indicados, sesiones de Chromium (escritorio y celular, tema claro y oscuro)
     recorren las pestañas y buscan errores de JavaScript, respuestas 5xx, desbordes, empalmes y pestañas lentas.

Al final, con SQL propio (no con funciones de la app), comprueba la integridad de las bases, que lo depositado en cada
meta cuadre con lo que el cliente vio aceptar, que no haya sobregiros nuevos y que no haya movimientos duplicados.

Se corre dentro del contenedor de Playwright (tests/auditoria/run_estres.sh). Nunca toca data/ ni los contenedores.
Salida: <carpeta>/informe_estres.md, estres.json y hallazgos/ (capturas).
"""

import argparse
import asyncio
import collections
import datetime
import itertools
import json
import math
import os
import random
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import escritorio  # noqa: E402  (solo sus constantes y el cargador de módulos)
import personas  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOY = personas.HOY_BASE
NEGOCIO = {400, 404, 409, 422}          # rechazos de reglas de negocio: esperados, no son fallos
PESADAS = ("/api/backup", "/api/exportar-excel", "/vista-previa", "/auth/login")   # fuera del p95 que decide el límite
LIMITE_ERRORES = 0.05                  # más de 5 % de errores en un escalón: ese es el límite
LIMITE_P95_MS = 5000
LENTA_MS = 3000                        # una pestaña que tarda más en pintar es un hallazgo
PESTANAS = ["tab-dashboard", "tab-movimientos", "tab-apartados", "tab-inversiones", "tab-presupuesto", "tab-calculadora-rf"]
MIX = {"alta_gasto": 8, "alta_ingreso": 3, "deposito": 6, "retiro": 3, "editar": 2, "borrar": 2, "tarjeta": 1, "pesada": 5}
LECTURAS = ["/api/resumen", "/api/transacciones", "/api/recurrentes", "/api/recurrentes/periodos", "/api/apartados",
            "/api/presupuesto", "/api/historial-periodos", "/api/notificaciones", "/api/tipo-cambio", "/api/categorias",
            "/api/gastos-por-categoria", "/api/inversiones", "/api/rendimiento", "/auth/me", "/api/public/tipo-cambio"]
REPARTO = (("laura", 80), ("mara", 50), ("sofia", 40), ("ivan", 30), ("beto", 20))

# Mismo detector de empalmes que usé en la revisión de capturas: un control cuya caja se cruza con otro elemento.
# Se ignoran los adornos de moneda ($, MXN, %) que se dibujan dentro del campo a propósito.
DETECTOR = """() => {
  const visible = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
      return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const raiz = document.querySelector('.tab-content.active') || document.body;
  const ctl = [...raiz.querySelectorAll('button, a[href], input, select, textarea')].filter(visible);
  const otros = [...raiz.querySelectorAll('h1,h2,h3,h4,p,span,small,strong,label,td,th,li,dt,dd,button,a[href],input,select')].filter(visible)
      .filter(e => e.children.length === 0 || ['BUTTON','A','INPUT','SELECT'].includes(e.tagName));
  const txt = e => e.tagName.toLowerCase() + (typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(' ')[0] : '')
      + (e.type ? '[' + e.type + ']' : '') + ' «' + (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') || '').trim().slice(0, 30) + '»';
  const res = [], vistos = new Set();
  for (const a of ctl) { const ra = a.getBoundingClientRect();
    for (const b of otros) {
      if (a === b || a.contains(b) || b.contains(a)) continue;
      if (['INPUT', 'SELECT'].includes(a.tagName) && a.parentElement.contains(b) && (b.innerText || '').trim().length <= 4) continue;
      const rb = b.getBoundingClientRect();
      const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left), h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (w > 3 && h > 3) { const k = [txt(a), txt(b)].sort().join('|'); if (!vistos.has(k)) { vistos.add(k); res.push({a: txt(a), b: txt(b), w: Math.round(w), h: Math.round(h)}); } }
    } }
  return res.slice(0, 8);
}"""


def percentil(valores, p):
    if not valores:
        return 0.0
    v = sorted(valores)
    return v[min(len(v) - 1, max(0, math.ceil(p / 100 * len(v)) - 1))]


def normalizar(ruta):
    return re.sub(r"/\d+", "/{id}", ruta.split("?")[0])


# ---------------------------------------------------------------------------------------------------------------------
# Servidor temporal
# ---------------------------------------------------------------------------------------------------------------------
class Servidor:
    """La app en una carpeta temporal, lanzada como su Dockerfile (con o sin --reload), con reloj fijo y sin salir a internet."""

    def __init__(self, fuente, tmp, logs):
        self.tmp, self.logs = tmp, logs
        dockerfile = os.path.join(fuente, "Dockerfile")
        self.con_reload = "--reload" in (open(dockerfile, encoding="utf-8").read() if os.path.exists(dockerfile) else "")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.puerto = s.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.puerto}"
        self.proc = None

    def arrancar(self):
        env = {**os.environ, **escritorio.CORREO_VACIO, "FINANZAS_MODO_PRUEBA": "1", "FINANZAS_LOG_DIR": self.logs,
               "FINANZAS_HOY": f"{HOY.isoformat()}T10:00:00", "NOTIFICACIONES_TAREA": "1", "NOTIFICACIONES_PERIODO_MIN": "1"}
        lanzador = f"import sys, uvicorn\nuvicorn.run('main:app', host='127.0.0.1', port=int(sys.argv[1]), reload={self.con_reload})\n"
        self.proc = subprocess.Popen([sys.executable, "-c", lanzador, str(self.puerto)], cwd=self.tmp,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env)
        for _ in range(240):
            try:
                urllib.request.urlopen(self.base + "/health", timeout=1)
                return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("el servidor no arrancó")

    def parar(self):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def pids(self):
        """El proceso lanzado y todos sus descendientes (con --reload la app corre en un hijo)."""
        if self.proc is None:
            return []
        padres = {}
        for nombre in os.listdir("/proc"):
            if nombre.isdigit():
                try:
                    padres[int(nombre)] = int(open(f"/proc/{nombre}/stat").read().rsplit(")", 1)[1].split()[1])
                except Exception:
                    pass
        encontrados, pendientes = [], [self.proc.pid]
        while pendientes:
            p = pendientes.pop()
            encontrados.append(p)
            pendientes += [h for h, pa in padres.items() if pa == p]
        return encontrados


def cpu_y_memoria(pids):
    """(ticks de CPU acumulados, MB residentes) sumando los procesos."""
    ticks, paginas = 0, 0
    for pid in pids:
        try:
            campos = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
            ticks += int(campos[11]) + int(campos[12])
            paginas += int(open(f"/proc/{pid}/statm").read().split()[1])
        except Exception:
            pass
    return ticks, paginas * os.sysconf("SC_PAGE_SIZE") / 1e6


# ---------------------------------------------------------------------------------------------------------------------
# Datos de la corrida
# ---------------------------------------------------------------------------------------------------------------------
class Cuenta:
    def __init__(self, usuario, persona, ruta):
        self.usuario, self.persona, self.ruta = usuario, persona, ruta
        self.metas = []                       # ids de metas de esta cuenta
        self.inicial_metas = {}               # id -> monto_actual en centavos antes de la prueba
        self.base_min_prefijo = 0.0
        self.dep_ok = collections.Counter()   # id -> centavos aceptados de depósitos
        self.ret_ok = collections.Counter()
        self.ambiguos = collections.Counter() # id -> movimientos sin respuesta clara (tiempo de espera, conexión)
        self.marcas_enviadas = 0
        self.para_navegador = False


class Metricas:
    """Todo lo que ocurre en un escalón."""

    def __init__(self):
        self.peticiones = []                  # (etiqueta, estado, ms)
        self.errores = collections.Counter()  # tipo -> n
        self.cinco_xx = []                    # (método, ruta, estado, cuerpo)
        self.salud = []                       # ms de /health
        self.logins_ok = 0                    # sesiones que lograron entrar
        self.cpu = []                         # % de un núcleo
        self.mb = []

    def registrar(self, etiqueta, estado, ms, error=None):
        self.peticiones.append((etiqueta, estado, ms))
        if error:
            self.errores[error] += 1
        elif estado >= 500:
            self.errores["5xx"] += 1
        elif estado in (401, 403):
            self.errores["auth"] += 1

    def total_errores(self):
        return sum(self.errores.values())


class Sesion:
    """Una sesión de API: un cliente httpx con su token. Dos sesiones pueden compartir la misma Cuenta."""

    _ids = itertools.count(1)

    def __init__(self, cuenta, base, rng, httpx):
        self.cuenta, self.rng = cuenta, rng
        self.sid = next(Sesion._ids)          # distinto en cada sesión: las marcas de dos sesiones de una cuenta no chocan
        self.cli = httpx.AsyncClient(base_url=base, timeout=httpx.Timeout(30.0), limits=httpx.Limits(max_connections=4))
        self.cab = {}
        self.httpx = httpx
        self.n = 0

    async def pedir(self, M, metodo, ruta, **kw):
        etiqueta = f"{metodo} {normalizar(ruta)}"
        t0 = time.perf_counter()
        try:
            r = await self.cli.request(metodo, ruta, headers=self.cab, **kw)
        except self.httpx.TimeoutException:
            M.registrar(etiqueta, 0, (time.perf_counter() - t0) * 1000, "tiempo de espera")
            return None
        except self.httpx.HTTPError as e:
            M.registrar(etiqueta, 0, (time.perf_counter() - t0) * 1000, f"conexión: {type(e).__name__}")
            return None
        ms = (time.perf_counter() - t0) * 1000
        M.registrar(etiqueta, r.status_code, ms)
        if r.status_code >= 500:
            M.cinco_xx.append((metodo, ruta.split("?")[0], r.status_code, r.text[:160]))
        return r

    async def entrar(self, M):
        r = await self.pedir(M, "POST", "/auth/login", json={"username": self.cuenta.usuario, "password": personas.CLAVE})
        if r is not None and r.status_code == 200:
            self.cab = {"Authorization": f"Bearer {r.json()['access_token']}"}
            M.logins_ok += 1
            return True
        if r is not None and r.status_code < 500 and r.status_code not in (401, 403):
            M.errores[f"login rechazado {r.status_code}"] += 1     # p. ej. 429: antes la sesión se perdía sin dejar rastro
        return False

    def marca(self):
        self.n += 1
        self.cuenta.marcas_enviadas += 1
        return f"est-{self.cuenta.usuario}-{self.sid}-{self.n}"

    async def accion(self, M):
        rng, c = self.rng, self.cuenta
        if rng.random() < 0.70:
            await self.pedir(M, "GET", rng.choice(LECTURAS))
            return
        nombre = rng.choices(list(MIX), weights=list(MIX.values()))[0]
        fecha = (HOY - datetime.timedelta(days=rng.randint(0, 12))).isoformat()
        if nombre == "alta_gasto":
            await self.pedir(M, "POST", "/api/transacciones", json={"fecha": fecha, "monto": round(rng.uniform(5, 80), 2), "tipo": "gasto",
                                                                    "categoria_id": rng.choice((1, 2, 7)), "descripcion": self.marca()})
        elif nombre == "alta_ingreso":
            await self.pedir(M, "POST", "/api/transacciones", json={"fecha": fecha, "monto": round(rng.uniform(20, 200), 2), "tipo": "ingreso",
                                                                    "categoria_id": 12, "descripcion": self.marca()})
        elif nombre in ("deposito", "retiro") and c.metas:
            meta = rng.choice(c.metas)
            centavos = rng.randint(100, 5000)
            accion = "depositar" if nombre == "deposito" else "retirar"
            r = await self.pedir(M, "POST", f"/api/apartados/{meta}/movimiento", json={"monto": centavos / 100, "accion": accion})
            if r is None or r.status_code >= 500:
                c.ambiguos[meta] += 1      # puede haberse aplicado o no
            elif r.status_code in (200, 201):
                (c.dep_ok if accion == "depositar" else c.ret_ok)[meta] += centavos
        elif nombre in ("editar", "borrar"):
            r = await self.pedir(M, "GET", "/api/transacciones")
            if r is not None and r.status_code == 200:
                propios = [t for t in r.json() if str(t.get("descripcion") or "").startswith(f"est-{c.usuario}-")]
                if propios:
                    t = rng.choice(propios)
                    if nombre == "editar":
                        await self.pedir(M, "PUT", f"/api/transacciones/{t['id']}", json={"monto": round(rng.uniform(5, 60), 2), "version_esperada": t.get("version")})
                    else:
                        await self.pedir(M, "DELETE", f"/api/transacciones/{t['id']}")
        elif nombre == "tarjeta":
            await self.pedir(M, "POST", "/api/presupuesto/tarjetas", json={"nombre": f"T{self.marca()[-10:]}", "monto": 100, "unidad_orden": 1})
        elif nombre == "pesada":
            ruta, metodo, cuerpo = rng.choice([("/api/backup", "GET", None), ("/api/exportar-excel", "GET", None),
                                               ("/api/presupuesto/exportar-a-fijos/vista-previa", "POST", {"inicio_cargos": "natural"})])
            await self.pedir(M, metodo, ruta, **({"json": cuerpo} if cuerpo is not None else {}))

    async def correr(self, M, fin, retraso):
        await asyncio.sleep(retraso)
        if not await self.entrar(M):
            return
        while time.monotonic() < fin:
            await self.accion(M)
            await asyncio.sleep(min(3.0, self.rng.expovariate(1 / 0.6)))

    async def cerrar(self):
        await self.cli.aclose()


# ---------------------------------------------------------------------------------------------------------------------
# Navegador bajo carga
# ---------------------------------------------------------------------------------------------------------------------
class Hallazgos:
    def __init__(self, carpeta):
        self.carpeta, self.items, self.n = os.path.join(carpeta, "hallazgos"), {}, 0
        os.makedirs(self.carpeta, exist_ok=True)

    async def anotar(self, sig, tipo, detalle, donde, page=None):
        if sig in self.items:
            self.items[sig]["veces"] += 1
            return
        self.n += 1
        captura = None
        if page is not None:
            captura = f"hallazgos/{self.n:03d}_{tipo}.png"
            destino = os.path.join(os.path.dirname(self.carpeta), captura)
            try:
                # La app desplaza su contenido dentro de un contenedor, no la ventana: se captura el contenido de la pestaña activa
                await page.locator(".tab-content.active").screenshot(path=destino, timeout=5000)
            except Exception:
                try:
                    await page.screenshot(path=destino)
                except Exception:
                    captura = None
        self.items[sig] = {"tipo": tipo, "detalle": detalle, "donde": donde, "veces": 1, "captura": captura}


async def sesion_navegador(navegador, cuenta, base, perfil, fin, hall, M):
    """Una persona en Chromium: entra por la pantalla y recorre las pestañas hasta que se acaba el escalón."""
    disp = {"escritorio": {"viewport": {"width": 1366, "height": 768}},
            "celular": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}}[perfil["dispositivo"]]
    ctx = await navegador.new_context(**disp)
    page = await ctx.new_page()
    errores, quintos = [], []
    page.on("pageerror", lambda e: errores.append(f"pageerror: {str(e)[:140]}"))
    page.on("console", lambda m: errores.append(f"console: {m.text[:140]}") if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.on("response", lambda r: quintos.append(f"{r.status} {r.url.split('?')[0]}") if r.status >= 500 else None)
    donde = f"{perfil['dispositivo']}/{perfil['tema']}"
    try:
        t0 = time.perf_counter()
        await page.goto(base + "/login")
        await page.wait_for_selector("#login-username:visible", timeout=60000)
        await page.fill("#login-username", cuenta.usuario)
        await page.fill("#login-password", personas.CLAVE)
        await page.click("#form-login [type=submit]")
        await page.wait_for_selector("#vista-app:not(.hidden)", timeout=60000)
        M.registrar("UI: iniciar sesión", 200, (time.perf_counter() - t0) * 1000)
        await page.evaluate(f"document.documentElement.setAttribute('data-theme', '{perfil['tema']}')")
        while time.monotonic() < fin:
            for tab in PESTANAS:
                if time.monotonic() >= fin:
                    break
                t0 = time.perf_counter()
                try:
                    await page.evaluate(f"document.querySelector('[data-tab=\"{tab}\"]').click()")
                    await page.wait_for_selector(f"#{tab}.active", timeout=20000)
                    await page.wait_for_timeout(400)
                except Exception as e:
                    M.registrar(f"UI: {tab}", 0, (time.perf_counter() - t0) * 1000, "pestaña no cargó")
                    await hall.anotar(f"nocarga:{donde}:{tab}", "pestana-no-carga", str(e).split("\n")[0][:140], f"{donde}/{tab}", page)
                    continue
                ms = (time.perf_counter() - t0) * 1000 - 400
                M.registrar(f"UI: {tab}", 200, ms)
                if ms > LENTA_MS:
                    await hall.anotar(f"lenta:{donde}:{tab}", "pestana-lenta", f"tardó {ms / 1000:.1f} s en pintar", f"{donde}/{tab}", page)
                ancho = await page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
                if ancho[0] > ancho[1]:
                    await hall.anotar(f"desborde:{donde}:{tab}", "desborde", f"scrollWidth {ancho[0]} > {ancho[1]}", f"{donde}/{tab}", page)
                for e in await page.evaluate(DETECTOR):
                    await hall.anotar(f"empalme:{donde}:{tab}:{e['a']}|{e['b']}", "empalme", f"{e['a']} × {e['b']} ({e['w']}×{e['h']} px)", f"{donde}/{tab}", page)
                for e in errores:
                    await hall.anotar(f"js:{e[:80]}", "error-js", e, f"{donde}/{tab}", page)
                for e in quintos:
                    await hall.anotar(f"5xx:{e}", "5xx-en-navegador", e, f"{donde}/{tab}", page)
                errores.clear()
                quintos.clear()
            # Acciones ligeras: menú de usuario y un depósito por la pantalla de Metas
            try:
                await page.evaluate("document.querySelector('#btn-user-menu-trigger')?.click()")
                await page.wait_for_selector("#user-dropdown-menu.show", timeout=5000)
                await page.keyboard.press("Escape")
            except Exception as e:
                await hall.anotar(f"menu:{donde}", "menu-usuario", str(e).split("\n")[0][:120], donde, page)
            try:
                await page.evaluate("document.querySelector('[data-tab=\"tab-apartados\"]').click()")
                await page.wait_for_selector(".card-apartado", timeout=10000)
                meta = await page.get_attribute(".card-apartado", "data-apartado-id")
                saldo_antes = await page.inner_text(f".card-apartado[data-apartado-id=\"{meta}\"] .apartado-saldo")
                await page.fill(f"#monto-acc-{meta}", "1")
                t0 = time.perf_counter()
                await page.click(f".card-apartado[data-apartado-id=\"{meta}\"] .acciones-apartado button:first-of-type", timeout=8000)
                # La app no avisa si el depósito sale bien: el éxito es que cambie el saldo de la meta. Si falla, sale un aviso.
                resultado = None
                while resultado is None and time.perf_counter() - t0 < 10:
                    if await page.inner_text(f".card-apartado[data-apartado-id=\"{meta}\"] .apartado-saldo") != saldo_antes:
                        resultado = "saldo"
                    elif await page.query_selector(".toast"):
                        resultado = "aviso"
                    else:
                        await page.wait_for_timeout(100)
                ms_dep = (time.perf_counter() - t0) * 1000
                if resultado is None:
                    await hall.anotar(f"deposito-sin-respuesta:{donde}", "deposito-sin-respuesta", "10 s sin que cambie el saldo ni salga un aviso", donde, page)
                elif resultado == "saldo":
                    M.registrar("UI: depositar en una meta", 200, ms_dep)
                else:
                    texto = await page.inner_text(".toast")
                    M.registrar("UI: depositar en una meta", 422, ms_dep)
                    if re.search(r"error de conexi|no se pudo|error interno", texto, re.I):
                        await hall.anotar(f"toast:{texto[:60]}", "aviso-de-error", texto[:140], donde, page)
            except Exception as e:
                await hall.anotar(f"deposito-ui:{donde}", "deposito-en-pantalla", str(e).split("\n")[0][:120], donde, page)
    except Exception as e:
        await hall.anotar(f"nav-fallo:{donde}:{str(e)[:50]}", "sesion-de-navegador", str(e).split("\n")[0][:160], donde)
    finally:
        await ctx.close()


# ---------------------------------------------------------------------------------------------------------------------
# Registro del servidor y comprobaciones finales
# ---------------------------------------------------------------------------------------------------------------------
class RegistroServidor:
    """Lee solo lo nuevo de los logs del servidor: errores con traza y bloqueos de SQLite."""

    def __init__(self, carpeta):
        self.carpeta, self.pos, self.firmas = carpeta, {}, collections.Counter()

    def leer(self):
        nuevos = 0
        for raiz, _d, archivos in os.walk(self.carpeta):
            for nombre in archivos:
                ruta = os.path.join(raiz, nombre)
                try:
                    with open(ruta, "rb") as f:
                        f.seek(self.pos.get(ruta, 0))
                        datos = f.read()
                        self.pos[ruta] = f.tell()
                except OSError:
                    continue
                for linea in datos.decode("utf-8", "replace").splitlines():
                    if '"error_servidor"' in linea or "database is locked" in linea or '"nivel": "ERROR"' in linea:
                        try:
                            d = json.loads(linea)
                            firma = (d.get("mensaje") or "")[:80] + " | " + ((d.get("traza") or d.get("exc") or "").strip().splitlines() or [""])[-1][:100]
                        except ValueError:
                            firma = linea[:160]
                        self.firmas[firma] += 1
                        nuevos += 1
        return nuevos


def comprobar_bases(cuentas, carpeta_datos):
    """SQL propio sobre las bases ya sin servidor. Devuelve la lista de violaciones."""
    violaciones = []
    maestra = os.path.join(carpeta_datos, "master.db")
    for nombre, ruta in [("base maestra", maestra)] + [(c.usuario, c.ruta) for c in cuentas]:
        try:
            con = sqlite3.connect(ruta)
            r = con.execute("PRAGMA integrity_check").fetchone()[0]
            con.close()
            if r != "ok":
                violaciones.append({"tipo": "integridad", "donde": nombre, "detalle": r[:160]})
        except sqlite3.Error as e:
            violaciones.append({"tipo": "integridad", "donde": nombre, "detalle": f"no se pudo abrir: {e}"})
    for c in cuentas:
        try:
            con = sqlite3.connect(c.ruta)
            for meta, inicial in c.inicial_metas.items():
                if c.para_navegador:
                    continue
                fila = con.execute("SELECT monto_actual FROM apartados WHERE id = ?", (meta,)).fetchone()
                if fila is None:
                    continue
                esperado = inicial + c.dep_ok[meta] - c.ret_ok[meta]
                if fila[0] != esperado:
                    clase = "meta-ambigua" if c.ambiguos[meta] else "meta-descuadrada"
                    violaciones.append({"tipo": clase, "donde": f"{c.usuario} meta {meta}",
                                        "detalle": f"guardado {fila[0] / 100:.2f}, esperado {esperado / 100:.2f} (inicial {inicial / 100:.2f}, depósitos {c.dep_ok[meta] / 100:.2f}, retiros {c.ret_ok[meta] / 100:.2f}, sin respuesta clara: {c.ambiguos[meta]})"})
                if fila[0] < 0:
                    violaciones.append({"tipo": "meta-negativa", "donde": f"{c.usuario} meta {meta}", "detalle": f"monto_actual {fila[0] / 100:.2f}"})
            # Saldo acumulado por fecha: no debe quedar más negativo que al empezar
            acum, minimo = 0, 0
            for _f, delta in con.execute("SELECT fecha, SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END) FROM transacciones GROUP BY fecha ORDER BY fecha"):
                acum += delta
                minimo = min(minimo, acum)
            if minimo / 100 < min(c.base_min_prefijo, 0) - 0.001:
                violaciones.append({"tipo": "sobregiro", "donde": c.usuario, "detalle": f"saldo mínimo {minimo / 100:.2f} (al empezar {c.base_min_prefijo:.2f})"})
            for marca, n in con.execute("SELECT descripcion, COUNT(*) FROM transacciones WHERE descripcion LIKE 'est-%' GROUP BY descripcion HAVING COUNT(*) > 1"):
                violaciones.append({"tipo": "duplicado", "donde": c.usuario, "detalle": f"«{marca}» aparece {n} veces"})
            con.close()
        except sqlite3.Error as e:
            violaciones.append({"tipo": "comprobacion", "donde": c.usuario, "detalle": f"no se pudo leer: {e}"})
    return violaciones


def foto_inicial(c):
    con = sqlite3.connect(f"file:{c.ruta}?mode=ro", uri=True)
    c.inicial_metas = {f[0]: f[1] for f in con.execute("SELECT id, monto_actual FROM apartados")}
    acum, minimo = 0, 0
    for _f, delta in con.execute("SELECT fecha, SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END) FROM transacciones GROUP BY fecha ORDER BY fecha"):
        acum += delta
        minimo = min(minimo, acum)
    c.base_min_prefijo = minimo / 100
    c.metas = list(c.inicial_metas)
    con.close()


# ---------------------------------------------------------------------------------------------------------------------
# Escalones y programa principal
# ---------------------------------------------------------------------------------------------------------------------
def resumen_escalon(n, dur, M, servidor_ticks):
    lecturas = [ms for (e, s, ms) in M.peticiones if s and not any(p in e for p in PESADAS) and not e.startswith("UI:")]
    todos = [ms for (e, s, ms) in M.peticiones if s and not e.startswith("UI:") and e != "POST /auth/login"]
    logins = [ms for (e, s, ms) in M.peticiones if s and e == "POST /auth/login"]
    n_req = len([1 for (e, _s, _m) in M.peticiones if not e.startswith("UI:")])
    negocio = sum(1 for (e, s, _m) in M.peticiones if s in NEGOCIO)
    err = M.total_errores()
    por_ep = collections.defaultdict(list)
    for (e, s, ms) in M.peticiones:
        if s:
            por_ep[e].append(ms)
    return {
        "sesiones": n, "duracion_s": dur, "peticiones": n_req, "rps": round(n_req / dur, 1), "errores": err,
        "errores_pct": round(100 * err / max(1, n_req), 2), "errores_tipo": dict(M.errores), "rechazos_negocio": negocio,
        "p50": round(percentil(todos, 50)), "p95": round(percentil(todos, 95)), "p99": round(percentil(todos, 99)),
        "p95_decisivo": round(percentil(lecturas, 95)),
        "login_p50": round(percentil(logins, 50)), "login_p95": round(percentil(logins, 95)), "logins": len(logins), "sesiones_activas": M.logins_ok,
        "cpu_max_pct": round(max(M.cpu or [0])), "cpu_media_pct": round(sum(M.cpu) / max(1, len(M.cpu))), "mb_max": round(max(M.mb or [0])),
        "salud_p95": round(percentil(M.salud, 95)), "salud_max": round(max(M.salud or [0])),
        "cinco_xx": [list(x) for x in M.cinco_xx[:5]], "cinco_xx_total": len(M.cinco_xx),
        "endpoints": sorted(([e, len(v), round(percentil(v, 50)), round(percentil(v, 95)), round(percentil(v, 99))] for e, v in por_ep.items()),
                            key=lambda x: -x[3])[:12],
    }


async def muestrear(servidor, M, base, parar):
    import httpx
    ult_t, ult_ticks = time.monotonic(), cpu_y_memoria(servidor.pids())[0]
    async with httpx.AsyncClient(timeout=15.0) as cli:
        while not parar.is_set():
            await asyncio.sleep(2)
            t0 = time.perf_counter()
            try:
                await cli.get(base + "/health")
                M.salud.append((time.perf_counter() - t0) * 1000)
            except httpx.HTTPError:
                M.salud.append(15000.0)
            ticks, mb = cpu_y_memoria(servidor.pids())
            ahora = time.monotonic()
            M.cpu.append(100 * (ticks - ult_ticks) / os.sysconf("SC_CLK_TCK") / max(0.1, ahora - ult_t))
            M.mb.append(mb)
            ult_t, ult_ticks = ahora, ticks


async def correr_escalon(n, dur, cuentas_api, cuentas_nav, base, servidor, rng, httpx, navegador, hall, con_navegador, n_nav, registro):
    M = Metricas()
    fin = time.monotonic() + dur
    usadas = cuentas_api[:max(1, math.ceil(n * 0.8))]
    sesiones = [Sesion(usadas[i % len(usadas)], base, random.Random(rng.random()), httpx) for i in range(n)]   # las sobrantes repiten cuenta
    parar = asyncio.Event()
    muestreo = asyncio.create_task(muestrear(servidor, M, base, parar))
    rampa = min(dur / 3, max(8.0, n / 8))     # ~8 logins por segundo como mucho
    tareas = [asyncio.create_task(s.correr(M, fin, rng.random() * rampa)) for s in sesiones]
    if con_navegador and navegador is not None:
        perfiles = [{"dispositivo": "escritorio" if i % 2 == 0 else "celular", "tema": "dark" if (i // 2) % 2 == 0 else "light"} for i in range(n_nav)]
        for i, p in enumerate(perfiles):
            tareas.append(asyncio.create_task(sesion_navegador(navegador, cuentas_nav[i % len(cuentas_nav)], base, p, fin, hall, M)))
    resultados = await asyncio.gather(*tareas, return_exceptions=True)
    parar.set()
    await muestreo
    for s in sesiones:
        await s.cerrar()
    fallos_arnes = [repr(r)[:200] for r in resultados if isinstance(r, Exception)]
    r = resumen_escalon(n, dur, M, None)
    r["fallos_del_arnes"] = fallos_arnes
    r["navegadores"] = n_nav if con_navegador else 0
    r["errores_servidor_nuevos"] = registro.leer()
    return r


def escribir_informe(carpeta, etiqueta, args, escalones, limite, hall, violaciones, firmas, servidor, semb):
    L = [f"# Prueba de estrés — {etiqueta}", "",
         f"- Servidor lanzado {'**con** `--reload` (como el Dockerfile del SaaS)' if servidor.con_reload else '**sin** `--reload` (como el Dockerfile de Community)'}, un solo proceso de uvicorn.",
         f"- {semb['cuentas']} cuentas sembradas ({semb['reparto']}); escalones de {args.duracion} s; cliente y servidor comparten esta máquina ({os.cpu_count()} núcleos), "
         "así que los números son **relativos**: sirven para comparar y encontrar errores, no como capacidad exacta de tu servidor.",
         "- Los percentiles p50/p95/p99 **no incluyen el inicio de sesión** (bcrypt, unos 200 ms de CPU cada uno): va aparte en «Login». "
         "Las sesiones entran escalonadas, como mucho unas 8 por segundo. Los navegadores (Chromium) comparten los mismos núcleos que el servidor.",
         f"- Regla de paro: más de {int(LIMITE_ERRORES * 100)} % de errores, o un p95 > {LIMITE_P95_MS} ms en las peticiones normales (sin descargas pesadas ni inicio de sesión).", ""]
    L += ["## Resultado por escalón", "",
          "| Sesiones (activas) | Navegadores | Peticiones | req/s | Errores | % | Rechazos de negocio | p50 ms | p95 ms | p99 ms | Login p50 / p95 ms | CPU máx % | MB | /health p95 ms |",
          "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for e in escalones:
        L.append(f"| {e['sesiones']} ({e['sesiones_activas']}) | {e['navegadores']} | {e['peticiones']} | {e['rps']} | {e['errores']} | {e['errores_pct']} | {e['rechazos_negocio']} | "
                 f"{e['p50']} | {e['p95']} | {e['p99']} | {e['login_p50']} / {e['login_p95']} | {e['cpu_max_pct']} | {e['mb_max']} | {e['salud_p95']} |")
    L += ["", f"**Límite encontrado:** {limite}", ""]
    errores = [e for e in escalones if e["errores"] or e["cinco_xx_total"] or e["fallos_del_arnes"] or e["errores_servidor_nuevos"]]
    if errores:
        L += ["## Errores por escalón", ""]
        for e in errores:
            L.append(f"- **{e['sesiones']} sesiones**: {e['errores']} errores {e['errores_tipo']}, {e['cinco_xx_total']} respuestas 5xx, "
                     f"{e['errores_servidor_nuevos']} líneas de error en el log del servidor" + (f"; **fallos del arnés**: {e['fallos_del_arnes'][:2]}" if e["fallos_del_arnes"] else ""))
            for x in e["cinco_xx"][:3]:
                L.append(f"    - `{x[0]} {x[1]}` → {x[2]} `{x[3]}`")
        L.append("")
    peor = escalones[-1] if escalones else None
    if peor:
        L += [f"## Endpoints más lentos en el último escalón ({peor['sesiones']} sesiones)", "", "| Endpoint | n | p50 ms | p95 ms | p99 ms |", "|---|---:|---:|---:|---:|"]
        L += [f"| `{x[0]}` | {x[1]} | {x[2]} | {x[3]} | {x[4]} |" for x in peor["endpoints"]]
        L.append("")
    L += ["## Hallazgos de interfaz (navegador bajo carga)", ""]
    if hall.items:
        L += ["| Tipo | Dónde | Detalle | Veces | Captura |", "|---|---|---|---:|---|"]
        L += [f"| {h['tipo']} | {h['donde']} | {h['detalle'].replace('|', '/')} | {h['veces']} | {h['captura'] or ''} |" for h in hall.items.values()]
    else:
        L.append("Ninguno.")
    L += ["", "## Comprobaciones de las bases al terminar", ""]
    if violaciones:
        L += ["| Tipo | Dónde | Detalle |", "|---|---|---|"] + [f"| {v['tipo']} | {v['donde']} | {v['detalle'].replace('|', '/')} |" for v in violaciones[:60]]
        if len(violaciones) > 60:
            L.append(f"\n… y {len(violaciones) - 60} más (ver estres.json).")
    else:
        L.append("Integridad de todas las bases, saldos de metas, sobregiros y duplicados: **sin violaciones**.")
    L += ["", "## Errores del log del servidor", ""]
    L += [f"- ×{n}: `{f}`" for f, n in firmas.most_common(10)] or ["Ninguno."]
    L += ["", "## Cómo clasificar cada punto", "",
          "- **Del arnés** si lo explica cómo actúa el robot (tiempos de espera del propio cliente saturado, selectores, etc.): ver `fallos_del_arnes`.",
          "- **De la app** si una persona real llegaría al mismo resultado: repetir con `run_estres.sh --escalones <n>` y anotarlo como hallazgo."]
    open(os.path.join(carpeta, "informe_estres.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")


async def principal(args):
    import httpx
    from playwright.async_api import async_playwright
    carpeta = os.path.abspath(args.carpeta)
    os.makedirs(carpeta, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="finanzas_estres_")
    logs = os.path.join(tmp, "logs_servidor")
    os.makedirs(logs, exist_ok=True)
    shutil.copytree(args.fuente, tmp, dirs_exist_ok=True, ignore=escritorio.IGNORAR_EN_COPIA)
    # Sin salir a internet: el módulo de mercado de la COPIA devuelve cotizaciones fijas (también en el hijo que crea --reload)
    with open(os.path.join(tmp, "mercado.py"), "a", encoding="utf-8") as f:
        f.write('\n\n_COT_ESTRES = {"USDMXN=X": 19.5, "EURUSD=X": 1.08}\n'
                'consultar_precio_y_moneda_mercado = lambda t: (_COT_ESTRES.get(t.strip().upper()), "USD")\n')
    mods = escritorio._cargar_modulos(tmp)
    servidor = Servidor(args.fuente, tmp, logs)
    registro = RegistroServidor(logs)
    rng = random.Random(args.semilla)
    etiqueta = args.etiqueta or os.path.basename(os.path.abspath(args.fuente))
    print(f"[estrés] {etiqueta}: servidor {'con' if servidor.con_reload else 'sin'} --reload; sembrando {args.usuarios} cuentas…", flush=True)
    servidor.arrancar()
    cuentas = []
    try:
        total = sum(p for _n, p in REPARTO)
        nombres = [n for n, p in REPARTO for _ in range(max(1, round(p * args.usuarios / total)))]
        t0 = time.time()
        with httpx.Client(base_url=servidor.base, timeout=60) as cli:
            for i, persona in enumerate(nombres):
                usuario = f"es{i:03d}"
                res = personas.PERSONAS[persona](cli, usuario, mods)
                cuenta = Cuenta(usuario, persona, res[1])
                r = cli.post("/api/apartados", json={"nombre": "Meta de prueba", "tipo": "meta", "meta_monto": 50000}, headers=res[0])
                if r.status_code not in (200, 201, 400, 409, 422):
                    raise RuntimeError(f"no se pudo sembrar la meta de {usuario}: {r.status_code}")
                foto_inicial(cuenta)
                cuentas.append(cuenta)
        print(f"[estrés] {len(cuentas)} cuentas sembradas en {time.time() - t0:.0f} s", flush=True)
        n_nav = args.navegadores
        cuentas_nav = cuentas[-n_nav:] if n_nav else []
        for c in cuentas_nav:
            c.para_navegador = True
        cuentas_api = [c for c in cuentas if not c.para_navegador]
        rng.shuffle(cuentas_api)

        hall = Hallazgos(carpeta)
        escalones, limite = [], "no se alcanzó: el servidor aguantó todos los escalones"
        async with async_playwright() as pw:
            navegador = await pw.chromium.launch() if n_nav else None
            for n in args.escalones:
                con_nav = n in args.navegador_en
                print(f"[estrés] escalón de {n} sesiones{' + ' + str(n_nav) + ' navegadores' if con_nav and n_nav else ''}, {args.duracion} s…", flush=True)
                r = await correr_escalon(n, args.duracion, cuentas_api, cuentas_nav, servidor.base, servidor, rng, httpx, navegador, hall, con_nav, n_nav, registro)
                escalones.append(r)
                print(f"[estrés]   {r['peticiones']} peticiones, {r['rps']}/s, errores {r['errores']} ({r['errores_pct']} %), "
                      f"p95 {r['p95']} ms, CPU máx {r['cpu_max_pct']} %, 5xx {r['cinco_xx_total']}", flush=True)
                if r["errores_pct"] > LIMITE_ERRORES * 100 or r["p95_decisivo"] > LIMITE_P95_MS:
                    limite = (f"**{n} sesiones simultáneas**: {r['errores_pct']} % de errores y p95 de {r['p95_decisivo']} ms en peticiones normales "
                              f"(antes de este escalón la app respondió dentro de los límites)")
                    break
            if navegador is not None:
                await navegador.close()
    finally:
        servidor.parar()
    firmas = registro.firmas
    violaciones = comprobar_bases(cuentas, os.path.join(tmp, "data"))
    semb = {"cuentas": len(cuentas), "reparto": ", ".join(f"{n} {sum(1 for c in cuentas if c.persona == n)}" for n, _p in REPARTO)}
    escribir_informe(carpeta, etiqueta, args, escalones, limite, hall, violaciones, firmas, servidor, semb)
    json.dump({"etiqueta": etiqueta, "con_reload": servidor.con_reload, "escalones": escalones, "limite": limite, "hallazgos": list(hall.items.values()),
               "violaciones": violaciones, "errores_servidor": firmas.most_common(20)}, open(os.path.join(carpeta, "estres.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    mal = bool(violaciones) or any(e["cinco_xx_total"] or e["fallos_del_arnes"] for e in escalones) or bool(firmas)
    print(f"[estrés] informe en {carpeta}/informe_estres.md — hallazgos de interfaz: {len(hall.items)}, violaciones: {len(violaciones)}, "
          f"límite: {limite}", flush=True)
    return 1 if mal else 0


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--fuente", default=RAIZ, help="carpeta del código a probar (por defecto este repo)")
    p.add_argument("--carpeta", default=os.path.join(RAIZ, "auditoria", "corridas", "estres"))
    p.add_argument("--etiqueta", default="")
    p.add_argument("--escalones", default="10,25,50,100,200")
    p.add_argument("--duracion", type=int, default=180, help="segundos por escalón")
    p.add_argument("--usuarios", type=int, default=220)
    p.add_argument("--navegadores", type=int, default=6)
    p.add_argument("--navegador-en", default="50,100", help="escalones en los que corren los navegadores")
    p.add_argument("--semilla", type=int, default=1)
    p.add_argument("--humo", action="store_true", help="corrida corta de comprobación: escalón de 10, 40 s, 30 cuentas, 2 navegadores")
    args = p.parse_args()
    if args.humo:
        args.escalones, args.duracion, args.usuarios, args.navegadores, args.navegador_en = "10", 40, 30, 2, "10"
    args.escalones = [int(x) for x in str(args.escalones).split(",") if x]
    args.navegador_en = {int(x) for x in str(args.navegador_en).split(",") if x}
    sys.exit(asyncio.run(principal(args)))


if __name__ == "__main__":
    main()
