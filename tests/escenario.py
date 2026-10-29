"""tests/escenario.py - Usuario de referencia con historia de varios meses, creado solo por la API.

Todas las cifras de ESPERADO se calcularon a mano con HOY = 20 oct 2026 (ver el desglose al final).
Si una prueba falla, la pregunta es si cambió la lógica o si cambió el escenario, no adivinar el número.

Requiere FINANZAS_MODO_PRUEBA=1 y FINANZAS_HOY=HOY_ISO en el proceso que corre la app.
"""

import os

HOY_ISO = "2026-10-20T10:00:00"
HOY = "2026-10-20"
USUARIO = "escenario"
CLAVE = "Escenario#2026"

# Categorías por id (ver tabla categorias en database.py)
CAT_GENERAL_GASTO = 1
CAT_COMIDA = 2
CAT_VIVIENDA = 4
CAT_NOMINA = 12


def activar_modo_prueba():
    os.environ["FINANZAS_MODO_PRUEBA"] = "1"
    os.environ["FINANZAS_HOY"] = HOY_ISO


def sembrar(client, headers):
    """Crea el escenario por la API. Devuelve los ids útiles para las pruebas."""
    def post(path, body, codigo=(200, 201)):
        r = client.post(path, json=body, headers=headers)
        assert r.status_code in codigo, f"{path} -> {r.status_code} {r.text[:200]}"
        return r.json()

    # Ingresos fijos primero: los gastos fijos exigen un ingreso fijo activo
    post("/api/recurrentes", {"descripcion": "Nómina quincenal", "tipo": "ingreso", "monto": 14000,
                              "frecuencia": "quincenal", "fecha_inicio": "2026-09-15", "categoria_id": CAT_NOMINA})
    post("/api/recurrentes", {"descripcion": "Renta", "tipo": "gasto", "monto": 9500,
                              "frecuencia": "mensual", "fecha_inicio": "2026-09-01", "categoria_id": CAT_VIVIENDA})
    post("/api/recurrentes", {"descripcion": "Netflix", "tipo": "gasto", "monto": 299,
                              "frecuencia": "mensual", "fecha_inicio": "2026-08-15", "categoria_id": 7})
    post("/api/recurrentes", {"descripcion": "Laptop a 12 MSI", "tipo": "gasto", "monto_total": 24000, "meses": 12,
                              "frecuencia": "mensual", "fecha_inicio": "2026-09-10", "categoria_id": 8})

    post("/api/transacciones", {"fecha": "2026-10-05", "monto": 3000, "tipo": "gasto",
                                "categoria_id": CAT_COMIDA, "descripcion": "Supermercado"})

    viaje = post("/api/apartados", {"nombre": "Viaje", "tipo": "meta", "meta_monto": 18000})["id"]
    fondo = post("/api/apartados", {"nombre": "Fondo de emergencia", "tipo": "meta", "meta_monto": 60000})["id"]
    post(f"/api/apartados/{viaje}/movimiento", {"monto": 6000, "accion": "depositar"})
    post(f"/api/apartados/{viaje}/movimiento", {"monto": 1000, "accion": "retirar"})
    post(f"/api/apartados/{fondo}/movimiento", {"monto": 2000, "accion": "depositar"})
    return {"viaje": viaje, "fondo": fondo}


# Cifras esperadas, calculadas a mano:
#   Nómina quincenal 14,000 desde 15 sep: 15 sep, 30 sep, 15 oct  -> ingresos 42,000
#   Renta 9,500 desde 1 sep: 1 sep, 1 oct                          -> 19,000
#   Netflix 299 desde 15 ago: 15 ago, 15 sep, 15 oct               -> 897
#   Laptop MSI 24,000 en 12 cuotas de 2,000 desde 10 sep: 10 sep, 10 oct -> 4,000 pagado, deuda 20,000
#   Supermercado 3,000 el 5 oct
#   Meta Viaje: deposita 6,000 y retira 1,000 hoy -> 5,000 guardados
#   Meta Fondo: deposita 2,000 hoy                -> 2,000 guardados
#   Disponible = 42,000 - 19,000 - 897 - 4,000 - 3,000 - 6,000 + 1,000 - 2,000 = 8,103
#   Octubre: ingresos 14,000 (15 oct); gastos 9,500 + 299 + 2,000 + 3,000 = 14,799 (sin metas)
#   Septiembre: ingresos 28,000; gastos 9,500 + 299 + 2,000 = 11,799
#   Agosto: gastos 299
ESPERADO = {
    "disponible": 8103.0,
    "apartados": 7000.0,
    "patrimonio_neto": 7000.0,
    "deuda_msi": 20000.0,
    "mes_actual": {"ingresos": 14000.0, "gastos": 14799.0},
    "historial": [
        {"mes": "2026-10", "ingresos": 14000.0, "gastos": 14799.0},
        {"mes": "2026-09", "ingresos": 28000.0, "gastos": 11799.0},
        {"mes": "2026-08", "ingresos": 0.0, "gastos": 299.0},
    ],
    "meta_viaje": 5000.0,
    "meta_fondo": 2000.0,
    "libre_hasta_cobro": 8103.0,
    "fecha_fin_periodo": "2026-10-31",
    # Periodos de la nómina quincenal (del cobro al día anterior al siguiente cobro):
    #   Saldo antes del 15 oct: 28,000 - 19,000 - 598 - 4,000 - 3,000 = 1,402
    #   15-30 oct: nómina 15 oct 14,000; Netflix 15 oct 299; metas 6,000 - 1,000 + 2,000 = 7,000
    #              remanente 14,000 - 299 - 7,000 = 6,701; saldo al cierre 1,402 + 6,701 = 8,103
    #   31 oct-14 nov: nómina 31 oct 14,000; renta 1 nov 9,500; cuota MSI 10 nov 2,000 -> 2,500; saldo 10,603
    #   15-29 nov: nómina 15 nov 14,000; Netflix 15 nov 299 -> 13,701; saldo 24,304
    "periodos": [
        {"inicio": "2026-10-15", "fin": "2026-10-30", "ingreso": 14000.0, "fijos": 299.0, "msi": 0.0, "normales": 0.0,
         "metas": 7000.0, "remanente": 6701.0, "saldo_inicial": 1402.0, "saldo_final": 8103.0},
        {"inicio": "2026-10-31", "fin": "2026-11-14", "ingreso": 14000.0, "fijos": 9500.0, "msi": 2000.0, "normales": 0.0,
         "metas": 0.0, "remanente": 2500.0, "saldo_inicial": 8103.0, "saldo_final": 10603.0},
        {"inicio": "2026-11-15", "fin": "2026-11-29", "ingreso": 14000.0, "fijos": 299.0, "msi": 0.0, "normales": 0.0,
         "metas": 0.0, "remanente": 13701.0, "saldo_inicial": 10603.0, "saldo_final": 24304.0},
    ],
}
