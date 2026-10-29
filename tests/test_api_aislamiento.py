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
    # Parte de los claims reales (incluida la huella de contraseña) y sobrescribe lo que se quiera falsificar
    payload = security.claims_sesion(master_db.get_user_by_id(user["id"]))
    payload.update(extra)
    return {"Authorization": f"Bearer {security.create_access_token(payload)}"}


# Rutas públicas aprobadas a conciencia (sin datos de usuario). Una ruta nueva debe agregarse aquí a propósito.
RUTAS_PUBLICAS = {"/api/public/tipo-cambio"}

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


def test_borrar_retiro_de_meta_devuelve_el_dinero(client, ua, ha):
    assert client.post("/api/transacciones", json=dict(MOV, tipo="ingreso", monto=5000.0, descripcion="ingreso-meta"), headers=ha).status_code in (200, 201)
    client.post("/api/apartados", json={"nombre": "Viaje", "tipo": "meta", "meta_monto": 18000}, headers=ha)
    aid = next(a["id"] for a in client.get("/api/apartados", headers=ha).json() if a["nombre"] == "Viaje")
    assert client.post(f"/api/apartados/{aid}/movimiento", json={"monto": 1000, "accion": "depositar"}, headers=ha).status_code == 200
    assert client.post(f"/api/apartados/{aid}/movimiento", json={"monto": 300, "accion": "retirar"}, headers=ha).status_code == 200
    retiro = next(t for t in client.get("/api/transacciones", headers=ha).json() if t["apartado_id"] == aid and t["tipo"] == "ingreso")
    assert client.delete(f"/api/transacciones/{retiro['id']}", headers=ha).status_code == 200
    saldo = next(a["monto_actual"] for a in client.get("/api/apartados", headers=ha).json() if a["id"] == aid)
    # El retiro se deshace: el dinero vuelve a la meta
    assert saldo == 1000.0, saldo


def test_token_deja_de_valer_tras_cambiar_contrasena(client, ua):
    h = _login(client, "usuario_a")
    assert client.get("/api/resumen", headers=h).status_code == 200
    r = client.patch("/auth/profile", json={"password": "NuevaClave#2026"}, headers=h)
    assert r.status_code == 200, r.text
    assert client.get("/api/resumen", headers=h).status_code == 401
    # El token que devuelve el propio cambio sí sirve
    nuevo = {"Authorization": f"Bearer {r.json()['new_token']}"}
    assert client.get("/api/resumen", headers=nuevo).status_code == 200


def test_reset_no_revela_existencia_de_cuenta(client):
    _crear_usuario("usuario_reset")
    inexistente = client.post("/auth/reset-password", json={"identifier": "nadie_asi", "code": "123456", "new_password": "Otra#Clave2026"})
    erroneo = client.post("/auth/reset-password", json={"identifier": "usuario_reset", "code": "123456", "new_password": "Otra#Clave2026"})
    assert inexistente.status_code == erroneo.status_code == 400
    assert inexistente.json()["detail"] == erroneo.json()["detail"], (inexistente.text, erroneo.text)


def test_sincronizar_tiene_enfriamiento(client, ha):
    primera = client.post("/api/inversiones/sincronizar", headers=ha)
    segunda = client.post("/api/inversiones/sincronizar", headers=ha)
    assert primera.status_code == 200, primera.text
    assert segunda.status_code == 429, segunda.text


def test_rendimiento_con_fecha_fuera_de_rango_se_rechaza(client, ha):
    r = client.post("/api/rendimiento", json={"nombre": "Viejo", "capital": 1000, "tasa_anual": 10, "fecha_inicio": "0001-01-01"}, headers=ha)
    assert r.status_code == 422, r.text


def test_excel_no_convierte_texto_en_formula(client, ha):
    import io
    import openpyxl
    client.post("/api/apartados", json={"nombre": "=1+1", "tipo": "libre"}, headers=ha)
    r = client.get("/api/exportar-excel?moneda_base=MXN", headers=ha)
    assert r.status_code == 200, r.text
    libro = openpyxl.load_workbook(io.BytesIO(r.content))
    tipos = [c.data_type for fila in libro["Resumen General"].iter_rows() for c in fila if c.value == "=1+1"]
    assert tipos and all(t == "s" for t in tipos), tipos


