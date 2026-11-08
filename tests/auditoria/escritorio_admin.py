"""tests/auditoria/escritorio_admin.py - Capas B y C de Admin en el navegador.

Mismo recorrido que escritorio_metas.py (1280×800 o 390×844 táctil), pero entrando como el administrador real: crear usuarios
desde el cuadro (válidos, repetidos y con datos inválidos), suspender y reactivar desde el menú de cada fila, cambiar la
contraseña de otra persona, eliminar escribiendo su nombre, buscar y filtrar la tabla. Después de cada acción se compara
la tabla, los contadores y las cifras con la base maestra (leída directo, como en tests/auditoria/administracion.py) y se
comprueba por la API de login quién puede entrar y quién no.

Corre dentro del contenedor de Playwright: tests/auditoria/run.sh --seccion admin --capa escritorio|celular.
"""

import datetime
import os
import random
import re
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import administracion  # noqa: E402
import escritorio  # noqa: E402
from bitacora import Bitacora  # noqa: E402

HOY_INICIAL = datetime.date(2026, 10, 1)
PASOS_MIN, PASOS_MAX = 5, 30
PESOS_UI = (
    ("crear_usuario", 24), ("suspender", 12), ("reactivar", 8), ("editar_clave", 12), ("eliminar_usuario", 12),
    ("buscar", 8), ("filtrar", 8), ("propia_fila", 4), ("recargar", 4), ("avanzar_dia", 4),
)
IGNORAR_EN_COPIA = escritorio.IGNORAR_EN_COPIA
CLAVE_ADMIN = "AdminAuditoria#12345"


def _quitar_avisos(page):
    page.evaluate("document.querySelectorAll('.toast').forEach(t => t.remove())")


def _ir_admin(page):
    escritorio._ir(page, "tab-admin")
    _quitar_avisos(page)
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#tabla-usuarios-admin", state="attached", timeout=15000)
    # Siempre se parte de la vista completa: todos, sin búsqueda y por último acceso
    page.evaluate("document.querySelector('.admin-filtro').click()")
    page.fill("#admin-buscar", "")
    page.select_option("#admin-orden", value="acceso")


def _filas(page):
    """Filas de la tabla: (id, nombre, rol, estado)."""
    filas = []
    for f in page.query_selector_all("#tabla-usuarios-admin tr[data-usuario]"):
        celdas = [c.inner_text().strip() for c in f.query_selector_all("td")]
        filas.append((int(f.get_attribute("data-usuario")), f.query_selector(".admin-nombre").inner_text().strip(), celdas[1], celdas[2]))
    return filas


def _esperado(maestro, filtro="todos", texto=""):
    texto = texto.strip().lower()
    filas = []
    for id_, nombre, rol, activo, _db, _n in maestro.usuarios():
        if filtro == "activos" and not activo or filtro == "suspendidos" and activo:
            continue
        if texto and texto not in nombre.lower():
            continue
        filas.append((id_, nombre, rol, "Activo" if activo else "Suspendido"))
    return sorted(filas)


def _esperar_tabla(page, maestro, filtro="todos", texto="", segundos=6.0):
    limite = time.perf_counter() + segundos
    while True:
        vistas = sorted(_filas(page))
        if vistas == _esperado(maestro, filtro, texto) or time.perf_counter() > limite:
            return vistas
        page.wait_for_timeout(200)


def _entrar(cliente, usuario, clave):
    from security import _intentos_fallidos_login
    _intentos_fallidos_login.clear()
    return cliente.post("/auth/login", json={"username": usuario, "password": clave}).status_code


def _esperar_efecto(page, maestro, antes, segundos=6.0):
    """Espera a que cambie la base maestra o aparezca un aviso de error."""
    limite = time.perf_counter() + segundos
    while time.perf_counter() < limite:
        if maestro.usuarios() != antes:
            return True, ""
        if page.locator(".toast-error").count() > 0:
            texto = page.locator(".toast-error").first.inner_text().strip()[:160]
            page.wait_for_timeout(500)
            return maestro.usuarios() != antes, texto
        page.wait_for_timeout(150)
    return maestro.usuarios() != antes, ""


