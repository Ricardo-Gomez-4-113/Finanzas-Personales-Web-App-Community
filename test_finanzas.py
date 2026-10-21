"""
test_finanzas.py - Pruebas base de la lógica financiera (sin pytest).

Ejecutar desde la raíz del proyecto:
    python3 test_finanzas.py
"""

import sqlite3
from datetime import date, timedelta

import database
from database import configure_sqlite_pragmas, apply_lazy_migrations
from finanzas_core import (
    convertir,
    calcular_resumen,
    siguiente_fecha,
    materializar_recurrentes,
    deuda_pendiente,
    valor_rendimiento,
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
    assert v == 8, v
    idx = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='uq_transacciones_rec_fecha'").fetchone()
    assert idx is not None


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
    test_materializar_ingreso_no_afecta_apartado()
    test_proxima_fecha_vigente_reactivar()
    test_concurrencia_materializar_sin_duplicados()
    test_resumen_fijos_msi_compromiso_mensual()
    test_exportar_presupuesto_a_fijos_misma_periodicidad()
    test_editar_recurrente_frecuencia_y_campos()
    print('OK')