def test_restore_rechaza_triggers_y_textos_enormes(client, hb):
    antes = client.get("/api/transacciones", headers=hb).json()
    respaldo = client.get("/api/backup", headers=hb).content

    def _variante(nombre, sql):
        ruta = os.path.join(TMP, nombre)
        with open(ruta, "wb") as f:
            f.write(respaldo)
        con = sqlite3.connect(ruta)
        con.execute(sql)
        con.commit()
        con.close()
        with open(ruta, "rb") as f:
            return f.read()

    casos = {
        "trigger": _variante("con_trigger.db", "CREATE TRIGGER t AFTER INSERT ON transacciones BEGIN SELECT 1; END"),
        "texto enorme": _variante("texto_enorme.db", "UPDATE transacciones SET descripcion = substr(hex(randomblob(4000)), 1, 3000)"),
    }
    for nombre, contenido in casos.items():
        r = client.post("/api/restore", files={"file": ("respaldo.db", contenido, "application/octet-stream")}, headers=hb)
        assert r.status_code == 400, (nombre, r.status_code, r.text)
    assert client.get("/api/transacciones", headers=hb).json() == antes


def test_tarjetas_por_porcentaje_no_se_guardan_en_cero(client, ha):
    cfg_original = client.get("/api/presupuesto", headers=ha).json()["config"]
    try:
        r = client.put("/api/presupuesto/config", json={"ingreso_base": 10000, "frecuencia": "mensual", "subdivision_tipo": "ninguna"}, headers=ha)
        assert r.status_code == 200, r.text
        # La interfaz envía monto 0 y porcentaje 25: manda el porcentaje
        r = client.post("/api/presupuesto/tarjetas", json={"nombre": "Pct prueba", "unidad_orden": 1, "monto": 0, "porcentaje": 25}, headers=ha)
        assert r.status_code == 201 and r.json()["monto"] == 2500.0 and r.json()["porcentaje"] == 25.0, r.text
        tid = r.json()["id"]
        r = client.patch(f"/api/presupuesto/tarjetas/{tid}", json={"nombre": "Pct prueba 2", "monto": 0, "porcentaje": 10}, headers=ha)
        assert r.status_code == 200 and r.json()["monto"] == 1000.0, r.text
        # Más de 100% se rechaza con 422
        r = client.post("/api/presupuesto/tarjetas", json={"nombre": "Alto", "unidad_orden": 1, "porcentaje": 120}, headers=ha)
        assert r.status_code == 422, r.text
        # Sin ingreso base no se puede convertir un porcentaje: error claro, no una tarjeta en 0%
        client.put("/api/presupuesto/config", json={"ingreso_base": 0, "frecuencia": "mensual", "subdivision_tipo": "ninguna"}, headers=ha)
        r = client.post("/api/presupuesto/tarjetas", json={"nombre": "Sin ingreso", "unidad_orden": 1, "monto": 0, "porcentaje": 25}, headers=ha)
        assert r.status_code == 422 and "ingreso fijo base" in r.text, r.text
        client.delete(f"/api/presupuesto/tarjetas/{tid}", headers=ha)
    finally:
        client.put("/api/presupuesto/config", json={"ingreso_base": cfg_original["ingreso_base"], "frecuencia": cfg_original["frecuencia"],
                                                    "subdivision_tipo": cfg_original.get("subdivision_tipo") or "ninguna"}, headers=ha)


def test_msi_en_el_presupuesto_por_api(client, ha):
    cfg_original = client.get("/api/presupuesto", headers=ha).json()["config"]
    try:
        r = client.put("/api/presupuesto/config", json={"ingreso_base": 10000, "frecuencia": "semanal", "subdivision_tipo": "ninguna"}, headers=ha)
        assert r.status_code == 200, r.text
        # Una compra a meses necesita su plazo
        r = client.post("/api/presupuesto/tarjetas", json={"nombre": "Celular MSI", "monto": 800, "unidad_orden": 1, "tipo": "msi"}, headers=ha)
        assert r.status_code == 422 and "meses" in r.text, r.text
        r = client.post("/api/presupuesto/tarjetas", json={"nombre": "Celular MSI", "monto": 800, "unidad_orden": 1, "tipo": "msi", "msi_cuotas": 12}, headers=ha)
        assert r.status_code == 201, r.text
        tarjeta = r.json()
        assert tarjeta["tipo"] == "msi" and tarjeta["msi_cuotas"] == 12, tarjeta
        # Pasarla a normal limpia el plazo
        r = client.patch(f"/api/presupuesto/tarjetas/{tarjeta['id']}", json={"tipo": "normal"}, headers=ha)
        assert r.status_code == 200 and r.json()["tipo"] == "normal" and r.json()["msi_cuotas"] is None, r.text
        client.delete(f"/api/presupuesto/tarjetas/{tarjeta['id']}", headers=ha)
    finally:
        client.put("/api/presupuesto/config", json={
            "ingreso_base": cfg_original["ingreso_base"], "frecuencia": cfg_original["frecuencia"],
            "subdivision_tipo": "ninguna"}, headers=ha)