def _crear_usuario(page, rng, maestro, propios, cliente):
    escenario = rng.choices(("valido", "repetido", "nombre_corto", "nombre_raro", "clave_corta", "clave_larga"), weights=(60, 10, 6, 8, 8, 8))[0]
    pre = maestro.usuarios()
    existentes = [u[1] for u in pre if u[0] != 1]
    nombre, clave = f"usr{rng.randint(1, 30)}", f"{administracion.CLAVE_BASE}{rng.randint(100000, 999999)}"
    if escenario == "repetido" and existentes:
        nombre = rng.choice(existentes)
    elif escenario == "nombre_corto":
        nombre = "ab"
    elif escenario == "nombre_raro":
        nombre = rng.choice(("con espacio", "ñandú", "x" * 60, "a/b"))
    elif escenario == "clave_corta":
        clave = "abc1234"
    elif escenario == "clave_larga":
        clave = "A#1" + "x" * 80
    rol = "admin" if rng.random() < 0.1 else "user"
    # Los campos recortan lo que se escribe (50 y 72): lo que llega al servidor es lo recortado
    nombre, clave = nombre[:50], clave[:72]
    repetido = any(u[1] == nombre for u in pre)
    valido_efectivo = bool(re.fullmatch(r"[A-Za-z0-9_-]{3,50}", nombre)) and 8 <= len(clave) <= 72
    page.evaluate("document.querySelector('#tab-admin .admin-cabecera .btn-primary').click()")
    page.wait_for_selector("#modal-crear-usuario:not(.hidden)")
    page.fill("#nuevo-user-username", nombre)
    page.fill("#nuevo-user-password", clave)
    page.select_option("#nuevo-user-role", value=rol)
    if rol == "admin":  # crear otro administrador pide la contraseña de quien lo crea
        page.fill("#nuevo-user-admin-clave", CLAVE_ADMIN)
    page.evaluate("document.getElementById('form-crear-usuario').requestSubmit()")
    cambio, aviso = _esperar_efecto(page, maestro, pre)
    page.wait_for_load_state("networkidle")
    if page.is_visible("#modal-crear-usuario"):
        page.evaluate("document.querySelector('#modal-crear-usuario .modal-actions .btn-secondary').click()")
        page.wait_for_selector("#modal-crear-usuario.hidden", state="attached", timeout=15000)
    datos = {"entrada": {"username": nombre, "rol": rol, "escenario": escenario}, "aceptada": cambio, "mensaje": aviso,
             "pre": pre, "escenario": escenario, "nombre": nombre, "rol": rol, "repetido": repetido,
             "valido": valido_efectivo and not repetido}
    if cambio:
        nuevo = next((u for u in maestro.usuarios() if u[1] == nombre), None)
        if nuevo:
            propios[nuevo[0]] = {"username": nombre, "password": clave, "activo": True, "rol": rol}
            datos["login_nuevo"] = _entrar(cliente, nombre, clave)
    return datos


def _elegir(rng, propios):
    if not propios:
        return None
    return rng.choice(sorted(propios))


def _menu_fila(page, id_):
    page.locator(f"#tabla-usuarios-admin tr[data-usuario='{id_}'] .admin-accion-icono").click(timeout=10000)
    page.wait_for_selector(f"#menu-usuario-{id_}:not([hidden])", timeout=5000)


def _cambiar_estado(page, rng, maestro, propios, cliente, activo):
    # A otro administrador no se le ofrece suspender: primero se le quita el rol
    candidatos = {i: d for i, d in propios.items() if d["activo"] != activo and (activo or d["rol"] != "admin")}
    id_ = _elegir(rng, candidatos)
    if id_ is None:
        return None
    pre = maestro.usuarios()
    _menu_fila(page, id_)
    texto = "Activar acceso" if activo else "Suspender acceso"
    page.locator(f"#menu-usuario-{id_} .admin-menu-item", has_text=texto).click(timeout=10000)
    cambio, aviso = _esperar_efecto(page, maestro, pre)
    page.wait_for_load_state("networkidle")
    datos = propios[id_]
    if cambio:
        datos["activo"] = activo
    login = _entrar(cliente, datos["username"], datos["password"])
    return {"entrada": {"id": id_, "activar": activo}, "aceptada": cambio, "mensaje": aviso, "pre": pre, "objetivo": id_,
            "activo": activo, "login": login}


