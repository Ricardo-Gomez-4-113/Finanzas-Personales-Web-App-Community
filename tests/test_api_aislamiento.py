"""tests/test_api_aislamiento.py - Aislamiento multi-tenant, JWT y RBAC contra la API real.

Requiere fastapi + httpx (se ejecuta en Docker, ver tests/run_tests.sh).
Copia el código a un directorio temporal: nunca toca 'data/' del proyecto.
"""

import base64
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import timedelta

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

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import master_db  # noqa: E402
import security  # noqa: E402
from main import app  # noqa: E402

PASS = "Passw0rd#Test1"


def _crear_usuario(nombre):
    return master_db.create_user_entry(nombre, security.hash_password(PASS), email=f"{nombre}@test.local")


def _login(client, usuario, clave=PASS):
    r = client.post("/auth/login", json={"username": usuario, "password": clave})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _token_forjado(user, **extra):
    payload = {"sub": str(user["id"]), "username": user["username"], "role": user["role"], "db_name": user["db_name"]}
    payload.update(extra)
    return {"Authorization": f"Bearer {security.create_access_token(payload)}"}


# Rutas públicas aprobadas a conciencia (sin datos de usuario). Una ruta nueva debe agregarse aquí a propósito.
RUTAS_PUBLICAS = {"/api/public/tipo-cambio", "/api/public/email-status"}

MOV = {"fecha": "2026-01-15", "monto": 100.0, "tipo": "gasto", "categoria_id": 1, "descripcion": "secreto-de-A"}


def test_usuario_no_ve_ni_modifica_datos_de_otro(client, ua, ub, ha, hb):
    ingreso = dict(MOV, tipo="ingreso", monto=1000.0, descripcion="ingreso-de-A")
    assert client.post("/api/transacciones", json=ingreso, headers=ha).status_code in (200, 201)
    r = client.post("/api/transacciones", json=MOV, headers=ha)
    assert r.status_code in (200, 201), r.text
    ids_a = [t["id"] for t in client.get("/api/transacciones", headers=ha).json()]
    assert ids_a, "A debe ver su propio movimiento"

    assert "secreto-de-A" not in client.get("/api/transacciones", headers=hb).text
    for tid in ids_a:
        assert client.delete(f"/api/transacciones/{tid}", headers=hb).status_code == 404
        assert client.put(f"/api/transacciones/{tid}", json={"monto": 1}, headers=hb).status_code == 404
    assert len(client.get("/api/transacciones", headers=ha).json()) == len(ids_a), "A no debe perder datos"


def test_claims_forjados_en_jwt_no_cruzan_tenants(client, ua, ub, ha):
    # Token firmado válidamente para A pero con db_name/role/sub-claims de B/admin: el servidor debe
    # resolver tenant y rol desde master_db, no desde el payload.
    h = _token_forjado(ua, db_name=ub["db_name"], role="admin")
    assert "secreto-de-A" in client.get("/api/transacciones", headers=h).text
    assert client.get("/admin/users", headers=h).status_code == 403


def test_tokens_invalidos_rechazados(client, ua):
    assert client.get("/api/resumen").status_code == 401
    assert client.get("/api/resumen", headers={"Authorization": "Bearer basura"}).status_code == 401
    expirado = security.create_access_token({"sub": str(ua["id"])}, expires_delta=timedelta(seconds=-5))
    assert client.get("/api/resumen", headers={"Authorization": f"Bearer {expirado}"}).status_code == 401
    inexistente = security.create_access_token({"sub": "999999"})
    assert client.get("/api/resumen", headers={"Authorization": f"Bearer {inexistente}"}).status_code == 401


def test_rbac_admin(client, ha, hadmin):
    for ruta in ("/admin/users", "/admin/stats"):
        assert client.get(ruta, headers=ha).status_code == 403, ruta
        assert client.get(ruta, headers=hadmin).status_code == 200, ruta
    assert client.post("/admin/users", json={"username": "x", "password": PASS}, headers=ha).status_code == 403


def test_usuario_desactivado_pierde_acceso_inmediato(client):
    u = _crear_usuario("desactivable")
    h = _login(client, "desactivable")
    assert client.get("/api/resumen", headers=h).status_code == 200
    master_db.set_user_status(u["id"], 0)
    assert client.get("/api/resumen", headers=h).status_code == 403
    master_db.set_user_status(u["id"], 1)
    master_db.update_subscription_status(u["id"], "suspended")
    assert client.get("/api/resumen", headers=h).status_code == 403


def test_todas_las_rutas_api_y_admin_exigen_token(client):
    probadas = 0
    for path, metodos in app.openapi()["paths"].items():
        if not (path.startswith("/api/") or path.startswith("/admin/")) or path in RUTAS_PUBLICAS:
            continue
        for metodo in metodos:
            url = re.sub(r"\{[^}]+\}", "1", path)
            r = client.request(metodo.upper(), url)
            assert r.status_code == 401, f"{metodo.upper()} {path} responde {r.status_code} sin token"
            probadas += 1
    assert probadas > 20, f"solo se probaron {probadas} rutas"