def test_pagos_del_presupuesto_por_api(client, ha):
    cfg_original = client.get("/api/presupuesto", headers=ha).json()["config"]
    try:
        r = client.put("/api/presupuesto/config", json={"ingreso_base": 10000, "frecuencia": "semanal", "subdivision_tipo": "ninguna"}, headers=ha)
        assert r.status_code == 200, r.text
        unidades = r.json()["unidades"]
        assert [u["activa"] for u in unidades] == [True, True, True, True], unidades
        # Las fechas de pago van en orden creciente
        for orden, dia in [(1, 5), (2, 12), (3, 19), (4, 26)]:
            assert client.put(f"/api/presupuesto/unidades/{orden}", json={"dia_pago": dia}, headers=ha).status_code == 200
        r = client.put("/api/presupuesto/unidades/2", json={"dia_pago": 3}, headers=ha)
        assert r.status_code == 422 and "orden creciente" in r.text, r.text
        client.put("/api/presupuesto/unidades/2", json={"dia_pago": 12}, headers=ha)
        # Una tarjeta de pago toma el día del pago y no admite más que el ingreso de ese pago
        r = client.post("/api/presupuesto/tarjetas", json={"nombre": "Renta semana", "monto": 4000, "unidad_orden": 1, "dia_pago": 28}, headers=ha)
        assert r.status_code == 201 and r.json()["unidad_orden"] == 1 and r.json()["dia_pago"] is None, r.text
        tid = r.json()["id"]
        r = client.post("/api/presupuesto/tarjetas", json={"nombre": "Exceso", "monto": 7000, "unidad_orden": 1}, headers=ha)
        assert r.status_code == 422 and "pago 1" in r.text, r.text
        # Con frecuencia mensual solo hay un pago: no se puede dejar una tarjeta en el pago 3
        r = client.post("/api/presupuesto/tarjetas", json={"nombre": "Pago tres", "monto": 500, "unidad_orden": 3}, headers=ha)
        assert r.status_code == 201, r.text
        tid3 = r.json()["id"]
        r = client.put("/api/presupuesto/config", json={"ingreso_base": 10000, "frecuencia": "mensual", "subdivision_tipo": "ninguna"}, headers=ha)
        assert r.status_code == 422 and "frecuencia mensual" in r.text, r.text
        client.delete(f"/api/presupuesto/tarjetas/{tid3}", headers=ha)
        # Exportar: la tarjeta de pago sale como fijo en la fecha de su pago, el tablero general no se mezcla
        r = client.post("/api/presupuesto/exportar-a-fijos", json={}, headers=ha)
        assert r.status_code == 200, r.text
        estado = client.get("/api/presupuesto", headers=ha).json()
        pago1 = next(u for u in estado["unidades"] if u["orden"] == 1)
        assert [t["nombre"] for t in pago1["tarjetas"]] == ["Renta semana"], pago1
        assert [t["nombre"] for t in estado["tarjetas"]].count("Renta semana") == 1, estado["tarjetas"]
        client.delete(f"/api/presupuesto/tarjetas/{tid}", headers=ha)
    finally:
        for orden in range(1, 5):
            client.put(f"/api/presupuesto/unidades/{orden}", json={"dia_pago": None}, headers=ha)
        client.put("/api/presupuesto/config", json={"ingreso_base": cfg_original["ingreso_base"], "frecuencia": cfg_original["frecuencia"],
                                                    "subdivision_tipo": cfg_original.get("subdivision_tipo") or "ninguna"}, headers=ha)


def test_hsts_solo_sobre_https(client):
    assert "strict-transport-security" not in client.get("/login").headers
    assert "strict-transport-security" in client.get("/login", headers={"X-Forwarded-Proto": "https"}).headers
    assert "strict-transport-security" in client.get("/login", headers={"CF-Visitor": '{"scheme":"https"}'}).headers


def test_csp_no_permite_scripts_inline(client):
    csp = client.get("/login").headers["content-security-policy"]
    script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src, script_src
    assert "default-src 'self'" in csp


