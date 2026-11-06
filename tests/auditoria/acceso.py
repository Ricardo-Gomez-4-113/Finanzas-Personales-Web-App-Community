"""tests/auditoria/acceso.py - Sección Login y comentarios por API: entrar, errores de acceso, bloqueo, tokens, perfil,
eliminar la propia cuenta, recuperar contraseña y enviar comentarios.

Mismo esquema que acciones.py y los demás módulos. Lo que se comprueba leyendo la base maestra directamente:
- Entrar bien suma una entrada, anota la fecha del día simulado y el token dice quién es.
- Un error de acceso no dice si el usuario existe: usuario inexistente y contraseña mala responden igual, y no suman entradas.
- Cinco intentos fallidos bloquean esa cuenta (429), aunque el sexto sea la contraseña correcta, sin bloquear a otras.
- Un token ausente, inventado, alterado o de una cuenta eliminada no entra.
- Cambiar la contraseña exige la actual; la nueva entra y la vieja no. Cambiar el nombre no deja entrar con el viejo.
- Eliminar la cuenta propia borra al usuario y su archivo sin tocar a los demás; la cuenta administradora no se puede eliminar así.
- Recuperar contraseña responde igual exista o no la cuenta.
- Los comentarios se guardan completos, no más de cinco por hora, y el texto no llega al registro de fallos.
"""

import datetime
import os
import sqlite3

import acciones
import administracion

PESOS_ACCION = (
    ("entrar_bien", 18), ("entrar_mal", 12), ("bloqueo", 6), ("token_invalido", 10), ("editar_perfil", 14),
    ("eliminar_cuenta", 6), ("eliminar_admin", 3), ("olvide_clave", 8), ("comentar", 16), ("comentar_mal", 8),
    ("avanzar_dia", 4),
)
CLAVE_BASE = "Clave#"
MENSAJE_USUARIO_MALO = "Usuario o contraseña incorrectos."
MAX_COMENTARIOS_POR_HORA = 5

Contexto = acciones.Contexto


class Base:
    """Lectura directa de la base maestra, al lado de la carpeta de usuarios."""

    def __init__(self, ruta_tenant):
        self.carpeta_tenants = os.path.dirname(ruta_tenant)
        self.ruta = os.path.join(os.path.dirname(self.carpeta_tenants), "auth_master.db")

    def _leer(self, consulta, parametros=()):
        con = sqlite3.connect(f"file:{self.ruta}?mode=ro", uri=True)
        try:
            return con.execute(consulta, parametros).fetchall()
        finally:
            con.close()

    def usuarios(self):
        return tuple(self._leer(
            "SELECT id, username, role, is_active, db_name, login_count, last_login, email, password_hash FROM users ORDER BY id"
        ))

    def comentarios(self, user_id):
        return tuple(self._leer("SELECT id, tipo, mensaje, contexto FROM comentarios WHERE user_id = ? ORDER BY id", (user_id,)))

    def comentarios_recientes(self, user_id):
        return self._leer("SELECT COUNT(*) FROM comentarios WHERE user_id = ? AND creado_en >= datetime('now', '-1 hours')", (user_id,))[0][0]

    def archivos(self):
        return frozenset(f for f in os.listdir(self.carpeta_tenants) if f.endswith(".db"))


def _base(ctx):
    return Base(ctx.oraculo.ruta_db)


def _cuentas(ctx):
    if getattr(ctx, "cuentas", None) is None:
        ctx.cuentas = {}
    return ctx.cuentas


def _entrar(ctx, usuario, clave):
    return ctx.cliente.post("/auth/login", json={"username": usuario, "password": clave})


def _crear_cuenta(ctx):
    """Crea una cuenta de prueba con el administrador. Devuelve (nombre, datos) o None si no se pudo."""
    administracion._admin(ctx)
    nombre = f"acc{ctx.rng.randint(1, 10**6)}"
    clave = f"{CLAVE_BASE}{ctx.rng.randint(100000, 999999)}"
    r = administracion._api_admin(ctx, "POST", "/admin/users", {"username": nombre, "password": clave, "role": "user"})
    if r.status_code != 201:
        return None
    datos = {"id": r.json()["user"]["id"], "username": nombre, "password": clave}
    _cuentas(ctx)[nombre] = datos
    return nombre, datos


