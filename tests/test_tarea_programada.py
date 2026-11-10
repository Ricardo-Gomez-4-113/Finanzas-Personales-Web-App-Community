"""tests/test_tarea_programada.py - La tarea que materializa los fijos y genera los avisos aunque nadie abra la app.

El reloj de la prueba es FINANZAS_HOY: el test arma las cuentas un día y luego «pasa el tiempo» sin hacer ninguna petición de esa persona.
Requiere fastapi + httpx (se ejecuta en Docker, ver tests/run_tests.sh). Copia el código a un directorio temporal: nunca toca 'data/' del proyecto.
"""

import asyncio
import os
import shutil
import sys
import tempfile
import threading

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
os.environ.pop("NOTIFICACIONES_TAREA", None)
os.environ.pop("NOTIFICACIONES_PERIODO_MIN", None)

from fastapi.testclient import TestClient  # noqa: E402

import master_db  # noqa: E402
import security  # noqa: E402
import tarea_programada  # noqa: E402
from database import get_tenant_conn  # noqa: E402
from main import app  # noqa: E402

PASS = "Passw0rd#Test1"
_reloj = {"t": 1_000_000.0}


def _hoy(texto):
    os.environ["FINANZAS_HOY"] = f"{texto}T10:00:00"


def _otra_hora():
    """Un instante de candado nuevo, siempre más de un periodo después del anterior: cada prueba parte con la tarea libre."""
    _reloj["t"] += 100_000
    return _reloj["t"]


def _cuenta(client, nombre):
    fila = master_db.create_user_entry(nombre, security.hash_password(PASS), email=f"{nombre}@test.local")
    r = client.post("/auth/login", json={"username": nombre, "password": PASS})
    assert r.status_code == 200, r.text
    return fila, {"Authorization": f"Bearer {r.json()['access_token']}"}


def _armar(client, cab, aporte=300, objetivo=10000):
    """Sueldo mensual desde el 10 y un aporte a una meta desde el 12 de noviembre: nada ocurre todavía (hoy es 9)."""
    _hoy("2026-11-09")
    assert client.post("/api/apartados", json={"nombre": "Casa", "tipo": "meta", "meta_monto": objetivo}, headers=cab).status_code == 201
    meta = client.get("/api/apartados", headers=cab).json()[0]["id"]
    for cuerpo in ({"descripcion": "Sueldo", "tipo": "ingreso", "monto": 10000, "frecuencia": "mensual", "fecha_inicio": "2026-11-10", "categoria_id": 12},
                   {"descripcion": "Ahorro casa", "tipo": "gasto", "monto": aporte, "frecuencia": "mensual", "fecha_inicio": "2026-11-12", "categoria_id": 7, "apartado_id": meta}):
        r = client.post("/api/recurrentes", json=cuerpo, headers=cab)
        assert r.status_code in (200, 201), r.text
    return meta


def _en_la_base(fila, sql):
    conn = get_tenant_conn(fila["db_name"])
    try:
        return [tuple(f) for f in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def test_la_ronda_crea_la_aportacion_y_su_aviso_sin_que_la_persona_abra_nada(client):
    fila, cab = _cuenta(client, "tarea_ausente")
    _armar(client, cab, aporte=300, objetivo=500)   # dos aportes cumplen la meta: 300 y luego 200
    assert _en_la_base(fila, "SELECT COUNT(*) FROM transacciones") == [(0,)]
    _hoy("2026-12-20")   # pasó un mes y la persona nunca volvió: ninguna petición suya de por medio
    resumen = tarea_programada.ronda(ahora=_otra_hora())
    assert resumen and resumen["errores"] == 0 and resumen["movimientos"] >= 4, resumen   # 2 nóminas y los 2 aportes (300 y 200)
    movs = _en_la_base(fila, "SELECT fecha, monto, tipo, apartado_id IS NOT NULL FROM transacciones WHERE tipo = 'gasto' ORDER BY fecha")
    assert [(m[0], m[1]) for m in movs] == [("2026-11-12", 300.0), ("2026-12-12", 200.0)], movs
    avisos = _en_la_base(fila, "SELECT tipo FROM notificaciones")
    assert avisos == [("meta_cumplida",)], avisos
    # Y cuando la persona por fin entra, la bandeja ya lo tiene
    assert client.get("/api/notificaciones", headers=cab).json()["no_leidas"] == 1


def test_sin_saldo_la_ronda_omite_el_aporte_y_deja_el_aviso(client):
    fila, cab = _cuenta(client, "tarea_sin_saldo")
    _armar(client, cab, aporte=9900)   # el sueldo es de 10 000
    conn = get_tenant_conn(fila["db_name"])   # un gasto que la persona ya hizo el 11 (la app lo habría validado contra su saldo de entonces)
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion, rubro) VALUES ('2026-11-11', 500000, 'gasto', 7, 'Renta', 'normales')")
    conn.commit()
    conn.close()
    _hoy("2026-11-20")
    tarea_programada.ronda(ahora=_otra_hora())
    assert _en_la_base(fila, "SELECT COUNT(*) FROM transacciones WHERE apartado_id IS NOT NULL") == [(0,)]
    avisos = client.get("/api/notificaciones", headers=cab).json()["notificaciones"]
    assert [a["tipo"] for a in avisos] == ["aporte_omitido"] and "no había saldo" in avisos[0]["texto"], avisos


