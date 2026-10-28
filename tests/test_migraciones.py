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


if __name__ == "__main__":
    test_bd_vacia_llega_a_la_version_actual_e_idempotente()
    test_actualizar_desde_cada_version_conserva_datos()
    test_respaldo_previo_a_migrar_y_rotacion()
    test_bd_nueva_no_genera_respaldo()
    print("OK")