def _una_cuenta(ctx):
    cuentas = _cuentas(ctx)
    if not cuentas or (len(cuentas) < 3 and ctx.rng.random() < 0.3):
        creada = _crear_cuenta(ctx)
        if creada is not None:
            return creada
    if not cuentas:
        return None
    nombre = ctx.rng.choice(sorted(cuentas))
    return nombre, cuentas[nombre]


def _con_token(ctx, datos):
    r = _entrar(ctx, datos["username"], datos["password"])
    if r.status_code != 200:
        return None
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _res(accion, respuesta, **extra):
    return {"accion": accion, "respuesta": respuesta, **extra}


def entrar_bien(ctx):
    elegida = _una_cuenta(ctx)
    if elegida is None:
        return None
    nombre, datos = elegida
    pre = _base(ctx).usuarios()
    r = _entrar(ctx, datos["username"], datos["password"])
    yo = None
    if r.status_code == 200:
        yo = ctx.cliente.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    return _res("entrar_bien", r, entrada={"usuario": nombre}, pre=pre, id=datos["id"], nombre=nombre, yo=yo)


def entrar_mal(ctx):
    elegida = _una_cuenta(ctx)
    if elegida is None:
        return None
    nombre, datos = elegida
    pre = _base(ctx).usuarios()
    mala = ctx.cliente.post("/auth/login", json={"username": datos["username"], "password": "Equivocada#" + str(ctx.rng.randint(1, 999999))})
    fantasma = ctx.cliente.post("/auth/login", json={"username": f"nadie{ctx.rng.randint(1, 10**9)}", "password": "Equivocada#123456"})
    return _res("entrar_mal", mala, entrada={"usuario": nombre}, pre=pre, fantasma=fantasma, id=datos["id"])


def bloqueo(ctx):
    if getattr(ctx, "hubo_bloqueo", False):
        return None  # cada intento fallido cuenta también para el tope por IP: se prueba una vez por episodio
    creada = _crear_cuenta(ctx)
    if creada is None:
        return None
    ctx.hubo_bloqueo = True
    nombre, datos = creada
    del _cuentas(ctx)[nombre]  # la cuenta queda bloqueada el resto del episodio
    pre = _base(ctx).usuarios()
    fallidos = [_entrar(ctx, nombre, f"Equivocada#{i}00000") for i in range(5)]
    sexto = _entrar(ctx, nombre, datos["password"])  # la contraseña correcta, pero ya está bloqueada
    otra = _una_cuenta(ctx)
    otro_login = _entrar(ctx, otra[1]["username"], otra[1]["password"]) if otra else None
    return _res("bloqueo", sexto, entrada={"usuario": nombre}, pre=pre, fallidos=fallidos, otro_login=otro_login, id=datos["id"])


def token_invalido(ctx):
    elegida = _una_cuenta(ctx)
    if elegida is None:
        return None
    _, datos = elegida
    cab = _con_token(ctx, datos)
    if cab is None:
        return None
    token = cab["Authorization"].split(" ", 1)[1]
    alterado = token[:-1] + ("A" if token[-1] != "A" else "B")
    intentos = [
        ctx.cliente.get("/auth/me"),
        ctx.cliente.get("/auth/me", headers={"Authorization": "Bearer basura"}),
        ctx.cliente.get("/auth/me", headers={"Authorization": f"Bearer {alterado}"}),
        ctx.cliente.get("/api/transacciones", headers={"Authorization": f"Bearer {alterado}"}),
    ]
    borrados = getattr(ctx, "tokens_borrados", [])
    if borrados:
        intentos.append(ctx.cliente.get("/auth/me", headers={"Authorization": f"Bearer {ctx.rng.choice(borrados)}"}))
    return _res("token_invalido", intentos[0], entrada={"intentos": len(intentos)}, pre=_base(ctx).usuarios(), intentos=intentos)


