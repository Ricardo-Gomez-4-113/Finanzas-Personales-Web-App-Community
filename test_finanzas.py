"""
test_finanzas.py - Pruebas base de la lógica financiera (sin pytest).

Ejecutar desde la raíz del proyecto:
    python3 test_finanzas.py
"""

import json
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
    assert v == 11, v
    cols = [col[1] for col in conn.execute("PRAGMA table_info(presupuesto_config)").fetchall()]
    assert "dia_pago" in cols


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
    assert r['ingreso_fijo_mensual'] == 18751.08, r['ingreso_fijo_mensual']
    assert r['gasto_fijo_mensual'] == 5000.0, r['gasto_fijo_mensual']
    assert r['cuota_msi_mensual'] == 1074.92, r['cuota_msi_mensual']
    assert r['compromiso_mensual_total'] == 6074.92, r['compromiso_mensual_total']
    assert r['gastos_normales_mes'] == 0.0, r['gastos_normales_mes']
    assert r['libre_al_mes'] == 12676.16, r['libre_al_mes']
    assert r['deuda_msi'] == 12899.0, r['deuda_msi']


def test_resumen_gastos_normales_mes():
    conn = nueva_bd()
    insertar_recurrente(conn, "Sueldo", "ingreso", 10000.0, "mensual", 1, date(2026, 10, 1))
    insertar_recurrente(conn, "Renta", "gasto", 3000.0, "mensual", 1, date(2026, 10, 1))
    conn.execute("""
        INSERT INTO transacciones (fecha, monto, tipo, categoria_id, descripcion, recurrente_id, apartado_id)
        VALUES ('2026-10-10', 1500.0, 'gasto', 1, 'Supermercado', NULL, NULL)
    """)
    conn.commit()

    r = calcular_resumen(conn, 'MXN', 18.0, date(2026, 10, 20))
    assert r['ingreso_fijo_mensual'] == 10000.0
    assert r['gasto_fijo_mensual'] == 3000.0
    assert r['compromiso_mensual_total'] == 3000.0
    assert r['gastos_normales_mes'] == 1500.0
    assert r['libre_al_mes'] == 5500.0


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
    assert r1['gastos_normales_mes'] == 1500.0
    assert r1['mes_actual']['gastos'] == 1500.0

    cursor.execute("DELETE FROM transacciones WHERE id = ?", (tx_id,))
    conn.commit()

    r2 = calcular_resumen(conn, 'MXN', 18.0, date(2026, 11, 20))
    assert r2['gastos_normales_mes'] == 0.0
    assert r2['mes_actual']['gastos'] == 0.0

    # 2. En tarjetas KPI del presupuesto (get_presupuesto): resumen asignado y disponible excluyen tarjetas eliminadas
    cursor.execute("UPDATE presupuesto_config SET ingreso_base = 10000.0 WHERE id = 1")
    cursor.execute("""
        INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, orden)
        VALUES ('Suscripciones', 'film', 'purple', 10.0, 1000.0, 1)
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


def test_exportar_presupuesto_no_duplica_msi_y_respeta_dias():
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
    # Pixel 10 debe ser ignorado como MSI
    msi_item = next((d for d in res["detalles"] if "Pixel" in d["descripcion"]), None)
    assert msi_item is not None
    assert msi_item["accion"] == "ignorado_msi"

    # Verificar que solo hay 1 registro de Pixel 10 en recurrentes (el MSI original)
    pixel_recs = cur.execute("SELECT * FROM recurrentes WHERE descripcion LIKE '%Pixel%'").fetchall()
    assert len(pixel_recs) == 1
    assert pixel_recs[0]["total_cuotas"] == 10

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

    # 2. Movimiento ligado a meta / apartado (retiro / ingreso): si se elimina el retiro, NO vuelve a la meta
    cur.execute("INSERT INTO transacciones (fecha, monto, tipo, apartado_id) VALUES ('2026-11-02', 200.0, 'ingreso', 1)")
    id_t2 = cur.lastrowid
    conn.commit()

    eliminar_transaccion(id_t2, conn)
    # El retiro se borra, pero el dinero no regresa a la meta: se mantiene en 700.0
    assert cur.execute("SELECT monto_actual FROM apartados WHERE id = 1").fetchone()[0] == 700.0
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
    # Apartado descuenta 100: 700 - 100 = 600
    assert cur.execute("SELECT monto_actual FROM apartados WHERE id = 1").fetchone()[0] == 600.0
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


if __name__ == '__main__':
    test_convertir()
    test_calcular_resumen()
    test_siguiente_fecha_mensual_ancla_31()
    test_siguiente_fecha_quincenal()
    test_msi_completo_suma_exacta_y_termina()
    test_msi_materializa_cuotas_hasta_hoy()
    test_materializar_es_idempotente_mismo_dia()
    test_valor_rendimiento_un_anio()
    test_migracion_v4_enlaza_apartados()
    test_migracion_v5_presupuesto()
    test_presupuesto_conversiones()
    test_subperiodos_calculo()
    test_migracion_v7_apartado_recurrente()
    test_recurrente_aporta_a_meta()
    test_importar_fijos_individual_y_limpieza()
    test_migracion_v8_indice_unico()
    test_migracion_v9_distribucion_subperiodos()
    test_migracion_v10_dia_pago()
    test_migracion_v11_dia_pago_config()
    test_distribucion_tarjeta_asimetrica()
    test_calcular_distribucion_tarjeta_asimetrica_recalcula_si_monto_cambia()
    test_materializar_ingreso_no_afecta_apartado()
    test_proxima_fecha_vigente_reactivar()
    test_concurrencia_materializar_sin_duplicados()
    test_resumen_fijos_msi_compromiso_mensual()
    test_resumen_gastos_normales_mes()
    test_exportar_presupuesto_a_fijos_misma_periodicidad()
    test_editar_recurrente_frecuencia_y_campos()
    test_crear_gasto_fijo_sin_ingreso_fijo_falla()
    test_gasto_fijo_rechazado_por_depender_de_remanente()
    test_gasto_previo_a_fecha_de_cobro_sin_remanente_falla()
    test_gasto_previo_a_fecha_de_cobro_con_remanente_exitoso()
    test_cargos_quincena_no_superan_presupuesto_de_quincena()
    test_tarjetas_kpi_mes_en_curso_excluyen_eliminados()
    test_importacion_presupuesto_a_fijos_estricta()
    test_gasto_respeta_saldo_retenido_en_apartados()
    test_edicion_ingreso_retroactivo_no_genera_saldo_negativo_futuro()
    test_normalizacion_fin_de_mes_febrero()
    test_presupuesto_tarjeta_dia_pago_y_subperiodos()
    test_exportar_presupuesto_no_duplica_msi_y_respeta_dias()
    test_exportar_presupuesto_dia_segundo_subperiodo()
    test_exportar_presupuesto_con_dia_pago_configurado()
    test_eliminacion_transacciones_individual_y_lote()
    print('OK')



