"""tests/test_notificaciones.py - La bandeja de avisos de la app: lectura, marcado, borrado, aislamiento entre cuentas y límites.

Los avisos los genera el core (ver tests/test_fijos_metas.py); aquí se provocan con acciones reales por la API.
Requiere fastapi + httpx (se ejecuta en Docker, ver tests/run_tests.sh). Copia el código a un directorio temporal: nunca toca 'data/' del proyecto.
"""

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
os.environ["FINANZAS_MODO_PRUEBA"] = "1"   # sin esto FINANZAS_HOY no tiene efecto
os.environ["FINANZAS_HOY"] = "2026-11-09T10:00:00"

from fastapi.testclient import TestClient  # noqa: E402

import master_db  # noqa: E402
import security  # noqa: E402
from asistente.tokens import emitir_token  # noqa: E402
from main import app  # noqa: E402

PASS = "Passw0rd#Test1"


def _cuenta(client, nombre):
    fila = master_db.create_user_entry(nombre, security.hash_password(PASS), email=f"{nombre}@test.local")
    r = client.post("/auth/login", json={"username": nombre, "password": PASS})
    assert r.status_code == 200, r.text
    return fila["id"], {"Authorization": f"Bearer {r.json()['access_token']}"}


def _cumplir_una_meta(client, cab, nombre="Moto"):
    """Una meta de 100 pesos depositada completa: el core la da por cumplida y deja un aviso."""
    ingreso = client.post("/api/transacciones", json={"fecha": "2026-09-01", "monto": 5000, "tipo": "ingreso", "categoria_id": 12, "descripcion": "Fondeo"}, headers=cab)
    assert ingreso.status_code in (200, 201), ingreso.text
    meta = client.post("/api/apartados", json={"nombre": nombre, "tipo": "meta", "meta_monto": 100}, headers=cab).json()["id"]
    r = client.post(f"/api/apartados/{meta}/movimiento", json={"monto": 100, "accion": "depositar"}, headers=cab)
    assert r.status_code == 200, r.text
    return meta


def test_la_bandeja_exige_sesion(client):
    for metodo, ruta in (("GET", "/api/notificaciones"), ("POST", "/api/notificaciones/leer-todas"), ("POST", "/api/notificaciones/1/leer"),
                         ("DELETE", "/api/notificaciones/1"), ("DELETE", "/api/notificaciones")):
        assert client.request(metodo, ruta).status_code == 401, (metodo, ruta)


def test_una_cuenta_sin_avisos_ve_la_bandeja_vacia(client):
    _, cab = _cuenta(client, "noti_vacia")
    assert client.get("/api/notificaciones", headers=cab).json() == {"no_leidas": 0, "notificaciones": []}


def test_cumplir_una_meta_deja_un_aviso_que_se_lee_se_marca_y_se_borra(client):
    _, cab = _cuenta(client, "noti_ciclo")
    _cumplir_una_meta(client, cab)
    j = client.get("/api/notificaciones", headers=cab).json()
    assert j["no_leidas"] == 1 and len(j["notificaciones"]) == 1, j
    aviso = j["notificaciones"][0]
    assert aviso["tipo"] == "meta_cumplida" and aviso["leida"] is False and "Moto" in aviso["texto"] and set(aviso) == {"id", "tipo", "titulo", "texto", "creada_en", "leida"}
    assert client.get("/api/notificaciones?solo_no_leidas=true", headers=cab).json()["no_leidas"] == 1

    assert client.post(f"/api/notificaciones/{aviso['id']}/leer", headers=cab).status_code == 200
    j = client.get("/api/notificaciones", headers=cab).json()
    assert j["no_leidas"] == 0 and j["notificaciones"][0]["leida"] is True
    assert client.get("/api/notificaciones?solo_no_leidas=true", headers=cab).json()["notificaciones"] == []

    assert client.delete(f"/api/notificaciones/{aviso['id']}", headers=cab).status_code == 200
    assert client.get("/api/notificaciones", headers=cab).json()["notificaciones"] == []
    assert client.delete(f"/api/notificaciones/{aviso['id']}", headers=cab).status_code == 404
    assert client.post(f"/api/notificaciones/{aviso['id']}/leer", headers=cab).status_code == 404


