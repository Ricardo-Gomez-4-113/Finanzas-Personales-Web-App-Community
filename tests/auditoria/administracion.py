"""tests/auditoria/administracion.py - Sección Admin por API: crear, listar, suspender, editar y eliminar usuarios.

Mismo esquema que acciones.py y los demás módulos. Lo que se comprueba leyendo la base maestra y la carpeta de usuarios
directamente (no con las funciones de la app):
- Crear: acepta nombres y contraseñas válidos y rechaza el resto; un nombre repetido da 400; el usuario nuevo existe en la
  base maestra con su archivo, puede entrar con su contraseña y no ve datos de nadie.
- Suspender y reactivar: un usuario suspendido no entra; reactivado, sí.
- Editar la contraseña: la nueva entra y la vieja ya no.
- Eliminar: el usuario y su archivo desaparecen y los datos de los demás no cambian.
- La cuenta admin no se puede suspender ni eliminar.
- Las estadísticas coinciden con la base maestra, y la lista de usuarios con lo que hay.
- Un usuario normal recibe 403 en todo lo de /admin y no cambia nada.
"""

import os
import sqlite3

import acciones

PESOS_ACCION = (
    ("crear_usuario", 20), ("listar", 8), ("suspender", 10), ("reactivar", 6), ("editar_clave", 10),
    ("eliminar_usuario", 12), ("proteger_admin", 6), ("estadisticas", 8), ("no_admin", 12),
)
CLAVE_BASE = "Clave#"
TOLERANCIA = acciones.TOLERANCIA

Contexto = acciones.Contexto


class Maestro:
    """Lectura directa de la base maestra y de la carpeta de usuarios, al lado de la base de la persona."""

    def __init__(self, ruta_tenant):
        self.carpeta_tenants = os.path.dirname(ruta_tenant)
        self.ruta = os.path.join(os.path.dirname(self.carpeta_tenants), "auth_master.db")

    def usuarios(self):
        con = sqlite3.connect(f"file:{self.ruta}?mode=ro", uri=True)
        try:
            return tuple(con.execute("SELECT id, username, role, is_active, db_name, login_count FROM users ORDER BY id").fetchall())
        finally:
            con.close()

    def archivos(self):
        return frozenset(f for f in os.listdir(self.carpeta_tenants) if f.endswith(".db"))


def _admin(ctx):
    """Cabecera de sesión del administrador, creada la primera vez que se usa."""
    if getattr(ctx, "cabecera_admin", None) is None:
        r = ctx.cliente.post("/auth/login", json={"username": "admin", "password": os.environ["DEFAULT_ADMIN_PASSWORD"]})
        assert r.status_code == 200, r.text
        ctx.cabecera_admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return ctx.cabecera_admin


def _api_admin(ctx, metodo, ruta, cuerpo=None):
    return ctx.cliente.request(metodo, ruta, json=cuerpo, headers=_admin(ctx))


def _entrar(ctx, usuario, clave):
    return ctx.cliente.post("/auth/login", json={"username": usuario, "password": clave})


def _usuarios_propios(ctx):
    if getattr(ctx, "usuarios", None) is None:
        ctx.usuarios = {}
    return ctx.usuarios


def _estado(ctx):
    _admin(ctx)  # la sesión del administrador se abre antes: entrar suma una entrada al contador y no debe verse como cambio
    maestro = Maestro(ctx.oraculo.ruta_db)
    return {"usuarios": maestro.usuarios(), "archivos": maestro.archivos(), "propia": ctx.oraculo.volcado()}


def _base(accion, respuesta=None, **extra):
    return {"accion": accion, "respuesta": respuesta, **extra}