def _editar_clave(page, rng, maestro, propios, cliente):
    id_ = _elegir(rng, propios)
    if id_ is None:
        return None
    datos = propios[id_]
    nueva = f"{administracion.CLAVE_BASE}{rng.randint(100000, 999999)}"
    pre = maestro.usuarios()
    page.locator(f"#tabla-usuarios-admin tr[data-usuario='{id_}'] .admin-accion", has_text="Editar").click(timeout=10000)
    page.wait_for_selector("#modal-editar-usuario:not(.hidden)")
    page.fill("#edit-user-password", nueva)
    page.fill("#edit-user-current-password", CLAVE_ADMIN)  # el campo aparece al escribir la nueva
    page.evaluate("document.getElementById('form-editar-usuario').requestSubmit()")
    page.wait_for_selector("#modal-editar-usuario.hidden", state="attached", timeout=15000)
    page.wait_for_load_state("networkidle")
    res = {"entrada": {"id": id_}, "aceptada": True, "mensaje": "", "pre": pre, "objetivo": id_, "activo": datos["activo"]}
    res["login_nueva"] = _entrar(cliente, datos["username"], nueva)
    res["login_vieja"] = _entrar(cliente, datos["username"], datos["password"])
    datos["password"] = nueva
    return res


def _eliminar_usuario(page, rng, maestro, propios):
    id_ = _elegir(rng, {i: d for i, d in propios.items() if d["rol"] != "admin"})
    if id_ is None:
        return None
    pre = maestro.usuarios()
    db_name = next((u[4] for u in pre if u[0] == id_), None)
    archivos_pre = maestro.archivos()
    nombre = propios[id_]["username"]
    _menu_fila(page, id_)
    page.locator(f"#menu-usuario-{id_} .admin-menu-peligro").click(timeout=10000)
    page.wait_for_selector("#modal-eliminar-usuario:not(.hidden)", timeout=10000)
    # Con el nombre a medias el botón sigue apagado
    page.fill("#eliminar-usuario-confirmar", nombre[:-1])
    apagado = page.is_disabled("#btn-confirmar-eliminar")
    page.fill("#eliminar-usuario-confirmar", nombre)
    # Primero con una contraseña incorrecta: no se borra y el cuadro sigue abierto
    _quitar_avisos(page)
    page.fill("#eliminar-usuario-contrasena", "Incorrecta#123")
    page.evaluate("document.getElementById('btn-confirmar-eliminar').click()")
    page.wait_for_selector(".toast-error", timeout=10000)
    mala_borro = maestro.usuarios() != pre
    mala_cerro = not page.is_visible("#modal-eliminar-usuario")
    if not mala_cerro:
        page.fill("#eliminar-usuario-contrasena", CLAVE_ADMIN)
        page.evaluate("document.getElementById('btn-confirmar-eliminar').click()")
        page.wait_for_selector("#modal-eliminar-usuario.hidden", state="attached", timeout=15000)
    cambio, aviso = _esperar_efecto(page, maestro, pre)
    page.wait_for_load_state("networkidle")
    if cambio:
        propios.pop(id_, None)
    return {"entrada": {"id": id_}, "aceptada": cambio, "mensaje": aviso, "pre": pre, "objetivo": id_, "db_name": db_name,
            "archivos_pre": archivos_pre, "boton_apagado_a_medias": apagado, "mala_borro": mala_borro, "mala_cerro": mala_cerro}


def _buscar(page, rng, maestro):
    nombres = [u[1] for u in maestro.usuarios()]
    texto = rng.choice([n[:rng.randint(1, max(1, len(n)))] for n in nombres] + ["zzz", "ADM", "usr"])
    page.fill("#admin-buscar", texto)
    vistas = _esperar_tabla(page, maestro, "todos", texto)
    return {"entrada": {"texto": texto}, "aceptada": None, "mensaje": "", "vistas": vistas, "esperado": _esperado(maestro, "todos", texto)}


def _filtrar(page, rng, maestro):
    filtro = rng.choice(("todos", "activos", "suspendidos"))
    indice = ("todos", "activos", "suspendidos").index(filtro)
    page.evaluate(f"document.querySelectorAll('.admin-filtro')[{indice}].click()")
    vistas = _esperar_tabla(page, maestro, filtro)
    chips = [int(page.inner_text(i)) for i in ("#cnt-admin-todos", "#cnt-admin-activos", "#cnt-admin-suspendidos")]
    usuarios = maestro.usuarios()
    esperados = [len(usuarios), sum(1 for u in usuarios if u[3]), sum(1 for u in usuarios if not u[3])]
    return {"entrada": {"filtro": filtro}, "aceptada": None, "mensaje": "", "vistas": vistas, "esperado": _esperado(maestro, filtro),
            "chips": chips, "chips_esperados": esperados}


