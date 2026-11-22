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
os.environ["IP_CABECERA_CLIENTE"] = "CF-Connecting-IP"   # estas pruebas simulan un despliegue detrás de Cloudflare

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
    security._fallos_por_cuenta.clear()


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


def test_la_ip_real_se_toma_de_cloudflare_solo_si_la_conexion_viene_de_un_proxy_de_confianza():
    """Detrás de cloudflared el origen que ve la app es la puerta de enlace de la red de Docker (172.17.0.1, 172.21.0.1...): si no se
    reconoce como proxy, todos los visitantes comparten una sola IP en los límites de intentos y uno solo bloquea a todos."""
    from types import SimpleNamespace as NS
    pedir = lambda directa, cf=None: NS(client=NS(host=directa), headers={"CF-Connecting-IP": cf} if cf else {})
    # Cualquier red de Docker (bridges 172.16.0.0/12) y el loopback valen por defecto
    for puerta in ("172.17.0.1", "172.21.0.1", "172.30.5.1", "127.0.0.1", "::1"):
        assert security.obtener_ip_cliente(pedir(puerta, "203.0.113.7")) == "203.0.113.7", puerta
    # Una conexión directa de internet no puede fingir una IP con la cabecera
    for ajena in ("203.0.113.9", "8.8.8.8", "100.64.0.5", "192.168.1.10", "172.15.0.1", "172.32.0.1"):
        assert security.obtener_ip_cliente(pedir(ajena, "1.2.3.4")) == ajena, ajena
    # Cabecera inválida o ausente: se usa la conexión directa
    assert security.obtener_ip_cliente(pedir("172.21.0.1", "no-es-ip")) == "172.21.0.1"
    assert security.obtener_ip_cliente(pedir("172.21.0.1")) == "172.21.0.1"
    # Dos visitantes distintos detrás del mismo proxy no comparten cubo
    assert security.obtener_ip_cliente(pedir("172.21.0.1", "198.51.100.1")) != security.obtener_ip_cliente(pedir("172.21.0.1", "198.51.100.2"))
    # La lista de TRUSTED_PROXY_IPS acepta direcciones y rangos, e ignora lo que no entiende
    redes = security.parsear_proxies("127.0.0.1, 10.0.0.0/8, basura, , 2001:db8::/32")
    assert security.es_proxy_confiable("10.9.8.7", redes) and security.es_proxy_confiable("2001:db8::1", redes)
    assert not security.es_proxy_confiable("172.21.0.1", redes) and not security.es_proxy_confiable("no-ip", redes)
    assert security.parsear_proxies("basura") == [] and not security.es_proxy_confiable("127.0.0.1", [])


def test_un_tope_por_cuenta_frena_las_pruebas_repartidas_entre_muchas_ips():
    """40 fallos en una hora a una cuenta, desde las IPs que sean, la cierran un rato; la contraseña correcta limpia el contador."""
    _crear_usuario("seg_por_cuenta")
    _vaciar_contadores()
    proxy = TestClient(app, client=("127.0.0.1", 50040))   # origen de proxy confiable: cada petición trae su propia IP
    intento = lambda i, clave: proxy.post("/auth/login", json={"username": "seg_por_cuenta", "password": clave}, headers={"CF-Connecting-IP": f"198.51.100.{i % 250 + 1}", "X-Prueba": str(i)})
    for i in range(security._MAX_FALLOS_CUENTA):
        assert intento(i, "incorrecta1").status_code == 401, i
    assert intento(99, PASS).status_code == 429, "con el tope alcanzado ni la clave correcta entra: seguiría la fuerza bruta"
    assert "esta cuenta" in intento(100, "otra").json()["detail"]
    # Otra cuenta no se ve afectada, y los nombres que no existen también cuentan (no se revela cuáles hay)
    _crear_usuario("seg_por_cuenta_b")
    assert proxy.post("/auth/login", json={"username": "seg_por_cuenta_b", "password": PASS}, headers={"CF-Connecting-IP": "203.0.113.50"}).status_code == 200
    for i in range(security._MAX_FALLOS_CUENTA):
        proxy.post("/auth/login", json={"username": "no_existe_nadie", "password": "x" * 9}, headers={"CF-Connecting-IP": f"198.51.101.{i % 250 + 1}"})
    assert proxy.post("/auth/login", json={"username": "no_existe_nadie", "password": "x" * 9}, headers={"CF-Connecting-IP": "203.0.113.51"}).status_code == 429
    _vaciar_contadores()
    # Una clave correcta limpia los fallos acumulados
    for i in range(10):
        intento(i, "incorrecta1")
    assert intento(300, PASS).status_code == 200
    assert security._fallos_por_cuenta.get("seg_por_cuenta") is None
    _vaciar_contadores()


