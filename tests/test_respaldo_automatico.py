"""tests/test_respaldo_automatico.py - El respaldo automático se puede restaurar y rota por antigüedad."""

import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import respaldo_automatico  # noqa: E402


def _crear_bd(ruta, valor):
    conn = sqlite3.connect(ruta)
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (valor,))
    conn.commit()
    conn.close()


def test_respaldo_restaurable_y_rotacion():
    with tempfile.TemporaryDirectory() as data:
        os.makedirs(os.path.join(data, "tenants"))
        _crear_bd(os.path.join(data, "auth_master.db"), "maestra")
        _crear_bd(os.path.join(data, "tenants", "user_1.db"), "uno")
        _crear_bd(os.path.join(data, "tenants", "user_2.db"), "dos")

        viejo = os.path.join(data, "backups", "20200101_000000")
        os.makedirs(viejo)
        hace_30_dias = time.time() - 30 * 86400
        os.utime(viejo, (hace_30_dias, hace_30_dias))

        carpeta = respaldo_automatico.respaldar_todo(data_dir=data, retener_dias=14)

        esperado = {"auth_master.db": "maestra", "tenants/user_1.db": "uno", "tenants/user_2.db": "dos"}
        for rel, valor in esperado.items():
            conn = sqlite3.connect(os.path.join(carpeta, rel))
            assert conn.execute("SELECT v FROM t").fetchone()[0] == valor, rel
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            conn.close()
        assert not os.path.exists(viejo), "debe borrar respaldos con más de 14 días"
        assert os.path.isdir(carpeta)


def test_respaldo_corrupto_no_deja_carpeta_a_medias():
    with tempfile.TemporaryDirectory() as data:
        os.makedirs(os.path.join(data, "tenants"))
        _crear_bd(os.path.join(data, "auth_master.db"), "ok")
        with open(os.path.join(data, "tenants", "user_1.db"), "wb") as f:
            f.write(b"esto no es sqlite" * 100)
        try:
            respaldo_automatico.respaldar_todo(data_dir=data)
        except Exception:
            pass
        else:
            raise AssertionError("debía fallar con una base corrupta")
        raiz = os.path.join(data, "backups")
        assert not os.path.exists(raiz) or os.listdir(raiz) == []


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import ejecutar_modulo
    sys.exit(ejecutar_modulo(sys.modules[__name__], "test_respaldo_automatico"))