def crear_usuario(ctx):
    rng = ctx.rng
    escenario = rng.choices(("valido", "nombre_corto", "nombre_raro", "clave_corta", "clave_larga"), weights=(70, 6, 6, 9, 9))[0]
    nombre = f"usr{rng.randint(1, 30)}"
    clave = f"{CLAVE_BASE}{rng.randint(100000, 999999)}"
    if escenario == "nombre_corto":
        nombre = "ab"
    elif escenario == "nombre_raro":
        nombre = rng.choice(("con espacio", "ñandú", "x" * 60, "a/b"))
    elif escenario == "clave_corta":
        clave = "abc1234"
    elif escenario == "clave_larga":
        clave = "A#1" + "x" * 80
    rol = "admin" if rng.random() < 0.1 else "user"
    pre = _estado(ctx)
    repetido = any(u[1] == nombre for u in pre["usuarios"])
    cuerpo = {"username": nombre, "password": clave, "role": rol}
    sin_clave = None
    if rol == "admin":  # crear otro administrador pide la contraseña: primero se intenta sin ella
        sin_clave = _api_admin(ctx, "POST", "/admin/users", cuerpo).status_code
        tras_sin_clave = _estado(ctx)
        cuerpo["current_password"] = os.environ["DEFAULT_ADMIN_PASSWORD"]
    respuesta = _api_admin(ctx, "POST", "/admin/users", cuerpo)
    res = _base("crear_usuario", respuesta, entrada={"username": nombre, "role": rol, "escenario": escenario}, pre=pre,
                escenario=escenario, nombre=nombre, clave=clave, rol=rol, repetido=repetido, sin_clave=sin_clave)
    if sin_clave is not None:
        res["sin_clave_cambio"] = tras_sin_clave["usuarios"] != pre["usuarios"]
    if respuesta.status_code == 201:
        datos = respuesta.json()["user"]
        _usuarios_propios(ctx)[datos["id"]] = {"username": nombre, "password": clave, "activo": True, "rol": rol}
        entro = _entrar(ctx, nombre, clave)
        res["login_nuevo"] = entro.status_code
        if entro.status_code == 200:
            cab = {"Authorization": f"Bearer {entro.json()['access_token']}"}
            res["datos_nuevo"] = (len(ctx.cliente.get("/api/transacciones", headers=cab).json()),
                                  len(ctx.cliente.get("/api/apartados", headers=cab).json()))
        res["id_nuevo"] = datos["id"]
    return res


def listar(ctx):
    pre = _estado(ctx)
    return _base("listar", _api_admin(ctx, "GET", "/admin/users"), entrada={}, pre=pre)


def _elegir_propio(ctx):
    propios = _usuarios_propios(ctx)
    if not propios:
        return None
    id_ = ctx.rng.choice(sorted(propios))
    return id_, propios[id_]


def _cambiar_estado(ctx, activo):
    elegido = _elegir_propio(ctx)
    if elegido is None:
        return None
    id_, datos = elegido
    pre = _estado(ctx)
    respuesta = _api_admin(ctx, "PATCH", f"/admin/users/{id_}/status", {"is_active": activo})
    res = _base("reactivar" if activo else "suspender", respuesta, entrada={"id": id_, "is_active": activo}, pre=pre,
                objetivo=id_, activo=activo, rol=datos["rol"])
    if respuesta.status_code == 200:
        datos["activo"] = activo
    res["login"] = _entrar(ctx, datos["username"], datos["password"]).status_code
    return res


def suspender(ctx):
    return _cambiar_estado(ctx, False)


def reactivar(ctx):
    return _cambiar_estado(ctx, True)


def editar_clave(ctx):
    elegido = _elegir_propio(ctx)
    if elegido is None:
        return None
    id_, datos = elegido
    nueva = f"{CLAVE_BASE}{ctx.rng.randint(100000, 999999)}"
    pre = _estado(ctx)
    sin_clave = _api_admin(ctx, "PATCH", f"/admin/users/{id_}", {"password": nueva}).status_code
    login_tras_sin_clave = _entrar(ctx, datos["username"], datos["password"]).status_code
    respuesta = _api_admin(ctx, "PATCH", f"/admin/users/{id_}", {"password": nueva, "current_password": os.environ["DEFAULT_ADMIN_PASSWORD"]})
    res = _base("editar_clave", respuesta, entrada={"id": id_}, pre=pre, objetivo=id_, vieja=datos["password"], nueva=nueva,
                activo=datos["activo"], sin_clave=sin_clave, login_tras_sin_clave=login_tras_sin_clave)
    if respuesta.status_code == 200:
        res["login_nueva"] = _entrar(ctx, datos["username"], nueva).status_code
        res["login_vieja"] = _entrar(ctx, datos["username"], datos["password"]).status_code
        datos["password"] = nueva
    return res