def test_arranque_define_la_ruta_de_clave_inicial(client):
    """La rutina de arranque escribe admin_inicial.txt en DATA_DIR; main debe importar ese símbolo."""
    import database
    import main
    assert main.DATA_DIR == database.DATA_DIR


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import ejecutar, verificar_cobertura

    # Orden importante: una prueba desactiva un usuario y otra agota los intentos de login.
    with TestClient(app) as client:
        ua, ub = _crear_usuario("usuario_a"), _crear_usuario("usuario_b")
        ha, hb = _login(client, "usuario_a"), _login(client, "usuario_b")
        hadmin = _login(client, "admin", os.environ["DEFAULT_ADMIN_PASSWORD"])
        registradas = [
            (test_usuario_no_ve_ni_modifica_datos_de_otro, lambda: test_usuario_no_ve_ni_modifica_datos_de_otro(client, ua, ub, ha, hb)),
            (test_claims_forjados_en_jwt_no_cruzan_tenants, lambda: test_claims_forjados_en_jwt_no_cruzan_tenants(client, ua, ub, ha)),
            (test_tokens_invalidos_rechazados, lambda: test_tokens_invalidos_rechazados(client, ua)),
            (test_rbac_admin, lambda: test_rbac_admin(client, ha, hadmin)),
            (test_usuario_desactivado_pierde_acceso_inmediato, lambda: test_usuario_desactivado_pierde_acceso_inmediato(client)),
            (test_todas_las_rutas_api_y_admin_exigen_token, lambda: test_todas_las_rutas_api_y_admin_exigen_token(client)),
            (test_exportar_presupuesto_con_fecha_base_no_da_500, lambda: test_exportar_presupuesto_con_fecha_base_no_da_500(client, ha)),
            (test_jwt_alg_none_y_algoritmos_ajenos_rechazados, lambda: test_jwt_alg_none_y_algoritmos_ajenos_rechazados(client, ua)),
            (test_restore_rechaza_archivos_malos_sin_tocar_datos, lambda: test_restore_rechaza_archivos_malos_sin_tocar_datos(client, ub, hb)),
            (test_restore_solo_afecta_al_propio_tenant, lambda: test_restore_solo_afecta_al_propio_tenant(client, ha, hb)),
            (test_health_publico_y_sin_datos_de_usuarios, lambda: test_health_publico_y_sin_datos_de_usuarios(client)),
            (test_borrar_retiro_de_meta_devuelve_el_dinero, lambda: test_borrar_retiro_de_meta_devuelve_el_dinero(client, ua, ha)),
            (test_reset_no_revela_existencia_de_cuenta, lambda: test_reset_no_revela_existencia_de_cuenta(client)),
            (test_sincronizar_tiene_enfriamiento, lambda: test_sincronizar_tiene_enfriamiento(client, ha)),
            (test_rendimiento_con_fecha_fuera_de_rango_se_rechaza, lambda: test_rendimiento_con_fecha_fuera_de_rango_se_rechaza(client, ha)),
            (test_excel_no_convierte_texto_en_formula, lambda: test_excel_no_convierte_texto_en_formula(client, ha)),
            (test_arranque_define_la_ruta_de_clave_inicial, lambda: test_arranque_define_la_ruta_de_clave_inicial(client)),
            (test_csp_no_permite_scripts_inline, lambda: test_csp_no_permite_scripts_inline(client)),
            (test_tarjetas_por_porcentaje_no_se_guardan_en_cero, lambda: test_tarjetas_por_porcentaje_no_se_guardan_en_cero(client, ha)),
            (test_pagos_del_presupuesto_por_api, lambda: test_pagos_del_presupuesto_por_api(client, ha)),
            (test_msi_en_el_presupuesto_por_api, lambda: test_msi_en_el_presupuesto_por_api(client, ha)),
            (test_hsts_solo_sobre_https, lambda: test_hsts_solo_sobre_https(client)),
            (test_restore_rechaza_triggers_y_textos_enormes, lambda: test_restore_rechaza_triggers_y_textos_enormes(client, hb)),
            (test_login_bloquea_tras_5_fallos, lambda: test_login_bloquea_tras_5_fallos(client)),
            (test_token_deja_de_valer_tras_cambiar_contrasena, lambda: test_token_deja_de_valer_tras_cambiar_contrasena(client, ua)),
        ]
        pruebas = verificar_cobertura(sys.modules[__name__], registradas)
        codigo = ejecutar(pruebas, "test_api_aislamiento")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)