def test_leer_todas_y_borrar_las_leidas(client):
    _, cab = _cuenta(client, "noti_masivo")
    _cumplir_una_meta(client, cab, "Uno")
    client.post("/api/apartados", json={"nombre": "Dos", "tipo": "meta", "meta_monto": 50}, headers=cab)
    otra = next(a["id"] for a in client.get("/api/apartados", headers=cab).json() if a["nombre"] == "Dos")
    assert client.post(f"/api/apartados/{otra}/movimiento", json={"monto": 50, "accion": "depositar"}, headers=cab).status_code == 200
    assert client.get("/api/notificaciones", headers=cab).json()["no_leidas"] == 2
    assert client.post("/api/notificaciones/leer-todas", headers=cab).json() == {"marcadas": 2}
    assert client.post("/api/notificaciones/leer-todas", headers=cab).json() == {"marcadas": 0}
    assert client.delete("/api/notificaciones", headers=cab).json() == {"borradas": 2}
    assert client.get("/api/notificaciones", headers=cab).json() == {"no_leidas": 0, "notificaciones": []}


def test_cada_cuenta_solo_ve_y_toca_sus_avisos(client):
    _, ana = _cuenta(client, "noti_ana")
    _, beto = _cuenta(client, "noti_beto")
    _cumplir_una_meta(client, ana)
    id_ana = client.get("/api/notificaciones", headers=ana).json()["notificaciones"][0]["id"]
    assert client.get("/api/notificaciones", headers=beto).json() == {"no_leidas": 0, "notificaciones": []}
    assert client.post(f"/api/notificaciones/{id_ana}/leer", headers=beto).status_code == 404
    assert client.delete(f"/api/notificaciones/{id_ana}", headers=beto).status_code == 404
    assert client.post("/api/notificaciones/leer-todas", headers=beto).json() == {"marcadas": 0}
    assert client.get("/api/notificaciones", headers=ana).json()["no_leidas"] == 1, "lo de Beto no tocó lo de Ana"


def test_el_texto_con_marcas_html_viaja_como_texto_y_el_limite_se_valida(client):
    _, cab = _cuenta(client, "noti_html")
    _cumplir_una_meta(client, cab, "<img src=x onerror=alert(1)>")
    r = client.get("/api/notificaciones", headers=cab)
    assert r.headers["content-type"].startswith("application/json") and "<img src=x onerror=alert(1)>" in r.json()["notificaciones"][0]["texto"]
    for malo in ("0", "101", "-1", "x"):
        assert client.get(f"/api/notificaciones?limite={malo}", headers=cab).status_code == 422, malo
    assert client.get("/api/notificaciones?limite=1", headers=cab).status_code == 200


def test_el_token_del_asistente_no_abre_la_bandeja(client):
    uid, cab = _cuenta(client, "noti_asistente")
    _cumplir_una_meta(client, cab)
    token = {"Authorization": f"Bearer {emitir_token(uid)}"}
    for metodo, ruta in (("GET", "/api/notificaciones"), ("POST", "/api/notificaciones/leer-todas"), ("DELETE", "/api/notificaciones")):
        assert client.request(metodo, ruta, headers=token).status_code in (401, 403), (metodo, ruta)
    assert client.get("/api/notificaciones", headers=cab).json()["no_leidas"] == 1


def test_la_bandeja_guarda_solo_los_ultimos_avisos(client):
    import finanzas_core
    uid, cab = _cuenta(client, "noti_tope")
    from database import get_tenant_conn
    conn = get_tenant_conn(master_db.get_user_by_id(uid)["db_name"])
    try:
        anterior = finanzas_core.MAX_AVISOS_GUARDADOS
        finanzas_core.MAX_AVISOS_GUARDADOS = 3
        try:
            for i in range(5):
                finanzas_core.avisar(conn.cursor(), "prueba", f"clave:{i}", f"t{i}", f"texto {i}")
            conn.commit()
        finally:
            finanzas_core.MAX_AVISOS_GUARDADOS = anterior
        assert [f[0] for f in conn.execute("SELECT clave FROM notificaciones ORDER BY id")] == ["clave:2", "clave:3", "clave:4"]
        assert finanzas_core.avisar(conn.cursor(), "prueba", "clave:4", "t4", "texto 4") is False, "el mismo hecho no se avisa dos veces"
    finally:
        conn.close()


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(RAIZ, "tests"))
    from ejecutor import descubrir, ejecutar

    with TestClient(app) as client:
        pruebas = [(n, (lambda f=f: f(client))) for n, f in descubrir(sys.modules[__name__])]
        codigo = ejecutar(pruebas, "test_notificaciones")
    shutil.rmtree(TMP, ignore_errors=True)
    shutil.rmtree(LOGS, ignore_errors=True)
    sys.exit(codigo)
