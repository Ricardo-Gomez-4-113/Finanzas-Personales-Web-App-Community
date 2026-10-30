"""
test_finanzas.py - Pruebas base de la lógica financiera (sin pytest).

Ejecutar desde la raíz del proyecto:
    python3 test_finanzas.py
"""

import json
import os
import sqlite3
import sys
import types
from datetime import date, timedelta

# Compatibilidad para ejecutar suite en entornos host donde FastAPI/Pydantic residen dentro del contenedor
if 'fastapi' not in sys.modules:
    try:
        import fastapi
    except ImportError:
        fastapi = types.ModuleType('fastapi')
        class HTTPException(Exception):
            def __init__(self, status_code, detail=None):
                self.status_code = status_code
                self.detail = detail
                super().__init__(f"{status_code}: {detail}")
        class APIRouter:
            def get(self, *a, **kw): return lambda f: f
            def post(self, *a, **kw): return lambda f: f
            def put(self, *a, **kw): return lambda f: f
            def patch(self, *a, **kw): return lambda f: f
            def delete(self, *a, **kw): return lambda f: f
        class Request: pass
        class status:
            HTTP_401_UNAUTHORIZED = 401
            HTTP_403_FORBIDDEN = 403
        def Depends(f): return f
        def Query(default=None, *a, **kw): return default

        fastapi.HTTPException = HTTPException
        fastapi.APIRouter = APIRouter
        fastapi.Depends = Depends
        fastapi.Query = Query
        fastapi.Request = Request
        fastapi.status = status

        sec = types.ModuleType('fastapi.security')
        class OAuth2PasswordBearer:
            def __init__(self, *a, **kw): pass
        sec.OAuth2PasswordBearer = OAuth2PasswordBearer
        fastapi.security = sec

        sys.modules['fastapi'] = fastapi
        sys.modules['fastapi.security'] = sec

if 'pydantic' not in sys.modules:
    try:
        import pydantic
    except ImportError:
        pydantic = types.ModuleType('pydantic')
        class BaseModel:
            def __init__(self, **kw):
                self.model_fields_set = set(kw.keys())
                for k, v in kw.items(): setattr(self, k, v)
            def dict(self): return self.__dict__
        def Field(default=None, **kw): return default
        pydantic.BaseModel = BaseModel
        pydantic.Field = Field
        sys.modules['pydantic'] = pydantic

import database
from database import configure_sqlite_pragmas, apply_lazy_migrations
from finanzas_core import (
    convertir,
    fijos_por_periodo,
    calcular_resumen,
    siguiente_fecha,
    materializar_recurrentes,
    deuda_pendiente,
    valor_rendimiento,
    validar_sustentabilidad_gasto_fijo,
    validar_saldo_disponible_fecha,
    validar_eliminacion_transaccion,
    validar_edicion_transaccion,
    obtener_ventana_corte_fecha,
    validar_limite_cargos_ventana,
    calcular_distribucion_tarjeta_asimetrica,
    exportar_presupuesto_a_fijos_core,
    repartir_en_centavos,
    calcular_distribucion_subperiodos,
    proxima_fecha_vigente,
    mensualizar,
    convertir_frecuencias,
    convertir_de_mensual,
    calcular_periodo_actual,
    resumen_mensual_core,
    revertir_movimiento_apartado,
)


def nueva_bd():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    configure_sqlite_pragmas(conn)
    apply_lazy_migrations(conn)
    return conn


def insertar_recurrente(conn, descripcion, tipo, monto, frecuencia, dia, proxima, total=None, monto_total=None):
    conn.execute(
        """
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha,
                                 total_cuotas, cuotas_generadas, monto_total, categoria_id, activo)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 1, 1)
        """,
        (descripcion, tipo, monto, frecuencia, dia, proxima.isoformat(), total, monto_total),
    )
    conn.commit()
    return conn.execute("SELECT * FROM recurrentes ORDER BY id DESC LIMIT 1").fetchone()


def test_convertir():
    assert convertir(100, 'USD', 'MXN', 18) == 1800
    assert convertir(1800, 'MXN', 'USD', 18) == 100
    assert convertir(5, 'MXN', 'MXN', 18) == 5


def test_calcular_resumen():
    conn = nueva_bd()
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-01-05', 1000, 'ingreso')")
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-01-06', 300, 'gasto')")
    conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual) VALUES ('Viaje', 'libre', 200)")
    conn.commit()
    r = calcular_resumen(conn, 'MXN', 18, date(2026, 1, 31))
    assert r['balance'] == 700, r
    assert r['patrimonio_neto'] == 200, r


def test_siguiente_fecha_mensual_ancla_31():
    f = date(2026, 1, 31)
    f = siguiente_fecha(f, 'mensual', 31)
    assert f == date(2026, 2, 28), f
    f = siguiente_fecha(f, 'mensual', 31)
    assert f == date(2026, 3, 31), f


def test_siguiente_fecha_quincenal():
    f = date(2026, 2, 10)
    f = siguiente_fecha(f, 'quincenal', None)
    assert f == date(2026, 2, 15), f
    f = siguiente_fecha(f, 'quincenal', None)
    assert f == date(2026, 2, 28), f
    f = siguiente_fecha(f, 'quincenal', None)
    assert f == date(2026, 3, 15), f


def test_msi_materializa_cuotas_hasta_hoy():
    conn = nueva_bd()
    insertar_recurrente(conn, "Laptop", 'gasto', 2583.33, 'mensual', 10, date(2026, 1, 10), total=12, monto_total=31000)
    n = materializar_recurrentes(conn, date(2026, 4, 15))
    assert n == 4, n
    filas = conn.execute("SELECT * FROM recurrentes").fetchone()
    assert deuda_pendiente(filas) == 20666.68, deuda_pendiente(filas)


def test_materializar_es_idempotente_mismo_dia():
    conn = nueva_bd()
    insertar_recurrente(conn, "Renta", 'gasto', 5000, 'mensual', 1, date(2026, 1, 1))
    assert materializar_recurrentes(conn, date(2026, 3, 5)) == 3
    assert materializar_recurrentes(conn, date(2026, 3, 5)) == 0


def test_msi_completo_suma_exacta_y_termina():
    conn = nueva_bd()
    insertar_recurrente(conn, "Celular", 'gasto', 2583.33, 'mensual', 10, date(2026, 1, 10), total=12, monto_total=31000)
    n = materializar_recurrentes(conn, date(2030, 1, 1))
    assert n == 12, n
    suma = conn.execute("SELECT ROUND(SUM(monto), 2) AS s FROM transacciones WHERE recurrente_id IS NOT NULL").fetchone()["s"]
    assert suma == 31000.00, suma
    fila = conn.execute("SELECT activo FROM recurrentes").fetchone()
    assert fila["activo"] == 0


def test_valor_rendimiento_un_anio():
    d = date(2026, 1, 1)
    assert valor_rendimiento(10000, 10, d, d + timedelta(days=365)) == 11051.56


def test_migracion_v4_enlaza_apartados():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    configure_sqlite_pragmas(conn)
    original = database.CURRENT_TENANT_SCHEMA_VERSION
    try:
        database.CURRENT_TENANT_SCHEMA_VERSION = 3
        apply_lazy_migrations(conn)
        conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual) VALUES ('Viaje', 'libre', 0)")
        conn.execute(
            "INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion) "
            "VALUES ('2026-01-05', 500, 'gasto', 1, 'Ahorro destinado a: Viaje')"
        )
        conn.commit()
        database.CURRENT_TENANT_SCHEMA_VERSION = 4
        apply_lazy_migrations(conn)
    finally:
        database.CURRENT_TENANT_SCHEMA_VERSION = original
    apa = conn.execute("SELECT id FROM apartados WHERE nombre='Viaje'").fetchone()["id"]
    trans = conn.execute("SELECT apartado_id FROM transacciones WHERE descripcion LIKE 'Ahorro destinado a:%'").fetchone()
    assert trans["apartado_id"] == apa, (trans["apartado_id"], apa)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4


def test_migracion_v5_presupuesto():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    configure_sqlite_pragmas(conn)
    apply_lazy_migrations(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 5
    cfg = conn.execute("SELECT id, ingreso_base, frecuencia, subdivision_tipo, subdivision_config FROM presupuesto_config WHERE id = 1").fetchone()
    assert cfg is not None
    assert cfg["ingreso_base"] == 0.0
    assert cfg["frecuencia"] == "mensual"
    assert cfg["subdivision_tipo"] == "ninguna"

    conn.execute(
        "INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, orden) VALUES (?, ?, ?, ?, ?, ?)",
        ("Gastos Fijos", "building-columns", "gold", 50.0, 5000.0, 0)
    )
    conn.commit()
    t = conn.execute("SELECT nombre, porcentaje, monto FROM presupuesto_tarjetas WHERE nombre='Gastos Fijos'").fetchone()
    assert t["nombre"] == "Gastos Fijos"
    assert t["porcentaje"] == 50.0
    assert t["monto"] == 5000.0


def test_presupuesto_conversiones():
    from finanzas_core import convertir_frecuencias, convertir_de_mensual
    res_m = convertir_frecuencias(10000.0, "mensual")
    assert res_m["mensual"] == 10000.0
    assert res_m["quincenal"] == 5000.0

    res_q = convertir_frecuencias(5000.0, "quincenal")
    assert res_q["mensual"] == 10000.0
    assert res_q["quincenal"] == 5000.0

    assert convertir_de_mensual(10000.0, "quincenal") == 5000.0
    assert convertir_de_mensual(10000.0, "mensual") == 10000.0


def test_subperiodos_calculo():
    from finanzas_core import generar_subperiodos_default, calcular_distribucion_subperiodos

    q = generar_subperiodos_default("mensual", "quincenas")
    assert len(q) == 2
    assert q[0]["porcentaje"] == 50.0
    assert q[1]["porcentaje"] == 50.0

    s_m = generar_subperiodos_default("mensual", "semanas")
    assert len(s_m) == 4
    assert s_m[0]["porcentaje"] == 25.0

    s_q = generar_subperiodos_default("quincenal", "semanas")
    assert len(s_q) == 2
    assert s_q[0]["porcentaje"] == 50.0

    dist = calcular_distribucion_subperiodos(20000.0, q)
    assert len(dist) == 2
    assert dist[0]["monto"] == 10000.0
    assert dist[1]["monto"] == 10000.0


def test_migracion_v7_apartado_recurrente():
    conn = nueva_bd()
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 7
    cols = [col[1] for col in conn.execute("PRAGMA table_info(recurrentes)").fetchall()]
    assert "apartado_id" in cols


def test_recurrente_aporta_a_meta():
    conn = nueva_bd()
    conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual, meta_monto) VALUES ('Fondo Emergencia', 'meta', 1000.0, 5000.0)")
    apa_id = conn.execute("SELECT id FROM apartados WHERE nombre='Fondo Emergencia'").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, categoria_id, apartado_id, activo)
        VALUES ('Ahorro mensual', 'gasto', 500.0, 'mensual', 1, '2026-01-01', 1, ?, 1)
        """,
        (apa_id,)
    )
    conn.commit()

    n = materializar_recurrentes(conn, date(2026, 2, 15))
    assert n == 2, n

    saldo = conn.execute("SELECT monto_actual FROM apartados WHERE id = ?", (apa_id,)).fetchone()["monto_actual"]
    assert saldo == 2000.0, saldo

    trans = conn.execute("SELECT * FROM transacciones WHERE recurrente_id IS NOT NULL").fetchall()
    assert len(trans) == 2
    for t in trans:
        assert t["apartado_id"] == apa_id


def test_importar_fijos_individual_y_limpieza():
    from finanzas_core import sincronizar_fijos_presupuesto_core
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    configure_sqlite_pragmas(conn)
    apply_lazy_migrations(conn)

    conn.execute("INSERT OR IGNORE INTO categorias (id, nombre, tipo) VALUES (1, 'General', 'gasto'), (12, 'Nómina', 'ingreso')")
    conn.execute("INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo, categoria_id) VALUES ('Nómina', 'ingreso', 10000.0, 'mensual', 1, '2026-11-01', 1, 12)")
    conn.execute("INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo, categoria_id) VALUES ('Internet', 'gasto', 500.0, 'mensual', 5, '2026-11-05', 1, 1)")
    conn.execute("INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo, categoria_id) VALUES ('Supermercado', 'gasto', 2000.0, 'mensual', 10, '2026-11-10', 1, 1)")
    
    # Tarjeta vieja residual
    conn.execute("INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden) VALUES ('Gastos Fijos', 'home', 'blue', 25.0, 2500.0, 'Importado de Fijos y MSI', 10)")
    conn.commit()

    cursor = conn.cursor()
    sincronizar_fijos_presupuesto_core(cursor, modo='individual', actualizar_ingreso=True, reemplazar=False)
    conn.commit()

    tarjetas = conn.execute("SELECT * FROM presupuesto_tarjetas").fetchall()
    nombres = [t["nombre"] for t in tarjetas]
    assert "Gastos Fijos" not in nombres, nombres
    assert "Internet" in nombres
    assert "Supermercado" in nombres

    cfg = conn.execute("SELECT ingreso_base FROM presupuesto_config WHERE id = 1").fetchone()
    assert cfg["ingreso_base"] == 10000.0


def test_migracion_v8_indice_unico():
    conn = nueva_bd()
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    assert v >= 8, v
    idx = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='uq_transacciones_rec_fecha'").fetchone()
    assert idx is not None


def test_migracion_v9_distribucion_subperiodos():
    conn = nueva_bd()
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    assert v >= 9, v
    cols = [col[1] for col in conn.execute("PRAGMA table_info(presupuesto_tarjetas)").fetchall()]
    assert "distribucion_subperiodos" in cols


def test_migracion_v10_dia_pago():
    conn = nueva_bd()
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    assert v >= 10, v
    cols = [col[1] for col in conn.execute("PRAGMA table_info(presupuesto_tarjetas)").fetchall()]
    assert "dia_pago" in cols


def test_migracion_v11_dia_pago_config():
    conn = nueva_bd()
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    assert v >= 11, v
    cols = [col[1] for col in conn.execute("PRAGMA table_info(presupuesto_config)").fetchall()]
    assert "dia_pago" in cols


def test_migracion_v12_presupuesto_tarjeta_id():
    conn = nueva_bd()
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    assert v >= 12, v
    cols = [col[1] for col in conn.execute("PRAGMA table_info(recurrentes)").fetchall()]
    assert "presupuesto_tarjeta_id" in cols


def test_exportar_presupuesto_con_dia_pago_configurado():
    from finanzas_core import exportar_presupuesto_a_fijos_core
    conn = nueva_bd()
    conn.execute("UPDATE presupuesto_config SET ingreso_base = 25000.0, frecuencia = 'mensual', dia_pago = 15 WHERE id = 1")
    conn.commit()

    res = exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 1))
    rec = conn.execute("SELECT descripcion, tipo, monto, frecuencia, dia, proxima_fecha FROM recurrentes WHERE tipo = 'ingreso'").fetchone()
    assert rec is not None
    assert rec["monto"] == 25000.0
    assert rec["dia"] == 15
    assert rec["proxima_fecha"] == "2026-10-15"

    # Si la fecha base ya pasó el día 15, proxima_fecha debe ser el 15 del mes siguiente
    res2 = exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 20))
    rec2 = conn.execute("SELECT dia, proxima_fecha FROM recurrentes WHERE tipo = 'ingreso'").fetchone()
    assert rec2["dia"] == 15
    assert rec2["proxima_fecha"] == "2026-11-15"


def test_distribucion_tarjeta_asimetrica():
    subperiodos = [
        {"id": "q1", "nombre": "Q1", "porcentaje": 50.0},
        {"id": "q2", "nombre": "Q2", "porcentaje": 50.0}
    ]
    custom = '[{"id": "q1", "nombre": "Q1", "porcentaje": 70.0, "monto": 700.0}, {"id": "q2", "nombre": "Q2", "porcentaje": 30.0, "monto": 300.0}]'
    dist = calcular_distribucion_tarjeta_asimetrica(1000.0, subperiodos, custom)
    assert len(dist) == 2
    assert dist[0]["monto"] == 700.0
    assert dist[1]["monto"] == 300.0



def test_materializar_ingreso_no_afecta_apartado():
    conn = nueva_bd()
    conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual) VALUES ('Ahorro', 'libre', 1000.0)")
    # Forzamos en BD un ingreso con apartado_id
    conn.execute(
        """
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, categoria_id, apartado_id, activo)
        VALUES ('Sueldo', 'ingreso', 5000.0, 'mensual', 1, '2026-10-01', 1, 1, 1)
        """
    )
    conn.commit()
    materializar_recurrentes(conn, date(2026, 10, 20))
    saldo = conn.execute("SELECT monto_actual FROM apartados WHERE id = 1").fetchone()[0]
    # El saldo del apartado no debe alterarse por un ingreso recurrente
    assert saldo == 1000.0, saldo
    r = calcular_resumen(conn, 'MXN', 18.0, date(2026, 10, 20))
    assert r['balance'] == 5000.0
    assert r['apartados'] == 1000.0
    assert r['patrimonio_neto'] == 1000.0


def test_proxima_fecha_vigente_reactivar():
    from finanzas_core import proxima_fecha_vigente
    # Fijo mensual pausado en marzo, reactivado el 20 de octubre
    f = proxima_fecha_vigente(date(2026, 3, 10), 'mensual', 10, date(2026, 10, 20))
    assert f == date(2026, 11, 10), f

    # Fijo mensual pausado en marzo, reactivado el 10 de octubre (el mismo día del corte)
    f2 = proxima_fecha_vigente(date(2026, 3, 10), 'mensual', 10, date(2026, 10, 10))
    assert f2 == date(2026, 10, 10), f2

    # Fijo quincenal pausado en enero, reactivado el 20 de octubre
    f3 = proxima_fecha_vigente(date(2026, 1, 15), 'quincenal', None, date(2026, 10, 20))
    assert f3 == date(2026, 10, 31), f3


def test_concurrencia_materializar_sin_duplicados():
    import tempfile, threading, shutil
    import database
    from finanzas_core import materializar_recurrentes

    tmp_dir = tempfile.mkdtemp()
    orig_data = database.DATA_DIR
    orig_tenants = database.TENANTS_DIR
    try:
        database.DATA_DIR = tmp_dir
        database.TENANTS_DIR = tmp_dir
        c = database.get_tenant_conn("test_conc.db")
        insertar_recurrente(c, "Streaming", "gasto", 200.0, "semanal", 1, date(2026, 1, 1))
        c.close()

        bar = threading.Barrier(2)
        def worker():
            conn_w = database.get_tenant_conn("test_conc.db")
            bar.wait()
            try:
                materializar_recurrentes(conn_w, date(2026, 5, 1))
            finally:
                conn_w.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start(); t2.start()
        t1.join(); t2.join()

        conn_check = database.get_tenant_conn("test_conc.db")
        dups = conn_check.execute("""
            SELECT COUNT(*) FROM (
                SELECT 1 FROM transacciones 
                WHERE recurrente_id IS NOT NULL 
                GROUP BY recurrente_id, fecha 
                HAVING COUNT(*) > 1
            )
        """).fetchone()[0]
        assert dups == 0, f"Se detectaron duplicados: {dups}"
        conn_check.close()
    finally:
        database.DATA_DIR = orig_data
        database.TENANTS_DIR = orig_tenants
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_resumen_fijos_msi_compromiso_mensual():
    conn = nueva_bd()
    # Ingreso quincenal de $9,375.54 (mensualizado = $18,751.08)
    insertar_recurrente(conn, "Nómina quincenal", "ingreso", 9375.54, "quincenal", None, date(2026, 10, 15))
    # Gasto fijo mensual de $5,000.00
    insertar_recurrente(conn, "Renta", "gasto", 5000.0, "mensual", 1, date(2026, 10, 30))
    # Compra a 12 meses de $12,899.00 (cuota = $1,074.92)
    conn.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha,
                                 total_cuotas, cuotas_generadas, monto_total, categoria_id, activo)
        VALUES ('Laptop MSI', 'gasto', 1074.92, 'mensual', 15, '2026-10-15', 12, 0, 12899.0, 1, 1)
    """)
    conn.commit()

    r = calcular_resumen(conn, 'MXN', 18.0, date(2026, 10, 20))
    # El periodo va del 15 al 30 de octubre: el cobro del 31 abre el siguiente
    p = r['periodos'][0]
    assert (p['inicio'], p['fin']) == ('2026-10-15', '2026-10-30'), p
    assert p['ingresos']['total'] == 9375.54, p['ingresos']
    assert p['gastos']['fijos'] == 5000.0 and p['gastos']['msi'] == 1074.92, p['gastos']
    assert p['gastos']['normales'] == 0.0, p['gastos']
    assert p['remanente'] == 3300.62, p
    assert r['deuda_msi'] == 12899.0, r['deuda_msi']