def test_quien_ya_entro_desde_su_lugar_no_se_queda_fuera_cuando_otro_bloquea_su_cuenta_a_proposito():
    """El tope por cuenta cierra la puerta a quien llegue de un lugar nuevo, pero no a quien ya entró bien desde esa misma IP."""
    _crear_usuario("seg_conocida")
    _vaciar_contadores()
    casa = TestClient(app, client=("127.0.0.1", 50060), headers={"CF-Connecting-IP": "198.51.100.200"})
    assert casa.post("/auth/login", json={"username": "seg_conocida", "password": PASS}).status_code == 200   # la cuenta recuerda esta IP
    atacante = TestClient(app, client=("127.0.0.1", 50061))
    for i in range(security._MAX_FALLOS_CUENTA):
        assert atacante.post("/auth/login", json={"username": "seg_conocida", "password": "incorrecta1"}, headers={"CF-Connecting-IP": f"203.0.113.{i % 250 + 1}"}).status_code == 401
    assert security.cuenta_con_demasiados_fallos("seg_conocida")
    otro_lugar = {"CF-Connecting-IP": "198.51.100.201"}
    assert atacante.post("/auth/login", json={"username": "seg_conocida", "password": PASS}, headers=otro_lugar).status_code == 429   # lugar nuevo: cerrada
    assert casa.post("/auth/login", json={"username": "seg_conocida", "password": PASS}).status_code == 200                             # su lugar de siempre: entra
    assert not security.cuenta_con_demasiados_fallos("seg_conocida"), "entrar bien limpia el contador"
    # Desde su IP conocida una clave equivocada sigue siendo un fallo normal y no abre nada
    assert casa.post("/auth/login", json={"username": "seg_conocida", "password": "incorrecta1"}).status_code == 401
    # Solo se guardan las últimas 5 direcciones, y guardan su huella, no la IP
    for i in range(7):
        TestClient(app, client=("127.0.0.1", 50070 + i), headers={"CF-Connecting-IP": f"192.0.2.{i + 1}"}).post("/auth/login", json={"username": "seg_conocida", "password": PASS})
    conn = master_db.get_master_conn()
    try:
        filas = conn.execute("SELECT huella FROM ips_conocidas WHERE user_id = (SELECT id FROM users WHERE username = 'seg_conocida')").fetchall()
    finally:
        conn.close()
    assert len(filas) == master_db.IPS_CONOCIDAS_POR_CUENTA and all("192.0.2" not in f["huella"] and "198.51" not in f["huella"] for f in filas)
    _vaciar_contadores()


def test_la_ip_real_solo_se_lee_de_la_cabecera_elegida_y_sin_ella_no_se_cree_a_nadie():
    from starlette.datastructures import Headers
    from types import SimpleNamespace as NS
    pedir = lambda directa, *lineas: NS(client=NS(host=directa), headers=Headers(raw=[(k.lower().encode(), v.encode()) for k, v in lineas]))
    anterior = os.environ.get("IP_CABECERA_CLIENTE")
    try:
        # Sin IP_CABECERA_CLIENTE no se lee ninguna cabecera: un proxy que no borra la del visitante no le deja escoger su IP
        os.environ.pop("IP_CABECERA_CLIENTE", None)
        for cab in ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For"):
            assert security.obtener_ip_cliente(pedir("172.21.0.1", (cab, "203.0.113.7"))) == "172.21.0.1", cab
        os.environ["IP_CABECERA_CLIENTE"] = "X-Real-IP"
        assert security.obtener_ip_cliente(pedir("172.21.0.1", ("X-Real-IP", "203.0.113.7"))) == "203.0.113.7"
        assert security.obtener_ip_cliente(pedir("172.21.0.1", ("CF-Connecting-IP", "203.0.113.8"))) == "172.21.0.1", "una cabecera que no es la elegida se ignora"
        assert security.obtener_ip_cliente(pedir("8.8.8.8", ("X-Real-IP", "203.0.113.7"))) == "8.8.8.8", "de internet directo no se confía en ninguna"
        os.environ["IP_CABECERA_CLIENTE"] = "X-Forwarded-For"
        # Lo que el visitante escribió va a la izquierda; vale lo último, que añadió nuestro proxy
        assert security.obtener_ip_cliente(pedir("172.21.0.1", ("X-Forwarded-For", "1.2.3.4, 5.6.7.8, 203.0.113.7"))) == "203.0.113.7"
        # Si el proxy añade su línea aparte en vez de concatenar, la línea del visitante tampoco gana
        assert security.obtener_ip_cliente(pedir("172.21.0.1", ("X-Forwarded-For", "9.9.9.9"), ("X-Forwarded-For", "203.0.113.7"))) == "203.0.113.7"
        assert security.obtener_ip_cliente(pedir("172.21.0.1", ("X-Forwarded-For", "1.2.3.4, basura"))) == "172.21.0.1"
        assert security.obtener_ip_cliente(pedir("172.21.0.1", ("X-Forwarded-For", "1.2.3.4,"))) == "172.21.0.1"
        # La IP queda en una forma única
        assert security.obtener_ip_cliente(pedir("172.21.0.1", ("X-Forwarded-For", "2001:DB8:0:0:0:0:0:5"))) == "2001:db8::5"
        assert security.obtener_ip_cliente(pedir("172.21.0.1", ("X-Forwarded-For", "::ffff:203.0.113.7"))) == "203.0.113.7"
    finally:
        if anterior is None:
            os.environ.pop("IP_CABECERA_CLIENTE", None)
        else:
            os.environ["IP_CABECERA_CLIENTE"] = anterior