def eliminar_usuario(ctx):
    elegido = _elegir_propio(ctx)
    if elegido is None:
        return None
    id_, datos = elegido
    pre = _estado(ctx)
    db_name = next((u[4] for u in pre["usuarios"] if u[0] == id_), None)
    sin_clave = _api_admin(ctx, "DELETE", f"/admin/users/{id_}").status_code
    mala_clave = _api_admin(ctx, "DELETE", f"/admin/users/{id_}", {"current_password": "Mala#clave999"}).status_code
    tras_rechazos = _estado(ctx)
    respuesta = _api_admin(ctx, "DELETE", f"/admin/users/{id_}", {"current_password": os.environ["DEFAULT_ADMIN_PASSWORD"]})
    if 200 in (sin_clave, mala_clave) or respuesta.status_code == 200:
        _usuarios_propios(ctx).pop(id_, None)
    return _base("eliminar_usuario", respuesta, entrada={"id": id_}, pre=pre, objetivo=id_, db_name=db_name,
                 sin_clave=sin_clave, mala_clave=mala_clave, tras_rechazos=tras_rechazos, rol=datos["rol"])


def proteger_admin(ctx):
    """La cuenta principal no se suspende ni se elimina. Si hay otro administrador creado, es él quien lo intenta
    (así se prueba la protección de la cuenta principal y no solo la de «no te borres a ti mismo»)."""
    segundo = next(((i, d) for i, d in sorted(_usuarios_propios(ctx).items()) if d["rol"] == "admin" and d["activo"]), None)
    if segundo is None:  # casi nunca hay otro administrador: se crea uno para que la protección se pruebe de verdad
        nombre, clave = f"adm{ctx.rng.randint(1, 10**6)}", f"{CLAVE_BASE}{ctx.rng.randint(100000, 999999)}"
        creado = _api_admin(ctx, "POST", "/admin/users", {"username": nombre, "password": clave, "role": "admin",
                                                          "current_password": os.environ["DEFAULT_ADMIN_PASSWORD"]})
        if creado.status_code == 201:
            _usuarios_propios(ctx)[creado.json()["user"]["id"]] = {"username": nombre, "password": clave, "activo": True, "rol": "admin"}
            segundo = next(((i, d) for i, d in sorted(_usuarios_propios(ctx).items()) if d["rol"] == "admin" and d["activo"]), None)
    cabecera = _admin(ctx)
    quien = "principal"
    if segundo is not None:
        entro = _entrar(ctx, segundo[1]["username"], segundo[1]["password"])
        if entro.status_code == 200:
            cabecera = {"Authorization": f"Bearer {entro.json()['access_token']}"}
            quien = "segundo_admin"
    pre = _estado(ctx)
    # Con la contraseña correcta de quien lo intenta: lo que debe frenar es la protección de la cuenta principal
    clave = segundo[1]["password"] if quien == "segundo_admin" else os.environ["DEFAULT_ADMIN_PASSWORD"]
    respuestas = [
        ctx.cliente.request("PATCH", "/admin/users/1/status", json={"is_active": False}, headers=cabecera),
        ctx.cliente.request("DELETE", "/admin/users/1", json={"current_password": clave}, headers=cabecera),
    ]
    if quien == "segundo_admin":  # la cuenta principal solo la edita ella misma
        # Con la misma contraseña que ya tiene: si la app lo acepta (violación), el arnés puede seguir entrando
        respuestas.append(ctx.cliente.request("PATCH", "/admin/users/1", json={"password": os.environ["DEFAULT_ADMIN_PASSWORD"],
                                                                              "current_password": clave}, headers=cabecera))
    return _base("proteger_admin", respuestas[0], entrada={"quien": quien}, pre=pre, respuestas=respuestas)


def estadisticas(ctx):
    pre = _estado(ctx)
    return _base("estadisticas", _api_admin(ctx, "GET", "/admin/stats"), entrada={}, pre=pre)