def _verificar_accion(nombre, datos, maestro):
    problemas = []
    pre = datos.get("pre")
    usuarios, archivos = maestro.usuarios(), maestro.archivos()
    aceptada = datos["aceptada"]
    if nombre == "crear_usuario":
        valido = datos["valido"]
        if aceptada and not valido:
            problemas.append(f"se creó un usuario con datos no válidos ({datos['escenario']}: «{datos['nombre'][:20]}»)")
        if not aceptada and valido:
            problemas.append(f"se rechazó un alta válida de «{datos['nombre']}»: {datos['mensaje']}")
        if aceptada:
            nuevo = next((u for u in usuarios if u[1] == datos["nombre"]), None)
            if len(usuarios) != len(pre) + 1 or nuevo is None or nuevo[2] != datos["rol"]:
                problemas.append("crear un usuario no dejó exactamente uno nuevo con su nombre y rol en la base maestra")
            elif nuevo[4] not in archivos:
                problemas.append(f"el usuario creado no tiene su archivo {nuevo[4]}")
            if datos.get("login_nuevo") != 200:
                problemas.append(f"el usuario creado en pantalla no puede entrar ({datos.get('login_nuevo')})")
        elif usuarios != pre:
            problemas.append("un alta rechazada cambió la base maestra")
    elif nombre in ("suspender", "reactivar"):
        fila = next((u for u in usuarios if u[0] == datos["objetivo"]), None)
        if not aceptada or fila is None or bool(fila[3]) != datos["activo"]:
            problemas.append(f"tras {nombre} desde el menú, el estado en la base maestra no es el pedido")
        if datos["activo"] and datos["login"] != 200:
            problemas.append(f"un usuario reactivado no puede entrar ({datos['login']})")
        if not datos["activo"] and datos["login"] == 200:
            problemas.append("un usuario suspendido pudo entrar")
    elif nombre == "editar_clave":
        if datos["activo"] and datos["login_nueva"] != 200:
            problemas.append(f"la contraseña nueva no entra ({datos['login_nueva']})")
        if datos["login_vieja"] == 200:
            problemas.append("la contraseña vieja sigue entrando")
    elif nombre == "eliminar_usuario":
        if not datos["boton_apagado_a_medias"]:
            problemas.append("con el nombre a medias el botón de eliminar estaba encendido")
        if datos["mala_borro"]:
            problemas.append("con una contraseña de administrador incorrecta se borró el usuario")
        if datos["mala_cerro"]:
            problemas.append("con una contraseña incorrecta el cuadro de eliminar se cerró")
        if not aceptada or any(u[0] == datos["objetivo"] for u in usuarios):
            problemas.append("el usuario eliminado sigue en la base maestra")
        if datos["db_name"] in archivos or len(archivos) != len(datos["archivos_pre"]) - 1:
            problemas.append("eliminar un usuario no quitó solo su archivo")
    elif nombre in ("buscar", "filtrar"):
        if datos["vistas"] != datos["esperado"]:
            problemas.append(f"la tabla muestra {[v[1] for v in datos['vistas']]} y la base maestra da {[v[1] for v in datos['esperado']]}")
        if nombre == "filtrar" and datos["chips"] != datos["chips_esperados"]:
            problemas.append(f"los contadores dicen {datos['chips']} y la base maestra {datos['chips_esperados']}")
    return problemas


def _verificar_pantalla(page, maestro, completa=True):
    """Tabla (sin filtros) y cifras contra la base maestra."""
    _ir_admin(page)
    problemas = []
    vistas = _esperar_tabla(page, maestro)
    if vistas != _esperado(maestro):
        problemas.append(f"la tabla muestra {[v[1:] for v in vistas]} y la base maestra {[v[1:] for v in _esperado(maestro)]}")
    usuarios = maestro.usuarios()
    for etiqueta, id_elemento, esperado in (("usuarios", "#lbl-kpi-total-users", len(usuarios)),
                                            ("suspendidos", "#lbl-kpi-inactive", sum(1 for u in usuarios if not u[3]))):
        for _ in range(20):
            visto = page.inner_text(id_elemento).strip()
            if visto == str(esperado):
                break
            page.wait_for_timeout(200)
        else:
            problemas.append(f"la cifra de {etiqueta} dice {visto} y la base maestra {esperado}")
    # Otros administradores: ni eliminar ni (si están activos) suspender en su menú
    for id_, _n, rol, activo, _db, _c in usuarios:
        if id_ == 1 or rol != "admin":
            continue
        menu = f"#menu-usuario-{id_}"
        if page.query_selector(f"{menu} .admin-menu-peligro") or (activo and page.query_selector(f"{menu} .admin-menu-item")):
            problemas.append(f"la fila del administrador #{id_} ofrece suspender o eliminar")
    # Su propia fila no tiene menú de acciones: su cuenta se gestiona desde Mi perfil
    if page.query_selector("#tabla-usuarios-admin tr[data-usuario='1'] .admin-menu-wrap"):
        problemas.append("la cuenta principal tiene menú de suspender o eliminar en su propia fila")
    return problemas


