"""tests/auditoria/escritorio_login.py - Capas B y C de Login, sesión y Mi perfil en el navegador.

Mismo recorrido que los demás drivers (1280×800 o 390×844 táctil), en la puerta de la app: entrar bien y mal (también con
un usuario que no existe), el bloqueo tras cinco fallos, cerrar sesión, recargar con la sesión abierta, «olvidé mi
contraseña», cambiar la contraseña y el correo desde Mi perfil, enviar comentarios y eliminar la propia cuenta. Lo que
pasa se juzga contra la base maestra (leída directo) y con entradas por la API: quién puede entrar, con qué contraseña,
qué correo quedó guardado y cuántas entradas se contaron.

El control de intentos vive en la memoria del servidor; para que el arnés no se bloquee solo, el servidor se reinicia
(como si pasara el tiempo de la ventana) antes de una acción que pueda pasar los topes.

Corre dentro del contenedor de Playwright: tests/auditoria/run.sh --seccion login --capa escritorio|celular.
"""

import datetime
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import acceso  # noqa: E402
import administracion  # noqa: E402
import escritorio  # noqa: E402
import personas  # noqa: E402
from bitacora import Bitacora  # noqa: E402

HOY_INICIAL = datetime.date(2026, 10, 1)
PASOS_MIN, PASOS_MAX = 5, 30
PESOS_UI = (
    ("entrar_bien", 16), ("entrar_mal", 14), ("bloqueo", 5), ("cerrar_sesion", 8), ("recargar", 6), ("olvide", 6),
    ("perfil_clave", 14), ("perfil_correo", 12), ("comentar", 8), ("eliminar_cuenta", 3), ("avanzar_dia", 4),
)
IGNORAR_EN_COPIA = escritorio.IGNORAR_EN_COPIA
TOPE_CUENTA, TOPE_IP = 4, 16  # por debajo de los topes reales (5 por cuenta y 20 por IP)


class Estado:
    def __init__(self, usuario, clave, user_id):
        self.usuario, self.clave, self.id = usuario, clave, user_id
        self.fallos_cuenta = self.fallos_ip = self.fallos_perfil = 0
        self.comentarios = 0
        self.eliminada = False


