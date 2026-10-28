"""scripts/migrar_todos.py - Lleva todos los tenants al esquema vigente (con respaldo previo por tenant).

Uso (dentro del contenedor):  python3 scripts/migrar_todos.py [--dry-run]
"""

import glob
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


def migrar_todos(dry_run: bool = False) -> int:
    pendientes = 0
    for ruta in sorted(glob.glob(os.path.join(database.TENANTS_DIR, "user_*.db"))):
        conn = sqlite3.connect(ruta, timeout=15.0)
        try:
            version = conn.execute("PRAGMA user_version;").fetchone()[0]
        finally:
            conn.close()
        nombre = os.path.basename(ruta)
        if version >= database.CURRENT_TENANT_SCHEMA_VERSION:
            print(f"= {nombre}: v{version} (al día)")
            continue
        pendientes += 1
        if dry_run:
            print(f"? {nombre}: v{version} -> v{database.CURRENT_TENANT_SCHEMA_VERSION} (pendiente)")
            continue
        database.olvidar_tenant(nombre)
        database.get_tenant_conn(nombre).close()
        print(f"+ {nombre}: v{version} -> v{database.CURRENT_TENANT_SCHEMA_VERSION}")
    return pendientes


if __name__ == "__main__":
    migrar_todos(dry_run="--dry-run" in sys.argv)
