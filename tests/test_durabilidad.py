"""tests/test_durabilidad.py - Un corte brusco (SIGKILL) en mitad de una escritura no corrompe la base del usuario.

La base usa WAL y synchronous=NORMAL (ver database.py). El proceso hijo escribe datos confirmados, abre una
transacción con cientos de filas sin confirmar y se mata a la fuerza. Al reabrir, los datos confirmados
deben estar, las filas a medias no, y la integridad debe ser correcta.
Trabaja sobre una copia temporal: nunca toca 'data/' del proyecto.
"""

import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HIJO = r'''
import sqlite3, sys, time
sys.path.insert(0, sys.argv[1])
from database import init_tenant_db
ruta = sys.argv[2]
init_tenant_db(ruta)
con = sqlite3.connect(ruta, timeout=5.0)
con.execute("PRAGMA journal_mode = WAL;")
con.execute("PRAGMA synchronous = NORMAL;")
con.execute("INSERT INTO categorias (nombre, tipo) VALUES ('confirmada', 'gasto')")
con.commit()
con.execute("BEGIN IMMEDIATE")
for i in range(500):
    con.execute("INSERT INTO categorias (nombre, tipo) VALUES (?, 'gasto')", (f"sin-confirmar-{i}",))
print("LISTO", flush=True)
time.sleep(60)
'''


def test_corte_en_mitad_de_escritura_no_corrompe_ni_guarda_lo_a_medias():
    carpeta = tempfile.mkdtemp(prefix="finanzas_durabilidad_")
    try:
        ruta = os.path.join(carpeta, "user.db")
        script = os.path.join(carpeta, "hijo.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(HIJO)
        proc = subprocess.Popen([sys.executable, script, RAIZ, ruta], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            linea = proc.stdout.readline().strip()
            assert linea == "LISTO", f"el hijo no llegó a escribir: {linea!r} {proc.stderr.read()[:300]}"
            proc.send_signal(signal.SIGKILL)
        finally:
            proc.wait(timeout=10)

        con = sqlite3.connect(ruta)
        try:
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "la base quedó corrupta"
            confirmadas = con.execute("SELECT COUNT(*) FROM categorias WHERE nombre = 'confirmada'").fetchone()[0]
            a_medias = con.execute("SELECT COUNT(*) FROM categorias WHERE nombre LIKE 'sin-confirmar-%'").fetchone()[0]
        finally:
            con.close()
        assert confirmadas == 1, "lo confirmado antes del corte debe seguir ahí"
        assert a_medias == 0, f"{a_medias} filas sin confirmar se guardaron"
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import ejecutar_modulo
    sys.exit(ejecutar_modulo(sys.modules[__name__], "test_durabilidad"))