def editar_perfil(ctx):
    elegida = _una_cuenta(ctx)
    if elegida is None:
        return None
    nombre, datos = elegida
    cab = _con_token(ctx, datos)
    if cab is None:
        return None
    variante = ctx.rng.choice(("email", "clave_sin_actual", "clave_actual_mala", "clave_ok", "nombre_repetido", "nombre_nuevo"))
    pre = _base(ctx).usuarios()
    otros = [d["username"] for n, d in _cuentas(ctx).items() if n != nombre]
    cuerpo, nueva = {}, None
    if variante == "email":
        cuerpo = {"email": f"{datos['username']}@prueba.local"}
    elif variante == "clave_sin_actual":
        nueva = f"{CLAVE_BASE}{ctx.rng.randint(100000, 999999)}"
        cuerpo = {"password": nueva}
    elif variante == "clave_actual_mala":
        nueva = f"{CLAVE_BASE}{ctx.rng.randint(100000, 999999)}"
        cuerpo = {"password": nueva, "current_password": "Equivocada#000000"}
    elif variante == "clave_ok":
        nueva = f"{CLAVE_BASE}{ctx.rng.randint(100000, 999999)}"
        cuerpo = {"password": nueva, "current_password": datos["password"]}
    elif variante == "nombre_repetido":
        if not otros:
            return None
        cuerpo = {"username": otros[0]}
    else:
        nueva = f"nvo{ctx.rng.randint(1, 10**6)}"
        cuerpo = {"username": nueva}
    r = ctx.cliente.request("PATCH", "/auth/profile", json=cuerpo, headers=cab)
    res = _res("editar_perfil", r, entrada={"variante": variante}, pre=pre, variante=variante, id=datos["id"], nueva=nueva,
               vieja=datos["password"], nombre_viejo=datos["username"], cuerpo=cuerpo)
    if r.status_code == 200 and variante in ("clave_ok", "nombre_nuevo"):
        res["login_nuevo"] = _entrar(ctx, nueva if variante == "nombre_nuevo" else datos["username"],
                                     datos["password"] if variante == "nombre_nuevo" else nueva).status_code
        res["login_viejo"] = _entrar(ctx, datos["username"], datos["password"]).status_code  # lo de antes, ya no debe entrar
        res["token_nuevo"] = ctx.cliente.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['new_token']}"}).status_code
        if variante == "clave_ok":
            datos["password"] = nueva
        else:
            del _cuentas(ctx)[nombre]
            datos["username"] = nueva
            _cuentas(ctx)[nueva] = datos
    return res


def eliminar_cuenta(ctx):
    cuentas = _cuentas(ctx)
    if not cuentas:
        return None
    nombre = ctx.rng.choice(sorted(cuentas))
    datos = cuentas[nombre]
    cab = _con_token(ctx, datos)
    if cab is None:
        return None
    pre = _base(ctx).usuarios()
    db_name = next((u[4] for u in pre if u[0] == datos["id"]), None)
    archivos_pre = _base(ctx).archivos()
    propia = ctx.oraculo.volcado()
    r = ctx.cliente.request("DELETE", "/auth/delete-account", headers=cab)
    res = _res("eliminar_cuenta", r, entrada={"usuario": nombre}, pre=pre, id=datos["id"], db_name=db_name,
               archivos_pre=archivos_pre, propia=propia)
    if r.status_code == 200:
        del cuentas[nombre]
        ctx.tokens_borrados = getattr(ctx, "tokens_borrados", []) + [cab["Authorization"].split(" ", 1)[1]]
        res["login_despues"] = _entrar(ctx, nombre, datos["password"]).status_code
        res["token_despues"] = ctx.cliente.get("/auth/me", headers=cab).status_code
    return res


def eliminar_admin(ctx):
    cab = administracion._admin(ctx)
    pre = _base(ctx).usuarios()
    r = ctx.cliente.request("DELETE", "/auth/delete-account", headers=cab)
    return _res("eliminar_admin", r, entrada={}, pre=pre)