def test_deuda_msi_incluye_compras_pausadas():
    conn = nueva_bd()
    conn.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha,
                                 total_cuotas, cuotas_generadas, monto_total, categoria_id, activo)
        VALUES ('Celular en pausa', 'gasto', 500, 'mensual', 5, '2026-10-05', 6, 2, 3000.0, 1, 0)
    """)
    conn.commit()
    r = calcular_resumen(conn, 'MXN', 18.0, date(2026, 10, 20))
    assert r['deuda_msi'] == 2000.0, r['deuda_msi']  # 3,000 - 2 cuotas de 500, aunque esté pausada
    assert r['periodos'][0]['gastos']['msi'] == 0.0, r['periodos'][0]['gastos']  # sin cobros programados


def test_resumen_gastos_normales_mes():
    conn = nueva_bd()
    insertar_recurrente(conn, "Sueldo", "ingreso", 10000.0, "mensual", 1, date(2026, 10, 1))
    insertar_recurrente(conn, "Renta", "gasto", 3000.0, "mensual", 1, date(2026, 10, 1))
    conn.execute("""
        INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion, recurrente_id, apartado_id)
        VALUES ('2026-10-10', 1500.0, 'gasto', 1, 'Supermercado', NULL, NULL)
    """)
    conn.commit()

    p = calcular_resumen(conn, 'MXN', 18.0, date(2026, 10, 20))['periodos'][0]
    assert p['ingresos']['total'] == 10000.0, p['ingresos']
    assert p['gastos']['normales'] == 1500.0 and p['gastos']['fijos'] == 3000.0, p['gastos']
    assert p['remanente'] == 5500.0, p


def test_exportar_presupuesto_a_fijos_misma_periodicidad():
    from finanzas_core import exportar_presupuesto_a_fijos_core
    conn = nueva_bd()
    # Presupuesto configurado con periodicidad quincenal
    conn.execute("UPDATE presupuesto_config SET ingreso_base = 9375.54, frecuencia = 'quincenal' WHERE id = 1")
    conn.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden)
        VALUES ('Transporte y Gasolina', 'shopping-bag', 'amber', 21.33, 2000.0, 'Transporte', 1)
    """)
    conn.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden)
        VALUES ('Dentista', 'wallet', 'blue', 5.33, 500.0, 'Salud', 2)
    """)
    conn.commit()

    res = exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 20))
    assert res['frecuencia'] == 'quincenal'
    assert res['total_procesados'] == 3  # nómina + 2 tarjetas

    recs = conn.execute("SELECT descripcion, tipo, monto, frecuencia FROM recurrentes ORDER BY id").fetchall()
    assert len(recs) == 3
    # Todos deben tener la misma periodicidad del presupuesto ('quincenal')
    for r in recs:
        assert r['frecuencia'] == 'quincenal', r

    # Si se actualiza el monto en el presupuesto y se reexporta, actualiza el recurrente sin duplicar
    conn.execute("UPDATE presupuesto_tarjetas SET monto = 2200.0 WHERE nombre = 'Transporte y Gasolina'")
    conn.commit()

    res2 = exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 20))
    assert res2['actualizados'] == 3
    recs2 = conn.execute("SELECT descripcion, tipo, monto, frecuencia FROM recurrentes WHERE descripcion = 'Transporte y Gasolina'").fetchall()
    assert len(recs2) == 1
    assert recs2[0]['monto'] == 2200.0
    assert recs2[0]['frecuencia'] == 'quincenal'


def test_editar_recurrente_frecuencia_y_campos():
    conn = nueva_bd()
    r = insertar_recurrente(conn, "Gimnasio", "gasto", 600.0, "mensual", 5, date(2026, 11, 5))
    rec_id = r["id"]

    # Simular la actualización de campos: descripcion, monto, frecuencia
    conn.execute(
        "UPDATE recurrentes SET descripcion = ?, monto = ?, frecuencia = ? WHERE id = ?",
        ("Gimnasio Premium", 850.0, "quincenal", rec_id)
    )
    conn.commit()

    fila = conn.execute("SELECT descripcion, monto, frecuencia, proxima_fecha, dia FROM recurrentes WHERE id = ?", (rec_id,)).fetchone()
    assert fila["descripcion"] == "Gimnasio Premium"
    assert fila["monto"] == 850.0
    assert fila["frecuencia"] == "quincenal"

    # Actualizar proxima_fecha y dia
    nueva_fecha = date.fromisoformat("2026-11-15")
    conn.execute(
        "UPDATE recurrentes SET proxima_fecha = ?, dia = ? WHERE id = ?",
        (nueva_fecha.isoformat(), nueva_fecha.day, rec_id)
    )
    conn.commit()
    fila2 = conn.execute("SELECT proxima_fecha, dia FROM recurrentes WHERE id = ?", (rec_id,)).fetchone()
    assert fila2["proxima_fecha"] == "2026-11-15"
    assert fila2["dia"] == 15


def test_crear_gasto_fijo_sin_ingreso_fijo_falla():
    conn = nueva_bd()
    cursor = conn.cursor()
    # 1. Sin ningún ingreso fijo registrado en recurrentes debe fallar
    try:
        validar_sustentabilidad_gasto_fijo(cursor, tipo='gasto', monto=1500.0, frecuencia='mensual')
        assert False, "Debió rechazar la creación de gasto fijo sin un ingreso fijo activo"
    except ValueError as e:
        assert "al menos un ingreso fijo recurrente activo" in str(e)

    # 2. Con ingreso fijo pero inactivo (activo = 0) debe seguir fallando
    cursor.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo)
        VALUES ('Sueldo Inactivo', 'ingreso', 20000.0, 'mensual', 1, '2026-01-01', 0)
    """)
    conn.commit()
    try:
        validar_sustentabilidad_gasto_fijo(cursor, tipo='gasto', monto=1500.0, frecuencia='mensual')
        assert False, "Debió rechazar la creación de gasto fijo con ingreso fijo inactivo"
    except ValueError as e:
        assert "al menos un ingreso fijo recurrente activo" in str(e)

    # 3. Con ingreso fijo activo, la validación tiene éxito si el monto es cubierto
    cursor.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo)
        VALUES ('Sueldo Activo', 'ingreso', 20000.0, 'mensual', 1, '2026-01-01', 1)
    """)
    conn.commit()
    validar_sustentabilidad_gasto_fijo(cursor, tipo='gasto', monto=1500.0, frecuencia='mensual')


def test_gasto_fijo_rechazado_por_depender_de_remanente():
    conn = nueva_bd()
    cursor = conn.cursor()
    # Saldo histórico masivo en transacciones de periodos pasados (remanente)
    cursor.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2025-01-01', 500000.0, 'ingreso')")

    # Ingreso fijo activo de 10,000 mensual
    cursor.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo)
        VALUES ('Nómina', 'ingreso', 10000.0, 'mensual', 1, '2026-11-01', 1)
    """)
    # Gasto fijo previo de 7,000 mensual -> Capacidad fija restante: 3,000 mensual
    cursor.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo)
        VALUES ('Renta', 'gasto', 7000.0, 'mensual', 1, '2026-11-01', 1)
    """)
    conn.commit()

    # Intentar registrar nuevo gasto fijo de 4,000 mensual (supera capacidad fija de 3,000)
    # A pesar de contar con 500,000 en caja de remanente, debe ser rechazado
    try:
        validar_sustentabilidad_gasto_fijo(cursor, tipo='gasto', monto=4000.0, frecuencia='mensual')
        assert False, "Debió rechazar el gasto fijo por depender de remanente"
    except ValueError as e:
        assert "No es posible registrar este compromiso como Gasto Fijo" in str(e)
        assert "remanente" in str(e)

    # Un compromiso de 3,000 mensual sí debe ser aceptado
    validar_sustentabilidad_gasto_fijo(cursor, tipo='gasto', monto=3000.0, frecuencia='mensual')


def test_gasto_previo_a_fecha_de_cobro_sin_remanente_falla():
    conn = nueva_bd()
    cursor = conn.cursor()
    # Nómina programada para el día 15
    cursor.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-11-15', 12000.0, 'ingreso')")
    conn.commit()

    # Intentar gastar el día 10 sin saldo previo acumulado debe fallar (saldo sería negativo)
    try:
        validar_saldo_disponible_fecha(cursor, fecha='2026-11-10', monto=2500.0, tipo='gasto')
        assert False, "Debió fallar por no contar con saldo disponible a la fecha"
    except ValueError as e:
        assert "Fondos insuficientes" in str(e)
        assert "2026-11-10" in str(e)


def test_gasto_previo_a_fecha_de_cobro_con_remanente_exitoso():
    conn = nueva_bd()
    cursor = conn.cursor()
    # Remanente disponible de periodo anterior
    cursor.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-10-31', 5000.0, 'ingreso')")
    # Próxima nómina el día 15
    cursor.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-11-15', 12000.0, 'ingreso')")
    conn.commit()

    # Gasto de 3,000 el día 10 debe permitirse porque el remanente previo (5,000) lo respalda
    validar_saldo_disponible_fecha(cursor, fecha='2026-11-10', monto=3000.0, tipo='gasto')

    # Intentar gastar 6,000 el día 10 debe fallar porque supera los 5,000 de remanente disponible
    try:
        validar_saldo_disponible_fecha(cursor, fecha='2026-11-10', monto=6000.0, tipo='gasto')
        assert False, "Debió fallar porque 6,000 supera el remanente disponible de 5,000"
    except ValueError as e:
        assert "Fondos insuficientes" in str(e)


def test_cargos_quincena_no_superan_presupuesto_de_quincena():
    conn = nueva_bd()
    cursor = conn.cursor()
    # Ingreso fijo recurrente de 8,000 quincenal (días 15 y 30)
    cursor.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo)
        VALUES ('Nómina Quincenal', 'ingreso', 8000.0, 'quincenal', 15, '2026-11-15', 1)
    """)
    # Registrar cargos existentes de 6,500 en la quincena (15 al 29 nov)
    cursor.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-11-16', 6500.0, 'gasto')")
    tx_id = cursor.lastrowid
    conn.commit()

    # Cargo adicional de 1,000 dentro de la ventana de corte no supera el límite (6,500 + 1,000 = 7,500 <= 8,000)
    validar_limite_cargos_ventana(cursor, fecha_str='2026-11-20', monto=1000.0)

    # Validar fechas límite de la quincena (días 28 y 29 de noviembre quedan dentro de la ventana de corte)
    v29 = obtener_ventana_corte_fecha(cursor, fecha_str='2026-11-29')
    assert v29["inicio"] == '2026-11-15'
    assert v29["fin"] == '2026-11-29'
    assert v29["cargos_actuales"] == 6500.0

    # Cargo adicional de 2,000 supera la capacidad de la quincena (6,500 + 2,000 = 8,500 > 8,000)
    try:
        validar_limite_cargos_ventana(cursor, fecha_str='2026-11-20', monto=2000.0)
        assert False, "Debió rechazar cargo que supera el límite de la quincena"
    except ValueError as e:
        assert "El presupuesto del período" in str(e)
        assert "ha sido superado" in str(e)

    # Actualizar transacción existente de 6,500 a 7,000 no debe duplicar el cargo (7,000 <= 8,000)
    validar_limite_cargos_ventana(cursor, fecha_str='2026-11-16', monto=7000.0, transaccion_id=tx_id)