def _episodio(semilla, numero, navegador, servidor, mods, bit, fallos, dispositivo):
    rng = random.Random(semilla)
    usuario = "admin"
    hoy = HOY_INICIAL
    os.environ["FINANZAS_HOY"] = f"{hoy.isoformat()}T10:00:00"  # las reglas leen el día simulado de aquí
    servidor.arrancar(hoy)
    import httpx
    cliente = httpx.Client(base_url=servidor.base, timeout=30)
    ruta_db = mods.database.get_tenant_path("user_1.db")
    maestro = administracion.Maestro(ruta_db)
    propios = {}

    ctx = navegador.new_context(**escritorio.DISPOSITIVOS[dispositivo])
    page = ctx.new_page()
    errores, respuestas_5xx = [], []
    page.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
    page.on("console", lambda m: errores.append(f"console: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.on("response", lambda r: respuestas_5xx.append(f"{r.status} {r.url.split('?')[0]}") if r.status >= 500 else None)
    escritorio._login(page, servidor.base, "admin", CLAVE_ADMIN)

    pasos = rng.randint(PASOS_MIN, PASOS_MAX)
    for paso in range(1, pasos + 1):
        nombre = rng.choices([n for n, _ in PESOS_UI], weights=[p for _, p in PESOS_UI])[0]
        fallo_accion = None
        errores.clear()
        respuestas_5xx.clear()
        inicio = time.perf_counter()
        datos = None
        try:
            if nombre == "avanzar_dia":
                hoy = hoy + datetime.timedelta(days=rng.choice((1, 1, 2, 7, 15)))
                os.environ["FINANZAS_HOY"] = f"{hoy.isoformat()}T10:00:00"
                servidor.parar()
                servidor.arrancar(hoy)
                page.goto(servidor.base + "/")
                if not page.query_selector("#vista-app:not(.hidden)"):
                    escritorio._login(page, servidor.base, usuario)
                datos = {"entrada": {"hoy": hoy.isoformat()}, "aceptada": None, "mensaje": ""}
            elif nombre == "recargar":
                page.reload()
                page.wait_for_selector("#vista-app:not(.hidden)", timeout=20000)
                datos = {"entrada": {}, "aceptada": None, "mensaje": ""}
            elif nombre == "dashboard":
                escritorio._ir(page, "tab-dashboard")
                datos = {"entrada": {}, "aceptada": None, "mensaje": ""}
            elif nombre == "crear_usuario":
                _ir_admin(page)
                datos = _crear_usuario(page, rng, maestro, propios, cliente)
            elif nombre in ("suspender", "reactivar"):
                _ir_admin(page)
                datos = _cambiar_estado(page, rng, maestro, propios, cliente, nombre == "reactivar")
            elif nombre == "editar_clave":
                _ir_admin(page)
                datos = _editar_clave(page, rng, maestro, propios, cliente)
            elif nombre == "eliminar_usuario":
                _ir_admin(page)
                datos = _eliminar_usuario(page, rng, maestro, propios)
            elif nombre == "buscar":
                _ir_admin(page)
                datos = _buscar(page, rng, maestro)
            elif nombre == "filtrar":
                _ir_admin(page)
                datos = _filtrar(page, rng, maestro)
            elif nombre == "propia_fila":
                datos = {"entrada": {}, "aceptada": None, "mensaje": ""}
        except Exception as error:
            fallo_accion = escritorio._primera_linea(error)
            datos = {"entrada": {}, "aceptada": None, "mensaje": ""}
        duracion_ms = round((time.perf_counter() - inicio) * 1000)

        nota = None
        if datos is None:
            nota = "omitida: no había usuarios creados que tocar"
            datos = {"entrada": {}, "aceptada": None, "mensaje": ""}
        problemas = []
        if fallo_accion:
            problemas.append(f"la acción no se pudo completar en pantalla: {fallo_accion}")
        else:
            if errores and nombre not in ("recargar", "avanzar_dia"):
                problemas.append(f"errores de JavaScript: {errores[0][:160]}")
            elif errores:
                errores = [e for e in errores if "Failed to fetch" not in e]
                if errores:
                    problemas.append(f"errores de JavaScript: {errores[0][:160]}")
            if respuestas_5xx:
                problemas.append(f"respuesta del servidor {respuestas_5xx[0]}")
            if nota is None and nombre in ("crear_usuario", "suspender", "reactivar", "editar_clave", "eliminar_usuario", "buscar", "filtrar"):
                problemas += _verificar_accion(nombre, datos, maestro)
            try:
                problemas += _verificar_pantalla(page, maestro)
            except Exception as error:
                problemas.append(f"la pantalla de administración no se pudo leer: {escritorio._primera_linea(error)}")

        registro = {
            "episodio": numero, "semilla": semilla, "persona": usuario, "paso": paso,
            "fecha_simulada": hoy.isoformat(), "accion": nombre, "entrada": datos.get("entrada", {}),
            "aceptada": datos.get("aceptada"), "mensaje": datos.get("mensaje") or None,
            "usuarios_base": len(maestro.usuarios()),
            "invariantes_rotas": problemas, "nota": nota,
        }
        bit.paso(registro, {"episodio": numero, "paso": paso, "duracion_ms": duracion_ms,
                            "carga_1m": round(os.getloadavg()[0], 2)})
        if problemas:
            fallos.append((numero, paso, nombre, problemas))
            carpeta = bit.fallo(f"{numero:03d}-{paso:02d}-{semilla}", {**registro, "errores_navegador": errores,
                                                                       "respuestas_5xx": respuestas_5xx}, ruta_db)
            try:
                page.screenshot(path=os.path.join(carpeta, "pantalla.png"), full_page=True)
            except Exception:
                pass

    ctx.close()
    cliente.close()
    servidor.parar()
    return pasos


def correr(semilla, episodios, fuente, carpeta, dispositivo):
    from playwright.sync_api import sync_playwright

    shutil.rmtree(carpeta, ignore_errors=True)
    os.makedirs(os.path.join(carpeta, "logs"), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="auditoria_admin_ui_")
    shutil.copytree(fuente, tmp, dirs_exist_ok=True, ignore=IGNORAR_EN_COPIA)
    os.chdir(tmp)
    os.environ["JWT_SECRET_KEY"] = "k" * 48
    mods = escritorio._cargar_modulos(tmp)

    servidor = escritorio.Servidor(tmp, os.path.join(carpeta, "logs"))
    bit = Bitacora(carpeta)
    fallos, pasos_totales = [], 0
    try:
        with sync_playwright() as pw:
            navegador = pw.chromium.launch(args=["--no-sandbox"])
            try:
                for numero in range(1, episodios + 1):
                    pasos_totales += _episodio(semilla + numero - 1, numero, navegador, servidor, mods, bit, fallos, dispositivo)
            finally:
                navegador.close()
    finally:
        servidor.parar()
        bit.cerrar()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"Corrida admin {dispositivo} s{semilla} n{episodios}: {episodios} episodios, {pasos_totales} acciones, carpeta {carpeta}")
    if fallos:
        print(f"FALLO: {len(fallos)} acciones con invariantes rotas")
        for numero, paso, nombre, problemas in fallos[:20]:
            print(f"  episodio {numero} paso {paso} ({nombre}): {'; '.join(problemas)}")
        return 1
    print("OK: todas las invariantes se cumplieron")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Capas B y C de Admin (navegador).")
    parser.add_argument("--semilla", type=int, default=1)
    parser.add_argument("--episodios", type=int, default=100)
    parser.add_argument("--fuente", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    parser.add_argument("--carpeta", required=True)
    parser.add_argument("--dispositivo", choices=sorted(escritorio.DISPOSITIVOS), default="escritorio")
    args = parser.parse_args()
    sys.exit(correr(args.semilla, args.episodios, os.path.abspath(args.fuente), os.path.abspath(args.carpeta), args.dispositivo))