def olvide_clave(ctx):
    elegida = _una_cuenta(ctx)
    if elegida is None:
        return None
    _, datos = elegida
    existente = ctx.cliente.post("/auth/forgot-password", json={"identifier": datos["username"]})
    fantasma = ctx.cliente.post("/auth/forgot-password", json={"identifier": f"nadie{ctx.rng.randint(1, 10**9)}"})
    reset_e = ctx.cliente.post("/auth/reset-password", json={"identifier": datos["username"], "code": "000000", "new_password": "Nueva#123456"})
    reset_f = ctx.cliente.post("/auth/reset-password", json={"identifier": f"nadie{ctx.rng.randint(1, 10**9)}", "code": "000000", "new_password": "Nueva#123456"})
    return _res("olvide_clave", existente, entrada={}, pre=_base(ctx).usuarios(), fantasma=fantasma, reset_e=reset_e, reset_f=reset_f)


def _yo(ctx):
    return ctx.api("GET", "/auth/me").json()["user"]["id"]


def comentar(ctx):
    yo = _yo(ctx)
    marca = f"SECRETO-{ctx.rng.randint(1, 10**9)}"
    cuerpo = {"tipo": ctx.rng.choice(("sugerencia", "error", "otro")),
              "mensaje": f"  Comentario {marca}  ", "contexto": ctx.rng.choice((None, "Movimientos", "Presupuesto"))}
    base = _base(ctx)
    antes = base.comentarios(yo)
    recientes = base.comentarios_recientes(yo)
    r = ctx.api("POST", "/api/comentarios", cuerpo)
    return _res("comentar", r, entrada={"tipo": cuerpo["tipo"]}, uid=yo, cuerpo=cuerpo, marca=marca, antes=antes, recientes=recientes)


def comentar_mal(ctx):
    yo = _yo(ctx)
    base = _base(ctx)
    antes = base.comentarios(yo)
    casos = [
        ("vacio", {"tipo": "otro", "mensaje": ""}),
        ("espacios", {"tipo": "otro", "mensaje": "   "}),
        ("largo", {"tipo": "otro", "mensaje": "x" * 1001}),
        ("tipo_malo", {"tipo": "queja", "mensaje": "hola"}),
    ]
    respuestas = [(nombre, ctx.api("POST", "/api/comentarios", cuerpo)) for nombre, cuerpo in casos]
    sin_sesion = ctx.cliente.post("/api/comentarios", json={"tipo": "otro", "mensaje": "hola"})
    return _res("comentar_mal", respuestas[0][1], entrada={}, uid=yo, antes=antes, respuestas=respuestas, sin_sesion=sin_sesion)


def avanzar_dia(ctx):
    ctx.hoy += datetime.timedelta(days=ctx.rng.choice((1, 1, 2, 7)))
    ctx.fijar_hoy()
    return _res("avanzar_dia", None, entrada={"hoy": ctx.hoy.isoformat()})


def _con_contador_limpio(funcion):
    """Los accesos fallidos que esta sección provoca a propósito suman para el tope por IP de la app, y en un episodio
    largo la IP se bloquea. Se reinicia el contador al empezar cada acción; el bloqueo por cuenta se prueba dentro de una."""
    def envuelta(ctx):
        import security
        security._intentos_fallidos_login.clear()
        return funcion(ctx)
    return envuelta


_FUNCIONES = {
    "entrar_bien": entrar_bien, "entrar_mal": entrar_mal, "bloqueo": bloqueo, "token_invalido": token_invalido,
    "editar_perfil": editar_perfil, "eliminar_cuenta": eliminar_cuenta, "eliminar_admin": eliminar_admin,
    "olvide_clave": olvide_clave, "comentar": comentar, "comentar_mal": comentar_mal, "avanzar_dia": avanzar_dia,
}
FUNCIONES = {nombre: _con_contador_limpio(funcion) for nombre, funcion in _FUNCIONES.items()}


def elegir(rng):
    nombres = [n for n, _ in PESOS_ACCION]
    pesos = [p for _, p in PESOS_ACCION]
    return rng.choices(nombres, weights=pesos)[0]


def _hoy():
    return os.environ["FINANZAS_HOY"][:10]


def _fila(usuarios, id_):
    return next((u for u in usuarios if u[0] == id_), None)


def _contenido_del_registro(oraculo):
    directorio = os.environ.get("FINANZAS_LOG_DIR", "")
    texto = ""
    if directorio and os.path.isdir(directorio):
        for nombre in os.listdir(directorio):
            if nombre.startswith("app.log"):
                with open(os.path.join(directorio, nombre), encoding="utf-8", errors="replace") as archivo:
                    texto += archivo.read()
    return texto


