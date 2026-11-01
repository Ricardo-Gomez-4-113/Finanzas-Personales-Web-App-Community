"""tests/test_api_rutas.py - Cobertura de rutas de inversiones, presupuesto, recurrentes, admin, auth y salud.

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
from datetime import date, datetime, timedelta

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
import routers.inversiones as rinversiones  # noqa: E402
import routers.salud as rsalud  # noqa: E402
from contextlib import contextmanager  # noqa: E402
import routers.salud as rsalud  # noqa: E402
from contextlib import contextmanager  # noqa: E402

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



@contextmanager
def _parchar(modulo, **reemplazos):
    originales = {nombre: getattr(modulo, nombre) for nombre in reemplazos}
    for nombre, valor in reemplazos.items():
        setattr(modulo, nombre, valor)
    try:
        yield
    finally:
        for nombre, valor in originales.items():
            setattr(modulo, nombre, valor)


def test_inversiones_crud_y_validaciones(client):
    _crear_usuario("ruta_inv")
    h = _login(client, "ruta_inv")
    r = client.post("/api/inversiones", json={"ticker": "VOO", "cantidad": 2, "precio_promedio": 100, "precio_actual": 110, "moneda": "USD"}, headers=h)
    assert r.status_code == 201, r.text
    inv = r.json()["id"]
    assert client.post("/api/inversiones", json={"ticker": "VOO", "cantidad": 1, "precio_promedio": 1, "precio_actual": 1}, headers=h).status_code == 400
    assert client.post("/api/inversiones", json={"ticker": "bad ticker!", "cantidad": 1, "precio_promedio": 1}, headers=h).status_code == 422
    assert client.put(f"/api/inversiones/{inv}", json={"cantidad": 5, "precio_actual": 120, "moneda": "EUR"}, headers=h).status_code == 200
    assert client.put("/api/inversiones/999999", json={"cantidad": 5}, headers=h).status_code == 404
    assert client.put(f"/api/inversiones/{inv}", json={"moneda": "GBP"}, headers=h).status_code == 422
    assert client.delete(f"/api/inversiones/{inv}", headers=h).status_code == 200
    assert client.delete(f"/api/inversiones/{inv}", headers=h).status_code == 404


def test_inversiones_con_mercado_simulado_sincronizar_cotizar_y_buscar(client):
    _crear_usuario("ruta_mercado")
    h = _login(client, "ruta_mercado")
    client.post("/api/inversiones", json={"ticker": "MSFT", "cantidad": 3, "precio_promedio": 300, "precio_actual": 300, "moneda": "USD"}, headers=h)
    simulado = {
        "consultar_precio_y_moneda_mercado": lambda t: (412.5, "USD"),
        "cotizar_varios": lambda ts: {t: (412.5, "USD") for t in ts},
        "buscar_tickers_mercado": lambda q: [{"symbol": "MSFT", "name": "Microsoft"}],
    }
    with _parchar(rinversiones, **simulado):
        r = client.post("/api/inversiones/sincronizar", headers=h)
        assert r.status_code == 200 and r.json()["actualizados"], r.text
        r = client.get("/api/inversiones/cotizar/msft", headers=h)
        assert r.status_code == 200 and r.json()["precio"] == 412.5, r.text
        assert client.get("/api/inversiones/buscar?q=micro", headers=h).json() == [{"symbol": "MSFT", "name": "Microsoft"}]
        assert client.get("/api/inversiones/buscar?q=", headers=h).json() == []
    with _parchar(rinversiones, MAX_POSICIONES_SYNC=0):
        assert client.post("/api/inversiones/sincronizar", headers=h).status_code == 422


def test_tipo_de_cambio_privado_y_publico(client):
    _crear_usuario("ruta_fx")
    h = _login(client, "ruta_fx")
    tasas = {"MXN": 19.5, "EUR": 0.92}
    with _parchar(rinversiones, obtener_tasas_cambio=lambda: tasas):
        r = client.get("/api/tipo-cambio", headers=h)
        assert r.status_code == 200, r.text
        r = client.get("/api/public/tipo-cambio")
        assert r.status_code == 200, r.text


def test_rendimiento_ciclo_completo(client):
    _crear_usuario("ruta_rend")
    h = _login(client, "ruta_rend")
    tasas = {"MXN": 20.0, "EUR": 0.5}
    with _parchar(rinversiones, obtener_tasas_cambio=lambda: tasas):
        r = client.post("/api/rendimiento", json={"nombre": "CETES", "capital": 1000, "tasa_anual": 10, "fecha_inicio": "2026-01-01"}, headers=h)
        assert r.status_code == 201, r.text
        cuenta = r.json()["id"]
        for base in ("MXN", "USD", "EUR"):
            r = client.get(f"/api/rendimiento?moneda_base={base}", headers=h)
            assert r.status_code == 200, r.text
        assert client.post(f"/api/rendimiento/{cuenta}/movimiento", json={"monto": 500, "accion": "aportar"}, headers=h).status_code == 200
        assert client.post(f"/api/rendimiento/{cuenta}/movimiento", json={"monto": 200, "accion": "retirar"}, headers=h).status_code == 200
        assert client.post(f"/api/rendimiento/{cuenta}/movimiento", json={"monto": 1e11, "accion": "retirar"}, headers=h).status_code in (400, 422)
        assert client.post("/api/rendimiento/999999/movimiento", json={"monto": 1, "accion": "aportar"}, headers=h).status_code == 404
        assert client.put(f"/api/rendimiento/{cuenta}", json={"nombre": "CETES 28", "tasa_anual": 9.5}, headers=h).status_code == 200
        assert client.put("/api/rendimiento/999999", json={"nombre": "X"}, headers=h).status_code == 404
        assert client.delete(f"/api/rendimiento/{cuenta}", headers=h).status_code == 200
        assert client.delete(f"/api/rendimiento/{cuenta}", headers=h).status_code == 404


def test_presupuesto_config_unidades_y_tarjetas(client):
    _crear_usuario("ruta_pres")
    h = _login(client, "ruta_pres")
    assert client.get("/api/presupuesto", headers=h).status_code == 200
    cfg = {"ingreso_base": 20000, "frecuencia": "quincenal", "dia_pago": 15, "subdivision_tipo": "ninguna"}
    assert client.put("/api/presupuesto/config", json=cfg, headers=h).status_code == 200
    assert client.put("/api/presupuesto/config", json={"ingreso_base": 1, "frecuencia": "anual"}, headers=h).status_code == 422
    assert client.put("/api/presupuesto/unidades/1", json={"dia_pago": 10}, headers=h).status_code == 200
    assert client.put("/api/presupuesto/unidades/1", json={"dia_pago": 40}, headers=h).status_code == 422

    r = client.post("/api/presupuesto/tarjetas", json={"nombre": "Comida", "porcentaje": 30, "notas": "super", "unidad_orden": 1}, headers=h)
    assert r.status_code in (200, 201), r.text
    tarjeta = r.json()["id"]
    assert client.post("/api/presupuesto/tarjetas", json={"nombre": "Mal", "porcentaje": 150}, headers=h).status_code == 422
    assert client.patch(f"/api/presupuesto/tarjetas/{tarjeta}", json={"nombre": "Super", "porcentaje": 25}, headers=h).status_code == 200
    assert client.patch("/api/presupuesto/tarjetas/999999", json={"nombre": "X"}, headers=h).status_code == 404
    assert client.delete(f"/api/presupuesto/tarjetas/{tarjeta}", headers=h).status_code == 200
    assert client.delete(f"/api/presupuesto/tarjetas/{tarjeta}", headers=h).status_code == 404


def test_presupuesto_exporta_fijos(client):
    _crear_usuario("ruta_pres_fijos")
    h = _login(client, "ruta_pres_fijos")
    assert client.put("/api/presupuesto/config", json={"ingreso_base": 20000, "frecuencia": "quincenal", "dia_pago": 15, "subdivision_tipo": "ninguna"}, headers=h).status_code == 200
    r = client.post("/api/presupuesto/tarjetas", json={"nombre": "Hogar", "porcentaje": 30, "notas": "", "unidad_orden": 2}, headers=h)
    assert r.status_code in (200, 201), r.text
    tarjeta = r.json()["id"]
    for orden in (2,):
        assert client.put(f"/api/presupuesto/unidades/{orden}", json={"dia_pago": 10}, headers=h).status_code == 200
    r = client.post("/api/presupuesto/exportar-a-fijos", json={"tarjetas_ids": [tarjeta]}, headers=h)
    assert r.status_code == 200, r.text


def test_recurrentes_listar_editar_saltar_y_borrar(client):
    _crear_usuario("ruta_rec")
    h = _login(client, "ruta_rec")
    ingreso = client.post("/api/recurrentes", json={"descripcion": "Sueldo", "tipo": "ingreso", "monto": 10000, "frecuencia": "quincenal", "fecha_inicio": "2026-10-01", "categoria_id": 12}, headers=h).json()["id"]
    gasto = client.post("/api/recurrentes", json={"descripcion": "Gym", "tipo": "gasto", "monto": 500, "frecuencia": "mensual", "fecha_inicio": "2026-10-01", "categoria_id": 7}, headers=h).json()["id"]
    msi = client.post("/api/recurrentes", json={"descripcion": "Tele", "tipo": "gasto", "monto_total": 1200, "meses": 3, "frecuencia": "mensual", "fecha_inicio": "2026-10-01"}, headers=h)
    assert msi.status_code in (200, 201), msi.text
    msi = msi.json()["id"]
    assert client.get("/api/recurrentes", headers=h).status_code == 200
    assert client.get("/api/recurrentes/periodos", headers=h).status_code == 200
    assert client.patch(f"/api/recurrentes/{gasto}", json={"descripcion": "Gimnasio", "monto": 550}, headers=h).status_code == 200
    assert client.patch("/api/recurrentes/999999", json={"monto": 1}, headers=h).status_code == 404
    assert client.post(f"/api/recurrentes/{gasto}/saltar", json={"contabilizar_cuota": False}, headers=h).status_code == 200
    assert client.post(f"/api/recurrentes/{msi}/saltar", json={"contabilizar_cuota": True}, headers=h).status_code == 200
    assert client.post("/api/recurrentes/999999/saltar", json={}, headers=h).status_code == 404
    assert client.delete(f"/api/recurrentes/{msi}", headers=h).status_code == 200
    assert client.delete(f"/api/recurrentes/{msi}", headers=h).status_code == 404
    assert client.delete(f"/api/recurrentes/{gasto}", headers=h).status_code == 200
    assert client.delete(f"/api/recurrentes/{ingreso}", headers=h).status_code == 200


def test_admin_lista_edita_suspende_y_elimina(client, hadmin):
    assert client.get("/admin/stats", headers=hadmin).status_code == 200
    assert client.get("/admin/users", headers=hadmin).status_code == 200
    r = client.post("/admin/users", json={"username": "ruta_adm", "password": PASS, "role": "user"}, headers=hadmin)
    assert r.status_code == 201, r.text
    uid = r.json()["user"]["id"]
    assert client.patch(f"/admin/users/{uid}/status", json={"is_active": False}, headers=hadmin).status_code == 200
    assert client.post("/auth/login", json={"username": "ruta_adm", "password": PASS}).status_code in (401, 403)
    assert client.patch(f"/admin/users/{uid}/status", json={"is_active": True}, headers=hadmin).status_code == 200
    assert client.patch(f"/admin/users/{uid}", json={"email": "adm@test.local", "role": "admin"}, headers=hadmin).status_code == 200
    assert client.patch(f"/admin/users/{uid}", json={"password": "corta"}, headers=hadmin).status_code == 422
    assert client.patch("/admin/users/999999", json={"email": "x@test.local"}, headers=hadmin).status_code == 404
    assert client.patch("/admin/users/999999/status", json={"is_active": False}, headers=hadmin).status_code == 404
    assert client.delete(f"/admin/users/{uid}", headers=hadmin).status_code == 200
    assert client.delete(f"/admin/users/{uid}", headers=hadmin).status_code == 404


def test_perfil_propio_se_lee_y_se_edita(client):
    _crear_usuario("ruta_perfil")
    h = _login(client, "ruta_perfil")
    r = client.get("/auth/me", headers=h)
    assert r.status_code == 200 and r.json()["user"]["username"] == "ruta_perfil", r.text
    assert client.patch("/auth/profile", json={"email": "perfil@test.local"}, headers=h).status_code == 200
    assert client.patch("/auth/profile", json={"username": "usuario_a"}, headers=h).status_code == 400
    assert client.patch("/auth/profile", json={"username": "no válido!"}, headers=h).status_code == 422


def test_recuperacion_no_revela_cuentas_y_valida_el_codigo(client):
    _crear_usuario("ruta_olvido")
    # Sin correo configurado la respuesta no depende de que la cuenta exista (no se revelan usuarios).
    existente = client.post("/auth/forgot-password", json={"identifier": "ruta_olvido"})
    inexistente = client.post("/auth/forgot-password", json={"identifier": "nadie_asi_existe"})
    assert (existente.status_code, existente.text) == (inexistente.status_code, inexistente.text), (existente.text, inexistente.text)
    assert client.post("/auth/reset-password", json={"identifier": "nadie_asi", "code": "123456", "new_password": PASS}).status_code == 400
    assert client.post("/auth/reset-password", json={"identifier": "nadie_asi", "code": "12ab", "new_password": PASS}).status_code == 422
    assert client.post("/auth/reset-password", json={"identifier": "nadie_asi", "code": "123456", "new_password": "corta"}).status_code == 422


def test_cuenta_propia_se_elimina_y_deja_de_entrar(client):
    _crear_usuario("ruta_borrar")
    h = _login(client, "ruta_borrar")
    assert client.delete("/auth/delete-account", headers=h).status_code == 200
    assert client.post("/auth/login", json={"username": "ruta_borrar", "password": PASS}).status_code == 401


def test_salud_degradada_si_el_respaldo_vence(client):
    admin = _login(client, "admin", os.environ["DEFAULT_ADMIN_PASSWORD"])
    with _parchar(rsalud, _horas_desde_ultimo_respaldo=lambda: 30.0):
        r = client.get("/api/salud/detalle", headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "degradado" and r.json()["respaldo"] == "vencido", r.text
    with _parchar(rsalud, _horas_desde_ultimo_respaldo=lambda: None):
        r = client.get("/api/salud/detalle", headers=admin)
    assert r.json()["respaldo"] == "vencido" and r.json()["horas_desde_respaldo"] is None, r.text


def test_apartado_solo_se_borra_sin_saldo(client):
    _crear_usuario("ruta_apartado")
    h = _login(client, "ruta_apartado")
    assert client.post("/api/transacciones", json={"fecha": "2026-10-01", "monto": 1000, "tipo": "ingreso", "categoria_id": 12, "descripcion": "base"}, headers=h).status_code in (200, 201)
    assert client.post("/api/apartados", json={"nombre": "Coche", "tipo": "meta", "meta_monto": 5000}, headers=h).status_code in (200, 201)
    apartado = next(a["id"] for a in client.get("/api/apartados", headers=h).json() if a["nombre"] == "Coche")
    assert client.post(f"/api/apartados/{apartado}/movimiento", json={"monto": 300, "accion": "depositar"}, headers=h).status_code in (200, 201)
    assert client.delete(f"/api/apartados/{apartado}", headers=h).status_code == 400
    assert client.post(f"/api/apartados/{apartado}/movimiento", json={"monto": 300, "accion": "retirar"}, headers=h).status_code in (200, 201)
    assert client.delete(f"/api/apartados/{apartado}", headers=h).status_code == 200
    assert client.delete(f"/api/apartados/{apartado}", headers=h).status_code == 404


def test_salud_lee_la_antiguedad_del_ultimo_respaldo(client):
    carpeta = os.path.join(TMP, "respaldos_salud")
    os.makedirs(os.path.join(carpeta, datetime.now().strftime("%Y%m%d_%H%M%S")), exist_ok=True)
    admin = _login(client, "admin", os.environ["DEFAULT_ADMIN_PASSWORD"])
    with _parchar(rsalud, RESPALDOS_DIR=carpeta):
        r = client.get("/api/salud/detalle", headers=admin)
    assert r.json()["respaldo"] == "ok" and r.json()["horas_desde_respaldo"] < 1, r.text
    with _parchar(rsalud, RESPALDOS_DIR=os.path.join(carpeta, "no_existe")):
        assert rsalud._horas_desde_ultimo_respaldo() is None


def test_ingreso_fijo_que_sostiene_gastos_no_se_borra(client):
    _crear_usuario("ruta_ingreso")
    h = _login(client, "ruta_ingreso")
    sueldo = client.post("/api/recurrentes", json={"descripcion": "Sueldo", "tipo": "ingreso", "monto": 10000, "frecuencia": "quincenal", "fecha_inicio": "2026-10-01", "categoria_id": 12}, headers=h).json()["id"]
    client.post("/api/recurrentes", json={"descripcion": "Renta", "tipo": "gasto", "monto": 9000, "frecuencia": "mensual", "fecha_inicio": "2026-10-01", "categoria_id": 4}, headers=h)
    assert client.delete(f"/api/recurrentes/{sueldo}", headers=h).status_code == 422
    otro = client.post("/api/recurrentes", json={"descripcion": "Extra chico", "tipo": "ingreso", "monto": 1000, "frecuencia": "mensual", "fecha_inicio": "2026-10-01", "categoria_id": 12}, headers=h).json()["id"]
    assert client.delete(f"/api/recurrentes/{sueldo}", headers=h).status_code == 422
    assert client.delete(f"/api/recurrentes/{otro}", headers=h).status_code == 200
    grande = client.post("/api/recurrentes", json={"descripcion": "Otro sueldo", "tipo": "ingreso", "monto": 20000, "frecuencia": "mensual", "fecha_inicio": "2026-10-01", "categoria_id": 12}, headers=h).json()["id"]
    assert client.delete(f"/api/recurrentes/{sueldo}", headers=h).status_code == 200
    assert client.delete(f"/api/recurrentes/{grande}", headers=h).status_code in (200, 422)



def test_dos_pestanas_editan_el_mismo_movimiento_y_la_segunda_recibe_409(client):
    """Con versión, una edición vieja no pisa el cambio de otra pestaña; al recargar y repetir, guarda."""
    _crear_usuario("ruta_dos_pestanas")
    h = _login(client, "ruta_dos_pestanas")
    client.post("/api/transacciones", json={"fecha": "2026-10-01", "monto": 1000, "tipo": "ingreso", "categoria_id": 12, "descripcion": "base"}, headers=h)
    assert client.post("/api/transacciones", json={"fecha": "2026-10-02", "monto": 100, "tipo": "gasto", "categoria_id": 2, "descripcion": "original"}, headers=h).status_code in (200, 201)
    mov = next(t for t in client.get("/api/transacciones", headers=h).json() if t["descripcion"] == "original")
    version_inicial = mov["version"]

    # Ambas pestañas abrieron el movimiento con la misma versión
    r = client.put(f"/api/transacciones/{mov['id']}", json={"monto": 150, "version_esperada": version_inicial}, headers=h)
    assert r.status_code == 200, r.text
    r = client.put(f"/api/transacciones/{mov['id']}", json={"monto": 175, "descripcion": "de-la-otra-pestana", "version_esperada": version_inicial}, headers=h)
    assert r.status_code == 409, r.text
    assert "cambió" in r.json()["detail"]
    tras_conflicto = next(t for t in client.get("/api/transacciones", headers=h).json() if t["id"] == mov["id"])
    assert tras_conflicto["monto"] == 150 and tras_conflicto["descripcion"] == "original", tras_conflicto
    assert tras_conflicto["version"] == version_inicial + 1

    # Al recargar, la pestaña ve la versión actual y guarda sin conflicto
    r = client.put(f"/api/transacciones/{mov['id']}", json={"monto": 175, "descripcion": "de-la-otra-pestana", "version_esperada": tras_conflicto["version"]}, headers=h)
    assert r.status_code == 200, r.text
    final = next(t for t in client.get("/api/transacciones", headers=h).json() if t["id"] == mov["id"])
    assert final["monto"] == 175 and final["descripcion"] == "de-la-otra-pestana", final


def test_edicion_sin_version_conserva_la_ultima_escritura(client):
    """Clientes que no envían version_esperada siguen con el comportamiento anterior: gana la última escritura."""
    _crear_usuario("ruta_sin_version")
    h = _login(client, "ruta_sin_version")
    client.post("/api/transacciones", json={"fecha": "2026-10-01", "monto": 1000, "tipo": "ingreso", "categoria_id": 12, "descripcion": "base"}, headers=h)
    client.post("/api/transacciones", json={"fecha": "2026-10-02", "monto": 100, "tipo": "gasto", "categoria_id": 2, "descripcion": "original"}, headers=h)
    mov = next(t["id"] for t in client.get("/api/transacciones", headers=h).json() if t["descripcion"] == "original")
    assert client.put(f"/api/transacciones/{mov}", json={"monto": 150}, headers=h).status_code == 200
    assert client.put(f"/api/transacciones/{mov}", json={"monto": 175}, headers=h).status_code == 200
    final = next(t for t in client.get("/api/transacciones", headers=h).json() if t["id"] == mov)
    assert final["monto"] == 175, final


def test_escritura_concurrente_entre_la_lectura_y_el_guardado_da_409(client):
    """Otra conexión cambia el movimiento justo después de leerlo: el UPDATE no pisa y responde 409 (cláusula AND version = ?)."""
    import database
    import routers.movimientos as rmovimientos

    usuario = _crear_usuario("ruta_carrera")
    h = _login(client, "ruta_carrera")
    assert client.post("/api/transacciones", json={"fecha": "2026-10-03", "monto": 500, "tipo": "ingreso", "categoria_id": 12, "descripcion": "ingreso-carrera"}, headers=h).status_code in (200, 201)
    mov = next(t for t in client.get("/api/transacciones", headers=h).json() if t["descripcion"] == "ingreso-carrera")

    # validar_edicion_transaccion se llama entre la lectura de la versión y el UPDATE, solo en ingresos
    original = rmovimientos.validar_edicion_transaccion

    def otra_conexion_guarda_antes(*args, **kwargs):
        resultado = original(*args, **kwargs)
        conexion = sqlite3.connect(database.get_tenant_path_by_id(usuario["id"]))
        try:
            conexion.execute("UPDATE transacciones SET version = version + 1 WHERE id = ?", (mov["id"],))
            conexion.commit()
        finally:
            conexion.close()
        return resultado

    rmovimientos.validar_edicion_transaccion = otra_conexion_guarda_antes
    try:
        r = client.put(f"/api/transacciones/{mov['id']}", json={"descripcion": "guardado-tardio", "version_esperada": mov["version"]}, headers=h)
    finally:
        rmovimientos.validar_edicion_transaccion = original
    assert r.status_code == 409, r.text
    tras = next(t for t in client.get("/api/transacciones", headers=h).json() if t["id"] == mov["id"])
    assert tras["descripcion"] == "ingreso-carrera", tras
    assert tras["version"] == mov["version"] + 1, tras


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import ejecutar, verificar_cobertura

    with TestClient(app) as client:
        _crear_usuario("usuario_a")
        hadmin = _login(client, "admin", os.environ["DEFAULT_ADMIN_PASSWORD"])
        registradas = [
            (test_inversiones_crud_y_validaciones, lambda: test_inversiones_crud_y_validaciones(client)),
            (test_inversiones_con_mercado_simulado_sincronizar_cotizar_y_buscar, lambda: test_inversiones_con_mercado_simulado_sincronizar_cotizar_y_buscar(client)),
            (test_tipo_de_cambio_privado_y_publico, lambda: test_tipo_de_cambio_privado_y_publico(client)),
            (test_rendimiento_ciclo_completo, lambda: test_rendimiento_ciclo_completo(client)),
            (test_presupuesto_config_unidades_y_tarjetas, lambda: test_presupuesto_config_unidades_y_tarjetas(client)),
            (test_presupuesto_exporta_fijos, lambda: test_presupuesto_exporta_fijos(client)),
            (test_recurrentes_listar_editar_saltar_y_borrar, lambda: test_recurrentes_listar_editar_saltar_y_borrar(client)),
            (test_admin_lista_edita_suspende_y_elimina, lambda: test_admin_lista_edita_suspende_y_elimina(client, hadmin)),
            (test_perfil_propio_se_lee_y_se_edita, lambda: test_perfil_propio_se_lee_y_se_edita(client)),
            (test_recuperacion_no_revela_cuentas_y_valida_el_codigo, lambda: test_recuperacion_no_revela_cuentas_y_valida_el_codigo(client)),
            (test_cuenta_propia_se_elimina_y_deja_de_entrar, lambda: test_cuenta_propia_se_elimina_y_deja_de_entrar(client)),
            (test_salud_degradada_si_el_respaldo_vence, lambda: test_salud_degradada_si_el_respaldo_vence(client)),
            (test_apartado_solo_se_borra_sin_saldo, lambda: test_apartado_solo_se_borra_sin_saldo(client)),
            (test_dos_pestanas_editan_el_mismo_movimiento_y_la_segunda_recibe_409, lambda: test_dos_pestanas_editan_el_mismo_movimiento_y_la_segunda_recibe_409(client)),
            (test_edicion_sin_version_conserva_la_ultima_escritura, lambda: test_edicion_sin_version_conserva_la_ultima_escritura(client)),
            (test_escritura_concurrente_entre_la_lectura_y_el_guardado_da_409, lambda: test_escritura_concurrente_entre_la_lectura_y_el_guardado_da_409(client)),
            (test_salud_lee_la_antiguedad_del_ultimo_respaldo, lambda: test_salud_lee_la_antiguedad_del_ultimo_respaldo(client)),
            (test_ingreso_fijo_que_sostiene_gastos_no_se_borra, lambda: test_ingreso_fijo_que_sostiene_gastos_no_se_borra(client)),
        ]
        pruebas = verificar_cobertura(sys.modules[__name__], registradas)
        codigo = ejecutar(pruebas, "test_api_rutas")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)