def test_la_huella_de_una_ip_no_la_revela_y_las_ipv6_de_una_misma_red_comparten_huella():
    assert security.huella_ip("203.0.113.7") == security.huella_ip("203.0.113.7") != security.huella_ip("203.0.113.8")
    assert "203" not in security.huella_ip("203.0.113.7") and len(security.huella_ip("203.0.113.7")) == 32
    assert security.huella_ip("2001:db8:1:2::1") == security.huella_ip("2001:DB8:1:2:aaaa:bbbb:cccc:dddd")   # misma /64: sus direcciones temporales cambian solas
    assert security.huella_ip("2001:db8:1:2::1") != security.huella_ip("2001:db8:1:3::1")
    assert security.huella_ip("::ffff:203.0.113.7") == security.huella_ip("203.0.113.7")


def test_la_recuperacion_no_manda_mas_de_10_codigos_al_dia_a_una_cuenta_ni_deja_a_una_ip_pedirlos_para_muchas():
    import email_service
    _crear_usuario("seg_forgot_dia")
    _vaciar_contadores()
    enviados = []
    proxy = TestClient(app, client=("127.0.0.1", 50041))
    with _parchar(email_service, esta_configurado_servicio_correo=lambda: True, enviar_correo_codigo_recuperacion=lambda *a, **k: enviados.append(a[0])):
        respuestas = []
        for i in range(14):
            _vaciar_contadores()   # los topes de 5 minutos no tapan el tope diario: aquí se prueba solo el de 24 horas
            respuestas.append(proxy.post("/auth/forgot-password", json={"identifier": "seg_forgot_dia"}, headers={"CF-Connecting-IP": f"198.51.102.{i + 1}"}))
    assert len(enviados) <= 10, f"{len(enviados)} códigos en un día a una sola cuenta"
    assert all(r.status_code == 200 for r in respuestas) and len({r.json()["mensaje"] for r in respuestas}) == 1, "la respuesta no cambia al alcanzar el tope"
    # Una sola IP no puede pedir códigos para muchas cuentas sin freno
    _vaciar_contadores()
    for i in range(12):
        _crear_usuario(f"seg_forgot_ip_{i}")
    with _parchar(email_service, esta_configurado_servicio_correo=lambda: True, enviar_correo_codigo_recuperacion=lambda *a, **k: None):
        codigos = [proxy.post("/auth/forgot-password", json={"identifier": f"seg_forgot_ip_{i}"}, headers={"CF-Connecting-IP": "198.51.103.9"}).status_code for i in range(12)]
    assert codigos[:10] == [200] * 10 and 429 in codigos[10:], codigos
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
        (test_la_ip_real_se_toma_de_cloudflare_solo_si_la_conexion_viene_de_un_proxy_de_confianza, test_la_ip_real_se_toma_de_cloudflare_solo_si_la_conexion_viene_de_un_proxy_de_confianza),
        (test_un_tope_por_cuenta_frena_las_pruebas_repartidas_entre_muchas_ips, test_un_tope_por_cuenta_frena_las_pruebas_repartidas_entre_muchas_ips),
        (test_quien_ya_entro_desde_su_lugar_no_se_queda_fuera_cuando_otro_bloquea_su_cuenta_a_proposito, test_quien_ya_entro_desde_su_lugar_no_se_queda_fuera_cuando_otro_bloquea_su_cuenta_a_proposito),
        (test_la_ip_real_solo_se_lee_de_la_cabecera_elegida_y_sin_ella_no_se_cree_a_nadie, test_la_ip_real_solo_se_lee_de_la_cabecera_elegida_y_sin_ella_no_se_cree_a_nadie),
        (test_la_huella_de_una_ip_no_la_revela_y_las_ipv6_de_una_misma_red_comparten_huella, test_la_huella_de_una_ip_no_la_revela_y_las_ipv6_de_una_misma_red_comparten_huella),
        (test_la_recuperacion_no_manda_mas_de_10_codigos_al_dia_a_una_cuenta_ni_deja_a_una_ip_pedirlos_para_muchas, test_la_recuperacion_no_manda_mas_de_10_codigos_al_dia_a_una_cuenta_ni_deja_a_una_ip_pedirlos_para_muchas),
    ]
    with TestClient(app):  # arranque de la app: crea la base maestra antes de las pruebas
        _crear_usuario("seg_ini")
        pruebas = verificar_cobertura(sys.modules[__name__], registradas)
        codigo = ejecutar(pruebas, "test_seguridad_login")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)