def verificar(nombre, res, oraculo, saldo_api):
    """Devuelve la lista de invariantes rotas en este paso (vacía si todo cuadra)."""
    problemas = []
    respuesta = res["respuesta"]
    todas = [respuesta] + [v for k, v in res.items() if k in ("fantasma", "reset_e", "reset_f", "otro_login", "sin_sesion", "yo") and v is not None]
    todas += list(res.get("intentos", [])) + list(res.get("fallidos", [])) + [r for _, r in res.get("respuestas", [])]
    # Recuperar contraseña responde 503 a propósito cuando el servidor no tiene correo configurado (como aquí)
    caidas = [r for r in todas if r is not None and r.status_code >= 500 and not (nombre == "olvide_clave" and r.status_code == 503)]
    if caidas:
        problemas.append("error del servidor (5xx)")
    if respuesta is None:
        return problemas

    base = Base(oraculo.ruta_db)
    usuarios = base.usuarios()
    pre = res.get("pre")
    codigo = respuesta.status_code

    if nombre == "entrar_bien":
        antes, ahora = _fila(pre, res["id"]), _fila(usuarios, res["id"])
        if codigo != 200 or not respuesta.json().get("access_token"):
            problemas.append(f"entrar con la contraseña correcta respondió {codigo}")
        else:
            if ahora[5] != antes[5] + 1:
                problemas.append(f"entrar sumó {ahora[5] - antes[5]} entradas y debía sumar 1")
            if not str(ahora[6] or "").startswith(_hoy()):
                problemas.append(f"la última entrada quedó como {ahora[6]} y el día simulado es {_hoy()}")
            yo = res["yo"].json().get("user", {}) if res["yo"] is not None and res["yo"].status_code == 200 else {}
            if yo.get("username") != res["nombre"] or yo.get("id") != res["id"]:
                problemas.append("el token no dice quién entró")
    elif nombre == "entrar_mal":
        mala, fantasma = respuesta, res["fantasma"]
        if mala.status_code != 401 or fantasma.status_code != 401:
            problemas.append(f"un acceso fallido respondió {mala.status_code} y {fantasma.status_code}, no 401")
        if mala.json().get("detail") != fantasma.json().get("detail") or mala.json().get("detail") != MENSAJE_USUARIO_MALO:
            problemas.append("el error de contraseña mala y el de usuario inexistente no son idénticos: se puede saber quién existe")
        if _fila(usuarios, res["id"])[5] != _fila(pre, res["id"])[5]:
            problemas.append("un acceso fallido sumó una entrada")
    elif nombre == "bloqueo":
        if [r.status_code for r in res["fallidos"]] != [401] * 5:
            problemas.append(f"los cinco intentos fallidos respondieron {[r.status_code for r in res['fallidos']]}")
        if codigo != 429:
            problemas.append(f"tras cinco fallos, la contraseña correcta respondió {codigo} y debía ser 429")
        if _fila(usuarios, res["id"])[5] != _fila(pre, res["id"])[5]:
            problemas.append("un acceso bloqueado sumó una entrada")
        if res["otro_login"] is not None and res["otro_login"].status_code != 200:
            problemas.append(f"el bloqueo de una cuenta impidió entrar a otra ({res['otro_login'].status_code})")
    elif nombre == "token_invalido":
        for i, r in enumerate(res["intentos"]):
            if r.status_code not in (401, 403):
                problemas.append(f"un token no válido (caso {i}) respondió {r.status_code} y no un rechazo")
    elif nombre == "editar_perfil":
        v = res["variante"]
        antes, ahora = _fila(pre, res["id"]), _fila(usuarios, res["id"])
        if v == "email":
            if codigo != 200 or ahora[7] != res["cuerpo"]["email"]:
                problemas.append(f"cambiar el correo respondió {codigo} y quedó {ahora[7]}")
        elif v in ("clave_sin_actual", "clave_actual_mala"):
            if codigo != 400:
                problemas.append(f"cambiar la contraseña sin la actual correcta respondió {codigo} y debía ser 400")
            if ahora[8] != antes[8]:
                problemas.append("la contraseña cambió aunque no se dio la actual correcta")
        elif v == "clave_ok":
            if codigo != 200 or res.get("login_nuevo") != 200:
                problemas.append(f"cambiar la contraseña ({codigo}) y entrar con la nueva ({res.get('login_nuevo')})")
            if res.get("login_viejo") == 200:
                problemas.append("la contraseña vieja sigue entrando")
            if res.get("token_nuevo") != 200:
                problemas.append("el token que devuelve el cambio de contraseña no funciona")
        elif v == "nombre_repetido":
            if codigo != 400:
                problemas.append(f"quedarse con el nombre de otro respondió {codigo} y debía ser 400")
            if ahora[1] != antes[1]:
                problemas.append("el nombre cambió a uno que ya tenía otra cuenta")
        elif v == "nombre_nuevo":
            if codigo != 200 or res.get("login_nuevo") != 200:
                problemas.append(f"cambiar el nombre ({codigo}) y entrar con el nuevo ({res.get('login_nuevo')})")
            if res.get("login_viejo") == 200:
                problemas.append("el nombre viejo sigue entrando")
    elif nombre == "eliminar_cuenta":
        if codigo != 200:
            problemas.append(f"eliminar la propia cuenta respondió {codigo}")
        else:
            if _fila(usuarios, res["id"]) is not None:
                problemas.append("la cuenta eliminada sigue en la base maestra")
            if res["db_name"] in base.archivos():
                problemas.append("el archivo de la cuenta eliminada sigue en la carpeta")
            if len(base.archivos()) != len(res["archivos_pre"]) - 1:
                problemas.append("eliminar una cuenta tocó archivos que no eran suyos")
            if res.get("login_despues") == 200:
                problemas.append("una cuenta eliminada pudo entrar")
            if res.get("token_despues") == 200:
                problemas.append("el token de una cuenta eliminada sigue funcionando")
            if oraculo.volcado() != res["propia"]:
                problemas.append("eliminar una cuenta cambió los datos de otra persona")
    elif nombre == "eliminar_admin":
        if codigo != 403:
            problemas.append(f"el administrador eliminando su cuenta respondió {codigo} y debía ser 403")
        if usuarios != pre:
            problemas.append("un administrador pudo cambiar la base maestra eliminando su cuenta")
    elif nombre == "olvide_clave":
        if (respuesta.status_code, respuesta.text) != (res["fantasma"].status_code, res["fantasma"].text):
            problemas.append("recuperar contraseña responde distinto si la cuenta existe: se puede saber quién existe")
        if (res["reset_e"].status_code, res["reset_e"].text) != (res["reset_f"].status_code, res["reset_f"].text):
            problemas.append("restablecer con un código malo responde distinto si la cuenta existe")
    elif nombre == "comentar":
        esperado = 201 if res["recientes"] < MAX_COMENTARIOS_POR_HORA else 429
        ahora = base.comentarios(res["uid"])
        if codigo != esperado:
            problemas.append(f"el comentario respondió {codigo} y con {res['recientes']} en la última hora debía ser {esperado}")
        if codigo == 201:
            nuevo = ahora[-1] if len(ahora) == len(res["antes"]) + 1 else None
            cuerpo = res["cuerpo"]
            if nuevo is None or nuevo[1] != cuerpo["tipo"] or nuevo[2] != cuerpo["mensaje"].strip() or nuevo[3] != cuerpo["contexto"]:
                problemas.append(f"el comentario no quedó guardado completo y sin los espacios de los extremos: {nuevo}")
            if res["marca"] in _contenido_del_registro(oraculo):
                problemas.append("el texto del comentario llegó al registro de fallos")
        elif ahora != res["antes"]:
            problemas.append("un comentario rechazado igual se guardó")
    elif nombre == "comentar_mal":
        for (caso, r) in res["respuestas"]:
            if r.status_code != 422:
                problemas.append(f"el comentario «{caso}» respondió {r.status_code} y debía ser 422")
        if res["sin_sesion"].status_code != 401:
            problemas.append(f"un comentario sin sesión respondió {res['sin_sesion'].status_code} y debía ser 401")
        if base.comentarios(res["uid"]) != res["antes"]:
            problemas.append("un comentario inválido se guardó")
    return problemas
