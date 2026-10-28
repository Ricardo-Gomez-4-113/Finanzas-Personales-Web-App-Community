"""scripts/respaldo_automatico.py - Copia consistente de auth_master.db y de todos los tenants.

Usa la API de backup de SQLite (segura con la app en marcha), verifica cada copia con
PRAGMA integrity_check y borra respaldos con más de N días.

Uso (dentro del contenedor):  python3 scripts/respaldo_automatico.py [--dias 14]
Salida: data/backups/AAAAmmdd_HHMMSS/{auth_master.db, tenants/user_*.db}
"""

import glob
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(RAIZ, "data")


def _copiar(origen: str, destino: str) -> None:
    src = sqlite3.connect(origen, timeout=15.0)
    dst = sqlite3.connect(destino)
    try:
        src.backup(dst)
        estado = dst.execute("PRAGMA integrity_check;").fetchone()[0]
        if estado != "ok":
            raise RuntimeError(f"integrity_check falló en la copia de {origen}: {estado}")
    finally:
        dst.close()
        src.close()


def respaldar_todo(data_dir: str = DATA_DIR, destino_raiz: str = None, retener_dias: int = 14) -> str:
    destino_raiz = destino_raiz or os.path.join(data_dir, "backups")
    carpeta = os.path.join(destino_raiz, datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.join(carpeta, "tenants"))

    bases = [(os.path.join(data_dir, "auth_master.db"), os.path.join(carpeta, "auth_master.db"))]
    for ruta in sorted(glob.glob(os.path.join(data_dir, "tenants", "user_*.db"))):
        bases.append((ruta, os.path.join(carpeta, "tenants", os.path.basename(ruta))))

    try:
        for origen, destino in bases:
            if os.path.exists(origen):
                _copiar(origen, destino)
    except Exception:
        shutil.rmtree(carpeta, ignore_errors=True)  # no dejar un respaldo a medias
        raise

    limite = time.time() - retener_dias * 86400
    for nombre in os.listdir(destino_raiz):
        ruta = os.path.join(destino_raiz, nombre)
        if os.path.isdir(ruta) and ruta != carpeta and os.path.getmtime(ruta) < limite:
            shutil.rmtree(ruta)
    return carpeta


if __name__ == "__main__":
    dias = int(sys.argv[sys.argv.index("--dias") + 1]) if "--dias" in sys.argv else 14
    print(f"Respaldo creado: {respaldar_todo(retener_dias=dias)}")