def test_tarjetas_kpi_mes_en_curso_excluyen_eliminados():
    from routers.presupuesto import get_presupuesto, eliminar_tarjeta
    conn = nueva_bd()
    cursor = conn.cursor()

    # 1. En métricas patrimoniales (calcular_resumen): gastos normales y mes actual excluyen transacciones eliminadas
    cursor.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-11-05', 1500.0, 'gasto')")
    tx_id = cursor.lastrowid
    conn.commit()

    r1 = calcular_resumen(conn, 'MXN', 18.0, date(2026, 11, 20))
    assert r1['periodos'][0]['gastos']['normales'] == 1500.0
    assert r1['mes_actual']['gastos'] == 1500.0

    cursor.execute("DELETE FROM transacciones WHERE id = ?", (tx_id,))
    conn.commit()

    r2 = calcular_resumen(conn, 'MXN', 18.0, date(2026, 11, 20))
    assert r2['periodos'][0]['gastos']['normales'] == 0.0
    assert r2['mes_actual']['gastos'] == 0.0

    # 2. En tarjetas KPI del presupuesto (get_presupuesto): resumen asignado y disponible excluyen tarjetas eliminadas
    cursor.execute("UPDATE presupuesto_config SET ingreso_base = 10000.0 WHERE id = 1")
    cursor.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, orden, unidad_orden)
        VALUES ('Suscripciones', 'film', 'purple', 10.0, 1000.0, 1, 1)
    """)
    t_id = cursor.lastrowid
    conn.commit()

    p1 = get_presupuesto(conn)
    assert p1['resumen']['total_asignado_monto'] == 1000.0
    assert p1['resumen']['disponible_monto'] == 9000.0
    assert len(p1['tarjetas']) == 1

    eliminar_tarjeta(t_id, conn)

    p2 = get_presupuesto(conn)
    assert p2['resumen']['total_asignado_monto'] == 0.0
    assert p2['resumen']['disponible_monto'] == 10000.0
    assert len(p2['tarjetas']) == 0


def test_importacion_presupuesto_a_fijos_estricta():
    from fastapi import HTTPException
    from routers.presupuesto import importar_fijos_a_presupuesto
    from finanzas_core import exportar_presupuesto_a_fijos_core

    conn = nueva_bd()

    # 1. Bloqueo estricto de Fijos -> Presupuesto con HTTP 400
    try:
        importar_fijos_a_presupuesto(None, conn)
        assert False, "Debió rechazar la importación de fijos a presupuesto con HTTP 400"
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "estrictamente unidireccional" in exc.detail

    # 2. Flujo permitido y estricto de Presupuesto -> Fijos
    conn.execute("UPDATE presupuesto_config SET ingreso_base = 15000.0, frecuencia = 'quincenal' WHERE id = 1")
    conn.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden)
        VALUES ('Ahorro Emergencia', 'shield', 'purple', 20.0, 3000.0, 'Reserva de liquidez', 1)
    """)
    conn.commit()

    res = exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 11, 1))
    assert res['frecuencia'] == 'quincenal'
    assert res['total_procesados'] == 2  # Nómina base + Tarjeta

    recs = conn.execute("SELECT descripcion, tipo, monto, frecuencia FROM recurrentes ORDER BY id").fetchall()
    assert len(recs) == 2
    assert recs[0]['tipo'] == 'ingreso'
    assert recs[0]['monto'] == 15000.0
    assert recs[0]['frecuencia'] == 'quincenal'
    assert recs[1]['descripcion'] == 'Ahorro Emergencia'
    assert recs[1]['tipo'] == 'gasto'
    assert recs[1]['monto'] == 3000.0
    assert recs[1]['frecuencia'] == 'quincenal'


def test_gasto_respeta_saldo_retenido_en_apartados():
    conn = nueva_bd()
    cursor = conn.cursor()
    # Ingreso de 10,000 en la cuenta
    cursor.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-11-01', 10000.0, 'ingreso')")
    # Apartado con 4,000 retenidos
    cursor.execute("INSERT INTO apartados (nombre, tipo, monto_actual) VALUES ('Fondo Emergencia', 'libre', 4000.0)")
    conn.commit()

    # Saldo en cuenta = 10,000, Apartados = 4,000 -> Saldo Operativo = 6,000
    # Gasto de 5,000 el 2026-11-05 debe ser permitido (6,000 - 5,000 = 1,000 >= 0)
    validar_saldo_disponible_fecha(cursor, fecha='2026-11-05', monto=5000.0, tipo='gasto')

    # Intentar gastar 7,000 el 2026-11-05 debe fallar porque el saldo operativo disponible es 6,000 (compromete apartados)
    try:
        validar_saldo_disponible_fecha(cursor, fecha='2026-11-05', monto=7000.0, tipo='gasto')
        assert False, "Debió fallar porque 7,000 compromete los 4,000 guardados en apartados"
    except ValueError as e:
        assert "Fondos insuficientes" in str(e)
        assert "apartados" in str(e).lower()

    # Si se registra el gasto de 5,000 en BD
    cursor.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-11-05', 5000.0, 'gasto')")
    conn.commit()

    # Ahora el saldo operativo disponible es 1,000.
    # Un gasto de 1,500 en fecha 2026-11-06 debe fallar
    try:
        validar_saldo_disponible_fecha(cursor, fecha='2026-11-06', monto=1500.0, tipo='gasto')
        assert False, "Debió fallar porque saldo operativo restante es 1,000"
    except ValueError as e:
        assert "Fondos insuficientes" in str(e)
        assert "apartados" in str(e).lower()

    # Validar que crear_transaccion vía API rechaza con HTTP 422
    from routers.movimientos import crear_transaccion, Transaccion
    try:
        crear_transaccion(Transaccion(fecha='2026-11-06', monto=1500.0, tipo='gasto', categoria_id=1), conn)
        assert False, "Debió fallar crear_transaccion con HTTP 422"
    except Exception as e:
        assert getattr(e, "status_code", None) == 422, f"Esperado HTTP 422, obtenido {e}"


def test_edicion_ingreso_retroactivo_no_genera_saldo_negativo_futuro():
    conn = nueva_bd()
    cursor = conn.cursor()
    # Ingreso de 10,000 el 2026-11-01
    cursor.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-11-01', 10000.0, 'ingreso')")
    id_ingreso = cursor.lastrowid
    # Gasto de 8,000 el 2026-11-05 respaldado por dicho ingreso
    cursor.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-11-05', 8000.0, 'gasto')")
    id_gasto = cursor.lastrowid
    conn.commit()

    # 1. Reducir el ingreso a 5,000 debe fallar porque dejaría saldo -3,000 en fecha 2026-11-05
    try:
        validar_edicion_transaccion(cursor, transaccion_id=id_ingreso, nueva_fecha='2026-11-01', nuevo_monto=5000.0, nuevo_tipo='ingreso')
        assert False, "Debió fallar: reducir ingreso a 5,000 deja déficit futuro de 3,000"
    except ValueError as e:
        assert "respalda gastos" in str(e) or "saldo negativo" in str(e)

    # 2. Postergar la fecha del ingreso al 2026-11-10 debe fallar porque el 2026-11-05 habría déficit de 8,000
    try:
        validar_edicion_transaccion(cursor, transaccion_id=id_ingreso, nueva_fecha='2026-11-10', nuevo_monto=10000.0, nuevo_tipo='ingreso')
        assert False, "Debió fallar: postergar fecha a posterior al gasto deja déficit temporal"
    except ValueError as e:
        assert "respalda gastos" in str(e) or "saldo negativo" in str(e)

    # 3. Cambiar el tipo de ingreso a gasto debe fallar
    try:
        validar_edicion_transaccion(cursor, transaccion_id=id_ingreso, nueva_fecha='2026-11-01', nuevo_monto=10000.0, nuevo_tipo='gasto')
        assert False, "Debió fallar: convertir ingreso en gasto provoca déficit masivo"
    except ValueError as e:
        assert "respalda gastos" in str(e) or "saldo negativo" in str(e)

    # 4. Eliminar el ingreso debe fallar porque respalda el gasto de 8,000
    try:
        validar_eliminacion_transaccion(cursor, transaccion_id=id_ingreso)
        assert False, "Debió fallar: eliminar ingreso con gastos posteriores"
    except ValueError as e:
        assert "respalda gastos" in str(e) or "saldo negativo" in str(e)

    # 5. Reducir el ingreso a 9,000 sí debe permitirse porque 9,000 - 8,000 = 1,000 >= 0
    validar_edicion_transaccion(cursor, transaccion_id=id_ingreso, nueva_fecha='2026-11-01', nuevo_monto=9000.0, nuevo_tipo='ingreso')

    # 6. Validar que la capa de API (routers/movimientos) rechaza con HTTP 422
    from routers.movimientos import eliminar_transaccion, actualizar_transaccion, TransaccionUpdate
    try:
        eliminar_transaccion(id_ingreso, conn)
        assert False, "Debió fallar eliminar_transaccion con 422"
    except Exception as e:
        assert getattr(e, "status_code", None) == 422, f"Esperado HTTP 422, obtenido {e}"

    try:
        actualizar_transaccion(id_ingreso, TransaccionUpdate(monto=5000.0), conn)
        assert False, "Debió fallar actualizar_transaccion con 422"
    except Exception as e:
        assert getattr(e, "status_code", None) == 422, f"Esperado HTTP 422, obtenido {e}"

    # 7. Actualización permitida vía API se persiste adecuadamente
    res_act = actualizar_transaccion(id_ingreso, TransaccionUpdate(monto=9000.0), conn)
    assert res_act["mensaje"] == "Transacción actualizada con éxito"
    assert float(cursor.execute("SELECT monto FROM transacciones WHERE id = ?", (id_ingreso,)).fetchone()[0]) == 9000.0

    # 8. Blindaje de movimientos vinculados a apartados
    cursor.execute("INSERT INTO apartados (id, nombre, tipo, monto_actual) VALUES (1, 'Fondo Emergencia', 'libre', 500.0)")
    cursor.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-11-02', 500.0, 'gasto', 1)")
    id_ap_mov = cursor.lastrowid
    conn.commit()

    # Direct core check
    try:
        validar_edicion_transaccion(cursor, id_ap_mov, '2026-11-02', 400.0, 'gasto')
        assert False, "Debió fallar modificar movimiento de apartado en core"
    except ValueError as e:
        assert "apartado" in str(e).lower()

    # Intentos de alterar fecha, monto o tipo vía API retornan HTTP 400
    try:
        actualizar_transaccion(id_ap_mov, TransaccionUpdate(monto=400.0), conn)
        assert False, "Debió fallar actualizar monto en apartado con 400"
    except Exception as e:
        assert getattr(e, "status_code", None) == 400

    try:
        actualizar_transaccion(id_ap_mov, TransaccionUpdate(fecha='2026-11-03'), conn)
        assert False, "Debió fallar actualizar fecha en apartado con 400"
    except Exception as e:
        assert getattr(e, "status_code", None) == 400

    # Actualizar únicamente descripción del apartado no provoca falso 422 ni doble deducción
    res_desc = actualizar_transaccion(id_ap_mov, TransaccionUpdate(descripcion='Apartado corregido'), conn)
    assert res_desc["mensaje"] == "Transacción actualizada con éxito"

    # Eliminación permitida de movimiento vinculado a apartado: descuenta de apartados.monto_actual
    eliminar_transaccion(id_ap_mov, conn)
    assert cursor.execute("SELECT monto_actual FROM apartados WHERE id = 1").fetchone()[0] == 0.0
    assert cursor.execute("SELECT COUNT(*) FROM transacciones WHERE id = ?", (id_ap_mov,)).fetchone()[0] == 0

    # 9. Al eliminar el gasto respaldado y vaciar apartado, el ingreso puede eliminarse limpiamente
    eliminar_transaccion(id_gasto, conn)
    eliminar_transaccion(id_ingreso, conn)
    assert cursor.execute("SELECT COUNT(*) FROM transacciones WHERE id IN (?, ?)", (id_ingreso, id_gasto)).fetchone()[0] == 0


def test_normalizacion_fin_de_mes_febrero():
    # Año normal (2026): febrero tiene 28 días
    # Transición 31 de enero -> 28 de febrero -> 31 de marzo
    f_ene_26 = date(2026, 1, 31)
    f_feb_26 = siguiente_fecha(f_ene_26, 'mensual', 31)
    assert f_feb_26 == date(2026, 2, 28), f"Esperado 2026-02-28, obtenido {f_feb_26}"
    f_mar_26 = siguiente_fecha(f_feb_26, 'mensual', 31)
    assert f_mar_26 == date(2026, 3, 31), f"Esperado 2026-03-31, obtenido {f_mar_26}"

    # Año bisiesto (2024): febrero tiene 29 días
    # Transición 31 de enero -> 29 de febrero -> 31 de marzo
    f_ene_24 = date(2024, 1, 31)
    f_feb_24 = siguiente_fecha(f_ene_24, 'mensual', 31)
    assert f_feb_24 == date(2024, 2, 29), f"Esperado 2024-02-29, obtenido {f_feb_24}"
    f_mar_24 = siguiente_fecha(f_feb_24, 'mensual', 31)
    assert f_mar_24 == date(2024, 3, 31), f"Esperado 2024-03-31, obtenido {f_mar_24}"

    # Año bisiesto futuro (2028): febrero tiene 29 días
    f_ene_28 = date(2028, 1, 31)
    f_feb_28 = siguiente_fecha(f_ene_28, 'mensual', 31)
    assert f_feb_28 == date(2028, 2, 29), f"Esperado 2028-02-29, obtenido {f_feb_28}"
    f_mar_28 = siguiente_fecha(f_feb_28, 'mensual', 31)
    assert f_mar_28 == date(2028, 3, 31), f"Esperado 2028-03-31, obtenido {f_mar_28}"


def test_presupuesto_tarjeta_dia_pago_y_subperiodos():
    conn = nueva_bd()
    cur = conn.cursor()
    sub_dist = [
        {"id": "q1", "nombre": "Quincena 1", "monto": 250.0, "porcentaje": 50.0, "dia": 5},
        {"id": "q2", "nombre": "Quincena 2", "monto": 250.0, "porcentaje": 50.0, "dia": 20}
    ]
    cur.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, dia_pago, distribucion_subperiodos)
        VALUES ('Dentista', 'wallet', 'blue', 5.0, 500.0, 5, ?)
    """, (json.dumps(sub_dist),))
    conn.commit()

    fila = cur.execute("SELECT * FROM presupuesto_tarjetas WHERE nombre = 'Dentista'").fetchone()
    assert fila["dia_pago"] == 5
    parsed = json.loads(fila["distribucion_subperiodos"])
    assert parsed[0]["dia"] == 5
    assert parsed[1]["dia"] == 20


def test_gastos_por_categoria_solo_cuenta_el_periodo_de_cobro():
    from finanzas_core import ventana_periodo_actual, gastos_por_categoria_periodo
    conn = nueva_bd()
    insertar_recurrente(conn, "Nómina", 'ingreso', 14000, 'mensual', 15, date(2026, 10, 15))
    for fecha, monto in [("2026-10-14", 100), ("2026-10-16", 300), ("2026-11-14", 50), ("2026-11-15", 999)]:
        conn.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES (?, ?, 'gasto')", (fecha, monto))
    conn.commit()
    ventana = ventana_periodo_actual(conn, date(2026, 10, 20))
    assert ventana["inicio"] <= "2026-10-20" <= ventana["fin"], ventana
    res = gastos_por_categoria_periodo(conn, ventana["inicio"], ventana["fin"], "MXN", 18.4)
    assert res["total"] == 350.0, (ventana, res)
    assert res["desde"] == ventana["inicio"] and res["hasta"] == ventana["fin"], res


def test_exportar_borra_msi_que_no_estan_en_el_presupuesto():
    conn = nueva_bd()
    cur = conn.cursor()
    # Compra a meses suelta (no está en el presupuesto): la exportación la borra
    cur.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, total_cuotas, cuotas_generadas, activo)
        VALUES ('Pixel 10', 'gasto', 1074.92, 'mensual', 14, '2026-11-14', 10, 1, 1)
    """)
    # Tarjeta del presupuesto con el mismo nombre (legacy, sin pago): se exporta como fijo normal
    cur.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, dia_pago)
        VALUES ('Pixel 10 (MSI)', 'credit-card', 'amber', 5.73, 1074.92, 14)
    """)
    conn.commit()

    res = exportar_presupuesto_a_fijos_core(cur, fecha_base=date(2026, 10, 22))
    assert res["eliminados"] >= 1
    assert not cur.execute("SELECT 1 FROM recurrentes WHERE descripcion = 'Pixel 10' AND total_cuotas IS NOT NULL").fetchone()
    pixel = cur.execute("SELECT * FROM recurrentes WHERE descripcion LIKE 'Pixel 10%'").fetchall()
    assert len(pixel) == 1 and pixel[0]["total_cuotas"] is None


def test_exportar_msi_del_presupuesto_crea_compra_a_cuotas():
    conn = nueva_bd()
    cur = conn.cursor()
    cur.execute("UPDATE presupuesto_config SET ingreso_base = 10000, frecuencia = 'mensual' WHERE id = 1")
    cur.execute("INSERT INTO presupuesto_unidades (orden, dia_pago) VALUES (1, 10) ON CONFLICT(orden) DO UPDATE SET dia_pago = 10")
    cur.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, unidad_orden, tipo, msi_cuotas)
        VALUES ('Celular', 'credit-card', 'amber', 10.0, 1000.0, 1, 'msi', 12)
    """)
    conn.commit()

    res = exportar_presupuesto_a_fijos_core(cur, fecha_base=date(2026, 10, 22))
    assert res["creados"] >= 1
    fijo = cur.execute("SELECT * FROM recurrentes WHERE descripcion = 'Celular'").fetchone()
    assert fijo["total_cuotas"] == 12
    assert fijo["cuotas_generadas"] == 0
    assert fijo["monto"] == 1000.0
    assert fijo["dia"] == 10

    # Segunda exportación: se actualiza en su lugar, sin duplicar la compra
    exportar_presupuesto_a_fijos_core(cur, fecha_base=date(2026, 10, 22))
    assert cur.execute("SELECT COUNT(*) FROM recurrentes WHERE descripcion = 'Celular'").fetchone()[0] == 1