def _maestro(ruta):
    con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _fila(maestro, user_id):
    con = _maestro(maestro.ruta)
    try:
        r = con.execute("SELECT id, username, email, login_count, password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def _comentarios(maestro, user_id):
    con = _maestro(maestro.ruta)
    try:
        return con.execute("SELECT COUNT(*) FROM comentarios WHERE user_id = ?", (user_id,)).fetchone()[0]
    finally:
        con.close()


def _entrar_api(cliente, usuario, clave):
    return cliente.post("/auth/login", json={"username": usuario, "password": clave}).status_code


def _quitar_avisos(page):
    page.evaluate("document.querySelectorAll('.toast').forEach(t => t.remove())")


def _asentar(page, segundos=5.0):
    """Espera a que la página termine de cargar, con tope: si hay una petición que no acaba, la acción ya hecha no se pierde
    (un timeout aquí, después de guardar, dejaba al arnés con la contraseña vieja y rompía el resto del episodio)."""
    try:
        page.wait_for_load_state("networkidle", timeout=segundos * 1000)
    except Exception:
        pass


def _leer_aviso(page, segundos=15.0):
    """(texto, clase) del primer aviso, leído en una sola llamada: un aviso dura 3.5 s y, si se esperara con un paso y
    se leyera con otro, podría irse entre los dos y la lectura se quedaría esperando 30 s a que vuelva."""
    limite = time.perf_counter() + segundos
    while time.perf_counter() < limite:
        visto = page.evaluate("(() => { const t = document.querySelector('.toast'); "
                              "return t ? {texto: t.innerText.trim(), clase: t.className} : null; })()")
        if visto:
            return visto["texto"][:160], visto["clase"]
        page.wait_for_timeout(40)
    raise TimeoutError("no apareció ningún aviso")


def _app_tras_recargar(page, segundos=10.0):
    """Tras recargar con sesión, el login se ve un instante hasta que /auth/me responde: se espera a la app y solo si no
    llega se concluye que pidió entrar de nuevo."""
    try:
        page.wait_for_selector("#vista-app:not(.hidden)", timeout=segundos * 1000)
        return True
    except Exception:
        return False


def _en_app(page):
    return page.query_selector("#vista-app:not(.hidden)") is not None


def _a_login(page, base):
    """Deja la pantalla en el login: si hay sesión, la cierra desde el menú como una persona."""
    if _en_app(page):
        page.evaluate("document.querySelector('.dropdown-btn-logout').click()")
        page.wait_for_selector("#modal-confirmar:not(.hidden)", timeout=10000)
        page.evaluate("document.getElementById('btn-confirmar-aceptar').click()")
    escritorio._ver_login(page, base)


def _a_app(page, base, est):
    if not _en_app(page):
        escritorio._login(page, base, est.usuario, est.clave, en_app=False)


def _escribir_login(page, usuario, clave):
    escritorio._esperar_foco(page, "login-username")
    page.fill("#login-username", usuario)
    page.fill("#login-password", clave)
    page.evaluate("document.getElementById('form-login').requestSubmit()")


def _esperar_resultado_login(page, segundos=15.0):
    """'app' si entró, o el texto del error que muestra el login."""
    limite = time.perf_counter() + segundos
    while time.perf_counter() < limite:
        if _en_app(page):
            return "app"
        error = page.query_selector("#login-error:not(.hidden)")
        if error and error.inner_text().strip() and not page.is_disabled("#form-login [type=submit]"):
            return error.inner_text().strip()
        page.wait_for_timeout(150)
    return "sin respuesta"


def _reiniciar(servidor, hoy, page, est):
    """El servidor nuevo empieza sin intentos fallidos guardados (equivale a dejar pasar la ventana)."""
    servidor.parar()
    servidor.arrancar(hoy)
    est.fallos_cuenta = est.fallos_ip = est.fallos_perfil = 0
    page.goto(servidor.base + escritorio.RUTA_APP)
    page.wait_for_selector("#vista-app:not(.hidden), #login-username:visible", timeout=60000)


def _entrar_bien(page, base, maestro, est):
    _a_login(page, base)
    antes = _fila(maestro, est.id)["login_count"]
    _escribir_login(page, est.usuario, est.clave)
    resultado = _esperar_resultado_login(page)
    despues = _fila(maestro, est.id)["login_count"]
    problemas = []
    if resultado != "app":
        problemas.append(f"con la contraseña correcta no entró: «{resultado}»")
    elif despues != antes + 1:
        problemas.append(f"entrar sumó {despues - antes} entradas y debía sumar 1")
    if resultado == "app":
        est.fallos_cuenta = 0  # un acierto limpia los fallos de esa cuenta
    return {"entrada": {}, "aceptada": resultado == "app", "mensaje": "" if resultado == "app" else resultado}, problemas


def _entrar_mal(page, base, maestro, est, rng):
    _a_login(page, base)
    antes = _fila(maestro, est.id)["login_count"]
    _escribir_login(page, est.usuario, est.clave + "x")
    mala = _esperar_resultado_login(page)
    page.wait_for_timeout(300)
    _escribir_login(page, f"nadie{rng.randint(1, 10**6)}", "Cualquiera#123456")
    fantasma = _esperar_resultado_login(page)
    est.fallos_cuenta += 1
    est.fallos_ip += 2
    problemas = []
    if mala == "app" or fantasma == "app":
        problemas.append("se entró con una contraseña equivocada o con un usuario que no existe")
    if mala != acceso.MENSAJE_USUARIO_MALO or fantasma != acceso.MENSAJE_USUARIO_MALO:
        problemas.append(f"los errores no son idénticos al mensaje genérico: «{mala}» y «{fantasma}» (se sabría quién existe)")
    if _fila(maestro, est.id)["login_count"] != antes:
        problemas.append("un acceso fallido sumó una entrada")
    return {"entrada": {}, "aceptada": False, "mensaje": mala}, problemas


def _bloqueo(page, base, maestro, est):
    _a_login(page, base)
    for _ in range(5):
        _escribir_login(page, est.usuario, est.clave + "x")
        _esperar_resultado_login(page)
        page.wait_for_timeout(200)
    est.fallos_cuenta += 5
    est.fallos_ip += 5
    _escribir_login(page, est.usuario, est.clave)
    resultado = _esperar_resultado_login(page)
    problemas = []
    if resultado == "app":
        problemas.append("tras cinco fallos, la contraseña correcta entró sin esperar")
    elif resultado == acceso.MENSAJE_USUARIO_MALO:
        problemas.append("tras cinco fallos, el aviso no dice que la cuenta está bloqueada por un rato")
    return {"entrada": {}, "aceptada": resultado == "app", "mensaje": resultado}, problemas


def _cerrar_sesion(page, base, est):
    _a_app(page, base, est)
    _a_login(page, base)
    token = page.evaluate("localStorage.getItem('jwt_token')")
    page.goto(base + escritorio.RUTA_APP)
    _asentar(page)  # sin token, el login se queda; con uno, la app tardaría en aparecer
    page.wait_for_timeout(500)
    problemas = []
    if token:
        problemas.append("al cerrar sesión el token se quedó guardado en el navegador")
    if _en_app(page):
        problemas.append("tras cerrar sesión, abrir la app entró sin pedir contraseña")
    return {"entrada": {}, "aceptada": None, "mensaje": ""}, problemas


def _recargar(page, base, est):
    _a_app(page, base, est)
    page.reload()
    problemas = [] if _app_tras_recargar(page) else ["recargar con la sesión abierta pidió entrar de nuevo"]
    return {"entrada": {}, "aceptada": None, "mensaje": ""}, problemas


def _olvide(page, base, est, rng):
    """Sin correo configurado, la app debe decirlo igual para una cuenta que existe y para una que no."""
    _a_login(page, base)
    vistos = []
    for ident in (est.usuario, f"nadie{rng.randint(1, 10**6)}"):
        page.evaluate("document.querySelector('.link-forgot-password').click()")
        page.wait_for_selector("#modal-recuperar-password:not(.hidden)", timeout=10000)
        page.evaluate("document.getElementById('form-solicitar-codigo').classList.remove('hidden');"
                      "document.getElementById('alerta-email-no-configurado').classList.add('hidden')")
        page.fill("#recuperar-identifier", ident)
        page.evaluate("document.getElementById('form-solicitar-codigo').requestSubmit()")
        # Se espera la respuesta (el aviso, otro paso o un error); networkidle puede llegar antes de que salga la petición
        try:
            page.wait_for_selector("#alerta-email-no-configurado:visible, #form-restablecer-password:visible, .toast-error", timeout=10000)
        except Exception:
            pass
        vistos.append(page.is_visible("#alerta-email-no-configurado"))
        page.evaluate("cerrarModalRecuperarPassword()")
    problemas = []
    if vistos[0] != vistos[1]:
        problemas.append("«olvidé mi contraseña» responde distinto si la cuenta existe: se puede saber quién existe")
    elif not vistos[0]:
        problemas.append("sin correo configurado, «olvidé mi contraseña» no avisa que no se puede recuperar así")
    return {"entrada": {}, "aceptada": None, "mensaje": ""}, problemas


def _abrir_perfil(page):
    page.evaluate("document.querySelector('[data-on-click*=\"abrirModalEditarMiPerfil\"]').click()")
    page.wait_for_selector("#modal-editar-usuario:not(.hidden)", timeout=10000)
    escritorio._esperar_foco(page, "edit-user-username")


def _guardar_perfil(page):
    _quitar_avisos(page)
    page.evaluate("document.getElementById('form-editar-usuario').requestSubmit()")
    texto, clase = _leer_aviso(page)
    _asentar(page)
    if page.is_visible("#modal-editar-usuario"):
        page.evaluate("cerrarModalEditarUsuario()")
    return texto, "toast-error" not in clase


def _perfil_clave(page, base, maestro, est, rng, cliente):
    _a_app(page, base, est)
    variante = rng.choice(("sin_actual", "mala", "ok", "ok"))
    nueva = f"{administracion.CLAVE_BASE}{rng.randint(100000, 999999)}"
    antes = _fila(maestro, est.id)["password_hash"]
    _abrir_perfil(page)
    page.fill("#edit-user-password", nueva)
    campo_visible = page.is_visible("#edit-user-current-password")
    if variante != "sin_actual":
        page.fill("#edit-user-current-password", est.clave if variante == "ok" else est.clave + "x")
    texto, ok = _guardar_perfil(page)
    problemas = []
    if not campo_visible:
        problemas.append("al escribir una contraseña nueva no apareció el campo de la actual")
    cambio = _fila(maestro, est.id)["password_hash"] != antes
    if variante == "ok":
        if not ok or not cambio:
            problemas.append(f"con la contraseña actual correcta no se cambió: «{texto}»")
        else:
            vieja, est.clave = est.clave, nueva
            if _entrar_api(cliente, est.usuario, nueva) != 200:
                problemas.append("la contraseña nueva no entra")
            if _entrar_api(cliente, est.usuario, vieja) == 200:
                problemas.append("la contraseña vieja sigue entrando")
            est.fallos_cuenta += 1
            est.fallos_ip += 1
            # El cambio invalida la sesión de la pantalla y devuelve un token nuevo: la pantalla sigue dentro
            page.reload()
            if not _app_tras_recargar(page):
                problemas.append("tras cambiar la contraseña, la pantalla perdió la sesión")
    else:
        if variante == "mala":
            est.fallos_perfil += 1
        if ok or cambio:
            problemas.append(f"la contraseña cambió sin la actual correcta ({variante})")
    return {"entrada": {"variante": variante}, "aceptada": ok, "mensaje": texto}, problemas


def _perfil_correo(page, base, maestro, est, rng):
    _a_app(page, base, est)
    variante = rng.choice(("sin_actual", "ok"))
    nuevo = f"{est.usuario}{rng.randint(1, 10**6)}@prueba.local"
    antes = _fila(maestro, est.id)["email"]
    _abrir_perfil(page)
    page.fill("#edit-user-email", nuevo)
    campo_visible = page.is_visible("#edit-user-current-password")
    if variante == "ok":
        page.fill("#edit-user-current-password", est.clave)
    texto, ok = _guardar_perfil(page)
    ahora = _fila(maestro, est.id)["email"]
    problemas = []
    if not campo_visible:
        problemas.append("al cambiar el correo no apareció el campo de la contraseña actual")
    if variante == "ok" and (not ok or ahora != nuevo):
        problemas.append(f"con la contraseña actual el correo no cambió: «{texto}»")
    if variante == "sin_actual" and (ok or ahora != antes):
        problemas.append("el correo cambió sin la contraseña actual (con él se recupera la cuenta)")
    return {"entrada": {"variante": variante}, "aceptada": ok, "mensaje": texto}, problemas


def _comentar(page, base, maestro, est, rng):
    if est.comentarios >= 4:  # el tope es de 5 por hora por persona: se queda por debajo
        return None, []
    _a_app(page, base, est)
    antes = _comentarios(maestro, est.id)
    page.evaluate("document.querySelector('.btn-comentarios').click()")
    page.wait_for_selector("#modal-comentarios:not(.hidden)", timeout=10000)
    page.select_option("#comentario-tipo", index=rng.randint(0, 2))
    page.fill("#comentario-mensaje", f"Comentario de prueba {rng.randint(1, 10**6)}")
    _quitar_avisos(page)
    page.evaluate("document.getElementById('form-comentarios').requestSubmit()")
    _, clase = _leer_aviso(page)
    ok = "toast-error" not in clase
    _asentar(page)
    est.comentarios += 1
    problemas = []
    if not ok or _comentarios(maestro, est.id) != antes + 1:
        problemas.append("el comentario no quedó guardado una vez")
    if page.is_visible("#modal-comentarios"):
        problemas.append("tras enviar el comentario el cuadro siguió abierto")
        page.evaluate("cerrarModalComentarios()")
    return {"entrada": {}, "aceptada": ok, "mensaje": ""}, problemas


def _eliminar_cuenta(page, base, maestro, est, cliente):
    _a_app(page, base, est)
    _abrir_perfil(page)
    page.evaluate("document.getElementById('danger-zone-eliminar-cuenta').open = true")
    page.fill("#eliminar-cuenta-confirmar", est.usuario)
    page.fill("#eliminar-cuenta-contrasena", est.clave + "x")
    _quitar_avisos(page)
    page.evaluate("document.getElementById('btn-eliminar-cuenta-confirmar').click()")
    page.wait_for_selector(".toast-error", timeout=15000)
    problemas = []
    if _fila(maestro, est.id) is None:
        problemas.append("con una contraseña incorrecta se eliminó la cuenta")
        est.eliminada = True
        return {"entrada": {}, "aceptada": True, "mensaje": ""}, problemas
    page.fill("#eliminar-cuenta-contrasena", est.clave)
    page.evaluate("document.getElementById('btn-eliminar-cuenta-confirmar').click()")
    page.wait_for_selector("#login-username:visible", timeout=30000)
    if _fila(maestro, est.id) is not None:
        problemas.append("con la contraseña correcta la cuenta no se eliminó")
    if _entrar_api(cliente, est.usuario, est.clave) == 200:
        problemas.append("una cuenta eliminada pudo entrar")
    est.eliminada = True
    return {"entrada": {}, "aceptada": True, "mensaje": ""}, problemas


def _episodio(semilla, numero, navegador, servidor, mods, bit, fallos, dispositivo):
    rng = random.Random(semilla)
    usuario = f"aud{semilla}"
    hoy = HOY_INICIAL
    os.environ["FINANZAS_HOY"] = f"{hoy.isoformat()}T10:00:00"
    servidor.arrancar(hoy)
    import httpx
    cliente = httpx.Client(base_url=servidor.base, timeout=30)
    entrada = mods.master_db.create_user_entry(usuario, mods.security.hash_password(personas.CLAVE), email=f"{usuario}@test.local")
    ruta_db = mods.database.get_tenant_path(entrada["db_name"])
    maestro = administracion.Maestro(ruta_db)
    est = Estado(usuario, personas.CLAVE, entrada["id"])

    ctx = navegador.new_context(**escritorio.DISPOSITIVOS[dispositivo])
    page = ctx.new_page()
    errores, respuestas_5xx = [], []
    page.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
    page.on("console", lambda m: errores.append(f"console: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.on("response", lambda r: respuestas_5xx.append(f"{r.status} {r.url.split('?')[0]}") if r.status >= 500 and r.status != 503 else None)
    escritorio._login(page, servidor.base, usuario, en_app=False)

    pasos = rng.randint(PASOS_MIN, PASOS_MAX)
    hechos = 0
    for paso in range(1, pasos + 1):
        if est.eliminada:
            break
        nombre = rng.choices([n for n, _ in PESOS_UI], weights=[p for _, p in PESOS_UI])[0]
        fallo_accion = None
        errores.clear()
        respuestas_5xx.clear()
        inicio = time.perf_counter()
        datos, problemas, nota = None, [], None
        try:
            # Antes de una acción que suma fallos, el servidor se reinicia si se acercaría a algún tope
            suma = {"entrar_mal": (1, 2), "bloqueo": (5, 5), "perfil_clave": (1, 1)}.get(nombre)
            if suma:
                if nombre == "bloqueo":  # el bloqueo se prueba desde cero fallos: tienen que ser exactamente cinco
                    reiniciar = est.fallos_cuenta > 0 or est.fallos_ip + 5 > TOPE_IP
                else:
                    reiniciar = (est.fallos_cuenta + suma[0] > TOPE_CUENTA or est.fallos_ip + suma[1] > TOPE_IP
                                 or est.fallos_perfil >= TOPE_CUENTA)
            if suma and reiniciar:
                _reiniciar(servidor, hoy, page, est)
            if nombre == "avanzar_dia":
                hoy = hoy + datetime.timedelta(days=rng.choice((1, 1, 2, 7, 15)))
                os.environ["FINANZAS_HOY"] = f"{hoy.isoformat()}T10:00:00"
                _reiniciar(servidor, hoy, page, est)
                datos = {"entrada": {"hoy": hoy.isoformat()}, "aceptada": None, "mensaje": ""}
            elif nombre == "entrar_bien":
                datos, problemas = _entrar_bien(page, servidor.base, maestro, est)
            elif nombre == "entrar_mal":
                datos, problemas = _entrar_mal(page, servidor.base, maestro, est, rng)
            elif nombre == "bloqueo":
                datos, problemas = _bloqueo(page, servidor.base, maestro, est)
                _reiniciar(servidor, hoy, page, est)  # deja pasar la ventana del bloqueo
            elif nombre == "cerrar_sesion":
                datos, problemas = _cerrar_sesion(page, servidor.base, est)
            elif nombre == "recargar":
                datos, problemas = _recargar(page, servidor.base, est)
            elif nombre == "olvide":
                datos, problemas = _olvide(page, servidor.base, est, rng)
            elif nombre == "perfil_clave":
                datos, problemas = _perfil_clave(page, servidor.base, maestro, est, rng, cliente)
            elif nombre == "perfil_correo":
                datos, problemas = _perfil_correo(page, servidor.base, maestro, est, rng)
            elif nombre == "comentar":
                datos, problemas = _comentar(page, servidor.base, maestro, est, rng)
            elif nombre == "eliminar_cuenta":
                datos, problemas = _eliminar_cuenta(page, servidor.base, maestro, est, cliente)
        except Exception as error:
            fallo_accion = escritorio._primera_linea(error)
        duracion_ms = round((time.perf_counter() - inicio) * 1000)
        hechos += 1

        if datos is None and not fallo_accion:
            nota = "omitida: ya se enviaron los comentarios de prueba de este episodio"
        datos = datos or {"entrada": {}, "aceptada": None, "mensaje": ""}
        if fallo_accion:
            problemas = [f"la acción no se pudo completar en pantalla: {fallo_accion}"]
        else:
            errores_reales = [e for e in errores if "Failed to fetch" not in e and "401" not in e]
            if errores_reales and nombre not in ("avanzar_dia", "bloqueo"):
                problemas.append(f"errores de JavaScript: {errores_reales[0][:160]}")
            if respuestas_5xx:
                problemas.append(f"respuesta del servidor {respuestas_5xx[0]}")

        fila = _fila(maestro, est.id)
        registro = {
            "episodio": numero, "semilla": semilla, "persona": usuario, "paso": paso,
            "fecha_simulada": hoy.isoformat(), "accion": nombre, "entrada": datos.get("entrada", {}),
            "aceptada": datos.get("aceptada"), "mensaje": datos.get("mensaje") or None,
            "entradas_base": fila["login_count"] if fila else None, "cuenta_existe": fila is not None,
            "invariantes_rotas": problemas, "nota": nota,
        }
        bit.paso(registro, {"episodio": numero, "paso": paso, "duracion_ms": duracion_ms,
                            "carga_1m": round(os.getloadavg()[0], 2)})
        if problemas:
            fallos.append((numero, paso, nombre, problemas))
            carpeta = bit.fallo(f"{numero:03d}-{paso:02d}-{semilla}", {**registro, "errores_navegador": errores,
                                                                       "respuestas_5xx": respuestas_5xx}, maestro.ruta)
            try:
                page.screenshot(path=os.path.join(carpeta, "pantalla.png"), full_page=True)
            except Exception:
                pass

    ctx.close()
    cliente.close()
    servidor.parar()
    return hechos


def correr(semilla, episodios, fuente, carpeta, dispositivo):
    from playwright.sync_api import sync_playwright

    shutil.rmtree(carpeta, ignore_errors=True)
    os.makedirs(os.path.join(carpeta, "logs"), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="auditoria_login_ui_")
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

    print(f"Corrida login {dispositivo} s{semilla} n{episodios}: {episodios} episodios, {pasos_totales} acciones, carpeta {carpeta}")
    if fallos:
        print(f"FALLO: {len(fallos)} acciones con invariantes rotas")
        for numero, paso, nombre, problemas in fallos[:20]:
            print(f"  episodio {numero} paso {paso} ({nombre}): {'; '.join(problemas)}")
        return 1
    print("OK: todas las invariantes se cumplieron")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Capas B y C de Login (navegador).")
    parser.add_argument("--semilla", type=int, default=1)
    parser.add_argument("--episodios", type=int, default=100)
    parser.add_argument("--fuente", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    parser.add_argument("--carpeta", required=True)
    parser.add_argument("--dispositivo", choices=sorted(escritorio.DISPOSITIVOS), default="escritorio")
    args = parser.parse_args()
    sys.exit(correr(args.semilla, args.episodios, os.path.abspath(args.fuente), os.path.abspath(args.carpeta), args.dispositivo))
