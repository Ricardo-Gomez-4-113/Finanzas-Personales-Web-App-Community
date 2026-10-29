"""tests/test_escenario_api.py - Flujo completo de funcionamiento por API con el escenario de referencia.

Cada cifra se compara contra ESPERADO (calculado a mano en tests/escenario.py), no contra el código.
Corre con el reloj fijo (FINANZAS_MODO_PRUEBA + FINANZAS_HOY) para que el resultado sea reproducible.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import escenario  # noqa: E402

escenario.activar_modo_prueba()

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="finanzas_escenario_")
shutil.copytree(
    RAIZ, TMP, dirs_exist_ok=True,
    ignore=shutil.ignore_patterns("data", ".git", "tests", "__pycache__", "*.db", "backups", "venv", ".venv", "node_modules"),
)
os.chdir(TMP)
sys.path.insert(0, TMP)
os.environ.setdefault("JWT_SECRET_KEY", "k" * 48)
os.environ["DEFAULT_ADMIN_PASSWORD"] = "AdminTest#12345"

import security  # noqa: E402
import master_db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402

CLIENT = None
ESTADO = {}


def _preparar():
    """Crea el usuario de referencia, lo siembra y guarda sus datos para las pruebas."""
    master_db.create_user_entry(escenario.USUARIO, security.hash_password(escenario.CLAVE), email="escenario@test.local")
    r = CLIENT.post("/auth/login", json={"username": escenario.USUARIO, "password": escenario.CLAVE})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    ESTADO["h"] = h
    ESTADO["ids"] = escenario.sembrar(CLIENT, h)


def _resumen():
    r = CLIENT.get("/api/resumen?moneda_base=MXN", headers=ESTADO["h"])
    assert r.status_code == 200, r.text
    return r.json()


def _apartado(aid):
    return next(a for a in CLIENT.get("/api/apartados", headers=ESTADO["h"]).json() if a["id"] == aid)


def test_cifras_del_resumen():
    r = _resumen()
    assert r["balance"] == escenario.ESPERADO["disponible"], r["balance"]
    assert r["apartados"] == escenario.ESPERADO["apartados"], r["apartados"]
    assert r["patrimonio_neto"] == escenario.ESPERADO["patrimonio_neto"], r["patrimonio_neto"]
    assert r["deuda_msi"] == escenario.ESPERADO["deuda_msi"], r["deuda_msi"]


def test_mes_actual_excluye_metas():
    mes = _resumen()["mes_actual"]
    assert mes["ingresos"] == escenario.ESPERADO["mes_actual"]["ingresos"], mes
    assert mes["gastos"] == escenario.ESPERADO["mes_actual"]["gastos"], mes


def test_historial_mensual_coincide_con_el_mes_actual():
    historial = CLIENT.get("/api/resumen-mensual", headers=ESTADO["h"]).json()
    assert [(m["mes"], m["ingresos"], m["gastos"]) for m in historial] == \
        [(e["mes"], e["ingresos"], e["gastos"]) for e in escenario.ESPERADO["historial"]], historial
    octubre = historial[0]
    mes = _resumen()["mes_actual"]
    assert (octubre["ingresos"], octubre["gastos"]) == (mes["ingresos"], mes["gastos"])


def test_periodo_libre_hasta_el_cobro():
    per = _resumen()["periodo"]
    assert per["fin"] == escenario.ESPERADO["fecha_fin_periodo"], per
    assert per["libre"] == escenario.ESPERADO["libre_hasta_cobro"], per
    assert per["comprometido"] == 0.0, per


def test_metas_guardan_lo_correcto():
    assert _apartado(ESTADO["ids"]["viaje"])["monto_actual"] == escenario.ESPERADO["meta_viaje"]
    assert _apartado(ESTADO["ids"]["fondo"])["monto_actual"] == escenario.ESPERADO["meta_fondo"]


def test_fijos_se_cargan_en_las_fechas_correctas():
    movs = CLIENT.get("/api/transacciones", headers=ESTADO["h"]).json()
    por_desc = {}
    for m in movs:
        if m["recurrente_id"] is not None:
            por_desc.setdefault(m["descripcion"].split(" (")[0], []).append(m["fecha"])
    assert sorted(por_desc["Nómina quincenal"]) == ["2026-09-15", "2026-09-30", "2026-10-15"], por_desc
    assert sorted(por_desc["Renta"]) == ["2026-09-01", "2026-10-01"], por_desc
    assert sorted(por_desc["Netflix"]) == ["2026-08-15", "2026-09-15", "2026-10-15"], por_desc
    assert sorted(por_desc["Laptop a 12 MSI"]) == ["2026-09-10", "2026-10-10"], por_desc


def test_ningun_fijo_cobra_fechas_futuras():
    movs = CLIENT.get("/api/transacciones", headers=ESTADO["h"]).json()
    futuros = [m for m in movs if m["recurrente_id"] is not None and m["fecha"] > escenario.HOY]
    assert not futuros, futuros


def test_cargos_fijos_no_se_duplican_al_recargar():
    antes = len(CLIENT.get("/api/transacciones", headers=ESTADO["h"]).json())
    for _ in range(3):
        assert CLIENT.get("/api/recurrentes", headers=ESTADO["h"]).status_code == 200
        assert CLIENT.get("/api/resumen", headers=ESTADO["h"]).status_code == 200
    assert len(CLIENT.get("/api/transacciones", headers=ESTADO["h"]).json()) == antes


def test_borrar_el_retiro_de_la_meta_restaura_el_saldo():
    h = ESTADO["h"]
    viaje = ESTADO["ids"]["viaje"]
    movs = CLIENT.get("/api/transacciones", headers=h).json()
    retiro = next(m for m in movs if m["apartado_id"] == viaje and m["tipo"] == "ingreso")
    assert CLIENT.delete(f"/api/transacciones/{retiro['id']}", headers=h).status_code == 200
    assert _apartado(viaje)["monto_actual"] == escenario.ESPERADO["meta_viaje"] + 1000.0
    # Y el disponible baja lo que el retiro había aportado
    assert _resumen()["balance"] == escenario.ESPERADO["disponible"] - 1000.0
    # Se restaura el retiro para no alterar las pruebas siguientes
    assert CLIENT.post(f"/api/apartados/{viaje}/movimiento", json={"monto": 1000, "accion": "retirar"}, headers=h).status_code == 200


def test_disponible_igual_a_lo_que_entro_menos_lo_que_salio():
    movs = CLIENT.get("/api/transacciones", headers=ESTADO["h"]).json()
    ingresos = sum(m["monto"] for m in movs if m["tipo"] == "ingreso")
    gastos = sum(m["monto"] for m in movs if m["tipo"] == "gasto")
    assert abs((ingresos - gastos) - _resumen()["balance"]) < 0.01


def test_saltar_una_cuota_msi_aplaza_sin_cobrar():
    h = ESTADO["h"]
    laptop = next(r for r in CLIENT.get("/api/recurrentes", headers=h).json() if r["descripcion"] == "Laptop a 12 MSI")
    antes = _resumen()["deuda_msi"]
    r = CLIENT.post(f"/api/recurrentes/{laptop['id']}/saltar", json={"contabilizar_cuota": False}, headers=h)
    assert r.status_code == 200, r.text
    assert _resumen()["deuda_msi"] == antes, "aplazar no debe cambiar la deuda"


def _fila_excel(libro, etiqueta):
    hoja = libro["Resumen General"]
    for fila in hoja.iter_rows(min_row=6, max_row=20, max_col=2, values_only=True):
        if fila[0] == etiqueta:
            return fila[1]
    raise AssertionError(f"no hay fila '{etiqueta}' en el Excel")


def test_excel_coincide_con_el_resumen():
    import io
    import openpyxl
    r = CLIENT.get("/api/exportar-excel?moneda_base=MXN", headers=ESTADO["h"])
    assert r.status_code == 200, r.text
    libro = openpyxl.load_workbook(io.BytesIO(r.content), data_only=True)
    resumen = _resumen()
    assert abs(_fila_excel(libro, "Balance en Efectivo Disponible") - resumen["balance"]) < 0.01
    assert abs(_fila_excel(libro, "Total en Sobres de Ahorro / Metas") - resumen["apartados"]) < 0.01
    # PENDIENTE DE DECISIÓN: el Excel llama "Patrimonio Neto" a disponible + metas + inversiones (15,103),
    # mientras el Resumen lo llama igual pero solo cuenta metas + inversiones (7,000). El README además dice
    # que el patrimonio resta la deuda MSI, y ningún cálculo lo hace. Aquí se fija el comportamiento actual.
    patrimonio_excel = _fila_excel(libro, "Patrimonio Neto Total")
    assert abs(patrimonio_excel - (resumen["balance"] + resumen["patrimonio_neto"])) < 0.01, (patrimonio_excel, resumen)


def test_respaldo_se_restaura_identico():
    h = ESTADO["h"]
    antes = _resumen()
    historial_antes = CLIENT.get("/api/resumen-mensual", headers=h).json()
    descarga = CLIENT.get("/api/backup", headers=h)
    assert descarga.status_code == 200, descarga.text
    assert descarga.content[:16] == b"SQLite format 3\x00"
    r = CLIENT.post("/api/restore", files={"file": ("respaldo.db", descarga.content, "application/octet-stream")}, headers=h)
    assert r.status_code == 200, r.text
    assert _resumen()["balance"] == antes["balance"]
    assert _resumen()["patrimonio_neto"] == antes["patrimonio_neto"]
    assert CLIENT.get("/api/resumen-mensual", headers=h).json() == historial_antes


if __name__ == "__main__":
    from ejecutor import ejecutar, verificar_cobertura  # noqa: E402
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    with TestClient(app) as cliente:
        CLIENT = cliente
        _preparar()
        registradas = [
            (test_cifras_del_resumen, test_cifras_del_resumen),
            (test_mes_actual_excluye_metas, test_mes_actual_excluye_metas),
            (test_historial_mensual_coincide_con_el_mes_actual, test_historial_mensual_coincide_con_el_mes_actual),
            (test_periodo_libre_hasta_el_cobro, test_periodo_libre_hasta_el_cobro),
            (test_metas_guardan_lo_correcto, test_metas_guardan_lo_correcto),
            (test_fijos_se_cargan_en_las_fechas_correctas, test_fijos_se_cargan_en_las_fechas_correctas),
            (test_ningun_fijo_cobra_fechas_futuras, test_ningun_fijo_cobra_fechas_futuras),
            (test_cargos_fijos_no_se_duplican_al_recargar, test_cargos_fijos_no_se_duplican_al_recargar),
            (test_borrar_el_retiro_de_la_meta_restaura_el_saldo, test_borrar_el_retiro_de_la_meta_restaura_el_saldo),
            (test_disponible_igual_a_lo_que_entro_menos_lo_que_salio, test_disponible_igual_a_lo_que_entro_menos_lo_que_salio),
            (test_saltar_una_cuota_msi_aplaza_sin_cobrar, test_saltar_una_cuota_msi_aplaza_sin_cobrar),
            (test_excel_coincide_con_el_resumen, test_excel_coincide_con_el_resumen),
            (test_respaldo_se_restaura_identico, test_respaldo_se_restaura_identico),
        ]
        codigo = ejecutar(verificar_cobertura(sys.modules[__name__], registradas), "test_escenario_api")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)
