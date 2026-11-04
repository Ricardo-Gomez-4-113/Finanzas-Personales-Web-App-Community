"""tests/auditoria/personas.py - Personas de la auditoría, sembradas solo por la API.

Laura cobra cada quincena y tiene una meta con 50,000 de ahorro propio que nunca pasó por la cuenta. Ese es el
caso real de la base del dueño que dio el falso «Fondos insuficientes». El monto propio se pone directo en la
base porque la API no permite crear ahorro sin movimientos; así quedó la base real.
"""

import sqlite3

CLAVE = "Auditoria#2026"
AHORRO_PROPIO_CENTAVOS = 5_000_000  # 50,000 pesos, en centavos como el resto del dinero


def crear_laura(cliente, usuario, mods):
    """Crea la cuenta, siembra la historia y devuelve (cabecera de sesión, ruta de su base)."""
    entrada = mods.master_db.create_user_entry(usuario, mods.security.hash_password(CLAVE), email=f"{usuario}@test.local")
    ruta = mods.database.get_tenant_path(entrada["db_name"])

    r = cliente.post("/auth/login", json={"username": usuario, "password": CLAVE})
    assert r.status_code == 200, r.text
    cabecera = {"Authorization": f"Bearer {r.json()['access_token']}"}

    def post(ruta_api, cuerpo):
        r = cliente.post(ruta_api, json=cuerpo, headers=cabecera)
        assert r.status_code in (200, 201), f"{ruta_api} -> {r.status_code} {r.text[:200]}"
        return r.json()

    post("/api/transacciones", {"fecha": "2026-09-15", "monto": 14000, "tipo": "ingreso", "categoria_id": 12, "descripcion": "Nómina"})
    post("/api/transacciones", {"fecha": "2026-10-01", "monto": 14000, "tipo": "ingreso", "categoria_id": 12, "descripcion": "Nómina"})
    meta = post("/api/apartados", {"nombre": "Fondo propio", "tipo": "meta", "meta_monto": 80000})["id"]

    con = sqlite3.connect(ruta)
    try:
        con.execute("UPDATE apartados SET monto_actual = ? WHERE id = ?", (AHORRO_PROPIO_CENTAVOS, meta))
        con.commit()
    finally:
        con.close()
    return cabecera, ruta
