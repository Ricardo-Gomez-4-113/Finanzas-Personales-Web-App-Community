"""tests/test_asistente_web.py - El asistente en la burbuja de la app: rutas de la app, servicio aparte, pase e interruptor del administrador.

Sin red y sin costo: el servicio del asistente corre en el mismo proceso (la app lo «llama» directo) y el modelo es un guion.
Requiere fastapi + httpx (se ejecuta en Docker, ver tests/run_tests.sh). Copia el código a un directorio temporal: nunca toca 'data/'.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import date

try:
    import httpx  # noqa: F401
    import fastapi  # noqa: F401
except ImportError:
    print("SKIP: requiere fastapi y httpx (ejecutar con tests/run_tests.sh)")
    sys.exit(0)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="finanzas_test_")
shutil.copytree(
    RAIZ, TMP, dirs_exist_ok=True,
    ignore=shutil.ignore_patterns("data", ".git", "tests", "__pycache__", "*.db", "backups", "venv", ".venv", "node_modules", ".env", ".env.*"),
)
os.chdir(TMP)
sys.path.insert(0, TMP)
os.environ.setdefault("JWT_SECRET_KEY", "k" * 48)
os.environ["DEFAULT_ADMIN_PASSWORD"] = "AdminTest#12345"
os.environ["FINANZAS_HOY"] = "2026-11-09T10:00:00"
SECRETO = "w" * 40                                  # secreto del canal WEB
SECRETO_TELEGRAM = "t" * 40                          # secreto del bot de Telegram: otro valor, no abre nada del canal web
os.environ["ASISTENTE_WEB_SECRETO"] = SECRETO
os.environ["ASISTENTE_SECRETO_INTERNO"] = SECRETO_TELEGRAM
os.environ.pop("ASISTENTE_WEB_URL", None)

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import master_db  # noqa: E402
import security  # noqa: E402
from main import app  # noqa: E402
from asistente import pase  # noqa: E402
from asistente.api import ejecutar_propuesta, obtener_con_proveedor  # noqa: E402
from asistente.herramientas import Herramientas  # noqa: E402
from asistente.modelo_falso import ModeloFalso  # noqa: E402
from asistente.motor import Motor, Respuesta  # noqa: E402
from asistente.propuestas import Propuestas  # noqa: E402
from asistente.servicio import ServicioHTTP  # noqa: E402
from asistente.servidor_web import Servidor, ServicioWeb, _manejador  # noqa: E402
from asistente.tokens import AsistenteDesactivado, emitir_token  # noqa: E402
from routers import asistente_web  # noqa: E402

_LLAMAR_REAL = asistente_web._llamar   # el de verdad; las pruebas que montan un servicio en el mismo proceso lo reemplazan
PASS = "Passw0rd#Test1"
HOY = date(2026, 11, 9)


def _cuenta(client, nombre, rol=None):
    fila = master_db.create_user_entry(nombre, security.hash_password(PASS), email=f"{nombre}@test.local")
    if rol:
        master_db.update_user(fila["id"], role=rol)
    r = client.post("/auth/login", json={"username": nombre, "password": PASS})
    assert r.status_code == 200, r.text
    return fila["id"], {"Authorization": f"Bearer {r.json()['access_token']}"}


def _con_fondos(client, h):
    cat = client.get("/api/categorias", headers=h).json()[0]["id"]
    assert client.post("/api/transacciones", json={"fecha": "2026-09-01", "monto": 50000, "tipo": "ingreso", "categoria_id": cat, "descripcion": "Fondeo"}, headers=h).status_code == 200


def _gastos(client, h):
    return [t for t in client.get("/api/transacciones", headers=h).json() if t["tipo"] == "gasto"]


def _montar(client, guiones):
    """Conecta la app con un servicio del asistente en el mismo proceso. `guiones`: {user_id: [Respuesta, ...]}."""
    propuestas = Propuestas()
    modelos = {}
    servicio = ServicioWeb(client, SECRETO)

    def crear_motor(cuenta, clave):
        modelo = modelos.setdefault(cuenta["id"], ModeloFalso(guiones.get(cuenta["id"], [Respuesta(texto="ok")] * 10)))
        obtener = obtener_con_proveedor(client, lambda: servicio.token(cuenta["pase"]))
        return Motor(modelo, Herramientas(obtener, propuestas, cuenta["id"], lambda: HOY), lambda: HOY)

    def ejecutar(propuesta, token):
        return ejecutar_propuesta(client, propuesta, lambda: token)

    servidor = Servidor(servicio, crear_motor, propuestas, ejecutar, SECRETO)
    llamadas = []

    def llamar(ruta, cuerpo=None, segundos=0):
        llamadas.append((ruta, cuerpo))
        codigo, datos = servidor.manejar("POST" if cuerpo is not None else "GET", ruta, {"x-asistente-secreto": SECRETO}, cuerpo)
        if codigo == 403:
            raise HTTPException(status_code=403, detail="El asistente está desactivado para esta cuenta.")
        if codigo != 200:
            raise asistente_web.ServicioNoDisponible()
        return datos

    os.environ["ASISTENTE_WEB_URL"] = "http://asistente.interno:8100"
    asistente_web._llamar = llamar
    asistente_web._salud.update(hasta=0.0, ok=False)
    servidor.llamadas, servidor.modelos = llamadas, modelos
    return servidor


def _propuesta_de_gasto(monto=31):
    return [Respuesta(llamadas=[{"nombre": "registrar_movimiento", "args": {"tipo": "gasto", "monto": monto, "categoria": "Comida", "descripcion": "Súper"}}]),
            Respuesta(texto="Quedó pendiente de confirmación.")]


def test_el_pase_firma_la_cuenta_vence_y_no_se_puede_alterar(client):
    p = pase.crear(7)
    assert pase.verificar(p) == 7
    assert pase.verificar(p, ahora=time.time() + pase.SEGUNDOS_DEL_PASE + 5) is None, "vence"
    u, v, f = p.split(".")
    for malo in ("²." + v + "." + f, f"{u}.٥٥.{f}", f"8.{v}.{f}", f"{u}.{int(v) + 999}.{f}", f"{u}.{v}.{'0' * 64}", f"{u}.{v}", "basura", "", None, 7, "1." * 200):
        assert pase.verificar(malo) is None, malo
    # Firmado con el secreto interno (que SÍ tiene el servicio) no vale: la clave del pase es de la app
    import hashlib
    import hmac
    falso = hmac.new(SECRETO.encode(), f"web:7:{v}".encode(), hashlib.sha256).hexdigest()
    assert pase.verificar(f"7.{v}.{falso}") is None


def test_la_api_interna_web_exige_secreto_y_pase_y_solo_da_lectura(client):
    import jwt as _jwt
    uid, h = _cuenta(client, "web_interna")
    otro, _ = _cuenta(client, "web_interna_2")
    servicio = ServicioWeb(client, SECRETO)
    malo = ServicioWeb(client, "x" * 40)
    p = pase.crear(uid)
    assert malo._cliente.post("/internal/asistente/web/token", json={"pase": p}, headers={"X-Asistente-Secreto": "x" * 40}).status_code == 401
    # El secreto del bot de Telegram no abre el canal web, ni el del canal web abre las rutas de Telegram
    assert client.post("/internal/asistente/web/token", json={"pase": p}, headers={"X-Asistente-Secreto": SECRETO_TELEGRAM}).status_code == 401
    assert client.post("/internal/asistente/chat", json={"chat_id": 1}, headers={"X-Asistente-Secreto": SECRETO}).status_code == 401
    assert client.post("/internal/asistente/chat", json={"chat_id": 1}, headers={"X-Asistente-Secreto": SECRETO_TELEGRAM}).status_code == 404, "el suyo sí entra (el chat no existe)"
    token = servicio.token(p)
    claims = _jwt.decode(token, options={"verify_signature": False})
    assert claims["sub"] == str(uid) and claims["uso"] == "asistente", "solo lectura y de ESA cuenta"
    assert client.get("/api/resumen", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    # Pedir escritura por esta vía no existe: aunque el cuerpo lo diga, el token es de lectura
    r = client.post("/internal/asistente/web/token", json={"pase": p, "escritura": True}, headers={"X-Asistente-Secreto": SECRETO})
    assert _jwt.decode(r.json()["token"], options={"verify_signature": False})["uso"] == "asistente"
    # Pase vencido, alterado o de una cuenta que no existe: no hay nada
    for invalido in (pase.crear(uid, ahora=time.time() - 1000), p[:-3] + "abc", pase.crear(999999)):
        r = client.post("/internal/asistente/web/token", json={"pase": invalido}, headers={"X-Asistente-Secreto": SECRETO})
        assert r.status_code == 404, (invalido, r.status_code)
    assert servicio.reservar_cupo(p) is None
    servicio.sumar_tokens(p, 25)
    assert client.post("/internal/asistente/web/uso", json={"pase": p, "tokens": 100_001}, headers={"X-Asistente-Secreto": SECRETO}).status_code == 422, "tope por llamada"
    from asistente.cupo import Cupo
    assert Cupo().uso(uid)["tokens"] == 25 and Cupo().uso(otro)["tokens"] == 0, "el uso se anota a la cuenta del pase"
    # Cuenta desactivada: ni token ni cupo
    master_db.set_user_status(uid, 0)
    for ruta, cuerpo in (("/token", {"pase": p}), ("/cupo", {"pase": p}), ("/uso", {"pase": p, "tokens": 1})):
        assert client.post("/internal/asistente/web" + ruta, json=cuerpo, headers={"X-Asistente-Secreto": SECRETO}).status_code == 404
    master_db.set_user_status(uid, 1)


def test_sin_servicio_configurado_o_caido_la_burbuja_no_existe_y_los_mensajes_dicen_no_disponible(client):
    uid, h = _cuenta(client, "web_sin_servicio")
    os.environ.pop("ASISTENTE_WEB_URL", None)
    assert client.get("/api/asistente/web/estado", headers=h).json() == {"disponible": False, "motivo": "no_configurado"}
    assert client.post("/api/asistente/web/mensaje", json={"texto": "hola"}, headers=h).status_code == 503
    # Configurado pero el servicio no contesta
    os.environ["ASISTENTE_WEB_URL"] = "http://127.0.0.1:9"   # nada escucha en el puerto 9
    asistente_web._llamar = _LLAMAR_REAL
    asistente_web._salud.update(hasta=0.0, ok=False)
    try:
        assert client.get("/api/asistente/web/estado", headers=h).json() == {"disponible": False, "motivo": "no_responde"}
        r = client.post("/api/asistente/web/mensaje", json={"texto": "hola"}, headers=h)
        assert r.status_code == 503 and "no está disponible" in r.json()["detail"] and "127.0.0.1" not in r.text, "no se filtra dónde está el servicio"
    finally:
        os.environ.pop("ASISTENTE_WEB_URL", None)
    assert client.get("/api/asistente/web/estado").status_code == 401, "sin sesión no hay estado"


def test_un_mensaje_web_propone_y_solo_confirmar_ejecuta_y_una_sola_vez(client):
    uid, h = _cuenta(client, "web_flujo")
    otro, ho = _cuenta(client, "web_flujo_2")
    _con_fondos(client, h)
    srv = _montar(client, {uid: _propuesta_de_gasto(31)})
    assert client.get("/api/asistente/web/estado", headers=h).json() == {"disponible": True, "motivo": ""}
    r = client.post("/api/asistente/web/mensaje", json={"texto": "gasté 31 en el súper"}, headers=h)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["estado"] == "ok" and len(cuerpo["propuestas"]) == 1 and "31.00" in cuerpo["propuestas"][0]["resumen"], cuerpo
    assert not _gastos(client, h), "proponer no escribe nada"
    id_ = cuerpo["propuestas"][0]["id"]
    # Otra cuenta no puede confirmar ni cancelar la propuesta ajena (ni con su propia sesión ni adivinando el id)
    assert client.post(f"/api/asistente/web/confirmar/{id_}", headers=ho).json()["resultado"] == "no_disponible"
    assert client.post(f"/api/asistente/web/cancelar/{id_}", headers=ho).json()["resultado"] == "no_disponible"
    assert not _gastos(client, h) and not _gastos(client, ho)
    r = client.post(f"/api/asistente/web/confirmar/{id_}", headers=h)
    assert r.status_code == 200 and r.json()["resultado"] == "confirmada" and "Listo" in r.json()["texto"], r.text
    assert len(_gastos(client, h)) == 1 and not _gastos(client, ho)
    assert client.post(f"/api/asistente/web/confirmar/{id_}", headers=h).json()["resultado"] == "no_disponible", "una sola vez"
    assert len(_gastos(client, h)) == 1
    assert srv._tokens_escritura == {}, "el servicio no se queda con el token de escritura"
    # Cancelar
    srv.modelos[uid].guion[:] = _propuesta_de_gasto(44)
    nueva = client.post("/api/asistente/web/mensaje", json={"texto": "otro de 44"}, headers=h).json()["propuestas"][0]["id"]
    assert client.post(f"/api/asistente/web/cancelar/{nueva}", headers=h).json()["resultado"] == "cancelada"
    assert client.post(f"/api/asistente/web/confirmar/{nueva}", headers=h).json()["resultado"] == "no_disponible"
    assert len(_gastos(client, h)) == 1


def test_validaciones_de_las_rutas_web_y_un_token_del_asistente_no_las_usa(client):
    uid, h = _cuenta(client, "web_valida")
    _montar(client, {})
    for cuerpo in ({}, {"texto": ""}, {"texto": "x" * 2001}, {"texto": 5}):
        assert client.post("/api/asistente/web/mensaje", json=cuerpo, headers=h).status_code == 422, cuerpo
    for malo in ("a", "x" * 40, "../etc"):
        r = client.post(f"/api/asistente/web/confirmar/{malo}", headers=h)
        assert r.status_code in (400, 404), (malo, r.status_code)
    assert client.post("/api/asistente/web/mensaje", json={"texto": "hola"}).status_code == 401
    for escritura in (False, True):   # el asistente no se llama a sí mismo
        t = {"Authorization": f"Bearer {emitir_token(uid, escritura=escritura)}"}
        assert client.post("/api/asistente/web/mensaje", json={"texto": "hola"}, headers=t).status_code == 403
        assert client.post("/api/asistente/web/confirmar/abcdef12", headers=t).status_code == 403
        assert client.get("/api/asistente/web/estado", headers=t).status_code == 403


def test_el_interruptor_del_administrador_corta_web_telegram_y_los_tokens_ya_emitidos(client):
    from asistente import vinculos
    admin_id, ha = _cuenta(client, "web_admin", rol="admin")
    uid, h = _cuenta(client, "web_interruptor")
    srv = _montar(client, {uid: [Respuesta(texto="Listo, dime más.")] * 20})
    codigo, _ = vinculos.crear_codigo(uid)
    assert vinculos.consumir_codigo(codigo, 7300, "persona")[0] == vinculos.OK
    interno = ServicioHTTP(client, SECRETO_TELEGRAM)
    token_viejo = {"Authorization": f"Bearer {emitir_token(uid)}"}
    assert client.get("/api/resumen", headers=token_viejo).status_code == 200
    assert client.post("/api/asistente/web/mensaje", json={"texto": "hola"}, headers=h).status_code == 200
    # Una cuenta normal no maneja el interruptor
    assert client.patch(f"/admin/users/{uid}/asistente", json={"activo": False}, headers=h).status_code == 403
    assert client.patch("/admin/users/999999/asistente", json={"activo": False}, headers=ha).status_code == 404
    r = client.patch(f"/admin/users/{uid}/asistente", json={"activo": False}, headers=ha)
    assert r.status_code == 200 and r.json()["asistente_activo"] is False, r.text
    # Web
    assert client.get("/api/asistente/web/estado", headers=h).json() == {"disponible": False, "motivo": "desactivado"}
    assert client.post("/api/asistente/web/mensaje", json={"texto": "hola"}, headers=h).status_code == 403
    assert client.post("/api/asistente/web/confirmar/abcdef12", headers=h).status_code == 403
    # Tokens: no se emiten y el que ya existía deja de valer en ese momento
    try:
        emitir_token(uid)
    except AsistenteDesactivado:
        pass
    else:
        raise AssertionError("no debe emitirse un token del asistente")
    assert client.get("/api/resumen", headers=token_viejo).status_code == 403
    assert client.get("/api/resumen", headers=h).status_code == 200, "la app normal sigue funcionando"
    # Telegram: el chat sigue vinculado pero no hay token, ni cupo, y el chat lo sabe
    assert interno.usuario_de_chat(7300) == {"id": uid, "username": "web_interruptor", "asistente": False}
    for operacion in (lambda: interno.token(7300), lambda: interno.reservar_cupo(7300, uid)):
        try:
            operacion()
        except Exception as error:
            assert type(error).__name__ == "AsistenteDesactivado", error
        else:
            raise AssertionError("debe negarse")
    # Volver a encenderlo: todo funciona sin escanear otra vez
    assert client.patch(f"/admin/users/{uid}/asistente", json={"activo": True}, headers=ha).status_code == 200
    assert client.post("/api/asistente/web/mensaje", json={"texto": "hola"}, headers=h).status_code == 200
    assert interno.usuario_de_chat(7300)["asistente"] is True and interno.token(7300)
    # La lista del panel de administración lo muestra
    assert [u["asistente_activo"] for u in master_db.list_users_with_metrics() if u["id"] == uid] == [1]


def test_nueva_conversacion_y_apagar_el_asistente_cancelan_las_propuestas_pendientes(client):
    uid, h = _cuenta(client, "web_olvida")
    admin_id, ha = _cuenta(client, "web_olvida_admin", rol="admin")
    _con_fondos(client, h)
    srv = _montar(client, {uid: _propuesta_de_gasto(21) + _propuesta_de_gasto(22)})
    id_ = client.post("/api/asistente/web/mensaje", json={"texto": "uno de 21"}, headers=h).json()["propuestas"][0]["id"]
    assert client.post("/api/asistente/web/olvidar", headers=h).status_code == 200
    assert client.post(f"/api/asistente/web/confirmar/{id_}", headers=h).json()["resultado"] == "no_disponible", "«nueva conversación» cancela lo pendiente"
    nueva = client.post("/api/asistente/web/mensaje", json={"texto": "uno de 22"}, headers=h).json()["propuestas"][0]["id"]
    assert client.patch(f"/admin/users/{uid}/asistente", json={"activo": False}, headers=ha).status_code == 200
    assert client.patch(f"/admin/users/{uid}/asistente", json={"activo": True}, headers=ha).status_code == 200
    assert client.post(f"/api/asistente/web/confirmar/{nueva}", headers=h).json()["resultado"] == "no_disponible", "apagar y encender no resucita propuestas"
    assert not _gastos(client, h)


def test_el_cupo_diario_es_el_mismo_para_web_y_telegram_y_no_lo_gasta_lo_rechazado(client):
    from asistente.cupo import Cupo
    uid, h = _cuenta(client, "web_cupo")
    _montar(client, {uid: [Respuesta(texto="Dime más.")] * 50})
    anterior = os.environ.get("ASISTENTE_MAX_MENSAJES_DIA")
    os.environ["ASISTENTE_MAX_MENSAJES_DIA"] = "3"
    try:
        Cupo().reservar(uid)   # un mensaje «de Telegram» ya gastó uno
        for _ in range(2):
            assert client.post("/api/asistente/web/mensaje", json={"texto": "hola"}, headers=h).json()["estado"] == "ok"
        r = client.post("/api/asistente/web/mensaje", json={"texto": "uno más"}, headers=h).json()
        assert r["estado"] == "dia" and "límite de mensajes de hoy" in r["texto"] and r["propuestas"] == []
        assert Cupo().uso(uid)["mensajes"] == 3, "lo rechazado no cuenta"
    finally:
        os.environ.pop("ASISTENTE_MAX_MENSAJES_DIA", None) if anterior is None else os.environ.__setitem__("ASISTENTE_MAX_MENSAJES_DIA", anterior)


def test_una_cuenta_no_tiene_dos_peticiones_a_la_vez_ni_el_conjunto_pasa_del_tope(client):
    uid, h = _cuenta(client, "web_en_curso")
    _montar(client, {})
    suelta, entraron = threading.Event(), []

    def lenta(ruta, cuerpo=None, segundos=0):
        entraron.append(1)
        suelta.wait(10)
        return {"estado": "ok", "texto": "listo", "propuestas": []}

    asistente_web._llamar = lenta
    resultados = []
    primero = threading.Thread(target=lambda: resultados.append(client.post("/api/asistente/web/mensaje", json={"texto": "hola"}, headers=h).status_code))
    primero.start()
    try:
        for _ in range(100):
            if entraron:
                break
            time.sleep(0.05)
        assert client.post("/api/asistente/web/mensaje", json={"texto": "otra pestaña"}, headers=h).status_code == 429, "una sola petición a la vez por cuenta"
        # El tope global: con el conjunto lleno ninguna cuenta nueva entra (no se agota el pool de hilos de la app)
        asistente_web._en_curso.update({10_000 + i: 1 for i in range(asistente_web.MAX_EN_CURSO_GLOBAL)})
        otro, ho = _cuenta(client, "web_en_curso_2")
        assert client.post("/api/asistente/web/mensaje", json={"texto": "hola"}, headers=ho).status_code == 429
        for i in range(asistente_web.MAX_EN_CURSO_GLOBAL):
            asistente_web._en_curso.pop(10_000 + i, None)
    finally:
        suelta.set()
        primero.join(timeout=15)
    assert resultados == [200]
    assert asistente_web._en_curso == {}, "el contador se libera"


def test_la_respuesta_del_servicio_no_es_de_fiar_y_se_acota_antes_de_llegar_al_navegador(client):
    uid, h = _cuenta(client, "web_respuesta")
    _montar(client, {})
    respuestas = [
        {"estado": "ok", "texto": "x" * 50_000, "propuestas": [{"id": "abcdef123456", "resumen": "r" * 5000}, {"id": "../../etc", "resumen": "malo"},
                                                                {"id": 5, "resumen": "malo"}, "malo", {"id": "abcdef123457"}] + [{"id": "abcdef12345" + str(i), "resumen": "ok"} for i in range(20)]},
        {"estado": "<script>", "texto": ["no es texto"], "propuestas": "no es lista"},
    ]
    for datos in respuestas:
        asistente_web._llamar = lambda ruta, cuerpo=None, segundos=0, d=datos: d
        r = client.post("/api/asistente/web/mensaje", json={"texto": "hola"}, headers=h)
        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert len(cuerpo["texto"]) <= asistente_web.MAX_TEXTO_RESPUESTA and len(cuerpo["propuestas"]) <= 5
        assert all(len(p["resumen"]) <= asistente_web.MAX_RESUMEN and asistente_web._ID_PROPUESTA.match(p["id"]) for p in cuerpo["propuestas"]), cuerpo
        assert cuerpo["estado"] in ("ok", "error")
    assert [p["id"] for p in client.post("/api/asistente/web/mensaje", json={"texto": "x"}, headers=h).json()["propuestas"]] == []
    # Una respuesta que no es un objeto JSON (o que lo anida sin fin) es «no disponible», no un 500
    for crudo in (b"[1, 2]", b"[" * 100_000, b"no es json", b'{"a": '):
        class Falsa:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def read(s, n=-1):
                return crudo
        asistente_web._llamar = _LLAMAR_REAL
        os.environ["ASISTENTE_WEB_URL"] = "http://x.interno:1"
        original = asistente_web._abrir
        asistente_web._abrir = lambda peticion, timeout=0: Falsa()
        try:
            r = client.post("/api/asistente/web/mensaje", json={"texto": "hola"}, headers=h)
            assert r.status_code == 503, (crudo[:10], r.status_code)
        finally:
            asistente_web._abrir = original


def test_una_redireccion_del_servicio_no_se_sigue(client):
    import urllib.error
    import urllib.request
    destino = []

    class Falso(urllib.request.HTTPHandler):
        def http_open(self, req):
            destino.append(req.full_url)
            if len(destino) == 1:
                import io
                raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": "http://169.254.169.254/latest/meta-data"}, io.BytesIO(b""))
            raise AssertionError("se siguió la redirección")

    os.environ["ASISTENTE_WEB_URL"] = "http://x.interno:1"
    abridor = urllib.request.build_opener(asistente_web._SinRedireccion, Falso)
    original = asistente_web._abrir
    asistente_web._abrir, asistente_web._llamar = abridor.open, _LLAMAR_REAL
    try:
        try:
            asistente_web._llamar("/health", None, 1)
        except asistente_web.ServicioNoDisponible:
            pass
        else:
            raise AssertionError("una redirección es «no disponible»")
        assert destino == ["http://x.interno:1/health"], destino
    finally:
        asistente_web._abrir = original
        os.environ.pop("ASISTENTE_WEB_URL", None)


def test_el_servicio_del_asistente_web_valida_secreto_ruta_y_cuerpo_y_no_filtra_errores(client):
    uid, _ = _cuenta(client, "web_servidor")
    srv = _montar(client, {})
    ok = {"x-asistente-secreto": SECRETO}
    cuerpo = {"user_id": uid, "username": "u", "pase": pase.crear(uid), "texto": "hola"}
    assert srv.manejar("GET", "/health", {}, None) == (200, {"ok": True}), "la salud no pide secreto"
    for cab in ({}, {"x-asistente-secreto": "otro" * 10}, {"x-asistente-secreto": ""}):
        assert srv.manejar("POST", "/web/mensaje", cab, cuerpo)[0] == 401, cab
    assert srv.manejar("POST", "/web/otra", ok, cuerpo)[0] == 404 and srv.manejar("GET", "/web/mensaje", ok, None)[0] == 404
    for malo in ({**cuerpo, "user_id": "1"}, {**cuerpo, "user_id": True}, {**cuerpo, "user_id": 0}, {**cuerpo, "pase": None}, [], "x"):
        assert srv.manejar("POST", "/web/mensaje", ok, malo)[0] == 400, malo
    for texto in ("", "  ", 5, None, "x" * 2001):
        assert srv.manejar("POST", "/web/mensaje", ok, {**cuerpo, "texto": texto})[0] == 400, texto
    assert srv.manejar("POST", "/web/confirmar", ok, {**cuerpo, "id": "a", "token_escritura": "t"})[0] == 400
    assert srv.manejar("POST", "/web/confirmar", ok, {**cuerpo, "id": "abcdef12", "token_escritura": ""})[0] == 400
    assert srv.manejar("POST", "/web/cancelar", ok, {**cuerpo, "id": "../x"})[0] == 400
    assert srv.manejar("POST", "/web/olvidar", ok, {"user_id": uid})[0] == 200


def test_el_servidor_http_real_limita_el_cuerpo_y_atiende_en_paralelo_con_tope(client):
    import socket
    from asistente import servidor_web
    from asistente.servidor_web import _ServidorAcotado
    uid, _ = _cuenta(client, "web_socket")
    srv = _montar(client, {uid: [Respuesta(texto="Dime más.")] * 5})
    libres = threading.BoundedSemaphore(1)
    http = _ServidorAcotado(("127.0.0.1", 0), _manejador(srv, libres), max_conexiones=3)
    hilo = threading.Thread(target=http.serve_forever, daemon=True)
    hilo.start()
    base = f"http://127.0.0.1:{http.server_address[1]}"

    def pedir(ruta, datos=None, cabeceras=None, metodo=None):
        peticion = urllib.request.Request(base + ruta, data=datos, method=metodo, headers=cabeceras or {})
        try:
            with urllib.request.urlopen(peticion, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    try:
        assert pedir("/health") == (200, {"ok": True})
        cab = {"X-Asistente-Secreto": SECRETO, "Content-Type": "application/json"}
        cuerpo = json.dumps({"user_id": uid, "username": "u", "pase": pase.crear(uid), "texto": "hola"}).encode()
        codigo, datos = pedir("/web/mensaje", cuerpo, cab)
        assert codigo == 200 and datos["estado"] == "ok" and datos["texto"] == "Dime más.", (codigo, datos)
        assert pedir("/web/mensaje", b"x" * 20_000, cab)[0] == 413
        assert pedir("/web/mensaje", b"x" * 20_000, {"Content-Type": "application/json"})[0] == 401, "sin el secreto no se llega ni a mirar el tamaño"
        assert pedir("/web/mensaje", b"{no es json", cab)[0] == 400
        assert pedir("/web/mensaje", cuerpo, {"Content-Type": "application/json"})[0] == 401
        # Un cuerpo que se manda a gotas se corta por el plazo total, no retiene el hilo
        anterior = servidor_web.PLAZO_DE_LECTURA_SEG
        servidor_web.PLAZO_DE_LECTURA_SEG = 0.5
        try:
            lento = socket.create_connection(("127.0.0.1", http.server_address[1]), timeout=10)
            lento.sendall(b"POST /web/mensaje HTTP/1.0\r\nX-Asistente-Secreto: " + SECRETO.encode() + b"\r\nContent-Length: 200\r\n\r\nabc")
            respuesta = lento.recv(4096)
            lento.close()
            assert b" 408 " in respuesta.split(b"\r\n")[0], respuesta[:60]
        finally:
            servidor_web.PLAZO_DE_LECTURA_SEG = anterior
        # Más conexiones abiertas que el tope: las que sobran se cierran sin atenderse, y al cerrarse las otras vuelve a atender
        ocupadas = [socket.create_connection(("127.0.0.1", http.server_address[1]), timeout=5) for _ in range(3)]
        time.sleep(0.3)
        extra = socket.create_connection(("127.0.0.1", http.server_address[1]), timeout=5)
        extra.sendall(b"GET /health HTTP/1.0\r\n\r\n")
        assert extra.recv(4096) == b"", "sin hilo libre la conexión se cierra"
        extra.close()
        [s.close() for s in ocupadas]
        time.sleep(0.5)
        assert pedir("/health")[0] == 200
        # Con el tope de simultáneas lleno, lo demás recibe 503 y no se apila
        libres.acquire()
        assert pedir("/health")[0] == 503
        libres.release()
        assert pedir("/health")[0] == 200
    finally:
        http.shutdown()
        http.server_close()


def test_las_imagenes_y_el_compose_aislan_el_servicio_web_igual_que_el_bot(client):
    compose = open(os.path.join(RAIZ, "docker-compose.yml"), encoding="utf-8").read()
    asistente = compose[compose.index("  finanzas-asistente:"):compose.index("# Redes separadas")]
    assert 'profiles: ["asistente"]' in asistente and "asistente.servidor_web" in asistente, "servicio aparte, mismo perfil"
    assert "env_file" not in asistente and "volumes:" not in asistente and "ports:" not in asistente, "sin .env completo, sin datos y sin puertos publicados"
    assert "read_only: true" in asistente and "cap_drop" in asistente and "no-new-privileges" in asistente
    assert "JWT_SECRET_KEY: relleno" in asistente and "TELEGRAM_BOT_TOKEN" not in asistente, "sin la clave JWT real ni el token de Telegram"
    bot = compose[compose.index("  finanzas-bot:"):compose.index("  finanzas-asistente:")]
    assert "asistente.servidor_web" not in bot, "el bot y el servicio web no comparten proceso"
    # Secretos y redes separados: ninguno de los dos puede usar el canal del otro
    assert "ASISTENTE_SECRETO_INTERNO" not in asistente and "ASISTENTE_WEB_SECRETO" in asistente, "el servicio web solo tiene el secreto web"
    assert "ASISTENTE_WEB_SECRETO" not in bot and "ASISTENTE_SECRETO_INTERNO" in bot, "el bot solo tiene el secreto de Telegram"
    assert "asistente_web" in asistente and "asistente_telegram" not in asistente and "asistente_telegram" in bot and "asistente_web" not in bot, "redes separadas"
    app_ = compose[:compose.index("  finanzas-bot:")]
    assert "asistente_web" in app_ and "asistente_telegram" in app_, "la app está en las dos redes"
    assert "ASISTENTE_WEB_URL" in compose[:compose.index("  finanzas-bot:")], "la app sabe dónde está el servicio web"


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import ejecutar, verificar_cobertura

    with TestClient(app) as client:
        registradas = [(f, (lambda f=f: f(client))) for f in (
            test_el_pase_firma_la_cuenta_vence_y_no_se_puede_alterar,
            test_la_api_interna_web_exige_secreto_y_pase_y_solo_da_lectura,
            test_sin_servicio_configurado_o_caido_la_burbuja_no_existe_y_los_mensajes_dicen_no_disponible,
            test_un_mensaje_web_propone_y_solo_confirmar_ejecuta_y_una_sola_vez,
            test_validaciones_de_las_rutas_web_y_un_token_del_asistente_no_las_usa,
            test_el_interruptor_del_administrador_corta_web_telegram_y_los_tokens_ya_emitidos,
            test_nueva_conversacion_y_apagar_el_asistente_cancelan_las_propuestas_pendientes,
            test_el_cupo_diario_es_el_mismo_para_web_y_telegram_y_no_lo_gasta_lo_rechazado,
            test_una_cuenta_no_tiene_dos_peticiones_a_la_vez_ni_el_conjunto_pasa_del_tope,
            test_la_respuesta_del_servicio_no_es_de_fiar_y_se_acota_antes_de_llegar_al_navegador,
            test_una_redireccion_del_servicio_no_se_sigue,
            test_el_servicio_del_asistente_web_valida_secreto_ruta_y_cuerpo_y_no_filtra_errores,
            test_el_servidor_http_real_limita_el_cuerpo_y_atiende_en_paralelo_con_tope,
            test_las_imagenes_y_el_compose_aislan_el_servicio_web_igual_que_el_bot,
        )]
        pruebas = verificar_cobertura(sys.modules[__name__], registradas)
        codigo = ejecutar(pruebas, "test_asistente_web")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)
