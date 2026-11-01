"""tests/test_seguridad_login.py - Control de fuerza bruta del login: ráfagas concurrentes y evicción de contadores.

Requiere fastapi + httpx (se ejecuta en Docker, ver tests/run_tests.sh).
Copia el código a un directorio temporal: nunca toca 'data/' del proyecto.
"""

import os
import shutil
import sys
import tempfile
import threading
import time
from contextlib import contextmanager

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
    ignore=shutil.ignore_patterns("data", ".git", "tests", "__pycache__", "*.db", "backups", "venv", ".venv", "node_modules"),
)
os.chdir(TMP)
sys.path.insert(0, TMP)
os.environ.setdefault("JWT_SECRET_KEY", "k" * 48)
os.environ["DEFAULT_ADMIN_PASSWORD"] = "AdminTest#12345"

from fastapi.testclient import TestClient  # noqa: E402

import master_db  # noqa: E402
import routers.auth as rauth  # noqa: E402
import security  # noqa: E402
from main import app  # noqa: E402

PASS = "Passw0rd#Test1"
CLAVE_MAL = "Equivocada#Clave9"
LIMITE = 5
LIMITE_IP = 20  # intentos por IP entre todas las cuentas en la ventana


def _crear_usuario(nombre):
    return master_db.create_user_entry(nombre, security.hash_password(PASS), email=f"{nombre}@test.local")


def _intento(clave):
    """Comprueba el límite y anota un intento, como lo hace el login antes de verificar la contraseña."""
    security.reservar_intento_login(clave)


def _vaciar_contadores():
    security._intentos_fallidos_login.clear()


@contextmanager
def _parchar(modulo, **reemplazos):
    """Sustituye funciones de un módulo y las restaura al salir, incluso si la prueba falla."""
    originales = {nombre: getattr(modulo, nombre) for nombre in reemplazos}
    for nombre, valor in reemplazos.items():
        setattr(modulo, nombre, valor)
    try:
        yield
    finally:
        for nombre, valor in originales.items():
            setattr(modulo, nombre, valor)