def test_exportar_presupuesto_respeta_dias():
    conn = nueva_bd()
    cur = conn.cursor()
    # 1. Crear recurrente MSI Pixel 10
    cur.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, total_cuotas, cuotas_generadas, activo)
        VALUES ('Pixel 10', 'gasto', 1074.92, 'mensual', 14, '2026-11-14', 10, 1, 1)
    """)
    # 2. Crear tarjeta en presupuesto nombrada 'Pixel 10 (MSI)' con dia_pago=14
    cur.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, dia_pago)
        VALUES ('Pixel 10 (MSI)', 'credit-card', 'amber', 5.73, 1074.92, 14)
    """)
    # 3. Crear tarjeta normal 'Sueldo madre' con subperiodo dia 30
    sub_madre = [
        {"id": "q1", "nombre": "Q1", "monto": 0.0, "porcentaje": 0.0, "dia": None},
        {"id": "q2", "nombre": "Q2", "monto": 2000.0, "porcentaje": 100.0, "dia": 30}
    ]
    cur.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, dia_pago, distribucion_subperiodos)
        VALUES ('Sueldo madre', 'heart', 'rose', 10.67, 2000.0, 30, ?)
    """, (json.dumps(sub_madre),))
    conn.commit()

    res = exportar_presupuesto_a_fijos_core(cur, fecha_base=date(2026, 10, 22))
    # La compra a meses 'Pixel 10' no está en el presupuesto: se borra y solo queda el fijo de la tarjeta
    pixel_recs = cur.execute("SELECT * FROM recurrentes WHERE descripcion LIKE '%Pixel%'").fetchall()
    assert len(pixel_recs) == 1
    assert pixel_recs[0]["total_cuotas"] is None

    # Sueldo madre se debió crear con dia = 30
    madre_rec = cur.execute("SELECT * FROM recurrentes WHERE descripcion = 'Sueldo madre'").fetchone()
    assert madre_rec is not None
    assert madre_rec["dia"] == 30


def test_calcular_distribucion_tarjeta_asimetrica_recalcula_si_monto_cambia():
    subperiodos = [
        {"id": "q1", "nombre": "Q1", "porcentaje": 50.0},
        {"id": "q2", "nombre": "Q2", "porcentaje": 50.0}
    ]
    # Asignación original 70% / 30% con monto base 1000 ($700 y $300)
    custom = [
        {"id": "q1", "nombre": "Q1", "porcentaje": 70.0, "monto": 700.0, "dia": 5},
        {"id": "q2", "nombre": "Q2", "porcentaje": 30.0, "monto": 300.0, "dia": 20}
    ]
    # Si el monto de la tarjeta cambia a 2000, los montos deben reescalarse a 1400 y 600
    dist = calcular_distribucion_tarjeta_asimetrica(2000.0, subperiodos, custom)
    assert len(dist) == 2
    assert dist[0]["monto"] == 1400.0
    assert dist[0]["dia"] == 5
    assert dist[1]["monto"] == 600.0
    assert dist[1]["dia"] == 20


def test_exportar_presupuesto_dia_segundo_subperiodo():
    conn = nueva_bd()
    cur = conn.cursor()
    # Tarjeta tipo NU: Q1 no tiene día asignado, Q2 tiene día 15
    sub_nu = [
        {"id": "q1", "nombre": "Q1", "monto": 0.0, "porcentaje": 0.0, "dia": None},
        {"id": "q2", "nombre": "Q2", "monto": 5000.0, "porcentaje": 100.0, "dia": 15}
    ]
    cur.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, dia_pago, distribucion_subperiodos)
        VALUES ('Tarjeta NU Especial', 'credit-card', 'purple', 26.67, 5000.0, NULL, ?)
    """, (json.dumps(sub_nu),))
    conn.commit()

    res = exportar_presupuesto_a_fijos_core(cur, fecha_base=date(2026, 10, 22))
    nu_rec = cur.execute("SELECT * FROM recurrentes WHERE descripcion = 'Tarjeta NU Especial'").fetchone()
    assert nu_rec is not None
    assert nu_rec["dia"] == 15, f"Esperado día 15, obtenido {nu_rec['dia']}"


def test_exportar_tarjeta_dividida_en_subperiodos():
    from finanzas_core import exportar_presupuesto_a_fijos_core, materializar_recurrentes
    conn = nueva_bd()
    cur = conn.cursor()
    # Ingreso quincenal de 10000; la tarjeta es 4000 POR QUINCENA (la interfaz muestra 8000 al mes)
    conn.execute("UPDATE presupuesto_config SET ingreso_base = 10000.0, frecuencia = 'quincenal', dia_pago = 15 WHERE id = 1")
    # Tarjeta dividida Transporte: 2000 en Dia 1 y 2000 en Dia 16 de cada quincena
    sub_transporte = [
        {"id": "q1", "nombre": "Quincena 1", "monto": 2000.0, "porcentaje": 50.0, "dia": 1},
        {"id": "q2", "nombre": "Quincena 2", "monto": 2000.0, "porcentaje": 50.0, "dia": 16}
    ]
    cur.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, dia_pago, distribucion_subperiodos)
        VALUES ('Transporte', 'wallet', 'cyan', 40.0, 4000.0, NULL, ?)
    """, (json.dumps(sub_transporte),))
    t_id = cur.lastrowid
    conn.commit()

    res = exportar_presupuesto_a_fijos_core(cur, fecha_base=date(2026, 10, 20))
    assert res["total_procesados"] >= 2

    # Cada subperiodo se repite en las dos quincenas del mes: 4 fijos mensuales, 8000 al mes
    recs = cur.execute("SELECT descripcion, tipo, monto, frecuencia, dia, presupuesto_tarjeta_id FROM recurrentes WHERE presupuesto_tarjeta_id = ? ORDER BY dia ASC, descripcion ASC", (t_id,)).fetchall()
    assert len(recs) == 4, [tuple(r) for r in recs]
    assert sorted(r["dia"] for r in recs) == [1, 1, 16, 16]
    assert all(r["frecuencia"] == "mensual" and r["monto"] == 2000.0 and r["presupuesto_tarjeta_id"] == t_id for r in recs)
    assert round(sum(r["monto"] for r in recs), 2) == 8000.0

    # Re-exportar con actualización: no debe duplicar
    cur.execute("UPDATE presupuesto_tarjetas SET monto = 4200.0 WHERE id = ?", (t_id,))
    sub_transporte_act = [
        {"id": "q1", "nombre": "Quincena 1", "monto": 2100.0, "porcentaje": 50.0, "dia": 1},
        {"id": "q2", "nombre": "Quincena 2", "monto": 2100.0, "porcentaje": 50.0, "dia": 16}
    ]
    cur.execute("UPDATE presupuesto_tarjetas SET distribucion_subperiodos = ? WHERE id = ?", (json.dumps(sub_transporte_act), t_id))
    conn.commit()

    res2 = exportar_presupuesto_a_fijos_core(cur, fecha_base=date(2026, 10, 20))
    recs2 = cur.execute("SELECT descripcion, monto FROM recurrentes WHERE presupuesto_tarjeta_id = ? ORDER BY dia ASC, descripcion ASC", (t_id,)).fetchall()
    assert len(recs2) == 4, [tuple(r) for r in recs2]
    assert all(r["monto"] == 2100.0 for r in recs2)
    assert len(recs) == 4


def test_exportar_tarjeta_subperiodo_dia_futuro_no_materializa_antes_de_tiempo():
    from finanzas_core import exportar_presupuesto_a_fijos_core, materializar_recurrentes
    conn = nueva_bd()
    cur = conn.cursor()
    conn.execute("UPDATE presupuesto_config SET ingreso_base = 20000.0, frecuencia = 'quincenal', dia_pago = 15 WHERE id = 1")
    # Tarjeta NU con 5000 el día 30
    sub_nu = [
        {"id": "q1", "nombre": "Q1", "monto": 0.0, "porcentaje": 0.0, "dia": None},
        {"id": "q2", "nombre": "Q2", "monto": 5000.0, "porcentaje": 100.0, "dia": 30}
    ]
    cur.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, dia_pago, distribucion_subperiodos)
        VALUES ('Tarjeta NU', 'credit-card', 'purple', 25.0, 5000.0, 30, ?)
    """, (json.dumps(sub_nu),))
    conn.commit()

    # Exportamos el día 26 de octubre
    exportar_presupuesto_a_fijos_core(cur, fecha_base=date(2026, 10, 26))
    rec = cur.execute("SELECT * FROM recurrentes WHERE descripcion = 'Tarjeta NU'").fetchone()
    assert rec is not None
    assert rec["monto"] == 5000.0
    assert rec["dia"] == 30
    assert rec["proxima_fecha"] == "2026-10-30"

    # Al ejecutar materializar_recurrentes el día 26, NO debe insertarse en transacciones
    mat_26 = materializar_recurrentes(conn, date(2026, 10, 26))
    txs_26 = cur.execute("SELECT * FROM transacciones WHERE recurrente_id = ?", (rec["id"],)).fetchall()
    assert len(txs_26) == 0

    # Cuando llega el día 30, se materializa exactamente por 5000 (no el mensual completo)
    mat_30 = materializar_recurrentes(conn, date(2026, 10, 30))
    txs_30 = cur.execute("SELECT * FROM transacciones WHERE recurrente_id = ?", (rec["id"],)).fetchall()
    assert len(txs_30) == 1
    assert txs_30[0]["monto"] == 5000.0
    assert txs_30[0]["fecha"] == "2026-10-30"


def test_edicion_transaccion_no_aumenta_gasto_permite_guardar_aunque_periodo_supere_limite():
    from routers.movimientos import actualizar_transaccion, TransaccionUpdate
    conn = nueva_bd()
    cur = conn.cursor()
    # Ingreso quincenal de 5000 el día 15
    cur.execute("INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo) VALUES ('Nómina', 'ingreso', 5000.0, 'quincenal', 15, '2026-11-15', 1)")
    # Gastos en la misma quincena que superan la capacidad (ej. 6000 total)
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo, descripcion, categoria_id) VALUES ('2026-11-16', 4000.0, 'gasto', 'Renta', 1)")
    id_t1 = cur.lastrowid
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo, descripcion, categoria_id) VALUES ('2026-11-18', 2000.0, 'gasto', 'Servicios', 1)")
    id_t2 = cur.lastrowid
    conn.commit()

    # Editar id_t2 reduciendo el monto de 2000 a 1000 debe permitirse
    actualizar_transaccion(id_t2, TransaccionUpdate(monto=1000.0), conn=conn)
    t2_mod = cur.execute("SELECT monto FROM transacciones WHERE id = ?", (id_t2,)).fetchone()
    assert t2_mod["monto"] == 1000.0

    # Editar fecha y monto individualmente (ej. mover fecha al día 20 con nuevo monto 1500) debe permitirse
    actualizar_transaccion(id_t2, TransaccionUpdate(fecha="2026-11-20", monto=1500.0), conn=conn)
    t2_mod2 = cur.execute("SELECT fecha, monto FROM transacciones WHERE id = ?", (id_t2,)).fetchone()
    assert t2_mod2["fecha"] == "2026-11-20"
    assert t2_mod2["monto"] == 1500.0

    # Editar descripción de id_t1 manteniendo el monto debe permitirse
    actualizar_transaccion(id_t1, TransaccionUpdate(descripcion="Renta Depto"), conn=conn)
    t1_mod = cur.execute("SELECT descripcion FROM transacciones WHERE id = ?", (id_t1,)).fetchone()
    assert t1_mod["descripcion"] == "Renta Depto"


def test_eliminacion_transacciones_individual_y_lote():
    from routers.movimientos import (
        eliminar_transaccion,
        eliminar_transacciones_lote,
        EliminarLoteRequest,
    )

    conn = nueva_bd()
    cur = conn.cursor()

    # 1. Movimiento ligado a meta / apartado (ahorro / gasto)
    cur.execute("INSERT INTO apartados (id, nombre, tipo, monto_actual) VALUES (1, 'Viaje', 'meta', 1000.0)")
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-11-01', 300.0, 'gasto', 1)")
    id_t1 = cur.lastrowid
    conn.commit()

    eliminar_transaccion(id_t1, conn)
    assert cur.execute("SELECT monto_actual FROM apartados WHERE id = 1").fetchone()[0] == 700.0
    assert cur.execute("SELECT COUNT(*) FROM transacciones WHERE id = ?", (id_t1,)).fetchone()[0] == 0

    # 2. Movimiento ligado a meta / apartado (retiro / ingreso): al borrar el retiro, el dinero vuelve a la meta
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-11-02', 200.0, 'ingreso', 1)")
    id_t2 = cur.lastrowid
    conn.commit()

    eliminar_transaccion(id_t2, conn)
    # El retiro se borra, pero el dinero no regresa a la meta: se mantiene en 700.0
    assert cur.execute("SELECT monto_actual FROM apartados WHERE id = 1").fetchone()[0] == 900.0
    assert cur.execute("SELECT COUNT(*) FROM transacciones WHERE id = ?", (id_t2,)).fetchone()[0] == 0

    # 3. Movimiento ligado a recurrente / MSI terminado
    cur.execute("""
        INSERT INTO recurrentes (id, descripcion, tipo, monto, frecuencia, dia, proxima_fecha, total_cuotas, cuotas_generadas, activo)
        VALUES (1, 'Compra TV', 'gasto', 1000.0, 'mensual', 15, '2026-11-15', 3, 3, 0)
    """)
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo, recurrente_id) VALUES ('2026-11-03', 1000.0, 'gasto', 1)")
    id_t3 = cur.lastrowid
    conn.commit()

    eliminar_transaccion(id_t3, conn)
    rec = cur.execute("SELECT cuotas_generadas, activo FROM recurrentes WHERE id = 1").fetchone()
    assert rec["cuotas_generadas"] == 2
    assert rec["activo"] == 1, "Debió reactivarse el recurrente al decrementar sus cuotas"

    # 4. Parámetro forzar=True en ingreso con gastos posteriores
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-11-04', 5000.0, 'ingreso')")
    id_ingreso = cur.lastrowid
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-11-05', 4000.0, 'gasto')")
    id_gasto = cur.lastrowid
    conn.commit()

    # Sin forzar falla con HTTP 422
    try:
        eliminar_transaccion(id_ingreso, conn, forzar=False)
        assert False, "Debió fallar con 422"
    except Exception as e:
        assert getattr(e, "status_code", None) == 422

    # Con forzar=True se elimina
    res_f = eliminar_transaccion(id_ingreso, conn, forzar=True)
    assert res_f["mensaje"] == "Transacción eliminada con éxito"
    assert cur.execute("SELECT COUNT(*) FROM transacciones WHERE id = ?", (id_ingreso,)).fetchone()[0] == 0

    # 5. Eliminación por lote (POST /api/transacciones/eliminar-lote)
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-11-06', 100.0, 'gasto', 1)")
    id_batch_1 = cur.lastrowid
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo, recurrente_id) VALUES ('2026-11-07', 500.0, 'gasto', 1)")
    id_batch_2 = cur.lastrowid
    conn.commit()

    res_lote = eliminar_transacciones_lote(EliminarLoteRequest(ids=[id_batch_1, id_batch_2]), conn)
    assert res_lote["eliminados"] == 2
    assert cur.execute("SELECT COUNT(*) FROM transacciones WHERE id IN (?, ?)", (id_batch_1, id_batch_2)).fetchone()[0] == 0
    # Apartado descuenta 100: 900 - 100 = 800
    assert cur.execute("SELECT monto_actual FROM apartados WHERE id = 1").fetchone()[0] == 800.0
    # Recurrente cuotas decrementa a 1
    assert cur.execute("SELECT cuotas_generadas FROM recurrentes WHERE id = 1").fetchone()[0] == 1

    # 6. Validación de déficit en lote (forzar=False vs forzar=True)
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-11-10', 3000.0, 'ingreso')")
    id_ing2 = cur.lastrowid
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-11-11', 2500.0, 'gasto')")
    id_gas2 = cur.lastrowid
    conn.commit()

    try:
        eliminar_transacciones_lote(EliminarLoteRequest(ids=[id_ing2], forzar=False), conn)
        assert False, "Debió fallar lote con 422 por déficit"
    except Exception as e:
        assert getattr(e, "status_code", None) == 422

    res_lote_f = eliminar_transacciones_lote(EliminarLoteRequest(ids=[id_ing2], forzar=True), conn)
    assert res_lote_f["eliminados"] == 1
    assert cur.execute("SELECT COUNT(*) FROM transacciones WHERE id = ?", (id_ing2,)).fetchone()[0] == 0

    # 7. Vaciado total (todos=True)
    res_vaciar = eliminar_transacciones_lote(EliminarLoteRequest(todos=True), conn)
    assert cur.execute("SELECT COUNT(*) FROM transacciones").fetchone()[0] == 0

    # 8. Comprobar que eliminar en lote no genere falso 422 en fechas históricas anteriores a los ingresos eliminados
    cur.execute("INSERT INTO apartados (id, nombre, tipo, monto_actual) VALUES (2, 'Auto', 'meta', 200.0)")
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2025-01-01', 50.0, 'gasto')")
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2025-01-02', 10000.0, 'ingreso')")
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2025-06-01', 1000.0, 'ingreso')")
    id_hist_del = cur.lastrowid
    conn.commit()

    # No debe fallar con 422 falso en 2025-01-01
    res_no_falso_422 = eliminar_transacciones_lote(EliminarLoteRequest(ids=[id_hist_del], forzar=False), conn)
    assert res_no_falso_422["eliminados"] == 1
    assert cur.execute("SELECT COUNT(*) FROM transacciones WHERE id = ?", (id_hist_del,)).fetchone()[0] == 0

    # 9. Eliminación múltiple ligada al mismo apartado y mismo recurrente
    cur.execute("UPDATE apartados SET monto_actual = 500.0 WHERE id = 1")
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-11-20', 100.0, 'gasto', 1)")
    id_ap_m1 = cur.lastrowid
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-11-21', 150.0, 'gasto', 1)")
    id_ap_m2 = cur.lastrowid
    cur.execute("UPDATE recurrentes SET cuotas_generadas = 2 WHERE id = 1")
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo, recurrente_id) VALUES ('2026-11-22', 500.0, 'gasto', 1)")
    id_rec_m1 = cur.lastrowid
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo, recurrente_id) VALUES ('2026-11-23', 500.0, 'gasto', 1)")
    id_rec_m2 = cur.lastrowid
    conn.commit()

    res_multi = eliminar_transacciones_lote(EliminarLoteRequest(ids=[id_ap_m1, id_ap_m2, id_rec_m1, id_rec_m2]), conn)
    assert res_multi["eliminados"] == 4
    # Apartado 1: 500 - 100 - 150 = 250
    assert cur.execute("SELECT monto_actual FROM apartados WHERE id = 1").fetchone()[0] == 250.0
    # Recurrente 1: 2 - 2 = 0 cuotas generadas
    assert cur.execute("SELECT cuotas_generadas FROM recurrentes WHERE id = 1").fetchone()[0] == 0