def test_exportar_presupuesto_con_fecha_base_no_da_500(client, ha):
    # Regresión: 'date' no estaba importado en routers/presupuesto.py (NameError -> 500)
    r = client.post("/api/presupuesto/exportar-a-fijos", json={"fecha_base": "2026-02-10"}, headers=ha)
    assert r.status_code < 500, r.text
    r = client.post("/api/presupuesto/exportar-a-fijos", json={"fecha_base": "2026-13-45"}, headers=ha)
    assert r.status_code == 400, r.text


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_jwt_alg_none_y_algoritmos_ajenos_rechazados(client, ua):
    claims = {"sub": str(ua["id"]), "username": ua["username"], "role": "admin", "db_name": ua["db_name"],
              "exp": int(time.time()) + 600}
    cuerpo = _b64(json.dumps(claims).encode())
    for alg in ("none", "None", "NONE"):
        token = f"{_b64(json.dumps({'alg': alg, 'typ': 'JWT'}).encode())}.{cuerpo}."
        assert client.get("/api/resumen", headers={"Authorization": f"Bearer {token}"}).status_code == 401, alg
    # Misma clave pero otro algoritmo HMAC, y HS256 con otra clave: ambos deben fallar.
    otro_alg = jwt.encode(claims, security.SECRET_KEY, algorithm="HS512")
    otra_clave = jwt.encode(claims, "x" * 48, algorithm="HS256")
    for token in (otro_alg, otra_clave):
        assert client.get("/api/resumen", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_restore_rechaza_archivos_malos_sin_tocar_datos(client, ub, hb):
    for mov in (dict(MOV, tipo="ingreso", monto=1000.0, descripcion="ingreso-de-B"), dict(MOV, descripcion="dato-de-B")):
        r = client.post("/api/transacciones", json=mov, headers=hb)
        assert r.status_code in (200, 201), r.text
    antes = client.get("/api/transacciones", headers=hb).json()
    respaldo = client.get("/api/backup", headers=hb).content
    assert respaldo[:16] == b"SQLite format 3\x00"

    sin_esquema = os.path.join(TMP, "ajena.db")
    con = sqlite3.connect(sin_esquema)
    con.execute("CREATE TABLE cosas (x)")
    con.commit()
    con.close()
    with open(sin_esquema, "rb") as f:
        sin_esquema = f.read()

    malos = {
        "extension .txt": ("respaldo.txt", respaldo),
        "extension doble": ("respaldo.db.php", respaldo),
        "vacio": ("respaldo.db", b""),
        "bytes al azar": ("respaldo.db", os.urandom(8192)),
        "cabecera SQLite + basura": ("respaldo.db", b"SQLite format 3\x00" + os.urandom(8192)),
        "truncado": ("respaldo.db", respaldo[: len(respaldo) // 2]),
        "SQLite sin esquema de la app": ("respaldo.db", sin_esquema),
        "demasiado grande": ("respaldo.db", b"SQLite format 3\x00" + b"\0" * (50 * 1024 * 1024)),
    }
    for caso, (nombre, contenido) in malos.items():
        r = client.post("/api/restore", files={"file": (nombre, contenido, "application/octet-stream")}, headers=hb)
        assert r.status_code == 400, f"{caso}: {r.status_code} {r.text[:200]}"
        assert client.get("/api/transacciones", headers=hb).json() == antes, f"{caso}: alteró los datos"

    # Un respaldo legítimo sí se restaura y conserva los datos.
    r = client.post("/api/restore", files={"file": ("respaldo.db", respaldo, "application/octet-stream")}, headers=hb)
    assert r.status_code == 200, r.text
    assert client.get("/api/transacciones", headers=hb).json() == antes


def test_restore_solo_afecta_al_propio_tenant(client, ha, hb):
    datos_a = client.get("/api/transacciones", headers=ha).json()
    respaldo_b = client.get("/api/backup", headers=hb).content
    # B sube su respaldo con un token de B: A no debe cambiar.
    r = client.post("/api/restore", files={"file": ("x.db", respaldo_b, "application/octet-stream")}, headers=hb)
    assert r.status_code == 200, r.text
    assert client.get("/api/transacciones", headers=ha).json() == datos_a
    assert client.post("/api/restore", files={"file": ("x.db", respaldo_b, "application/octet-stream")}).status_code == 401


def test_health_publico_y_sin_datos_de_usuarios(client):
    r = client.get("/health")
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"status", "base_maestra", "respaldo", "horas_desde_respaldo"}, r.json()
    assert r.json()["base_maestra"] == "ok"
    assert "usuario_a" not in r.text and "secreto" not in r.text


def test_login_bloquea_tras_5_fallos(client):
    _crear_usuario("bruteforce")
    codigos = [client.post("/auth/login", json={"username": "bruteforce", "password": "mal"}).status_code for _ in range(6)]
    assert codigos[:5] == [401] * 5 and codigos[5] == 429, codigos
    assert client.post("/auth/login", json={"username": "bruteforce", "password": PASS}).status_code == 429


if __name__ == "__main__":
    with TestClient(app) as client:
        ua, ub = _crear_usuario("usuario_a"), _crear_usuario("usuario_b")
        ha, hb = _login(client, "usuario_a"), _login(client, "usuario_b")
        hadmin = _login(client, "admin", os.environ["DEFAULT_ADMIN_PASSWORD"])
        test_usuario_no_ve_ni_modifica_datos_de_otro(client, ua, ub, ha, hb)
        test_claims_forjados_en_jwt_no_cruzan_tenants(client, ua, ub, ha)
        test_tokens_invalidos_rechazados(client, ua)
        test_rbac_admin(client, ha, hadmin)
        test_usuario_desactivado_pierde_acceso_inmediato(client)
        test_todas_las_rutas_api_y_admin_exigen_token(client)
        test_exportar_presupuesto_con_fecha_base_no_da_500(client, ha)
        test_jwt_alg_none_y_algoritmos_ajenos_rechazados(client, ua)
        test_restore_rechaza_archivos_malos_sin_tocar_datos(client, ub, hb)
        test_restore_solo_afecta_al_propio_tenant(client, ha, hb)
        test_health_publico_y_sin_datos_de_usuarios(client)
        test_login_bloquea_tras_5_fallos(client)
    shutil.rmtree(TMP, ignore_errors=True)
    print("OK")