def test_rafaga_concurrente_no_evalua_mas_que_el_limite():
    """Con latencia de bcrypt, 30 peticiones simultáneas no pueden evaluar más de LIMITE contraseñas."""
    _crear_usuario("seg_rafaga")
    _vaciar_contadores()
    evaluadas = []
    lock = threading.Lock()
    original = rauth.verify_password

    def verificar_lento(clave, hash_guardado):
        with lock:
            evaluadas.append(clave)
        time.sleep(0.05)  # ventana en la que, sin reserva previa, otras peticiones ya pasaron la comprobación
        return original(clave, hash_guardado)

    barrera = threading.Barrier(30)
    codigos = []

    def intento():
        cliente = TestClient(app)
        barrera.wait()
        r = cliente.post("/auth/login", json={"username": "seg_rafaga", "password": CLAVE_MAL})
        with lock:
            codigos.append(r.status_code)

    with _parchar(rauth, verify_password=verificar_lento):
        hilos = [threading.Thread(target=intento) for _ in range(30)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

    assert len(evaluadas) == LIMITE, f"se evaluaron {len(evaluadas)} contraseñas con un límite de {LIMITE}"
    assert codigos.count(401) == LIMITE and codigos.count(429) == 30 - LIMITE, codigos


def test_flujo_de_claves_nuevas_no_libera_una_cuenta_bloqueada():
    """Un atacante que llena el registro de contadores no debe reiniciar el contador de la cuenta ya bloqueada."""
    _vaciar_contadores()
    victima = "127.0.0.1:seg_victima"
    for _ in range(LIMITE):
        _intento(victima)
    try:
        _intento(victima)
        assert False, "la víctima debió quedar bloqueada tras el límite"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 429, exc

    for i in range(security._MAX_CLAVES_LOGIN + 500):
        try:
            _intento(f"10.9.{i // 250}.{i % 250}:usuario{i}")
        except Exception as exc:  # tabla saturada por claves bloqueadas: se rechaza la clave nueva, no la víctima
            assert getattr(exc, "status_code", None) == 429, exc

    assert len(security._intentos_fallidos_login) <= security._MAX_CLAVES_LOGIN, "la memoria del control debe seguir acotada"
    try:
        _intento(victima)
        assert False, "el flujo de claves nuevas liberó la cuenta bloqueada"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 429, exc
    _vaciar_contadores()


def test_bloqueo_se_mantiene_y_un_acierto_limpia_el_contador():
    """Tras LIMITE fallos, incluso la contraseña correcta espera; un acierto antes del límite borra los fallos."""
    _crear_usuario("seg_secuencia")
    _vaciar_contadores()
    cliente = TestClient(app)
    codigos = [cliente.post("/auth/login", json={"username": "seg_secuencia", "password": CLAVE_MAL}).status_code for _ in range(LIMITE + 1)]
    assert codigos == [401] * LIMITE + [429], codigos
    assert cliente.post("/auth/login", json={"username": "seg_secuencia", "password": PASS}).status_code == 429

    _vaciar_contadores()
    for _ in range(LIMITE - 1):
        assert cliente.post("/auth/login", json={"username": "seg_secuencia", "password": CLAVE_MAL}).status_code == 401
    assert cliente.post("/auth/login", json={"username": "seg_secuencia", "password": PASS}).status_code == 200
    for _ in range(LIMITE - 1):
        assert cliente.post("/auth/login", json={"username": "seg_secuencia", "password": CLAVE_MAL}).status_code == 401
    _vaciar_contadores()


def test_spraying_desde_una_ip_se_frena_entre_cuentas():
    """Una IP que prueba una contraseña común contra muchas cuentas recibe 429 antes de agotar la lista."""
    _vaciar_contadores()
    hash_comun = security.hash_password(PASS)
    for i in range(40):
        master_db.create_user_entry(f"seg_spray{i:02d}", hash_comun, email=f"spray{i}@test.local")
    evaluadas = []
    original = rauth.verify_password

    def verificar_rapido(clave, hash_guardado):
        evaluadas.append(clave)
        return original(clave, hash_guardado)

    cliente = TestClient(app)
    with _parchar(rauth, verify_password=verificar_rapido):
        codigos = [cliente.post("/auth/login", json={"username": f"seg_spray{i:02d}", "password": CLAVE_MAL}).status_code for i in range(40)]
    assert 429 in codigos, f"40 cuentas probadas desde una IP sin ningún 429: {codigos}"
    assert len(evaluadas) <= LIMITE_IP, f"{len(evaluadas)} contraseñas evaluadas desde una IP"
    _vaciar_contadores()


def test_aciertos_no_cuentan_para_el_tope_de_la_ip():
    """Una IP compartida con muchas cuentas válidas no se bloquea por sus inicios de sesión correctos."""
    _vaciar_contadores()
    hash_comun = security.hash_password(PASS)
    for i in range(LIMITE_IP + 5):
        master_db.create_user_entry(f"seg_ok{i:02d}", hash_comun, email=f"ok{i}@test.local")
    original = rauth.verify_password
    cliente = TestClient(app, client=("127.0.0.1", 50003), headers={"CF-Connecting-IP": "198.51.100.30"})
    with _parchar(rauth, verify_password=lambda clave, h: clave == PASS or original(clave, h)):
        codigos = [cliente.post("/auth/login", json={"username": f"seg_ok{i:02d}", "password": PASS}).status_code for i in range(LIMITE_IP + 5)]
    assert codigos == [200] * (LIMITE_IP + 5), codigos
    _vaciar_contadores()


def test_tope_de_una_ip_no_afecta_a_otra():
    """El tope por IP es por origen: una IP bloqueada no impide entrar desde otra."""
    _crear_usuario("seg_otra_ip")
    _vaciar_contadores()
    atacante = TestClient(app, client=("127.0.0.1", 50001), headers={"CF-Connecting-IP": "198.51.100.10"})
    for i in range(LIMITE_IP + 2):
        atacante.post("/auth/login", json={"username": f"seg_nadie{i}", "password": CLAVE_MAL})
    assert atacante.post("/auth/login", json={"username": "seg_otra_ip", "password": PASS}).status_code == 429
    legitimo = TestClient(app, client=("127.0.0.1", 50002), headers={"CF-Connecting-IP": "198.51.100.20"})
    assert legitimo.post("/auth/login", json={"username": "seg_otra_ip", "password": PASS}).status_code == 200
    _vaciar_contadores()


def _id_de(usuario):
    return master_db.get_user_by_username(usuario)["id"]


def _codigos_activos(usuario):
    conn = master_db.get_master_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM password_resets WHERE user_id = ?", (_id_de(usuario),)).fetchone()[0]
    finally:
        conn.close()


def test_codigo_de_recuperacion_no_se_adivina_desde_varias_ips():
    """Varias IPs no multiplican los intentos contra una misma cuenta: el tope es por cuenta."""
    _crear_usuario("seg_reset_ips")
    _vaciar_contadores()
    master_db.create_password_reset_code(_id_de("seg_reset_ips"))
    verificaciones = []
    original = master_db.verify_reset_code

    def contar(identifier, code):
        verificaciones.append(identifier)
        return original(identifier, code)

    cuerpo = {"identifier": "seg_reset_ips", "code": "000000", "new_password": "Nueva#Clave123"}
    cliente = TestClient(app, client=("127.0.0.1", 50020))  # origen de proxy confiable: la IP viene de CF-Connecting-IP
    with _parchar(master_db, verify_reset_code=contar):
        respuestas = [cliente.post("/auth/reset-password", json=cuerpo, headers={"CF-Connecting-IP": f"198.51.100.{i}"}) for i in range(1, 13)]
    assert len(verificaciones) <= LIMITE, f"{len(verificaciones)} verificaciones contra una cuenta desde 12 IPs"
    assert all(r.status_code == 400 for r in respuestas), [r.status_code for r in respuestas]
    assert {r.json()["detail"] for r in respuestas} == {master_db.MENSAJE_CODIGO_INVALIDO}, "la respuesta no debe revelar el tope"
    _vaciar_contadores()


def test_alias_de_la_cuenta_comparte_el_mismo_tope_de_recuperacion():
    """El correo y el nombre de usuario de una misma cuenta comparten el tope: no suman dos límites."""
    _crear_usuario("seg_reset_alias")
    _vaciar_contadores()
    master_db.create_password_reset_code(_id_de("seg_reset_alias"))
    verificaciones = []
    original = master_db.verify_reset_code

    def contar(identifier, code):
        verificaciones.append(identifier)
        return original(identifier, code)

    cliente = TestClient(app, client=("127.0.0.1", 50010), headers={"CF-Connecting-IP": "198.51.100.90"})
    with _parchar(master_db, verify_reset_code=contar):
        for _ in range(LIMITE):
            cliente.post("/auth/reset-password", json={"identifier": "seg_reset_alias", "code": "000000", "new_password": "Nueva#Clave123"})
        cliente.post("/auth/reset-password", json={"identifier": "seg_reset_alias@test.local", "code": "000000", "new_password": "Nueva#Clave123"})
    assert len(verificaciones) <= LIMITE, f"{len(verificaciones)} verificaciones con el alias"
    _vaciar_contadores()


def test_solicitudes_de_codigo_no_inundan_el_correo_de_la_cuenta():
    """Desde muchas IPs no se emiten más códigos que el tope por cuenta; la respuesta no cambia."""
    import email_service
    _crear_usuario("seg_forgot_ips")
    _vaciar_contadores()
    cuerpo = {"identifier": "seg_forgot_ips"}
    cliente = TestClient(app, client=("127.0.0.1", 50021))  # origen de proxy confiable
    with _parchar(email_service, esta_configurado_servicio_correo=lambda: True, enviar_correo_codigo_recuperacion=lambda *a, **k: None):
        respuestas = [cliente.post("/auth/forgot-password", json=cuerpo, headers={"CF-Connecting-IP": f"198.51.100.{100 + i}"}) for i in range(8)]
    assert _codigos_activos("seg_forgot_ips") <= LIMITE, f"{_codigos_activos('seg_forgot_ips')} códigos emitidos desde 8 IPs"
    assert all(r.status_code == 200 for r in respuestas), [r.status_code for r in respuestas]
    assert len({r.json()["mensaje"] for r in respuestas}) == 1, "la respuesta no debe cambiar al alcanzar el tope"
    _vaciar_contadores()


def test_login_rechaza_campos_fuera_de_limite_sin_guardar_nada():
    """Un usuario o una contraseña mayores que los límites del alta se rechazan antes de tocar el contador."""
    _vaciar_contadores()
    cliente = TestClient(app, client=("127.0.0.1", 50030))
    antes = len(security._intentos_fallidos_login)
    # 5 MB: lo corta el tope del cuerpo antes de validar; 100 KB: entra en el tope y lo rechaza la validación
    r = cliente.post("/auth/login", json={"username": "u" * 5_000_000, "password": "x"})
    assert r.status_code == 413, r.status_code
    r = cliente.post("/auth/login", json={"username": "u" * 100_000, "password": "x"})
    assert r.status_code == 422, r.status_code
    assert len(r.content) < 10_000, f"el 422 repite el valor enviado: {len(r.content)} bytes"
    r = cliente.post("/auth/login", json={"username": "seg_largo", "password": "x" * 73})
    assert r.status_code == 422, r.status_code
    assert len(security._intentos_fallidos_login) == antes, "un campo fuera de límite no debe crear entradas en el contador"
    assert max((len(k) for k in security._intentos_fallidos_login), default=0) < 1000
    _vaciar_contadores()


def _servidor_real():
    """uvicorn en un hilo, en loopback: los tiempos de respuesta reales incluyen las tareas de fondo."""
    import socket
    import urllib.request
    import uvicorn
    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        puerto = s_.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=puerto, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{puerto}/health", timeout=1)
            return srv, puerto
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("el servidor de la prueba no arrancó")


def test_tiempo_de_la_solicitud_de_codigo_no_revela_la_cuenta():
    """Con una cuenta que tiene correo, la respuesta no tarda más que con una cuenta inexistente."""
    import email_service
    import json
    import statistics
    import urllib.request
    _crear_usuario("seg_tiempo")
    _vaciar_contadores()
    original_crear = master_db.create_password_reset_code

    def crear_lento(user_id):
        time.sleep(0.3)  # coste de escritura: en la ruta síncrona se vería como diferencia de tiempo
        return original_crear(user_id)

    def pedir(identificador, ip, puerto):
        body = json.dumps({"identifier": identificador}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{puerto}/auth/forgot-password", data=body,
                                     headers={"Content-Type": "application/json", "CF-Connecting-IP": ip})
        inicio = time.perf_counter()
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        return time.perf_counter() - inicio

    srv, puerto = _servidor_real()
    try:
        with _parchar(email_service, esta_configurado_servicio_correo=lambda: True, enviar_correo_codigo_recuperacion=lambda *a, **k: None), \
                _parchar(master_db, create_password_reset_code=crear_lento):
            con, sin = [], []
            for i in range(5):
                _vaciar_contadores()
                con.append(pedir("seg_tiempo", f"198.51.100.{i + 1}", puerto))
                _vaciar_contadores()
                sin.append(pedir(f"seg_nadie{i}", f"198.51.101.{i + 1}", puerto))
    finally:
        srv.should_exit = True
    mediana_con, mediana_sin = statistics.median(con), statistics.median(sin)
    assert abs(mediana_con - mediana_sin) < 0.1, f"con cuenta {mediana_con:.3f}s, sin cuenta {mediana_sin:.3f}s"
    _vaciar_contadores()


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import ejecutar, verificar_cobertura

    registradas = [
        (test_rafaga_concurrente_no_evalua_mas_que_el_limite, test_rafaga_concurrente_no_evalua_mas_que_el_limite),
        (test_flujo_de_claves_nuevas_no_libera_una_cuenta_bloqueada, test_flujo_de_claves_nuevas_no_libera_una_cuenta_bloqueada),
        (test_bloqueo_se_mantiene_y_un_acierto_limpia_el_contador, test_bloqueo_se_mantiene_y_un_acierto_limpia_el_contador),
        (test_spraying_desde_una_ip_se_frena_entre_cuentas, test_spraying_desde_una_ip_se_frena_entre_cuentas),
        (test_tope_de_una_ip_no_afecta_a_otra, test_tope_de_una_ip_no_afecta_a_otra),
        (test_aciertos_no_cuentan_para_el_tope_de_la_ip, test_aciertos_no_cuentan_para_el_tope_de_la_ip),
        (test_codigo_de_recuperacion_no_se_adivina_desde_varias_ips, test_codigo_de_recuperacion_no_se_adivina_desde_varias_ips),
        (test_alias_de_la_cuenta_comparte_el_mismo_tope_de_recuperacion, test_alias_de_la_cuenta_comparte_el_mismo_tope_de_recuperacion),
        (test_solicitudes_de_codigo_no_inundan_el_correo_de_la_cuenta, test_solicitudes_de_codigo_no_inundan_el_correo_de_la_cuenta),
        (test_login_rechaza_campos_fuera_de_limite_sin_guardar_nada, test_login_rechaza_campos_fuera_de_limite_sin_guardar_nada),
        (test_tiempo_de_la_solicitud_de_codigo_no_revela_la_cuenta, test_tiempo_de_la_solicitud_de_codigo_no_revela_la_cuenta),
    ]
    with TestClient(app):  # arranque de la app: crea la base maestra antes de las pruebas
        _crear_usuario("seg_ini")
        pruebas = verificar_cobertura(sys.modules[__name__], registradas)
        codigo = ejecutar(pruebas, "test_seguridad_login")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)
