"""tests/test_migraciones.py - Cadena de migraciones, conservación de datos y respaldo previo.

No requiere fastapi. Usa directorios temporales: nunca toca 'data/' del proyecto.
"""

import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402

ACTUAL = database.CURRENT_TENANT_SCHEMA_VERSION


def _migrar_hasta(conn, version):
    anterior = database.CURRENT_TENANT_SCHEMA_VERSION
    database.CURRENT_TENANT_SCHEMA_VERSION = version
    try:
        database.apply_lazy_migrations(conn)
    finally:
        database.CURRENT_TENANT_SCHEMA_VERSION = anterior


def _conteos(conn):
    tablas = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tablas}


def _version(conn):
    return conn.execute("PRAGMA user_version;").fetchone()[0]


def _db_en_version(ruta, version):
    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    database.configure_sqlite_pragmas(conn)
    _migrar_hasta(conn, version)
    assert _version(conn) == version
    return conn


def test_bd_vacia_llega_a_la_version_actual_e_idempotente():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    database.configure_sqlite_pragmas(conn)
    database.apply_lazy_migrations(conn)
    assert _version(conn) == ACTUAL
    antes = _conteos(conn)
    database.apply_lazy_migrations(conn)
    assert _version(conn) == ACTUAL and _conteos(conn) == antes


def test_actualizar_desde_cada_version_conserva_datos():
    with tempfile.TemporaryDirectory() as tmp:
        for inicio in range(3, ACTUAL):  # v1-v3 se aplican siempre juntas (sin guarda CURRENT >= N)
            ruta = os.path.join(tmp, f"v{inicio}.db")
            conn = _db_en_version(ruta, inicio)
            try:
                conn.execute(
                    "INSERT INTO transacciones (fecha, monto, tipo, descripcion) VALUES ('2026-01-01', 10, 'ingreso', 'dato-previo')"
                )
                conn.commit()
            except sqlite3.Error:
                conn.rollback()  # el esquema de esa versión no admite esta forma; igual se verifican los conteos
            antes = _conteos(conn)
            database.apply_lazy_migrations(conn)
            despues = _conteos(conn)
            assert _version(conn) == ACTUAL, f"desde v{inicio}"
            for tabla, n in antes.items():
                assert despues.get(tabla, 0) >= n, f"v{inicio}->v{ACTUAL}: la tabla {tabla} perdió filas ({n} -> {despues.get(tabla)})"
            assert conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
            assert conn.execute("PRAGMA foreign_key_check;").fetchall() == [], f"desde v{inicio}: FK rotas"
            conn.close()


def test_respaldo_previo_a_migrar_y_rotacion():
    with tempfile.TemporaryDirectory() as tmp:
        tenants = os.path.join(tmp, "tenants")
        backups = os.path.join(tmp, "backups_premigracion")
        os.makedirs(tenants)
        orig = (database.DATA_DIR, database.TENANTS_DIR, database.PREMIGRACION_DIR, set(database._tenants_migrados))
        database.DATA_DIR, database.TENANTS_DIR, database.PREMIGRACION_DIR = tmp, tenants, backups
        try:
            ruta = os.path.join(tenants, "user_9.db")
            conn = _db_en_version(ruta, 8)
            conn.execute("INSERT INTO categorias (nombre, tipo) VALUES ('marca-v8', 'gasto')")
            conn.commit()
            conn.close()

            database.olvidar_tenant("user_9.db")
            database.get_tenant_conn("user_9.db").close()
            copias = os.listdir(backups)
            assert len(copias) == 1 and ".v8." in copias[0], copias
            bak = sqlite3.connect(os.path.join(backups, copias[0]))
            assert bak.execute("PRAGMA user_version;").fetchone()[0] == 8
            assert bak.execute("SELECT COUNT(*) FROM categorias WHERE nombre='marca-v8'").fetchone()[0] == 1
            bak.close()
            actual = sqlite3.connect(ruta)
            assert actual.execute("PRAGMA user_version;").fetchone()[0] == ACTUAL
            actual.close()

            # Ya al día: otra conexión no genera respaldos nuevos
            database.olvidar_tenant("user_9.db")
            database.get_tenant_conn("user_9.db").close()
            assert len(os.listdir(backups)) == 1

            # Rotación: solo se conservan los últimos PREMIGRACION_CONSERVAR
            for i in range(database.PREMIGRACION_CONSERVAR + 2):
                conn = sqlite3.connect(ruta)
                conn.execute("PRAGMA user_version = 11;")
                conn.commit()
                conn.close()
                time.sleep(0.01)
                database.olvidar_tenant("user_9.db")
                database.get_tenant_conn("user_9.db").close()
                time.sleep(1.1)  # el sello del nombre tiene resolución de segundos
            assert len(os.listdir(backups)) == database.PREMIGRACION_CONSERVAR
        finally:
            database.DATA_DIR, database.TENANTS_DIR, database.PREMIGRACION_DIR = orig[:3]
            database._tenants_migrados.clear()
            database._tenants_migrados.update(orig[3])