def test_listar_transacciones_incluye_metadata_presupuesto():
    from routers.movimientos import listar_transacciones
    conn = nueva_bd()
    cur = conn.cursor()
    cur.execute("INSERT INTO presupuesto_tarjetas (id, nombre, icono, color, porcentaje, monto) VALUES (5, 'Transporte', 'car', 'blue', 10.0, 4000.0)")
    cur.execute("""
        INSERT INTO recurrentes (id, descripcion, tipo, monto, frecuencia, dia, proxima_fecha, presupuesto_tarjeta_id, activo)
        VALUES (10, 'Transporte (Quincena 1)', 'gasto', 2000.0, 'mensual', 1, '2026-11-01', 5, 1)
    """)
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo, descripcion, recurrente_id) VALUES ('2026-11-01', 2000.0, 'gasto', 'Transporte (Quincena 1)', 10)")
    conn.commit()

    txs = listar_transacciones(conn)
    assert len(txs) == 1
    t = txs[0]
    assert t["presupuesto_tarjeta_id"] == 5
    assert t["tarjeta_nombre"] == "Transporte"
    assert t["tarjeta_monto_total"] == 4000.0


def test_sincronizacion_config_presupuesto_con_recurrentes():
    from routers.presupuesto import update_config_presupuesto, ConfigPresupuestoUpdate
    conn = nueva_bd()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO recurrentes (id, descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo)
        VALUES (1, 'Nómina Fija (Presupuesto)', 'ingreso', 10000.0, 'mensual', 15, '2026-11-15', 1)
    """)
    conn.commit()

    # Actualizar config de presupuesto a quincenal de 18,751.08
    req = ConfigPresupuestoUpdate(ingreso_base=18751.08, frecuencia='quincenal', dia_pago=15)
    update_config_presupuesto(req, conn)

    rec = cur.execute("SELECT monto, frecuencia, dia FROM recurrentes WHERE id = 1").fetchone()
    assert rec["monto"] == 18751.08
    assert rec["frecuencia"] == 'quincenal'
    assert rec["dia"] == 15


def test_quincena_cortes_dias_estandar_15_y_fin_de_mes():
    from finanzas_core import obtener_ventana_corte_fecha
    conn = nueva_bd()
    cur = conn.cursor()
    # Nómina quincenal con día 15
    cur.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo)
        VALUES ('Nómina', 'ingreso', 18751.08, 'quincenal', 15, '2026-10-15', 1)
    """)
    conn.commit()

    # Octubre 26 cae en la quincena 15 al 29
    v26 = obtener_ventana_corte_fecha(cur, '2026-10-26')
    assert v26["inicio"] == '2026-10-15'
    assert v26["fin"] == '2026-10-29'
    assert v26["ingreso_ventana"] == 18751.08

    # Octubre 30 cae en la quincena 30 de oct al 14 de nov
    v30 = obtener_ventana_corte_fecha(cur, '2026-10-30')
    assert v30["inicio"] == '2026-10-30'
    assert v30["fin"] == '2026-11-14'
    assert v30["ingreso_ventana"] == 18751.08

    # Noviembre 5 cae en la misma quincena 30 de oct al 14 de nov
    v5 = obtener_ventana_corte_fecha(cur, '2026-11-05')
    assert v5["inicio"] == '2026-10-30'
    assert v5["fin"] == '2026-11-14'
    assert v5["ingreso_ventana"] == 18751.08


def test_calcular_fecha_inicio_exportacion():
    from finanzas_core import calcular_fecha_inicio_exportacion
    # Natural: hoy
    assert calcular_fecha_inicio_exportacion('natural', hoy=date(2026, 10, 28)) == date(2026, 10, 28)
    # Próximo mes
    assert calcular_fecha_inicio_exportacion('proximo_mes', hoy=date(2026, 10, 28)) == date(2026, 11, 1)
    # Próximo mes en diciembre (cambio de año)
    assert calcular_fecha_inicio_exportacion('proximo_mes', hoy=date(2026, 12, 15)) == date(2027, 1, 1)
    # Siguiente quincena cuando hoy <= 15
    assert calcular_fecha_inicio_exportacion('siguiente_quincena', hoy=date(2026, 10, 10)) == date(2026, 10, 16)
    # Siguiente quincena cuando hoy > 15
    assert calcular_fecha_inicio_exportacion('siguiente_quincena', hoy=date(2026, 10, 20)) == date(2026, 11, 1)
    # Personalizada
    assert calcular_fecha_inicio_exportacion('personalizada', fecha_base_custom=date(2026, 11, 20), hoy=date(2026, 10, 28)) == date(2026, 11, 20)


def test_saltar_recurrente_gasto_fijo():
    from finanzas_core import saltar_recurrente_core
    conn = nueva_bd()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo)
        VALUES ('Internet Casa', 'gasto', 650.0, 'quincenal', 15, '2026-10-15', 1)
    """)
    rec_id = cur.lastrowid
    conn.commit()

    # Saltar el pago de la 1ra quincena (15 oct)
    res = saltar_recurrente_core(cur, rec_id, contabilizar_cuota=False, hoy=date(2026, 10, 10))
    conn.commit()

    assert res["nueva_proxima_fecha"] == '2026-10-31'
    assert res["activo"] is True
    # Verificar que NO se crearon transacciones
    txs = cur.execute("SELECT * FROM transacciones WHERE recurrente_id = ?", (rec_id,)).fetchall()
    assert len(txs) == 0

    # Saltar el pago de la 2da quincena (31 oct)
    res2 = saltar_recurrente_core(cur, rec_id, contabilizar_cuota=False, hoy=date(2026, 10, 10))
    conn.commit()
    assert res2["nueva_proxima_fecha"] == '2026-11-15'
    assert res2["cuotas_generadas"] == 0


def test_saltar_recurrente_msi_contabilizar_cuota():
    from finanzas_core import saltar_recurrente_core, deuda_pendiente
    conn = nueva_bd()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, total_cuotas, cuotas_generadas, monto_total, activo)
        VALUES ('Laptop MSI', 'gasto', 1000.0, 'mensual', 15, '2026-10-15', 3, 0, 3000.0, 1)
    """)
    rec_id = cur.lastrowid
    conn.commit()

    # Cuota 1 marcada como pagada por fuera
    res1 = saltar_recurrente_core(cur, rec_id, contabilizar_cuota=True, hoy=date(2026, 10, 10))
    conn.commit()
    assert res1["cuotas_generadas"] == 1
    assert res1["nueva_proxima_fecha"] == '2026-11-15'
    assert res1["activo"] is True

    fila1 = cur.execute("SELECT * FROM recurrentes WHERE id = ?", (rec_id,)).fetchone()
    assert deuda_pendiente(fila1) == 2000.0

    # Cuota 2 marcada como pagada por fuera
    res2 = saltar_recurrente_core(cur, rec_id, contabilizar_cuota=True, hoy=date(2026, 10, 10))
    conn.commit()
    assert res2["cuotas_generadas"] == 2
    assert res2["nueva_proxima_fecha"] == '2026-12-15'
    assert res2["activo"] is True

    fila2 = cur.execute("SELECT * FROM recurrentes WHERE id = ?", (rec_id,)).fetchone()
    assert deuda_pendiente(fila2) == 1000.0

    # Cuota 3 final: debe liquidar y desactivar (activo=0)
    res3 = saltar_recurrente_core(cur, rec_id, contabilizar_cuota=True, hoy=date(2026, 10, 10))
    conn.commit()
    assert res3["cuotas_generadas"] == 3
    assert res3["activo"] is False

    fila3 = cur.execute("SELECT * FROM recurrentes WHERE id = ?", (rec_id,)).fetchone()
    assert deuda_pendiente(fila3) == 0.0
    assert fila3["activo"] == 0

    # Transacciones nunca se insertaron
    txs = cur.execute("SELECT * FROM transacciones WHERE recurrente_id = ?", (rec_id,)).fetchall()
    assert len(txs) == 0


def test_saltar_recurrente_msi_solo_aplazar():
    from finanzas_core import saltar_recurrente_core, deuda_pendiente
    conn = nueva_bd()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, total_cuotas, cuotas_generadas, monto_total, activo)
        VALUES ('Celular MSI', 'gasto', 800.0, 'mensual', 20, '2026-10-20', 6, 1, 4800.0, 1)
    """)
    rec_id = cur.lastrowid
    conn.commit()

    # Aplazar fecha sin contabilizar cuota
    res = saltar_recurrente_core(cur, rec_id, contabilizar_cuota=False, hoy=date(2026, 10, 10))
    conn.commit()

    assert res["cuotas_generadas"] == 1
    assert res["nueva_proxima_fecha"] == '2026-11-20'
    assert res["activo"] is True

    fila = cur.execute("SELECT * FROM recurrentes WHERE id = ?", (rec_id,)).fetchone()
    assert fila["cuotas_generadas"] == 1
    assert deuda_pendiente(fila) == 4000.0


def test_saltar_recurrente_inactivo_falla():
    from finanzas_core import saltar_recurrente_core
    conn = nueva_bd()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo)
        VALUES ('Gasto Pausado', 'gasto', 300.0, 'mensual', 5, '2026-11-05', 0)
    """)
    rec_id = cur.lastrowid
    conn.commit()

    try:
        saltar_recurrente_core(cur, rec_id, contabilizar_cuota=False)
        assert False, "Debió lanzar ValueError por recurrente inactivo"
    except ValueError as e:
        assert "no está activo" in str(e).lower() or "inactivo" in str(e).lower()


def test_exportar_presupuesto_con_inicio_proximo_mes_evita_materializacion():
    from finanzas_core import exportar_presupuesto_a_fijos_core, materializar_recurrentes, calcular_fecha_inicio_exportacion
    conn = nueva_bd()
    cur = conn.cursor()
    conn.execute("UPDATE presupuesto_config SET ingreso_base = 25000.0, frecuencia = 'quincenal', dia_pago = 15 WHERE id = 1")
    cur.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, dia_pago)
        VALUES ('Supermercado', 'shopping-cart', 'emerald', 20.0, 5000.0, 15)
    """)
    conn.commit()

    hoy = date(2026, 10, 20)
    fecha_inicio = calcular_fecha_inicio_exportacion('proximo_mes', hoy=hoy)
    assert fecha_inicio == date(2026, 11, 1)

    exportar_presupuesto_a_fijos_core(cur, fecha_base=fecha_inicio)
    conn.commit()

    # Verificar que los recurrentes tienen fecha en noviembre
    recs = cur.execute("SELECT * FROM recurrentes WHERE activo = 1").fetchall()
    assert len(recs) >= 2  # Nómina + Supermercado
    for r in recs:
        assert date.fromisoformat(r["proxima_fecha"]) >= date(2026, 11, 1)

    # Materializar en fecha de hoy (20 oct) NO debe generar ningún cobro retroactivo
    mat = materializar_recurrentes(conn, hoy)
    assert mat == 0
    txs = cur.execute("SELECT * FROM transacciones").fetchall()
    assert len(txs) == 0


def test_exportar_presupuesto_con_inicio_siguiente_quincena():
    from finanzas_core import exportar_presupuesto_a_fijos_core, materializar_recurrentes, calcular_fecha_inicio_exportacion
    conn = nueva_bd()
    cur = conn.cursor()
    conn.execute("UPDATE presupuesto_config SET ingreso_base = 30000.0, frecuencia = 'quincenal', dia_pago = 15 WHERE id = 1")
    # Tarjeta dividida: Q1 día 1 ($2,000) y Q2 día 16 ($2,000)
    sub = [
        {"id": "q1", "nombre": "Q1", "monto": 2000.0, "porcentaje": 50.0, "dia": 1},
        {"id": "q2", "nombre": "Q2", "monto": 2000.0, "porcentaje": 50.0, "dia": 16}
    ]
    cur.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, distribucion_subperiodos)
        VALUES ('Gastos Servicios', 'zap', 'amber', 13.33, 4000.0, ?)
    """, (json.dumps(sub),))
    conn.commit()

    # Usuario empezó el 10 de octubre y elige 'siguiente_quincena'
    hoy = date(2026, 10, 10)
    fecha_inicio = calcular_fecha_inicio_exportacion('siguiente_quincena', hoy=hoy)
    assert fecha_inicio == date(2026, 10, 16)

    exportar_presupuesto_a_fijos_core(cur, fecha_base=fecha_inicio)
    conn.commit()

    # Q1 del día 1 ya no se cobra en octubre; pasa directamente a noviembre
    q1 = cur.execute("SELECT * FROM recurrentes WHERE descripcion LIKE '%(Q1)%'").fetchone()
    assert q1 is not None
    assert q1["proxima_fecha"] == '2026-11-01'

    # Q2 del día 16 queda para el 16 de octubre
    q2 = cur.execute("SELECT * FROM recurrentes WHERE descripcion LIKE '%(Q2)%'").fetchone()
    assert q2 is not None
    assert q2["proxima_fecha"] == '2026-10-16'

    # Hoy es 10 de octubre: ninguna se materializa
    mat = materializar_recurrentes(conn, hoy)
    assert mat == 0
    txs = cur.execute("SELECT * FROM transacciones").fetchall()
    assert len(txs) == 0