def no_admin(ctx):
    """Un usuario normal (la persona) intenta lo de administrador: todo debe dar 403 y no cambiar nada."""
    pre = _estado(ctx)
    intentos = [
        ctx.api("GET", "/admin/users"),
        ctx.api("GET", "/admin/stats"),
        ctx.api("POST", "/admin/users", {"username": "intruso", "password": "Clave#123456", "role": "admin"}),
        ctx.api("PATCH", "/admin/users/1/status", {"is_active": False}),
        ctx.api("DELETE", "/admin/users/1"),
    ]
    return _base("no_admin", intentos[0], entrada={}, pre=pre, intentos=intentos)


FUNCIONES = {
    "crear_usuario": crear_usuario, "listar": listar, "suspender": suspender, "reactivar": reactivar,
    "editar_clave": editar_clave, "eliminar_usuario": eliminar_usuario, "proteger_admin": proteger_admin,
    "estadisticas": estadisticas, "no_admin": no_admin,
}


def elegir(rng):
    nombres = [n for n, _ in PESOS_ACCION]
    pesos = [p for _, p in PESOS_ACCION]
    return rng.choices(nombres, weights=pesos)[0]


def verificar(nombre, res, oraculo, saldo_api):
    """Devuelve la lista de invariantes rotas en este paso (vacía si todo cuadra)."""
    problemas = []
    respuesta = res["respuesta"]
    if respuesta is not None and respuesta.status_code >= 500:
        problemas.append("error del servidor (5xx)")
    if respuesta is None:
        return problemas

    maestro = Maestro(oraculo.ruta_db)
    pre = res["pre"]
    usuarios, archivos = maestro.usuarios(), maestro.archivos()
    codigo = respuesta.status_code

    if nombre == "crear_usuario":
        if res.get("sin_clave") is not None and (res["sin_clave"] == 201 or res.get("sin_clave_cambio")):
            problemas.append(f"se creó un administrador sin la contraseña de quien lo crea ({res['sin_clave']})")
        valido = res["escenario"] == "valido"
        esperado = 201 if (valido and not res["repetido"]) else (400 if (valido and res["repetido"]) else 422)
        if codigo != esperado:
            problemas.append(f"crear «{res['nombre']}» ({res['escenario']}{', repetido' if res['repetido'] else ''}) respondió {codigo} y debía ser {esperado}")
        if codigo == 201:
            if len(usuarios) != len(pre["usuarios"]) + 1:
                problemas.append("crear un usuario no añadió exactamente uno a la base maestra")
            nuevo = next((u for u in usuarios if u[0] == res.get("id_nuevo")), None)
            if nuevo is None or nuevo[1] != res["nombre"] or nuevo[2] != res["rol"]:
                problemas.append("el usuario creado no quedó en la base maestra con su nombre y rol")
            elif nuevo[4] not in archivos:
                problemas.append(f"el usuario creado no tiene su archivo {nuevo[4]}")
            if res.get("login_nuevo") != 200:
                problemas.append(f"el usuario nuevo no puede entrar con su contraseña ({res.get('login_nuevo')})")
            if res.get("datos_nuevo") not in (None, (0, 0)):
                problemas.append(f"el usuario nuevo ve datos que no son suyos: {res['datos_nuevo']}")
        elif usuarios != pre["usuarios"] or archivos != pre["archivos"]:
            problemas.append("un alta rechazada cambió la base maestra o la carpeta de usuarios")
    elif nombre == "listar":
        lista = respuesta.json() if codigo == 200 else []
        if codigo != 200:
            problemas.append(f"listar usuarios respondió {codigo}")
        elif {u["username"] for u in lista} != {u[1] for u in usuarios} or len(lista) != len(usuarios):
            problemas.append(f"la lista tiene {len(lista)} usuarios y la base maestra {len(usuarios)}")
    elif nombre == "suspender" and res.get("rol") == "admin":
        if codigo != 400:
            problemas.append(f"suspender a otro administrador respondió {codigo} y debía ser 400")
        # Sin el contador de entradas: tras el intento, el arnés entra con esa cuenta para probar el acceso
        if tuple(u[:5] for u in usuarios) != tuple(u[:5] for u in pre["usuarios"]):
            problemas.append("un intento de suspender a un administrador cambió la base maestra")
    elif nombre in ("suspender", "reactivar"):
        if codigo != 200:
            problemas.append(f"{nombre} respondió {codigo}")
        else:
            fila = next((u for u in usuarios if u[0] == res["objetivo"]), None)
            if fila is None or bool(fila[3]) != res["activo"]:
                problemas.append(f"tras {nombre}, el estado en la base maestra no es el pedido")
            if res["activo"] and res["login"] != 200:
                problemas.append(f"un usuario reactivado no puede entrar ({res['login']})")
            if not res["activo"] and res["login"] == 200:
                problemas.append("un usuario suspendido pudo entrar")
    elif nombre == "editar_clave":
        if res.get("sin_clave") != 400 or res.get("login_tras_sin_clave") == 401 and res["activo"]:
            problemas.append(f"cambiar la contraseña de otra persona sin la del administrador respondió {res.get('sin_clave')}")
        if codigo != 200:
            problemas.append(f"editar la contraseña respondió {codigo}")
        else:
            if res["activo"] and res.get("login_nueva") != 200:
                problemas.append(f"la contraseña nueva no entra ({res.get('login_nueva')})")
            if res.get("login_vieja") == 200:
                problemas.append("la contraseña vieja sigue entrando")
    elif nombre == "eliminar_usuario" and res.get("rol") == "admin":
        if codigo != 400 or any(u[0] == res["objetivo"] for u in usuarios) is False:
            problemas.append(f"eliminar a otro administrador respondió {codigo} y debía ser 400 sin borrarlo")
    elif nombre == "eliminar_usuario":
        if res.get("sin_clave") != 400 or res.get("mala_clave") != 400:
            problemas.append(f"borrar un usuario sin la contraseña del administrador respondió {res.get('sin_clave')} y con una incorrecta {res.get('mala_clave')}; ambas debían ser 400")
        elif res["tras_rechazos"]["usuarios"] != pre["usuarios"] or res["tras_rechazos"]["archivos"] != pre["archivos"]:
            problemas.append("un borrado rechazado por contraseña cambió la base maestra o la carpeta")
        if codigo != 200:
            problemas.append(f"eliminar un usuario respondió {codigo}")
        else:
            if any(u[0] == res["objetivo"] for u in usuarios):
                problemas.append("el usuario eliminado sigue en la base maestra")
            if res["db_name"] in archivos:
                problemas.append(f"el archivo {res['db_name']} del usuario eliminado sigue en la carpeta")
            if len(archivos) != len(pre["archivos"]) - 1:
                problemas.append("eliminar un usuario tocó archivos que no eran suyos")
            if oraculo.volcado() != pre["propia"]:
                problemas.append("eliminar a otro usuario cambió los datos de la persona")
    elif nombre == "proteger_admin":
        for intento, r in zip(("suspender", "eliminar", "editar"), res["respuestas"]):
            if r.status_code != 400:
                problemas.append(f"{intento} la cuenta admin respondió {r.status_code} y debía ser 400")
        if usuarios != pre["usuarios"]:
            problemas.append("un intento sobre la cuenta admin cambió la base maestra")
    elif nombre == "estadisticas":
        if codigo != 200:
            problemas.append(f"las estadísticas respondieron {codigo}")
        else:
            datos = respuesta.json()
            if datos["total_users"] != len(usuarios):
                problemas.append(f"las estadísticas cuentan {datos['total_users']} usuarios y la base maestra {len(usuarios)}")
            if datos["inactive_users"] != sum(1 for u in usuarios if not u[3]):
                problemas.append("las estadísticas no cuentan bien los usuarios suspendidos")
            if datos["total_logins"] != sum(u[5] or 0 for u in usuarios):
                problemas.append(f"las estadísticas suman {datos['total_logins']} entradas y la base maestra {sum(u[5] or 0 for u in usuarios)}")
    elif nombre == "no_admin":
        for ruta, r in zip(("GET /admin/users", "GET /admin/stats", "POST /admin/users", "PATCH /admin/users/1/status",
                            "DELETE /admin/users/1"), res["intentos"]):
            if r.status_code != 403:
                problemas.append(f"{ruta} con un usuario normal respondió {r.status_code} y debía ser 403")
        if usuarios != pre["usuarios"] or archivos != pre["archivos"]:
            problemas.append("un usuario normal cambió la base maestra desde /admin")
    return problemas
