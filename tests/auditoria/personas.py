"""tests/auditoria/personas.py - Personas de la auditoría, sembradas solo por la API.

Beto cobra cada semana y registra muchos gastos pequeños (volumen). Mara paga una renta y dos compras a meses. Iván tiene
inversiones en tres monedas y cuentas con rendimiento. Sofía es nueva y no tiene nada. Las dos del dueño son una copia de su base
real, una como administrador y otra como usuario común.

Laura cobra cada quincena y tiene una meta con 50,000 de ahorro propio que nunca pasó por la cuenta. Ese es el
caso real de la base del dueño que dio el falso «Fondos insuficientes». El monto propio se pone directo en la
base porque la API no permite crear ahorro sin movimientos; así quedó la base real.
"""

import datetime
import os
import random
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


HOY_BASE = datetime.date(2026, 10, 1)


def _abrir(cliente, usuario, mods, rol="user"):
    """Crea la cuenta y entra. Devuelve (cabecera, ruta de su base, función para crear por la API)."""
    entrada = mods.master_db.create_user_entry(usuario, mods.security.hash_password(CLAVE), role=rol, email=f"{usuario}@test.local")
    ruta = mods.database.get_tenant_path(entrada["db_name"])
    r = cliente.post("/auth/login", json={"username": usuario, "password": CLAVE})
    assert r.status_code == 200, r.text
    cabecera = {"Authorization": f"Bearer {r.json()['access_token']}"}

    def crear(ruta_api, cuerpo, codigos=(200, 201)):
        r = cliente.post(ruta_api, json=cuerpo, headers=cabecera)
        assert r.status_code in codigos, f"{ruta_api} -> {r.status_code} {r.text[:200]}"
        return r.json()
    return cabecera, ruta, crear


def crear_beto(cliente, usuario, mods, **_):
    """Cobra 3,500 cada viernes y gasta poco y a diario: más de cien movimientos."""
    cabecera, ruta, crear = _abrir(cliente, usuario, mods)
    rng = random.Random(usuario)
    saldo = 0.0
    dia = datetime.date(2026, 8, 7)  # viernes
    while dia < HOY_BASE:
        crear("/api/transacciones", {"fecha": dia.isoformat(), "monto": 3500, "tipo": "ingreso", "categoria_id": 12, "descripcion": "Pago semanal"})
        saldo += 3500
        for _i in range(rng.randint(8, 14)):
            monto = round(rng.uniform(20, 350), 2)
            fecha = dia + datetime.timedelta(days=rng.randint(0, 6))
            if fecha >= HOY_BASE or monto > saldo:
                continue
            crear("/api/transacciones", {"fecha": fecha.isoformat(), "monto": monto, "tipo": "gasto", "categoria_id": rng.choice((1, 2, 7)), "descripcion": f"Gasto {rng.randint(1, 999)}"})
            saldo -= monto
        dia += datetime.timedelta(days=7)
    return cabecera, ruta, HOY_BASE


def crear_mara(cliente, usuario, mods, **_):
    """Cobra el día 1, paga renta y tiene una laptop a 12 meses y un celular a 24."""
    cabecera, ruta, crear = _abrir(cliente, usuario, mods)
    crear("/api/recurrentes", {"descripcion": "Sueldo", "tipo": "ingreso", "frecuencia": "mensual", "monto": 18500, "fecha_inicio": "2026-08-01", "categoria_id": 12})
    crear("/api/recurrentes", {"descripcion": "Renta", "tipo": "gasto", "frecuencia": "mensual", "monto": 6500, "fecha_inicio": "2026-08-01", "categoria_id": 4})
    crear("/api/recurrentes", {"descripcion": "Laptop", "monto_total": 24000, "meses": 12, "fecha_inicio": "2026-08-10"})
    crear("/api/recurrentes", {"descripcion": "Celular", "monto_total": 15000, "meses": 24, "fecha_inicio": "2026-09-05"})
    crear("/api/apartados", {"nombre": "Vacaciones", "tipo": "meta", "meta_monto": 20000})
    return cabecera, ruta, HOY_BASE


def crear_ivan(cliente, usuario, mods, **_):
    """Tiene posiciones en USD, MXN y EUR y dos cuentas con rendimiento."""
    cabecera, ruta, crear = _abrir(cliente, usuario, mods)
    crear("/api/transacciones", {"fecha": "2026-09-01", "monto": 40000, "tipo": "ingreso", "categoria_id": 12, "descripcion": "Honorarios"})
    for ticker, cantidad, promedio, actual, moneda in (("AAPL", 10, 150, 190, "USD"), ("WALMEX.MX", 100, 60, 65, "MXN"), ("SAP.DE", 20, 120, 150, "EUR")):
        crear("/api/inversiones", {"ticker": ticker, "cantidad": cantidad, "precio_promedio": promedio, "precio_actual": actual, "moneda": moneda})
    crear("/api/rendimiento", {"nombre": "Cetes", "capital": 50000, "tasa_anual": 10, "fecha_inicio": "2026-08-01"})
    crear("/api/rendimiento", {"nombre": "Fondo sin interés", "capital": 12000, "tasa_anual": 0, "fecha_inicio": "2026-09-01"})
    return cabecera, ruta, HOY_BASE


def crear_sofia(cliente, usuario, mods, **_):
    """Usuaria nueva: no tiene ni un movimiento."""
    cabecera, ruta, _crear = _abrir(cliente, usuario, mods)
    return cabecera, ruta, HOY_BASE


def _crear_dueno(rol):
    def crear(cliente, usuario, mods, base_dueno=None, **_):
        """Copia de la base real del dueño en una cuenta nueva (nunca se abre el original con escritura)."""
        assert base_dueno and os.path.exists(base_dueno), f"no encuentro la base del dueño: {base_dueno}"
        cabecera, ruta, _crear = _abrir(cliente, usuario, mods, rol)
        origen = sqlite3.connect(f"file:{base_dueno}?mode=ro&immutable=1", uri=True)
        destino = sqlite3.connect(ruta)
        try:
            origen.backup(destino)
        finally:
            destino.close()
            origen.close()
        mods.database.olvidar_tenant(os.path.basename(ruta))
        mods.database.init_tenant_db(ruta)  # la misma migración que haría la app al primer acceso
        con = sqlite3.connect(ruta)
        try:
            ultima = con.execute("SELECT MAX(fecha) FROM transacciones").fetchone()[0]
        finally:
            con.close()
        hoy = datetime.date.fromisoformat(ultima) if ultima else HOY_BASE
        return cabecera, ruta, max(hoy, HOY_BASE)
    return crear


def crear_laura_con_dia(cliente, usuario, mods, **_):
    cabecera, ruta = crear_laura(cliente, usuario, mods)
    return cabecera, ruta, HOY_BASE


PERSONAS = {
    "laura": crear_laura_con_dia, "beto": crear_beto, "mara": crear_mara, "ivan": crear_ivan, "sofia": crear_sofia,
    "dueno_admin": _crear_dueno("admin"), "dueno_usuario": _crear_dueno("user"),
}