def test_endpoint_saltar_recurrente():
    from routers.recurrentes import saltar_recurrente, SaltarPagoRequest
    conn = nueva_bd()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, total_cuotas, cuotas_generadas, monto_total, activo)
        VALUES ('Laptop MSI', 'gasto', 1000.0, 'mensual', 15, '2026-10-15', 3, 0, 3000.0, 1)
    """)
    rec_id = cur.lastrowid
    conn.commit()

    req = SaltarPagoRequest(contabilizar_cuota=True)
    res = saltar_recurrente(rec_id, req, conn)
    assert res["cuotas_generadas"] == 1
    assert res["deuda_pendiente"] == 2000.0
    assert "detalle_salto" in res
    assert res["detalle_salto"]["cuota_contabilizada"] is True


def test_endpoint_exportar_presupuesto_diferido():
    from routers.presupuesto import exportar_presupuesto_a_fijos, ExportarFijosRequest
    conn = nueva_bd()
    cur = conn.cursor()
    conn.execute("UPDATE presupuesto_config SET ingreso_base = 25000.0, frecuencia = 'quincenal', dia_pago = 15 WHERE id = 1")
    cur.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, dia_pago)
        VALUES ('Supermercado', 'shopping-cart', 'emerald', 20.0, 5000.0, 15)
    """)
    conn.commit()

    req = ExportarFijosRequest(inicio_cargos='proximo_mes')
    res = exportar_presupuesto_a_fijos(req, conn)
    assert res["inicio_cargos_modo"] == 'proximo_mes'
    assert res["total_procesados"] >= 2
    assert "fecha_inicio_cargos" in res


def test_endpoint_saltar_recurrente_sin_body_y_con_conexion_directa():
    from routers.recurrentes import saltar_recurrente
    conn = nueva_bd()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo)
        VALUES ('Luz CFE', 'gasto', 400.0, 'mensual', 15, '2026-10-15', 1)
    """)
    rec_id = cur.lastrowid
    conn.commit()

    # Llamada pasando solo id y conn (sin SaltarPagoRequest)
    res = saltar_recurrente(rec_id, conn)
    assert res["proxima_fecha"] == '2026-11-15'
    assert res["activo"] == 1


def test_endpoint_saltar_recurrente_no_encontrado_404():
    from routers.recurrentes import saltar_recurrente
    conn = nueva_bd()
    try:
        saltar_recurrente(99999, conn)
        assert False, "Debió fallar con 404 por recurrente inexistente"
    except Exception as e:
        assert getattr(e, "status_code", None) == 404


def test_saltar_recurrente_ingreso_fijo():
    from finanzas_core import saltar_recurrente_core
    conn = nueva_bd()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, activo)
        VALUES ('Nómina Quincenal', 'ingreso', 12000.0, 'quincenal', 15, '2026-10-15', 1)
    """)
    rec_id = cur.lastrowid
    conn.commit()

    res = saltar_recurrente_core(cur, rec_id, hoy=date(2026, 10, 10))
    conn.commit()
    assert res["nueva_proxima_fecha"] == '2026-10-31'
    assert res["activo"] is True
    txs = cur.execute("SELECT * FROM transacciones WHERE recurrente_id = ?", (rec_id,)).fetchall()
    assert len(txs) == 0


