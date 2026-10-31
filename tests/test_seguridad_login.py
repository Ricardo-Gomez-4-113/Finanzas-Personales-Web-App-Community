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
    ]
    with TestClient(app):  # arranque de la app: crea la base maestra antes de las pruebas
        _crear_usuario("seg_ini")
        pruebas = verificar_cobertura(sys.modules[__name__], registradas)
        codigo = ejecutar(pruebas, "test_seguridad_login")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)