def test_la_ronda_no_se_repite_antes_de_tiempo_ni_corre_dos_a_la_vez(client):
    t = _otra_hora()
    assert tarea_programada.ronda(ahora=t) is not None
    assert tarea_programada.ronda(ahora=t + 60) is None, "dentro del periodo no se repite"
    assert tarea_programada.ronda(ahora=t + tarea_programada.periodo_seg()) is not None
    # Con el candado tomado por otro proceso, esta ronda no corre; cuando el candado vence (el otro murió), sí
    t = _otra_hora()
    assert master_db.adquirir_tarea(tarea_programada.NOMBRE, 600, 0, ahora=t)
    assert tarea_programada.ronda(ahora=t + 5) is None
    assert tarea_programada.ronda(ahora=t + 601 + tarea_programada.periodo_seg()) is not None

    # Ocho procesos a la vez, mismo instante: gana uno solo
    t = _otra_hora()
    resultados, barrera = [], threading.Barrier(8)

    def correr():
        barrera.wait()
        resultados.append(tarea_programada.ronda(ahora=t))

    hilos = [threading.Thread(target=correr) for _ in range(8)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert sum(r is not None for r in resultados) == 1, resultados


def test_una_cuenta_rota_no_frena_a_las_demas_y_una_desactivada_no_se_toca(client):
    rota, _ = _cuenta(client, "tarea_rota")
    buena, cab = _cuenta(client, "tarea_buena")
    apagada, _ = _cuenta(client, "tarea_apagada")
    master_db.set_user_status(apagada["id"], 0)
    _armar(client, cab)
    _hoy("2026-11-20")
    abiertas = []
    original = tarea_programada.get_tenant_conn

    def abrir(db_name):
        abiertas.append(db_name)
        if db_name == rota["db_name"]:
            raise RuntimeError("base dañada con datos secretos")
        return original(db_name)

    tarea_programada.get_tenant_conn = abrir
    try:
        resumen = tarea_programada.ronda(ahora=_otra_hora())
    finally:
        tarea_programada.get_tenant_conn = original
    assert resumen["errores"] == 1 and resumen["cuentas"] >= 1 and resumen["movimientos"] >= 2, resumen
    assert apagada["db_name"] not in abiertas, "una cuenta desactivada no se abre"
    assert _en_la_base(buena, "SELECT COUNT(*) FROM transacciones WHERE recurrente_id IS NOT NULL")[0][0] >= 2
    registro = open(os.path.join(LOGS, "app.log"), encoding="utf-8").read()
    assert "tarea_materializar_fallo" in registro and "RuntimeError" in registro and "datos secretos" not in registro


def test_una_cuenta_suspendida_o_cancelada_no_se_toca_y_al_reactivarla_no_aparece_nada_de_ese_tiempo(client):
    activa, cab = _cuenta(client, "tarea_activa")
    suspendida, cab_s = _cuenta(client, "tarea_suspendida")
    cancelada, cab_c = _cuenta(client, "tarea_cancelada")
    for cuenta, cabecera in ((activa, cab), (suspendida, cab_s), (cancelada, cab_c)):
        _armar(client, cabecera)
    master_db.update_subscription_status(suspendida["id"], "suspended")
    master_db.update_subscription_status(cancelada["id"], "cancelled")
    _hoy("2026-12-20")
    abiertas = []
    original = tarea_programada.get_tenant_conn
    tarea_programada.get_tenant_conn = lambda db: (abiertas.append(db), original(db))[1]
    try:
        tarea_programada.ronda(ahora=_otra_hora())
    finally:
        tarea_programada.get_tenant_conn = original
    assert activa["db_name"] in abiertas and suspendida["db_name"] not in abiertas and cancelada["db_name"] not in abiertas, abiertas
    assert _en_la_base(suspendida, "SELECT COUNT(*) FROM transacciones") == [(0,)]
    assert _en_la_base(cancelada, "SELECT COUNT(*) FROM transacciones") == [(0,)]
    assert _en_la_base(activa, "SELECT COUNT(*) FROM transacciones")[0][0] >= 2


def test_una_cuenta_borrada_o_suspendida_en_plena_ronda_no_se_procesa_ni_deja_una_base_vacia(client):
    primera, cab1 = _cuenta(client, "tarea_ronda_1")
    segunda, cab2 = _cuenta(client, "tarea_ronda_2")
    _armar(client, cab1)
    _armar(client, cab2)
    _hoy("2026-12-20")
    original = tarea_programada.get_tenant_conn
    calls = []

    def abrir(db):
        calls.append(db)
        if db == primera["db_name"]:   # mientras se procesa la primera, la segunda se elimina
            master_db.delete_user_and_tenant(segunda["id"])
        return original(db)

    ruta_segunda = tarea_programada.get_tenant_path(segunda["db_name"])
    tarea_programada.get_tenant_conn = abrir
    try:
        tarea_programada.ronda(ahora=_otra_hora())
    finally:
        tarea_programada.get_tenant_conn = original
    assert segunda["db_name"] not in calls, "una cuenta borrada a media ronda no se abre"
    assert not os.path.exists(ruta_segunda), "y no se recrea su base vacía"


def test_una_ronda_lenta_no_suelta_el_candado_de_otra_que_ya_tomo_la_tarea(client):
    t = _otra_hora()
    assert master_db.adquirir_tarea(tarea_programada.NOMBRE, 600, 0, ahora=t)
    # el candado venció y otro proceso tomó la tarea más tarde
    assert master_db.adquirir_tarea(tarea_programada.NOMBRE, 600, 0, ahora=t + 700)
    master_db.liberar_tarea(tarea_programada.NOMBRE, t)   # el primero termina tarde y quiere soltar «su» candado
    assert master_db.adquirir_tarea(tarea_programada.NOMBRE, 600, 0, ahora=t + 800) is False, "el candado del segundo sigue puesto"
    master_db.liberar_tarea(tarea_programada.NOMBRE, t + 700)
    assert master_db.adquirir_tarea(tarea_programada.NOMBRE, 600, 0, ahora=t + 900)


def test_la_tarea_se_activa_y_se_ajusta_por_entorno():
    assert tarea_programada.activa() is False
    for valor, esperado in (("1", True), (" 1 ", True), ("0", False), ("true", False), ("", False)):
        os.environ["NOTIFICACIONES_TAREA"] = valor
        assert tarea_programada.activa() is esperado, valor
    del os.environ["NOTIFICACIONES_TAREA"]
    for valor, esperado in (("", 3600), ("15", 900), ("0", 60), ("-5", 60), ("99999", 86400), ("abc", 3600), ("1", 60)):
        os.environ["NOTIFICACIONES_PERIODO_MIN"] = valor
        assert tarea_programada.periodo_seg() == esperado, (valor, tarea_programada.periodo_seg())
    del os.environ["NOTIFICACIONES_PERIODO_MIN"]


def test_el_bucle_corre_una_ronda_sobrevive_a_un_fallo_y_se_cancela_limpio():
    llamadas = []
    ronda_original, retraso, periodo = tarea_programada.ronda, tarea_programada.RETRASO_INICIAL_SEG, tarea_programada.periodo_seg

    def ronda_falsa(*a, **k):
        llamadas.append(1)
        if len(llamadas) == 1:
            raise RuntimeError("la primera ronda falla")

    async def probar():
        tarea = asyncio.create_task(tarea_programada.bucle())
        for _ in range(100):
            if len(llamadas) >= 2:
                break
            await asyncio.sleep(0.02)
        tarea.cancel()
        try:
            await tarea
        except asyncio.CancelledError:
            return True
        return False

    tarea_programada.ronda, tarea_programada.RETRASO_INICIAL_SEG, tarea_programada.periodo_seg = ronda_falsa, 0, lambda: 0.01
    try:
        assert asyncio.run(probar()) is True
    finally:
        tarea_programada.ronda, tarea_programada.RETRASO_INICIAL_SEG, tarea_programada.periodo_seg = ronda_original, retraso, periodo
    assert len(llamadas) >= 2, "tras fallar la primera ronda, el bucle siguió con la segunda"


def test_la_app_solo_arranca_la_tarea_si_se_pide():
    arrancadas = []

    async def falsa():
        arrancadas.append(1)
        await asyncio.sleep(60)

    original = tarea_programada.bucle
    tarea_programada.bucle = falsa
    try:
        with TestClient(app):
            pass
        assert arrancadas == [], "sin NOTIFICACIONES_TAREA=1 no arranca"
        os.environ["NOTIFICACIONES_TAREA"] = "1"
        with TestClient(app):
            import time as _t
            _t.sleep(0.3)
        assert arrancadas == [1]
    finally:
        tarea_programada.bucle = original
        os.environ.pop("NOTIFICACIONES_TAREA", None)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(RAIZ, "tests"))
    from ejecutor import descubrir, ejecutar

    with TestClient(app) as client:
        pruebas = [(n, (lambda f=f: f(client) if f.__code__.co_argcount else f())) for n, f in descubrir(sys.modules[__name__])]
        codigo = ejecutar(pruebas, "test_tarea_programada")
    shutil.rmtree(TMP, ignore_errors=True)
    shutil.rmtree(LOGS, ignore_errors=True)
    sys.exit(codigo)