def test_exportar_presupuesto_subperiodos_asimetricos_febrero():
    from finanzas_core import exportar_presupuesto_a_fijos_core
    conn = nueva_bd()
    cur = conn.cursor()
    conn.execute("UPDATE presupuesto_config SET ingreso_base = 30000.0, frecuencia = 'mensual' WHERE id = 1")
    sub = [
        {"id": "sp1", "nombre": "Semana 1", "monto": 1000.0, "porcentaje": 25.0, "dia": 1},
        {"id": "sp2", "nombre": "Semana 2", "monto": 1000.0, "porcentaje": 25.0, "dia": 15},
        {"id": "sp3", "nombre": "Semana 3", "monto": 1000.0, "porcentaje": 25.0, "dia": 31}
    ]
    cur.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, distribucion_subperiodos)
        VALUES ('Super Asimétrico', 'shopping-cart', 'emerald', 10.0, 3000.0, ?)
    """, (json.dumps(sub),))
    conn.commit()

    # Exportar el 10 de febrero de año no bisiesto (2027)
    res = exportar_presupuesto_a_fijos_core(cur, fecha_base=date(2027, 2, 10))
    conn.commit()

    recs = cur.execute('SELECT descripcion, dia, proxima_fecha FROM recurrentes WHERE descripcion LIKE "%Super Asimétrico%" ORDER BY id ASC').fetchall()
    assert len(recs) == 3
    # sp1 (dia 1) pasa a marzo porque 10 feb > 1 feb
    assert recs[0]["proxima_fecha"] == '2027-03-01'
    # sp2 (dia 15) queda para el 15 de feb
    assert recs[1]["proxima_fecha"] == '2027-02-15'
    # sp3 (dia 31) se ancla al último día de febrero (28 de feb) sin reventar
    assert recs[2]["dia"] == 31
    assert recs[2]["proxima_fecha"] == '2027-02-28'


def test_repartir_en_centavos_cuadra_siempre():
    import random
    rnd = random.Random(7)
    configs = [[25] * 4, [50, 50], [33.33, 33.33, 33.34], [10, 20, 70], [100], [0, 100]]
    for _ in range(2000):
        total = round(rnd.uniform(0.01, 99999), 2)
        for pcts in configs:
            montos = repartir_en_centavos(total, pcts)
            assert round(sum(montos), 2) == total, (total, pcts, montos)
            for m, p in zip(montos, pcts):
                assert abs(m - total * p / 100) <= 0.01 + 1e-9, (total, pcts, montos)
    assert repartir_en_centavos(100.03, [25] * 4) == [25.01, 25.01, 25.01, 25.0]
    assert repartir_en_centavos(10, [0, 100]) == [0.0, 10.0]
    assert repartir_en_centavos(10, []) == []
    # Si no suman 100 no se fuerza el total: cada parte se redondea por separado (mitad hacia arriba).
    assert repartir_en_centavos(2.01, [50]) == [1.01]


def test_distribucion_subperiodos_y_tarjeta_cuadran_al_centavo():
    sps = [{"id": f"s{i}", "nombre": f"Semana {i}", "porcentaje": 25.0} for i in range(1, 5)]
    d = calcular_distribucion_subperiodos(100.03, sps)
    assert round(sum(x["monto"] for x in d), 2) == 100.03, d
    assert [x["id"] for x in d] == ["s1", "s2", "s3", "s4"]
    # Tarjeta asimétrica cuyo monto cambió: se reescala y sigue cuadrando.
    custom = [{"id": "q1", "monto": 333.33}, {"id": "q2", "monto": 666.67}]
    d = calcular_distribucion_tarjeta_asimetrica(1000.01, [], json.dumps(custom))
    assert round(sum(x["monto"] for x in d), 2) == 1000.01, d
    custom = [{"id": "q1", "monto": 1, "porcentaje": 50}, {"id": "q2", "monto": 1, "porcentaje": 50}]
    d = calcular_distribucion_tarjeta_asimetrica(1000.01, [], custom)
    assert round(sum(x["monto"] for x in d), 2) == 1000.01, d


def test_fechas_bisiesto_y_cambio_de_anio():
    assert siguiente_fecha(date(2028, 2, 15), 'quincenal', None) == date(2028, 2, 29)
    assert siguiente_fecha(date(2028, 2, 29), 'quincenal', None) == date(2028, 3, 15)
    assert siguiente_fecha(date(2027, 2, 15), 'quincenal', None) == date(2027, 2, 28)
    assert siguiente_fecha(date(2026, 12, 31), 'quincenal', None) == date(2027, 1, 15)
    assert siguiente_fecha(date(2026, 12, 20), 'quincenal', None) == date(2026, 12, 31)
    assert siguiente_fecha(date(2026, 12, 31), 'mensual', 31) == date(2027, 1, 31)
    assert siguiente_fecha(date(2028, 1, 29), 'mensual', 29) == date(2028, 2, 29)
    assert siguiente_fecha(date(2027, 1, 29), 'mensual', 29) == date(2027, 2, 28)
    assert siguiente_fecha(date(2026, 12, 28), 'semanal', None) == date(2027, 1, 4)
    # Ya vigente: no avanza. Vencida: salta hasta la primera >= hoy.
    assert proxima_fecha_vigente(date(2026, 5, 10), 'mensual', 10, date(2026, 5, 10)) == date(2026, 5, 10)
    assert proxima_fecha_vigente(date(2026, 1, 31), 'mensual', 31, date(2026, 4, 1)) == date(2026, 4, 30)


def test_frecuencias_ida_y_vuelta():
    assert mensualizar(100, 'semanal') == 433.33
    assert mensualizar(1500, 'quincenal') == 3000
    eq = convertir_frecuencias(100, 'semanal')
    assert eq["mensual"] == 433.33 and eq["semanal"] == 100.0, eq
    assert abs(eq["quincenal"] - 433.33 / 2) <= 0.005 + 1e-9, eq  # float: 216.665 puede quedar en .66 o .67
    for frec in ('mensual', 'quincenal', 'semanal'):
        for monto in (0.01, 99.99, 1234.56, 50000):
            vuelta = convertir_de_mensual(mensualizar(monto, frec), frec)
            assert abs(vuelta - monto) <= 0.01, (frec, monto, vuelta)


def test_convertir_y_rendimiento_casos_borde():
    assert convertir(100, 'usd', 'mxn', 18) == 1800
    assert convertir(100, 'EUR', 'MXN', 18) == 100
    assert convertir(100, 'MXN', 'USD', 0) == 100
    d = date(2026, 1, 1)
    assert valor_rendimiento(10000, 10, d, d - timedelta(days=30)) == 10000
    assert valor_rendimiento(10000, 0, d, d + timedelta(days=365)) == 10000


def test_msi_monto_no_divisible_suma_exacta():
    conn = nueva_bd()
    insertar_recurrente(conn, "Tablet", 'gasto', 333.33, 'mensual', 5, date(2026, 1, 5), total=3, monto_total=1000)
    assert materializar_recurrentes(conn, date(2026, 12, 1)) == 3
    montos = [r["monto"] for r in conn.execute("SELECT monto FROM transacciones ORDER BY fecha")]
    assert round(sum(montos), 2) == 1000.00, montos
    assert montos[-1] == 333.34, montos
    assert deuda_pendiente(conn.execute("SELECT * FROM recurrentes").fetchone()) == 0.0


def test_periodo_actual_quincena_eventos_y_libre():
    conn = nueva_bd()
    hoy = date(2026, 10, 20)
    insertar_recurrente(conn, "Nómina", 'ingreso', 14000, 'quincenal', None, date(2026, 10, 31))
    insertar_recurrente(conn, "Netflix", 'gasto', 299, 'mensual', 25, date(2026, 10, 25))
    insertar_recurrente(conn, "Renta", 'gasto', 9500, 'mensual', 1, date(2026, 11, 1))  # fuera del periodo
    insertar_recurrente(conn, "Clases", 'gasto', 200, 'semanal', None, date(2026, 10, 21))  # 21 y 28
    insertar_recurrente(conn, "Tablet", 'gasto', 500, 'mensual', 22, date(2026, 10, 22), total=3, monto_total=1500)
    conn.execute("UPDATE recurrentes SET cuotas_generadas = 2 WHERE descripcion = 'Tablet'")  # queda 1 cuota
    p = calcular_periodo_actual(conn, hoy, 5000.0)
    assert (p["inicio"], p["fin"], p["dias_restantes"], p["dias_totales"]) == ("2026-10-15", "2026-10-31", 11, 16), p
    assert p["cobro"]["descripcion"] == "Nómina", p
    assert [(e["fecha"], e["descripcion"]) for e in p["eventos"]] == [
        ("2026-10-21", "Clases"), ("2026-10-22", "Tablet"), ("2026-10-25", "Netflix"), ("2026-10-28", "Clases")], p["eventos"]
    assert p["comprometido"] == 1199.0 and p["libre"] == 3801.0, p
    # Cobro de la primera quincena: el periodo arranca el fin del mes anterior.
    conn.execute("UPDATE recurrentes SET proxima_fecha = '2026-03-15' WHERE descripcion = 'Nómina'")
    p = calcular_periodo_actual(conn, date(2026, 3, 2), 0.0)
    assert (p["inicio"], p["fin"]) == ("2026-02-28", "2026-03-15"), p


def test_periodo_actual_sin_ingreso_fijo_usa_mes_calendario():
    conn = nueva_bd()
    p = calcular_periodo_actual(conn, date(2028, 2, 10), 1000.0)
    assert (p["inicio"], p["fin"], p["cobro"], p["libre"]) == ("2028-02-01", "2028-02-29", None, 1000.0), p


def test_fijos_por_periodo_programados_pausados_y_cuota_msi():
    conn = nueva_bd()
    hoy = date(2026, 10, 20)
    insertar_recurrente(conn, "Nómina", 'ingreso', 14000, 'quincenal', None, date(2026, 10, 31))
    insertar_recurrente(conn, "Netflix", 'gasto', 299, 'mensual', 25, date(2026, 10, 25))
    insertar_recurrente(conn, "Tablet", 'gasto', 500, 'mensual', 22, date(2026, 10, 22), total=3, monto_total=1500)
    conn.execute("UPDATE recurrentes SET cuotas_generadas = 2 WHERE descripcion = 'Tablet'")  # queda la cuota 3
    insertar_recurrente(conn, "Gym", 'gasto', 400, 'mensual', 5, date(2026, 11, 5))
    conn.execute("UPDATE recurrentes SET activo = 0 WHERE descripcion = 'Gym'")
    r = fijos_por_periodo(conn, hoy, 2)
    p1, p2 = r["periodos"]
    # El periodo cierra el día antes del siguiente cobro: la nómina del 31 abre el segundo periodo
    assert (p1["inicio"], p1["fin"], p1["en_curso"]) == ("2026-10-15", "2026-10-30", True), p1
    assert [(i["fecha"], i["descripcion"], i["estado"]) for i in p1["items"]] == [
        ("2026-10-22", "Tablet", "programado"), ("2026-10-25", "Netflix", "programado")], p1["items"]
    tablet = p1["items"][0]
    assert (tablet["es_msi"], tablet["cuota"], tablet["total_cuotas"]) == (True, 3, 3), tablet
    assert p1["totales"] == {"ingresos": 0.0, "fijos": 299.0, "msi": 500.0, "por_pagar": 799.0,
                             "libre": -799.0, "saldo_al_cierre": -799.0}, p1["totales"]
    assert (p2["inicio"], p2["fin"], p2["fecha_cobro"]) == ("2026-10-31", "2026-11-14", "2026-11-15"), p2
    assert [(i["descripcion"], i["estado"]) for i in p2["items"]] == [("Nómina", "programado")], p2["items"]
    assert p2["totales"]["ingresos"] == 14000.0, p2["totales"]
    # Libre del periodo = ingreso - fijos - MSI; el saldo al cierre arrastra el déficit del periodo anterior
    assert p2["totales"]["libre"] == 14000.0, p2["totales"]
    assert p2["totales"]["saldo_al_cierre"] == 13201.0, p2["totales"]
    # Los pausados no tienen periodo: van aparte y no suman a ningún periodo
    assert [(i["descripcion"], i["estado"]) for i in r["pausados"]] == [("Gym", "pausado")], r["pausados"]
    assert all(i["descripcion"] != "Gym" for p in r["periodos"] for i in p["items"])


def test_fijos_por_periodo_marca_aplicados_y_no_cuenta_dos_veces():
    from finanzas_core import materializar_recurrentes
    conn = nueva_bd()
    insertar_recurrente(conn, "Nómina", 'ingreso', 14000, 'quincenal', None, date(2026, 10, 31))
    insertar_recurrente(conn, "Netflix", 'gasto', 299, 'mensual', 25, date(2026, 10, 25))
    hoy = date(2026, 10, 26)
    materializar_recurrentes(conn, hoy)
    conn.commit()
    p1 = fijos_por_periodo(conn, hoy, 1)["periodos"][0]
    estados = [(i["descripcion"], i["estado"]) for i in p1["items"]]
    assert estados.count(("Netflix", "aplicado")) == 1, estados
    assert ("Netflix", "programado") not in estados, estados  # ya cargado: no se programa de nuevo
    assert p1["totales"]["fijos"] == 299.0, p1["totales"]


def _con_pagos(frecuencia, dias, tarjetas):
    """Presupuesto con pagos: dias por orden ({orden: día}) y tarjetas [(orden, nombre, monto)]."""
    conn = nueva_bd()
    conn.execute("UPDATE presupuesto_config SET ingreso_base = 5000, frecuencia = ? WHERE id = 1", (frecuencia,))
    for orden, dia in dias.items():
        conn.execute("UPDATE presupuesto_unidades SET dia_pago = ? WHERE orden = ?", (dia, orden))
    for orden, nombre, monto in tarjetas:
        conn.execute(
            "INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden, unidad_orden) "
            "VALUES (?, 'x', 'amber', 0, ?, '', 1, ?)", (nombre, monto, orden))
    conn.commit()
    return conn


def test_pagos_semanales_cada_tarjeta_cae_en_su_fecha():
    from finanzas_core import exportar_presupuesto_a_fijos_core
    conn = _con_pagos('semanal', {1: 5, 2: 12, 3: 19, 4: 26},
                      [(1, 'Renta', 1500.0), (3, 'Despensa', 800.0)])
    res = exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 20))
    conn.commit()
    fijos = {r["descripcion"]: r for r in conn.execute("SELECT * FROM recurrentes WHERE activo = 1")}
    # Las tarjetas de pago son fijos mensuales en el día de su pago
    assert fijos["Renta"]["dia"] == 5 and fijos["Renta"]["frecuencia"] == 'mensual', dict(fijos["Renta"])
    assert fijos["Despensa"]["dia"] == 19 and fijos["Despensa"]["monto"] == 800.0
    assert fijos["Nómina Fija (Presupuesto)"]["frecuencia"] == 'semanal'
    assert res["creados"] == 3, res


def test_pago_con_tarjetas_exige_fecha():
    from finanzas_core import exportar_presupuesto_a_fijos_core
    conn = _con_pagos('quincenal', {1: None, 2: 30}, [(1, 'Renta', 1500.0)])
    try:
        exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 20))
    except ValueError as e:
        assert "fecha" in str(e).lower(), e
    else:
        raise AssertionError("debía fallar: el pago 1 tiene tarjetas y no tiene fecha")


def test_fechas_de_pago_en_orden_creciente():
    from finanzas_core import exportar_presupuesto_a_fijos_core
    conn = _con_pagos('quincenal', {1: 20, 2: 10}, [(1, 'Renta', 1500.0), (2, 'Luz', 300.0)])
    try:
        exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 20))
    except ValueError as e:
        assert "orden" in str(e).lower(), e
    else:
        raise AssertionError("debía fallar: el pago 2 (día 10) va antes que el pago 1 (día 20)")


def test_pago_no_supera_su_ingreso_al_exportar():
    from finanzas_core import exportar_presupuesto_a_fijos_core
    conn = _con_pagos('quincenal', {1: 15, 2: 30}, [(1, 'Renta', 4000.0), (1, 'Coche', 1500.0)])
    try:
        exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 20))
    except ValueError as e:
        assert "pago 1" in str(e), e
    else:
        raise AssertionError("debía fallar: el pago 1 suma 5500 con un ingreso de 5000")


def test_pago_inactivo_no_se_exporta_pero_conserva_la_tarjeta():
    from finanzas_core import exportar_presupuesto_a_fijos_core
    conn = _con_pagos('mensual', {1: 15}, [(1, 'Renta', 1500.0), (3, 'Escondida', 900.0)])
    res = exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 20))
    conn.commit()
    assert _activos(conn).get("Escondida") is None, _activos(conn)
    assert conn.execute("SELECT COUNT(*) FROM presupuesto_tarjetas WHERE nombre = 'Escondida'").fetchone()[0] == 1
    assert res["creados"] == 2, res


def _presupuesto_para_export():
    conn = nueva_bd()
    conn.execute("UPDATE presupuesto_config SET ingreso_base = 9000, frecuencia = 'quincenal' WHERE id = 1")
    conn.execute("INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden) VALUES ('Transporte', 'x', 'amber', 20, 2000, 'Transporte', 1)")
    conn.execute("INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden) VALUES ('Dentista', 'x', 'blue', 5, 500, 'Salud', 2)")
    conn.commit()
    return conn


def _activos(conn):
    return {r["descripcion"]: r["monto"] for r in conn.execute("SELECT descripcion, monto FROM recurrentes WHERE activo = 1")}


def test_export_sobrescribe_todo_y_borra_lo_que_sobra():
    from finanzas_core import exportar_presupuesto_a_fijos_core
    conn = _presupuesto_para_export()
    exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 20))
    conn.commit()
    # Se cambia el monto y se desmarca Dentista: el fijo de Dentista debe desactivarse
    conn.execute("UPDATE presupuesto_tarjetas SET monto = 2200 WHERE nombre = 'Transporte'")
    conn.commit()
    res = exportar_presupuesto_a_fijos_core(conn.cursor(), tarjetas_ids=[1], fecha_base=date(2026, 10, 20))
    conn.commit()
    assert _activos(conn) == {"Nómina Fija (Presupuesto)": 9000.0, "Transporte": 2200.0}, _activos(conn)
    assert res["eliminados"] == 1 and res["actualizados"] == 2 and res["creados"] == 0, res
    assert conn.execute("SELECT COUNT(*) FROM recurrentes WHERE descripcion = 'Dentista'").fetchone()[0] == 0


def test_export_borra_tambien_los_fijos_a_mano_y_los_msi():
    from finanzas_core import exportar_presupuesto_a_fijos_core
    conn = _presupuesto_para_export()
    insertar_recurrente(conn, "Nómina quincenal", 'ingreso', 14250, 'quincenal', 15, date(2026, 10, 31))
    insertar_recurrente(conn, "Netflix", 'gasto', 299, 'mensual', 15, date(2026, 10, 25))
    conn.execute("""
        INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, total_cuotas, cuotas_generadas, activo)
        VALUES ('Pixel 10', 'gasto', 1074.92, 'mensual', 14, '2026-11-14', 10, 1, 1)
    """)
    conn.commit()
    exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 20))
    conn.commit()
    # Los fijos hechos a mano y la compra a meses que no está en el presupuesto se borran; la nómina sigue
    activos = _activos(conn)
    assert "Netflix" not in activos and "Nómina quincenal" not in activos, activos
    assert "Pixel 10" not in activos, activos
    assert activos["Nómina Fija (Presupuesto)"] == 9000.0, activos


def test_export_renombrar_tarjeta_reutiliza_fijo_sin_duplicar_cargo():
    from finanzas_core import exportar_presupuesto_a_fijos_core
    conn = _presupuesto_para_export()
    exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 15))
    conn.commit()
    materializar_recurrentes(conn, date(2026, 10, 15))
    conn.execute("UPDATE presupuesto_tarjetas SET nombre = 'Transporte MX' WHERE nombre = 'Transporte'")
    conn.commit()
    exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 15))
    conn.commit()
    materializar_recurrentes(conn, date(2026, 10, 15))
    assert _activos(conn).get("Transporte MX") == 2000.0, _activos(conn)
    assert "Transporte" not in _activos(conn)
    cargos = conn.execute("SELECT COUNT(*) FROM transacciones WHERE descripcion LIKE 'Transporte%' AND fecha = '2026-10-15'").fetchone()[0]
    assert cargos == 1, cargos


def test_fijos_de_tarjeta_borrada_dejan_de_cobrarse():
    from finanzas_core import exportar_presupuesto_a_fijos_core
    conn = _presupuesto_para_export()
    exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 20))
    conn.commit()
    tid = conn.execute("SELECT id FROM presupuesto_tarjetas WHERE nombre = 'Dentista'").fetchone()[0]
    conn.execute("UPDATE recurrentes SET activo = 0 WHERE presupuesto_tarjeta_id = ? AND total_cuotas IS NULL", (tid,))
    conn.execute("DELETE FROM presupuesto_tarjetas WHERE id = ?", (tid,))
    conn.commit()
    assert "Dentista" not in _activos(conn), _activos(conn)


def test_export_no_duplica_cargo_ya_registrado():
    from finanzas_core import exportar_presupuesto_a_fijos_core
    conn = _presupuesto_para_export()
    exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 15))
    conn.commit()
    materializar_recurrentes(conn, date(2026, 10, 15))
    # Se sobrescribe todo: el fijo viejo se borra y el nuevo debe saltar el cargo ya registrado
    exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 15))
    conn.commit()
    materializar_recurrentes(conn, date(2026, 10, 15))
    cargos = conn.execute("SELECT COUNT(*) FROM transacciones WHERE descripcion = 'Transporte' AND fecha = '2026-10-15'").fetchone()[0]
    assert cargos == 1, cargos
    assert _activos(conn)["Transporte"] == 2000.0


def test_resumen_mensual_excluye_movimientos_de_apartados():
    conn = nueva_bd()
    conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual, meta_monto) VALUES ('Viaje', 'meta', 0, 18000)")
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-10-01', 28500, 'ingreso')")
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-10-05', 1500, 'gasto')")
    # Depósito a meta (gasto ligado a apartado) y retiro (ingreso ligado): no son gasto ni ingreso del mes
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-10-06', 4500, 'gasto', 1)")
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-10-07', 500, 'ingreso', 1)")
    conn.commit()
    mes = resumen_mensual_core(conn)
    assert mes == [{"mes": "2026-10", "ingresos": 28500.0, "gastos": 1500.0}], mes
    # Coincide con el mes actual que usa el Resumen
    r = calcular_resumen(conn, 'MXN', 18, date(2026, 10, 31))
    assert r["mes_actual"]["gastos"] == mes[0]["gastos"] and r["mes_actual"]["ingresos"] == mes[0]["ingresos"], r["mes_actual"]


def test_saldo_de_apartados_coincide_con_sus_movimientos():
    import random
    conn = nueva_bd()
    conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual) VALUES ('A', 'libre', 0)")
    conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual) VALUES ('B', 'libre', 0)")
    conn.commit()
    rnd = random.Random(3)
    for _ in range(400):
        aid = rnd.choice([1, 2])
        monto = round(rnd.uniform(1, 300), 2)
        guardado = conn.execute("SELECT monto_actual FROM apartados WHERE id = ?", (aid,)).fetchone()[0]
        if rnd.random() < 0.6 or guardado < monto:
            conn.execute("INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion, apartado_id) VALUES ('2026-01-06', ?, 'gasto', 1, 'dep', ?)", (monto, aid))
            conn.execute("UPDATE apartados SET monto_actual = monto_actual + ? WHERE id = ?", (monto, aid))
        elif rnd.random() < 0.5 and guardado >= monto:
            conn.execute("INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion, apartado_id) VALUES ('2026-01-06', ?, 'ingreso', 1, 'ret', ?)", (monto, aid))
            conn.execute("UPDATE apartados SET monto_actual = monto_actual - ? WHERE id = ?", (monto, aid))
        else:
            # Borrar un movimiento de la meta (depósito o retiro) al azar: el saldo debe seguir cuadrando
            mov = conn.execute("SELECT id, tipo, monto FROM transacciones WHERE apartado_id = ? ORDER BY RANDOM() LIMIT 1", (aid,)).fetchone()
            if mov:
                try:
                    revertir_movimiento_apartado(conn.cursor(), aid, mov["tipo"], mov["monto"])
                except ValueError:
                    pass  # la app lo rechaza: el depósito ya tiene retiros encima
                else:
                    conn.execute("DELETE FROM transacciones WHERE id = ?", (mov["id"],))
        conn.commit()
    for aid in (1, 2):
        guardado = conn.execute("SELECT monto_actual FROM apartados WHERE id = ?", (aid,)).fetchone()[0]
        movimientos = conn.execute("SELECT COALESCE(SUM(CASE WHEN tipo = 'gasto' THEN monto ELSE -monto END), 0) FROM transacciones WHERE apartado_id = ?", (aid,)).fetchone()[0]
        assert abs(guardado - movimientos) < 0.01, (aid, guardado, movimientos)


def test_msi_saldo_y_prorrateo_se_mantienen():
    conn = nueva_bd()
    insertar_recurrente(conn, "Laptop", 'gasto', 2000, 'mensual', 10, date(2026, 1, 10), total=12, monto_total=24000)
    materializar_recurrentes(conn, date(2026, 3, 15))
    fila = conn.execute("SELECT * FROM recurrentes").fetchone()
    assert fila["cuotas_generadas"] == 3 and deuda_pendiente(fila) == 18000.0
    # Cambiar el total re-prorratea y la última cuota absorbe el redondeo
    conn.execute("UPDATE recurrentes SET monto_total = 25000 WHERE id = 1")
    conn.commit()
    materializar_recurrentes(conn, date(2026, 12, 31))
    assert round(conn.execute("SELECT SUM(monto) FROM transacciones").fetchone()[0], 2) == 25000.0


def test_quincenal_cae_en_15_y_fin_de_mes():
    conn = nueva_bd()
    insertar_recurrente(conn, "Gym", 'gasto', 300, 'quincenal', None, date(2026, 10, 15))
    assert materializar_recurrentes(conn, date(2026, 10, 31)) == 2
    fechas = [r[0] for r in conn.execute("SELECT fecha FROM transacciones ORDER BY fecha")]
    assert fechas == ["2026-10-15", "2026-10-31"], fechas


def test_conciliacion_resumen_historial_y_periodo():
    conn = nueva_bd()
    conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual, meta_monto) VALUES ('Viaje', 'meta', 0, 18000)")
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-10-01', 28500, 'ingreso')")
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2026-10-05', 1500, 'gasto')")
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-10-06', 4500, 'gasto', 1)")
    conn.execute("UPDATE apartados SET monto_actual = 4500 WHERE id = 1")
    conn.commit()
    hoy = date(2026, 10, 20)
    r = calcular_resumen(conn, 'MXN', 18, hoy)
    mes = resumen_mensual_core(conn)[0]
    per = calcular_periodo_actual(conn, hoy, r["balance"])
    # Disponible = todo lo que entró menos todo lo que salió, incluido lo guardado en metas
    assert r["balance"] == 28500 - 1500 - 4500 == 22500.0, r["balance"]
    assert r["mes_actual"]["gastos"] == mes["gastos"] == 1500.0
    assert r["mes_actual"]["ingresos"] == mes["ingresos"] == 28500.0
    assert r["apartados"] == 4500.0 and r["patrimonio_neto"] == 4500.0
    assert per["disponible"] == r["balance"], per
    # Lo guardado en metas más lo disponible es el efectivo total: 22,500 + 4,500 = 27,000
    assert r["balance"] + r["apartados"] == 27000.0


def test_no_se_borra_deposito_si_ya_se_retiro_parte():
    conn = nueva_bd()
    conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual) VALUES ('Viaje', 'meta', 100)")
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-10-01', 100, 'gasto', 1)")
    dep = conn.execute("SELECT id FROM transacciones").fetchone()["id"]
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-10-02', 50, 'ingreso', 1)")
    conn.execute("UPDATE apartados SET monto_actual = 50 WHERE id = 1")
    conn.commit()
    try:
        revertir_movimiento_apartado(conn.cursor(), 1, 'gasto', 100)
    except ValueError as e:
        assert "retiro" in str(e).lower(), str(e)
    else:
        raise AssertionError("debía rechazar: el depósito ya tiene un retiro encima")
    # El rechazo no cambia nada
    assert conn.execute("SELECT monto_actual FROM apartados WHERE id = 1").fetchone()[0] == 50


def test_borrar_retiro_luego_deposito_queda_cuadrado():
    conn = nueva_bd()
    conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual) VALUES ('Viaje', 'meta', 100)")
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-10-01', 100, 'gasto', 1)")
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-10-02', 50, 'ingreso', 1)")
    conn.execute("UPDATE apartados SET monto_actual = 50 WHERE id = 1")
    ret = conn.execute("SELECT id FROM transacciones WHERE tipo = 'ingreso'").fetchone()["id"]
    conn.commit()
    # Primero se borra el retiro (el dinero vuelve a la meta) y después el depósito
    revertir_movimiento_apartado(conn.cursor(), 1, 'ingreso', 50)
    conn.execute("DELETE FROM transacciones WHERE id = ?", (ret,))
    revertir_movimiento_apartado(conn.cursor(), 1, 'gasto', 100)
    conn.execute("DELETE FROM transacciones WHERE apartado_id = 1 AND tipo = 'gasto'")
    conn.commit()
    assert conn.execute("SELECT monto_actual FROM apartados WHERE id = 1").fetchone()[0] == 0.0


def _escenario_periodos():
    """
    Hoy 20 oct 2026, nómina quincenal de 14,000 (ya cobrada el 15 oct, próxima el 31).
    Antes del periodo: nómina 30 sep +14,000, súper 5 oct -3,000, cuota 1/3 de la tablet 10 oct -333.33
        -> saldo inicial 10,666.67
    """
    conn = nueva_bd()
    nomina = insertar_recurrente(conn, "Nómina", 'ingreso', 14000, 'quincenal', None, date(2026, 10, 31))
    insertar_recurrente(conn, "Renta", 'gasto', 9500, 'mensual', 1, date(2026, 11, 1))
    insertar_recurrente(conn, "Clases", 'gasto', 200, 'semanal', None, date(2026, 10, 21))
    tablet = insertar_recurrente(conn, "Tablet", 'gasto', 333.33, 'mensual', 10, date(2026, 11, 10), total=3, monto_total=1000)
    conn.execute("UPDATE recurrentes SET cuotas_generadas = 1 WHERE id = ?", (tablet["id"],))
    conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual) VALUES ('Viaje', 'meta', 800)")
    aporte = insertar_recurrente(conn, "Aporte viaje", 'gasto', 500, 'mensual', 16, date(2026, 11, 16))
    conn.execute("UPDATE recurrentes SET apartado_id = 1 WHERE id = ?", (aporte["id"],))
    for fecha, monto, tipo, rec, ap in [
        ("2026-09-30", 14000, 'ingreso', nomina["id"], None),
        ("2026-10-05", 3000, 'gasto', None, None),
        ("2026-10-10", 333.33, 'gasto', tablet["id"], None),
        ("2026-10-15", 14000, 'ingreso', nomina["id"], None),
        ("2026-10-16", 2000, 'ingreso', None, None),  # bono puntual
        ("2026-10-18", 1000, 'gasto', None, None),
        ("2026-10-20", 1000, 'gasto', None, 1),  # depósito a la meta
        ("2026-10-20", 200, 'ingreso', None, 1),  # retiro de la meta
    ]:
        conn.execute("INSERT INTO transacciones (fecha, monto, tipo, recurrente_id, apartado_id) VALUES (?, ?, ?, ?, ?)",
                     (fecha, monto, tipo, rec, ap))
    conn.commit()
    return conn, date(2026, 10, 20)


def test_msi_pausada_cuenta_como_deuda_pero_no_resta_en_el_periodo():
    conn = nueva_bd()
    insertar_recurrente(conn, "Sueldo", 'ingreso', 5000, 'mensual', 1, date(2026, 10, 1))
    insertar_recurrente(conn, "Celular", 'gasto', 500, 'mensual', 25, date(2026, 10, 25), total=6, monto_total=3000)
    conn.execute("UPDATE recurrentes SET activo = 0 WHERE descripcion = 'Celular'")
    conn.commit()
    r = calcular_resumen(conn, 'MXN', 18.0, date(2026, 10, 20))
    assert r['deuda_msi'] == 3000.0, r['deuda_msi']  # sigue siendo deuda
    p = r['periodos'][0]
    assert p['gastos']['msi'] == 0.0 and p['remanente'] == 5000.0, p  # no resta en el periodo
    assert r['periodo']['comprometido'] == 0.0 and r['periodo']['libre'] == r['balance'] + 5000.0, r['periodo']  # + ingreso por recibir


def test_fijos_pausados_no_cuentan_en_el_periodo():
    conn = nueva_bd()
    insertar_recurrente(conn, "Sueldo", 'ingreso', 5000, 'mensual', 1, date(2026, 10, 1))
    insertar_recurrente(conn, "Bono pausado", 'ingreso', 2000, 'mensual', 15, date(2026, 10, 15))
    insertar_recurrente(conn, "Netflix pausado", 'gasto', 300, 'mensual', 25, date(2026, 10, 25))
    conn.execute("UPDATE recurrentes SET activo = 0 WHERE descripcion IN ('Bono pausado', 'Netflix pausado')")
    conn.commit()
    r = calcular_resumen(conn, 'MXN', 18.0, date(2026, 10, 20))
    p = r['periodos'][0]
    assert p['ingresos']['total'] == 5000.0 and p['gastos']['fijos'] == 0.0, p  # ni el bono ni el gasto pausados
    assert p['remanente'] == 5000.0 and r['periodo']['comprometido'] == 0.0, p
    assert r['deuda_msi'] == 0.0, r['deuda_msi']
    assert all(e['descripcion'] != 'Netflix pausado' for e in p['eventos']), p['eventos']


def test_periodos_siguen_la_quincena_y_comparan_ingreso_con_gastos():
    from finanzas_core import calcular_periodos
    conn, hoy = _escenario_periodos()
    datos = calcular_periodos(conn, hoy, 5)
    ps = datos["periodos"]
    assert datos["ingreso_principal"]["descripcion"] == "Nómina"
    assert [(p["inicio"], p["fin"], p["fecha_cobro"]) for p in ps] == [
        ("2026-10-15", "2026-10-30", "2026-10-31"),
        ("2026-10-31", "2026-11-14", "2026-11-15"),
        ("2026-11-15", "2026-11-29", "2026-11-30"),
        ("2026-11-30", "2026-12-14", "2026-12-15"),
        ("2026-12-15", "2026-12-30", "2026-12-31"),
    ], ps
    assert [p["en_curso"] for p in ps] == [True, False, False, False, False]
    # P0 15-30 oct: entra nómina 14,000 + bono 2,000; súper 1,000 pagado; clases 21 y 28 por pagar 400;
    #     meta: depósito 1,000 - retiro 200 = 800. Remanente 16,000 - 1,400 - 800 = 13,800
    p0 = ps[0]
    assert p0["ingresos"] == {"recibido": 16000.0, "por_recibir": 0.0, "total": 16000.0}, p0["ingresos"]
    assert p0["gastos"] == {"normales": 1000.0, "fijos": 400.0, "msi": 0.0, "fijos_y_msi": 400.0, "pagado": 1000.0, "por_pagar": 400.0, "total": 1400.0}, p0["gastos"]
    assert (p0["metas"], p0["remanente"], p0["saldo_inicial"], p0["saldo_final"]) == (800.0, 13800.0, 10666.67, 24466.67), p0
    # P1 31 oct-14 nov: nómina 31 oct 14,000; renta 1 nov 9,500 + clases 4 y 11 nov 400; cuota 2/3 333.33
    p1 = ps[1]
    assert p1["ingresos"]["por_recibir"] == 14000.0 and p1["gastos"]["fijos"] == 9900.0 and p1["gastos"]["msi"] == 333.33, p1
    assert (p1["remanente"], p1["saldo_final"]) == (3766.67, 28233.34), p1
    # P2 15-29 nov: nómina 14,000; clases 18 y 25 nov 400; aporte a la meta 16 nov 500 (no es gasto)
    p2 = ps[2]
    assert (p2["gastos"]["total"], p2["metas"], p2["remanente"], p2["saldo_final"]) == (400.0, 500.0, 13100.0, 41333.34), p2
    # P3 30 nov-14 dic: nómina 14,000; renta 1 dic 9,500 + clases 2 y 9 dic 400; última cuota 3/3 ajustada 333.34
    p3 = ps[3]
    assert (p3["gastos"]["msi"], p3["remanente"], p3["saldo_final"]) == (333.34, 3766.66, 45100.0), p3
    # P4 15-30 dic: la tablet ya terminó; clases 16, 23 y 30 dic = 600 (fechas reales, no un promedio)
    p4 = ps[4]
    assert (p4["gastos"]["msi"], p4["gastos"]["fijos"], p4["metas"], p4["remanente"]) == (0.0, 600.0, 500.0, 12900.0), p4
    for anterior, siguiente in zip(ps, ps[1:]):
        assert anterior["saldo_final"] == siguiente["saldo_inicial"], (anterior, siguiente)


def test_periodo_actual_libre_es_el_saldo_al_cierre_del_periodo():
    from finanzas_core import calcular_periodos
    conn, hoy = _escenario_periodos()
    balance = float(conn.execute("SELECT SUM(CASE WHEN tipo='ingreso' THEN monto ELSE -monto END) FROM transacciones").fetchone()[0])
    p = calcular_periodo_actual(conn, hoy, balance)
    assert (p["inicio"], p["fin"]) == ("2026-10-15", "2026-10-31"), p
    assert [e["fecha"] for e in p["eventos"]] == ["2026-10-21", "2026-10-28"], p["eventos"]
    assert p["libre"] == calcular_periodos(conn, hoy, 1)["periodos"][0]["saldo_final"] == 24466.67, p


def test_periodos_sin_ingreso_fijo_son_meses_calendario():
    from finanzas_core import calcular_periodos
    conn = nueva_bd()
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2028-02-01', 1000, 'ingreso')")
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2028-02-03', 100, 'gasto')")
    datos = calcular_periodos(conn, date(2028, 2, 10), 2)
    ps = datos["periodos"]
    assert datos["ingreso_principal"] is None
    assert [(p["inicio"], p["fin"], p["fecha_cobro"], p["remanente"], p["saldo_final"]) for p in ps] == [
        ("2028-02-01", "2028-02-29", None, 900.0, 900.0),
        ("2028-03-01", "2028-03-31", None, 0.0, 900.0),
    ], ps


def test_periodo_lo_marca_el_ingreso_fijo_mayor():
    from finanzas_core import calcular_periodos
    conn = nueva_bd()
    insertar_recurrente(conn, "Freelance", 'ingreso', 5000, 'quincenal', None, date(2026, 10, 31))  # 10,000 al mes
    insertar_recurrente(conn, "Sueldo", 'ingreso', 20000, 'mensual', 1, date(2026, 11, 1))
    p0 = calcular_periodos(conn, date(2026, 10, 20), 1)["periodos"][0]
    # El sueldo mensual marca el periodo (1-31 oct); el freelance del 31 oct entra como ingreso por recibir
    assert (p0["inicio"], p0["fin"], p0["fecha_cobro"]) == ("2026-10-01", "2026-10-31", "2026-11-01"), p0
    assert p0["ingresos"]["por_recibir"] == 5000.0, p0["ingresos"]


def test_rubro_sobrevive_al_borrar_el_fijo():
    from finanzas_core import calcular_periodos, materializar_recurrentes
    conn = nueva_bd()
    f = insertar_recurrente(conn, "Netflix", 'gasto', 300, 'mensual', 5, date(2026, 10, 5))
    materializar_recurrentes(conn, date(2026, 10, 20))
    conn.execute("DELETE FROM recurrentes WHERE id = ?", (f["id"],))
    conn.commit()
    g = calcular_periodos(conn, date(2026, 10, 20), 1)["periodos"][0]["gastos"]
    assert (g["fijos"], g["normales"], g["pagado"]) == (300.0, 0.0, 300.0), g  # sigue siendo un fijo, no un gasto normal


def test_periodo_mensual_dia_31_empieza_en_el_cobro_real():
    from finanzas_core import calcular_periodos
    conn = nueva_bd()
    insertar_recurrente(conn, "Principal", 'ingreso', 5000, 'mensual', 31, date(2027, 4, 30))
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2027-03-30', 1000, 'gasto')")  # antes del cobro del 31 de marzo
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES ('2027-03-31', 200, 'gasto')")
    conn.commit()
    p = calcular_periodos(conn, date(2027, 4, 10), 1)["periodos"][0]
    assert p["inicio"] == "2027-03-31", p
    assert p["gastos"]["normales"] == 200.0, p["gastos"]


def test_exportar_quincenal_cuadra_con_el_presupuesto():
    from finanzas_core import exportar_presupuesto_a_fijos_core, materializar_recurrentes
    casos = [
        ("mensual", None, None, 1000.0),
        ("mensual", 10, None, 1000.0),
        ("quincenal", None, None, 2000.0),
        ("quincenal", 10, None, 2000.0),
        ("quincenal", None, [{"nombre": "1a", "monto": 500.0, "porcentaje": 50.0, "dia": 1},
                             {"nombre": "2a", "monto": 500.0, "porcentaje": 50.0, "dia": 16}], 2000.0),
    ]
    for frecuencia, dia_pago, subs, esperado in casos:
        conn = nueva_bd()
        cur = conn.cursor()
        cur.execute("UPDATE presupuesto_config SET ingreso_base = 10000, frecuencia = ?, subdivision_tipo = 'ninguna' WHERE id = 1", (frecuencia,))
        cur.execute("""INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden, dia_pago, distribucion_subperiodos)
                       VALUES ('Renta', 'x', 'blue', 10, 1000, 'Vivienda', 1, ?, ?)""", (dia_pago, json.dumps(subs) if subs else None))
        conn.commit()
        exportar_presupuesto_a_fijos_core(cur, fecha_base=date(2026, 10, 1))
        conn.commit()
        for dia in range(1, 31):
            materializar_recurrentes(conn, date(2026, 11, dia))
        real = cur.execute("SELECT COALESCE(SUM(monto), 0) FROM transacciones WHERE descripcion LIKE 'Renta%' AND fecha BETWEEN '2026-11-01' AND '2026-11-30'").fetchone()[0]
        assert abs(real - esperado) < 0.01, (frecuencia, dia_pago, subs, real, esperado)


def test_periodos_invariantes_con_escenarios_aleatorios():
    """Sin huecos ni solapes entre periodos, cada movimiento en un único periodo y saldos encadenados."""
    import random
    from datetime import timedelta
    from finanzas_core import calcular_periodos, materializar_recurrentes
    for seed in range(60):
        rng = random.Random(seed)
        conn = nueva_bd()
        cur = conn.cursor()
        hoy = date(2026, 1, 1) + timedelta(days=rng.randint(0, 900))
        frec_p = rng.choice(["quincenal", "mensual", "semanal"])
        dia_p = rng.randint(1, 31) if frec_p == "mensual" else None
        insertar_recurrente(conn, "Principal", 'ingreso', round(rng.uniform(1000, 9000), 2), frec_p, dia_p,
                            hoy + timedelta(days=rng.randint(-40, 40)))
        for i in range(rng.randint(0, 3)):
            f = rng.choice(["mensual", "quincenal", "semanal"])
            insertar_recurrente(conn, f"Gasto{i}", 'gasto', round(rng.uniform(100, 3000), 2), f,
                                rng.randint(1, 31) if f == "mensual" else None, hoy + timedelta(days=rng.randint(-40, 40)))
        materializar_recurrentes(conn, hoy)
        for i in range(rng.randint(0, 5)):
            cur.execute("INSERT INTO transacciones (fecha, monto, tipo) VALUES (?, ?, ?)",
                        ((hoy - timedelta(days=rng.randint(0, 90))).isoformat(), round(rng.uniform(10, 4000), 2), rng.choice(["ingreso", "gasto"])))
        conn.commit()
        ps = calcular_periodos(conn, hoy, 6)["periodos"]
        for a, b in zip(ps, ps[1:]):
            assert date.fromisoformat(a["fin"]) + timedelta(days=1) == date.fromisoformat(b["inicio"]), (seed, a["fin"], b["inicio"])
            assert abs(a["saldo_final"] - b["saldo_inicial"]) < 0.02, (seed, a, b)
        assert date.fromisoformat(ps[0]["inicio"]) <= hoy <= date.fromisoformat(ps[0]["fin"]), (seed, hoy, ps[0])
        desde, hasta = ps[0]["inicio"], ps[-1]["fin"]
        ing_db = cur.execute("SELECT COALESCE(SUM(monto),0) FROM transacciones WHERE tipo='ingreso' AND substr(fecha,1,10) BETWEEN ? AND ?", (desde, hasta)).fetchone()[0]
        gas_db = cur.execute("SELECT COALESCE(SUM(monto),0) FROM transacciones WHERE tipo='gasto' AND substr(fecha,1,10) BETWEEN ? AND ?", (desde, hasta)).fetchone()[0]
        assert abs(sum(p["ingresos"]["recibido"] for p in ps) - ing_db) < 0.02, seed
        assert abs(sum(p["gastos"]["pagado"] for p in ps) - gas_db) < 0.02, seed


def test_editar_proximo_cargo_no_cambia_el_dia_ancla_si_cae_a_fin_de_mes():
    from finanzas_core import ancla_al_editar_proximo_cargo
    # Un mensual del 31 que se edita al último día de febrero conserva el 31: marzo vuelve a caer el 31
    assert ancla_al_editar_proximo_cargo(31, date(2027, 2, 28), 'mensual') == 31
    assert ancla_al_editar_proximo_cargo(31, date(2027, 3, 31), 'mensual') == 31
    assert ancla_al_editar_proximo_cargo(30, date(2027, 2, 28), 'mensual') == 30
    # Una fecha elegida a mano que no es fin de mes sí cambia el ancla
    assert ancla_al_editar_proximo_cargo(31, date(2027, 2, 20), 'mensual') == 20
    # Si el ancla ya era el día de la fecha elegida, o es fin de mes con ancla menor, se usa esa fecha
    assert ancla_al_editar_proximo_cargo(15, date(2027, 2, 28), 'mensual') == 28
    assert ancla_al_editar_proximo_cargo(None, date(2027, 2, 28), 'mensual') == 28
    # Quincenal no usa ancla: el día es el de la fecha guardada
    assert ancla_al_editar_proximo_cargo(31, date(2027, 2, 28), 'quincenal') == 28
    # Y el ciclo siguiente confirma que el cargo sigue en el ancla
    from finanzas_core import siguiente_fecha
    assert siguiente_fecha(date(2027, 2, 28), 'mensual', 31) == date(2027, 3, 31)


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests'))
    from ejecutor import ejecutar_modulo
    sys.exit(ejecutar_modulo(sys.modules[__name__], 'finanzas'))
