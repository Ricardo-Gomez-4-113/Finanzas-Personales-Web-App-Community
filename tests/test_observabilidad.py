"""tests/test_observabilidad.py - El registro de fallos: id por petición, traza, datos que no se guardan y tolerancia a fallos.

Requiere fastapi + httpx (se ejecuta en Docker, ver tests/run_tests.sh).
Copia el código a un directorio temporal y escribe el registro en otro, también temporal.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile

try:
    import httpx  # noqa: F401
    import fastapi  # noqa: F401
except ImportError:
    print("SKIP: requiere fastapi y httpx (ejecutar con tests/run_tests.sh)")
    sys.exit(0)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="finanzas_test_")
LOGS = tempfile.mkdtemp(prefix="finanzas_logs_")
shutil.copytree(
    RAIZ, TMP, dirs_exist_ok=True,
    ignore=shutil.ignore_patterns("data", ".git", "tests", "__pycache__", "*.db", "backups", "venv", ".venv", "node_modules"),
)
os.chdir(TMP)
sys.path.insert(0, TMP)
os.environ.setdefault("JWT_SECRET_KEY", "k" * 48)
os.environ["DEFAULT_ADMIN_PASSWORD"] = "AdminTest#12345"
os.environ["FINANZAS_LOG_DIR"] = LOGS

from fastapi.testclient import TestClient  # noqa: E402

import master_db  # noqa: E402
import observabilidad  # noqa: E402
import routers.movimientos as rmovimientos  # noqa: E402
import security  # noqa: E402
from main import app  # noqa: E402

PASS = "Passw0rd#Test1"
ARCHIVO = os.path.join(LOGS, observabilidad.ARCHIVO_LOG)


def _crear_usuario(nombre):
    return master_db.create_user_entry(nombre, security.hash_password(PASS), email=f"{nombre}@test.local")


def _login(client, usuario, clave=PASS):
    r = client.post("/auth/login", json={"username": usuario, "password": clave})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _texto_del_registro():
    with open(ARCHIVO, encoding="utf-8") as archivo:
        return archivo.read()


def _registros(evento=None):
    if not os.path.exists(ARCHIVO):
        return []
    filas = [json.loads(linea) for linea in _texto_del_registro().splitlines() if linea.strip()]
    return [f for f in filas if evento is None or f.get("evento") == evento]


def test_error_inesperado_responde_500_con_id_y_traza(client):
    _crear_usuario("obs_error")
    token = _login(client, "obs_error")

    def romper(*args, **kwargs):
        raise RuntimeError("fallo provocado para la prueba")

    original = rmovimientos.calcular_resumen
    rmovimientos.calcular_resumen = romper
    try:
        r = client.get("/api/resumen", headers={"Authorization": f"Bearer {token}"})
    finally:
        rmovimientos.calcular_resumen = original

    assert r.status_code == 500, r.text
    assert r.json() == {"detail": "Error interno del servidor."}, r.text
    assert r.headers.get("x-content-type-options") == "nosniff"  # las cabeceras de seguridad también llegan al 500
    id_peticion = r.headers["x-request-id"]
    coincidencias = [f for f in _registros() if f.get("id_peticion") == id_peticion]
    assert len(coincidencias) == 1, coincidencias
    registro = coincidencias[0]
    assert registro["nivel"] == "ERROR" and registro["evento"] == "error_servidor", registro
    assert registro["estado"] == 500 and registro["ruta"] == "/api/resumen" and registro["metodo"] == "GET", registro
    assert registro["excepcion"].startswith("RuntimeError"), registro
    assert "fallo provocado para la prueba" in registro["traza"], registro
    assert registro["usuario_id"] is not None, registro


def test_401_y_403_quedan_como_advertencia_sin_datos_sensibles(client):
    _crear_usuario("obs_clave")
    clave_mala = "Equivocada#Clave987"
    r = client.post("/auth/login", json={"username": "obs_clave", "password": clave_mala})
    assert r.status_code == 401, r.text

    token = _login(client, "obs_clave")
    r = client.get("/api/transacciones?buscar=secreto-en-la-consulta")  # sin token
    assert r.status_code == 401, r.text

    advertencias = _registros("acceso_rechazado")
    assert [f["estado"] for f in advertencias] == [401, 401], advertencias
    assert all(f["nivel"] == "WARNING" for f in advertencias), advertencias
    assert advertencias[-1]["ruta"] == "/api/transacciones"  # sin la cadena de consulta
    texto = _texto_del_registro()
    for dato in (clave_mala, PASS, token, "secreto-en-la-consulta"):
        assert dato not in texto, f"el registro guardó un dato sensible: {dato[:12]}..."


def test_peticion_correcta_no_deja_linea(client):
    _crear_usuario("obs_ok")
    token = _login(client, "obs_ok")
    antes = len(_registros())
    r = client.get("/api/transacciones", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert len(_registros()) == antes, "una petición correcta no debe escribir en el registro"
    assert len(r.headers["x-request-id"]) == 16


def test_reporte_del_navegador_se_registra_y_valida_su_tamano(client):
    antes = len(_registros("cliente"))
    r = client.post("/api/diagnostico/cliente", json={
        "tipo": "js", "mensaje": "fallo-del-navegador-prueba", "origen": "/static/js/x.js", "linea": 12,
    })
    assert r.status_code == 204, r.text
    registros = _registros("cliente")
    assert len(registros) == antes + 1, registros
    assert registros[-1]["tipo"] == "js" and registros[-1]["linea"] == 12, registros[-1]
    assert registros[-1]["origen"] == "/static/js/x.js", registros[-1]

    rechazados = [
        {"tipo": "js", "mensaje": "x" * 501},
        {"tipo": "otro", "mensaje": "tipo que no existe"},
        {"tipo": "js", "mensaje": "con id", "id_peticion": "no-es-hexadecimal"},
        {"tipo": "js", "mensaje": ""},
    ]
    for cuerpo in rechazados:
        assert client.post("/api/diagnostico/cliente", json=cuerpo).status_code == 422, cuerpo
    assert len(_registros("cliente")) == antes + 1, "un reporte inválido no debe registrarse"


def test_fallo_al_abrir_el_registro_no_rompe_la_app(client):
    archivo_bloqueante = tempfile.mkstemp()[1]
    try:
        destino = observabilidad.configurar(os.path.join(archivo_bloqueante, "carpeta-imposible"))
        assert destino == "stderr", destino
        _crear_usuario("obs_sin_registro")
        token = _login(client, "obs_sin_registro")
        r = client.get("/api/transacciones", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        r = client.get("/api/transacciones")  # 401 que se intenta registrar por stderr
        assert r.status_code == 401, r.text
    finally:
        os.remove(archivo_bloqueante)
        observabilidad.configurar(LOGS)


def test_rotacion_acota_el_disco():
    manejador = observabilidad.logger.handlers[0]
    assert manejador.maxBytes == observabilidad.TAMANO_MAXIMO_BYTES
    assert manejador.backupCount == observabilidad.ARCHIVOS_CONSERVADOS
    assert os.path.basename(manejador.baseFilename) == observabilidad.ARCHIVO_LOG


def _cargar_resumen():
    ruta = os.path.join(RAIZ, "scripts", "resumen_logs.py")
    spec = importlib.util.spec_from_file_location("resumen_logs", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_resumen_cuenta_fallos_y_salta_lineas_rotas():
    resumen = _cargar_resumen()
    carpeta = tempfile.mkdtemp(prefix="finanzas_resumen_")
    rotado = os.path.join(carpeta, "app.log.1")
    actual = os.path.join(carpeta, "app.log")
    with open(rotado, "w", encoding="utf-8") as f:
        f.write(json.dumps({"nivel": "ERROR", "evento": "error_servidor", "metodo": "GET", "ruta": "/api/resumen", "estado": 500, "ts": "a", "id_peticion": "1" * 16}) + "\n")
        f.write("{linea-cortada\n")
        f.write(json.dumps({"nivel": "ERROR", "evento": "error_servidor", "metodo": "GET", "ruta": "/api/resumen", "estado": 500, "ts": "b", "id_peticion": "2" * 16}) + "\n")
    with open(actual, "w", encoding="utf-8") as f:
        f.write(json.dumps({"nivel": "WARNING", "evento": "acceso_rechazado", "metodo": "GET", "ruta": "/api/transacciones", "estado": 401, "ts": "c", "id_peticion": "3" * 16}) + "\n")
        f.write("\n")

    assert [os.path.basename(p) for p in resumen.archivos_en_orden(carpeta)] == ["app.log.1", "app.log"]
    res = resumen.resumir(carpeta, ultimos=2)
    assert res["total"] == 4 and res["malformadas"] == 1, res
    assert res["por_nivel"] == {"ERROR": 2, "WARNING": 1}, res
    assert res["por_grupo"][0] == (("error_servidor", "GET", "/api/resumen", 500), 2), res
    assert [r["ts"] for r in res["ultimos"]] == ["b", "c"], res


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import ejecutar, verificar_cobertura

    with TestClient(app) as client:
        registradas = [
            (test_error_inesperado_responde_500_con_id_y_traza, lambda: test_error_inesperado_responde_500_con_id_y_traza(client)),
            (test_401_y_403_quedan_como_advertencia_sin_datos_sensibles, lambda: test_401_y_403_quedan_como_advertencia_sin_datos_sensibles(client)),
            (test_peticion_correcta_no_deja_linea, lambda: test_peticion_correcta_no_deja_linea(client)),
            (test_reporte_del_navegador_se_registra_y_valida_su_tamano, lambda: test_reporte_del_navegador_se_registra_y_valida_su_tamano(client)),
            (test_fallo_al_abrir_el_registro_no_rompe_la_app, lambda: test_fallo_al_abrir_el_registro_no_rompe_la_app(client)),
            (test_rotacion_acota_el_disco, test_rotacion_acota_el_disco),
            (test_resumen_cuenta_fallos_y_salta_lineas_rotas, test_resumen_cuenta_fallos_y_salta_lineas_rotas),
        ]
        pruebas = verificar_cobertura(sys.modules[__name__], registradas)
        codigo = ejecutar(pruebas, "test_observabilidad")
    shutil.rmtree(TMP, ignore_errors=True)
    shutil.rmtree(LOGS, ignore_errors=True)
    sys.exit(codigo)