def test_bd_nueva_no_genera_respaldo():
    with tempfile.TemporaryDirectory() as tmp:
        tenants, backups = os.path.join(tmp, "tenants"), os.path.join(tmp, "bk")
        os.makedirs(tenants)
        orig = (database.DATA_DIR, database.TENANTS_DIR, database.PREMIGRACION_DIR)
        database.DATA_DIR, database.TENANTS_DIR, database.PREMIGRACION_DIR = tmp, tenants, backups
        try:
            database.get_tenant_conn("user_5.db").close()
            assert not os.path.exists(backups) or os.listdir(backups) == []
        finally:
            database.DATA_DIR, database.TENANTS_DIR, database.PREMIGRACION_DIR = orig
            database.olvidar_tenant("user_5.db")


def test_v13_rellena_el_rubro_de_los_movimientos_existentes():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db_en_version(os.path.join(tmp, "t.db"), 12)
        conn.execute("INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, total_cuotas, cuotas_generadas, activo) VALUES ('Netflix', 'gasto', 300, 'mensual', 5, '2026-11-05', NULL, 0, 1)")
        conn.execute("INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, total_cuotas, cuotas_generadas, monto_total, activo) VALUES ('Celular', 'gasto', 500, 'mensual', 6, '2026-11-06', 6, 1, 3000, 1)")
        conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual) VALUES ('Viaje', 'meta', 0)")
        conn.executemany("INSERT INTO transacciones (fecha, monto, tipo, descripcion, recurrente_id, apartado_id) VALUES (?, ?, ?, ?, ?, ?)", [
            ("2026-10-05", 300, "gasto", "Netflix", 1, None),
            ("2026-10-06", 500, "gasto", "Celular (2/6)", 2, None),
            ("2026-10-07", 1000, "gasto", "Súper", None, None),
            ("2026-10-08", 14000, "ingreso", "Nómina", None, None),
            ("2026-10-09", 800, "gasto", "Ahorro Viaje", None, 1),
        ])
        conn.commit()
        _migrar_hasta(conn, 13)
        assert _version(conn) == 13
        rubros = dict(conn.execute("SELECT descripcion, rubro FROM transacciones").fetchall())
        assert rubros == {"Netflix": "fijos", "Celular (2/6)": "msi", "Súper": "normales",
                          "Nómina": "ingreso", "Ahorro Viaje": "metas"}, rubros
        _migrar_hasta(conn, 13)  # idempotente
        assert _version(conn) == 13


def test_v15_pasa_las_tarjetas_del_tablero_general_al_pago_1():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db_en_version(os.path.join(tmp, "t.db"), 14)
        conn.execute("UPDATE presupuesto_config SET ingreso_base = 9000, frecuencia = 'quincenal', dia_pago = 15, subdivision_tipo = 'quincenas', subdivision_config = '[{\"id\": \"q1\"}]' WHERE id = 1")
        conn.execute("INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden, dia_pago, distribucion_subperiodos) VALUES ('Renta', 'home', 'blue', 50, 4500, '', 1, 10, '[{\"id\": \"q1\"}]')")
        conn.execute("INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden, unidad_orden) VALUES ('Luz', 'x', 'amber', 10, 900, '', 1, 2)")
        conn.commit()
        _migrar_hasta(conn, 15)
        assert _version(conn) == 15
        tarjetas = dict(conn.execute("SELECT nombre, unidad_orden FROM presupuesto_tarjetas").fetchall())
        assert tarjetas == {"Renta": 1, "Luz": 2}, tarjetas
        # El pago 1 toma el día del ingreso fijo; la subdivisión deja de existir
        assert conn.execute("SELECT dia_pago FROM presupuesto_unidades WHERE orden = 1").fetchone()[0] == 15
        assert conn.execute("SELECT subdivision_tipo FROM presupuesto_config WHERE id = 1").fetchone()[0] == "ninguna"
        assert conn.execute("SELECT distribucion_subperiodos FROM presupuesto_tarjetas WHERE nombre = 'Renta'").fetchone()[0] is None
        _migrar_hasta(conn, 15)  # idempotente
        assert _version(conn) == 15


def test_v16_tarjetas_msi_y_tarjetas_normales_quedan_intactas():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db_en_version(os.path.join(tmp, "t.db"), 15)
        conn.execute("INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, notas, orden, unidad_orden) VALUES ('Luz', 'x', 'amber', 10, 900, '', 1, 1)")
        conn.commit()
        _migrar_hasta(conn, 16)
        assert _version(conn) == 16
        fila = conn.execute("SELECT tipo, msi_cuotas, monto FROM presupuesto_tarjetas WHERE nombre = 'Luz'").fetchone()
        assert tuple(fila) == ("normal", None, 900.0), tuple(fila)
        _migrar_hasta(conn, 16)  # idempotente
        assert _version(conn) == 16

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import ejecutar_modulo
    sys.exit(ejecutar_modulo(sys.modules[__name__], "test_migraciones"))
