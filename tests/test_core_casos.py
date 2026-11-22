"""tests/test_core_casos.py - Casos directos de las funciones públicas del core que no tenían prueba propia.

Sin red ni navegador: bases SQLite en memoria con las migraciones reales.
Ejecutar: python3 tests/test_core_casos.py
"""

import os
import sqlite3
import sys
from datetime import date

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("JWT_SECRET_KEY", "k" * 48)

import finanzas_core as core  # noqa: E402
from database import apply_lazy_migrations, configure_sqlite_pragmas  # noqa: E402
from dinero import a_centavos, fabrica_filas  # noqa: E402


def _bd():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = fabrica_filas
    configure_sqlite_pragmas(conn)
    apply_lazy_migrations(conn)
    return conn


def _falla(funcion, *args, texto=None):
    try:
        funcion(*args)
    except ValueError as error:
        assert texto is None or texto in str(error), str(error)
        return
    raise AssertionError(f"{funcion.__name__}{args} debía lanzar ValueError")


def test_validar_fecha_operativa_acota_y_exige_el_formato_canonico():
    for ok in ("2000-01-01", "2026-02-28", "2028-02-29", "2100-12-31"):
        assert core.validar_fecha_operativa(ok) == ok
    for mal in ("1999-12-31", "2101-01-01", "2026-02-29", "2026-13-01"):
        _falla(core.validar_fecha_operativa, mal)
    for rara in ("20261003", "2026-W40-1", "2026-10-3", "", "hoy"):   # 3.11 acepta las dos primeras en fromisoformat
        _falla(core.validar_fecha_operativa, rara)


def test_primera_fecha_quincenal():
    assert core.primera_fecha_quincenal(date(2026, 10, 1)) == date(2026, 10, 15)
    assert core.primera_fecha_quincenal(date(2026, 10, 15)) == date(2026, 10, 15)
    assert core.primera_fecha_quincenal(date(2026, 10, 16)) == date(2026, 10, 31)
    assert core.primera_fecha_quincenal(date(2028, 2, 16)) == date(2028, 2, 29)   # bisiesto
    assert core.primera_fecha_quincenal(date(2027, 2, 16)) == date(2027, 2, 28)
    assert core.primera_fecha_quincenal(date(2026, 12, 31)) == date(2026, 12, 31)


def test_nombre_en_aviso_limpia_y_acota():
    assert core.nombre_en_aviso("Ahorro\n\tviaje\x00") == "Ahorro viaje"
    assert core.nombre_en_aviso("  a   b  ") == "a b"
    assert core.nombre_en_aviso(None) == ""
    assert len(core.nombre_en_aviso("x" * 200)) == 60
    assert core.nombre_en_aviso(1234) == "1234"


def test_fecha_corta():
    assert core.fecha_corta("2026-11-03") == "3 nov 2026"
    assert core.fecha_corta("2026-01-31") == "31 ene 2026"
    for invalida in ("2026-13-01", "hola", "", "2026-11"):
        assert core.fecha_corta(invalida) == invalida


def test_pesos_en_mensaje():
    assert core.pesos_en_mensaje(49) == "$49.00 MXN"
    assert core.pesos_en_mensaje(-49) == "−$49.00 MXN"
    assert core.pesos_en_mensaje(1234567.5) == "$1,234,567.50 MXN"
    assert core.pesos_en_mensaje(-0.001) == "$0.00 MXN"        # un residuo de redondeo no se rotula como negativo
    assert core.pesos_en_mensaje(-0.005) == "−$0.01 MXN" or core.pesos_en_mensaje(-0.005) == "−$0.00 MXN"


def test_clase_movimiento_cubre_todas_las_ramas():
    assert core.clase_movimiento("gasto", 3, None, False) == "metas"
    assert core.clase_movimiento("ingreso", 3, None, False) == "metas"       # un retiro de meta sigue siendo de metas
    assert core.clase_movimiento("ingreso", None, 7, False) == "ingreso"
    assert core.clase_movimiento("gasto", None, None, False) == "normales"
    assert core.clase_movimiento("gasto", None, 7, False) == "fijos"
    assert core.clase_movimiento("gasto", None, 7, True) == "msi"


def test_unidades_y_dias_de_pago():
    assert (core.unidades_activas("semanal"), core.unidades_activas("quincenal"), core.unidades_activas("mensual")) == (4, 2, 1)
    assert core.unidades_activas("desconocida") == 1
    core.validar_dias_unidades({})
    core.validar_dias_unidades({1: 5})
    core.validar_dias_unidades({2: 20, 1: 5})                    # el orden de las llaves se ordena solo
    _falla(core.validar_dias_unidades, {1: 5, 2: None}, texto="fecha de cada pago")
    _falla(core.validar_dias_unidades, {1: 15, 2: 15}, texto="orden creciente")
    _falla(core.validar_dias_unidades, {1: 20, 2: 5}, texto="orden creciente")
    _falla(core.validar_dias_unidades, {1: 1, 2: 10, 3: 10}, texto="orden creciente")


def test_ordenar_para_revertir_pone_los_retiros_de_meta_primero_y_es_estable():
    t = [{"id": 1, "apartado_id": 1, "tipo": "gasto"}, {"id": 2, "apartado_id": None, "tipo": "ingreso"},
         {"id": 3, "apartado_id": 1, "tipo": "ingreso"}, {"id": 4, "apartado_id": 2, "tipo": "gasto"},
         {"id": 5, "apartado_id": 2, "tipo": "ingreso"}]
    assert [x["id"] for x in core.ordenar_para_revertir(t)] == [3, 5, 1, 2, 4]
    assert core.ordenar_para_revertir([]) == []


def _fijo(conn, tipo, monto, frecuencia="mensual", activo=1, apartado=None, total=None, generadas=0):
    conn.execute("INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, total_cuotas, cuotas_generadas, categoria_id, apartado_id, activo) "
                 "VALUES (?, ?, ?, ?, 1, '2026-12-01', ?, ?, 1, ?, ?)", (f"{tipo} {frecuencia}", tipo, a_centavos(monto), frecuencia, total, generadas, apartado, activo))
    conn.commit()
    return conn.execute("SELECT MAX(id) FROM recurrentes").fetchone()[0]


def test_validar_fijos_con_ingreso_tras_exportar():
    conn = _bd()
    cur = conn.cursor()
    core.validar_fijos_con_ingreso_tras_exportar(cur)                          # nada: pasa
    _fijo(conn, "gasto", 500)
    _falla(core.validar_fijos_con_ingreso_tras_exportar, cur, texto="sin un ingreso fijo")
    _fijo(conn, "ingreso", 1000, activo=0)
    _falla(core.validar_fijos_con_ingreso_tras_exportar, cur, texto="sin un ingreso fijo")   # un ingreso inactivo no sostiene
    _fijo(conn, "ingreso", 1000, total=3)
    _falla(core.validar_fijos_con_ingreso_tras_exportar, cur, texto="sin un ingreso fijo")   # ni uno en cuotas
    _fijo(conn, "ingreso", 1000)
    core.validar_fijos_con_ingreso_tras_exportar(cur)


def test_validar_capacidad_al_cambiar_ingreso():
    conn = _bd()
    cur = conn.cursor()
    ing = _fijo(conn, "ingreso", 10000)
    core.validar_capacidad_al_cambiar_ingreso(cur, ing, 1, "mensual")           # sin gastos todo se permite
    _fijo(conn, "gasto", 4000)
    core.validar_capacidad_al_cambiar_ingreso(cur, ing, 4000, "mensual")        # justo igual al compromiso
    core.validar_capacidad_al_cambiar_ingreso(cur, ing, 2000, "quincenal")      # 4000 al mes
    core.validar_capacidad_al_cambiar_ingreso(cur, ing, 3999.99, "mensual")     # un centavo de faltante se tolera (redondeo)
    _falla(core.validar_capacidad_al_cambiar_ingreso, cur, ing, 3999.98, "mensual", texto="capacidad")
    _falla(core.validar_capacidad_al_cambiar_ingreso, cur, ing, 3999, "mensual", texto="capacidad")
    _falla(core.validar_capacidad_al_cambiar_ingreso, cur, ing, 900, "semanal", texto="capacidad")   # 3900 al mes
    otro = _fijo(conn, "ingreso", 2000)                                         # otro ingreso suma a la capacidad
    core.validar_capacidad_al_cambiar_ingreso(cur, ing, 2000, "mensual")
    core.validar_capacidad_al_cambiar_ingreso(cur, otro, 4000, "mensual")
    # una compra a meses ya pagada completa no compromete nada
    _fijo(conn, "gasto", 9000, total=3, generadas=3)
    core.validar_capacidad_al_cambiar_ingreso(cur, ing, 2000, "mensual")
    _fijo(conn, "gasto", 9000, total=3, generadas=2)                            # a esta le falta una cuota: sí compromete
    _falla(core.validar_capacidad_al_cambiar_ingreso, cur, ing, 2000, "mensual", texto="capacidad")


def test_detectar_fijos_recurrentes_core_suma_por_tipo_y_separa_las_compras_a_meses():
    conn = _bd()
    cur = conn.cursor()
    _fijo(conn, "ingreso", 10000)
    _fijo(conn, "ingreso", 500, "semanal")                                      # 500 * 52 / 12 = 2166.67
    _fijo(conn, "gasto", 1000, "quincenal")                                     # 2000 al mes
    _fijo(conn, "gasto", 300, activo=0)                                         # inactivo: no cuenta
    _fijo(conn, "gasto", 700, total=6, generadas=2)                             # cuota que aún falta
    _fijo(conn, "gasto", 800, total=6, generadas=6)                             # ya terminada: fuera
    r = core.detectar_fijos_recurrentes_core(cur, "mensual")
    assert r["ingreso_fijo_mensual"] == 12166.67
    assert r["gasto_fijo_mensual"] == 2000.0
    assert r["cuota_msi_mensual"] == 700.0
    assert r["conteo_gastos"] == 2
    assert [i["cuotas_info"] for i in r["items_gasto"] if i["es_msi"]] == ["Mes 3/6"]
    q = core.detectar_fijos_recurrentes_core(cur, "quincenal")
    assert q["ingreso_fijo_periodo"] == 6083.34 and q["gasto_fijo_periodo"] == 1000.0 and q["cuota_msi_periodo"] == 350.0


def _meta(conn, objetivo, actual, nombre="Viaje"):
    conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual, meta_monto) VALUES (?, 'meta', ?, ?)",
                 (nombre, a_centavos(actual), None if objetivo is None else a_centavos(objetivo)))
    conn.commit()
    return conn.execute("SELECT MAX(id) FROM apartados").fetchone()[0]


def test_cerrar_meta_si_cumplida():
    conn = _bd()
    cur = conn.cursor()
    assert core.cerrar_meta_si_cumplida(cur, 999, "2026-11-01") is False        # no existe
    libre = _meta(conn, None, 5000, "Libre")
    assert core.cerrar_meta_si_cumplida(cur, libre, "2026-11-01") is False      # sin objetivo nunca se cumple
    corta = _meta(conn, 1000, 999.99, "Casi")
    fijo_corta = _fijo(conn, "gasto", 10, apartado=corta)
    assert core.cerrar_meta_si_cumplida(cur, corta, "2026-11-01") is False      # un centavo antes no cuenta
    assert conn.execute("SELECT COUNT(*) FROM recurrentes WHERE id = ?", (fijo_corta,)).fetchone()[0] == 1
    llena = _meta(conn, 1000, 1000, "Llena")
    f1 = _fijo(conn, "gasto", 10, apartado=llena)
    f2 = _fijo(conn, "gasto", 20, apartado=llena)
    otra = _fijo(conn, "gasto", 30)                                             # sin meta: no se toca
    assert core.cerrar_meta_si_cumplida(cur, llena, "2026-11-01") is True
    assert [r[0] for r in conn.execute("SELECT id FROM recurrentes ORDER BY id").fetchall()] == [fijo_corta, otra]
    avisos = conn.execute("SELECT * FROM notificaciones WHERE tipo = 'meta_cumplida'").fetchall()
    assert len(avisos) == 1 and "Llena" in avisos[0]["texto"] and "los gastos fijos" in avisos[0]["texto"], [dict(a) for a in avisos]
    assert core.cerrar_meta_si_cumplida(cur, llena, "2026-11-01") is True      # repetirlo no duplica el aviso (una vez por hecho)
    assert conn.execute("SELECT COUNT(*) FROM notificaciones WHERE tipo = 'meta_cumplida'").fetchone()[0] == 1
    assert f1 != f2


def test_el_patron_de_correo_acepta_correos_reales_y_rechaza_lo_que_no_sirve_de_destinatario():
    import re
    from routers.auth import PATRON_CORREO
    ok = ["", "ana@example.com", "ana.perez+finanzas@mail.example.com.mx", "a_b-c%d@x-y.org", "A@B.io"]
    mal = ["ana", "ana@", "@example.com", "ana@example", "ana@@example.com", "ana perez@example.com", "ana@example.c", 'ana"@example.com',
           "<ana@example.com>", "ana@example.com,otra@example.com", "ana@example.com;x", "ana@exa(mple.com", "ana@-.com.", "ana@example.com\n", "ñandú@example.com"]
    for correo in ok:
        assert re.fullmatch(PATRON_CORREO, correo), correo
    for correo in mal:
        assert not re.fullmatch(PATRON_CORREO, correo), correo


# ---------------------------------------------------------------- ventana de pago (Regla 3.1)

def _config(conn, ingreso, frecuencia, dia):
    conn.execute("UPDATE presupuesto_config SET ingreso_base = ?, frecuencia = ?, dia_pago = ? WHERE id = 1", (a_centavos(ingreso), frecuencia, dia))
    conn.commit()


def _mov(conn, fecha, tipo, monto):
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo, categoria_id) VALUES (?, ?, ?, 1)", (fecha, a_centavos(monto), tipo))
    conn.commit()
    return conn.execute("SELECT MAX(id) FROM transacciones").fetchone()[0]


def test_la_ventana_de_pago_sin_ingreso_fijo_sigue_la_configuracion_del_presupuesto():
    """Presupuesto configurado pero ningún ingreso fijo: los cortes salen del día de pago configurado. Cada fila se calculó a mano."""
    casos = [
        # frecuencia, día de pago, fecha consultada -> (inicio, fin, ingreso de la ventana)
        ("mensual", None, "2026-11-15", ("2026-11-01", "2026-11-30", 10000)),
        ("mensual", None, "2026-11-01", ("2026-11-01", "2026-11-30", 10000)),
        ("mensual", None, "2026-12-15", ("2026-12-01", "2026-12-31", 10000)),
        ("mensual", 10, "2026-11-15", ("2026-11-10", "2026-12-09", 10000)),
        ("mensual", 10, "2026-11-10", ("2026-11-10", "2026-12-09", 10000)),
        ("mensual", 10, "2026-11-09", ("2026-10-10", "2026-11-09", 10000)),
        ("mensual", 10, "2026-01-05", ("2025-12-10", "2026-01-09", 10000)),
        ("mensual", 10, "2026-12-20", ("2026-12-10", "2027-01-09", 10000)),
        ("mensual", 31, "2026-02-15", ("2026-01-31", "2026-02-27", 10000)),      # febrero no tiene 31: el corte cae el 28
        ("mensual", 31, "2026-02-28", ("2026-02-28", "2026-03-30", 10000)),
        ("quincenal", None, "2026-11-20", ("2026-11-15", "2026-11-29", 10000)),   # sin día: el 15 y fin de mes
        ("quincenal", None, "2026-11-30", ("2026-11-30", "2026-12-14", 10000)),
        ("quincenal", None, "2026-11-05", ("2026-10-30", "2026-11-14", 10000)),
        ("quincenal", None, "2026-02-20", ("2026-02-15", "2026-02-27", 10000)),
        ("quincenal", None, "2026-02-28", ("2026-02-28", "2026-03-14", 10000)),
        ("quincenal", 15, "2026-11-20", ("2026-11-15", "2026-11-29", 10000)),
        ("quincenal", 28, "2026-11-20", ("2026-11-15", "2026-11-29", 10000)),     # 28 en adelante cuenta como fin de mes
        ("quincenal", 1, "2026-11-10", ("2026-11-01", "2026-11-15", 10000)),
        ("quincenal", 1, "2026-11-16", ("2026-11-16", "2026-11-30", 10000)),
        ("quincenal", 16, "2026-11-10", ("2026-11-01", "2026-11-15", 10000)),
        ("quincenal", 5, "2026-11-07", ("2026-11-05", "2026-11-19", 10000)),
        ("quincenal", 5, "2026-11-25", ("2026-11-20", "2026-12-04", 10000)),
        ("quincenal", 20, "2026-11-07", ("2026-11-05", "2026-11-19", 10000)),     # 20 y 5 son la misma pareja de cortes
    ]
    for frecuencia, dia, fecha, (inicio, fin, ingreso) in casos:
        conn = _bd()
        _config(conn, 10000, frecuencia, dia)
        v = core.obtener_ventana_corte_fecha(conn.cursor(), fecha)
        assert (v["inicio"], v["fin"], v["ingreso_ventana"]) == (inicio, fin, ingreso), (frecuencia, dia, fecha, v)


def test_sin_presupuesto_ni_ingresos_la_ventana_es_el_mes_y_no_inventa_capacidad():
    conn = _bd()
    v = core.obtener_ventana_corte_fecha(conn.cursor(), "2026-02-10")
    assert (v["inicio"], v["fin"], v["ingreso_ventana"], v["capacidad_total"], v["cargos_actuales"]) == ("2026-02-01", "2026-02-28", 0.0, 0.0, 0.0)
    core.validar_limite_cargos_ventana(conn.cursor(), "2026-02-10", 999999.0)      # capacidad 0 = sin presupuesto: no limita


def test_la_capacidad_de_la_ventana_suma_ingreso_o_cobros_reales_y_el_remanente_y_descuenta_los_cargos():
    conn = _bd()
    cur = conn.cursor()
    _config(conn, 10000, "mensual", 10)                                          # ventana de la consulta: 10 nov - 9 dic
    _mov(conn, "2026-10-20", "ingreso", 3000)
    _mov(conn, "2026-10-25", "gasto", 500)                                       # remanente que llega a la ventana: 2500
    _mov(conn, "2026-11-12", "ingreso", 12000)                                   # el cobro real supera lo configurado
    gasto = _mov(conn, "2026-11-20", "gasto", 4000)
    _mov(conn, "2026-12-09", "gasto", 100)                                       # último día de la ventana: cuenta
    _mov(conn, "2026-12-10", "gasto", 777)                                       # ya es de la siguiente: no cuenta
    _mov(conn, "2026-11-09", "gasto", 888)                                       # la víspera es de la anterior: no cuenta como cargo...
    v = core.obtener_ventana_corte_fecha(cur, "2026-11-15")
    assert v["ingreso_ventana"] == 12000.0                                       # max(configurado, real)
    assert v["remanente_disponible"] == 2500.0 - 888.0                           # ...pero sí baja el remanente (todo lo anterior al inicio)
    assert v["cargos_actuales"] == 4100.0
    assert v["capacidad_total"] == 12000.0 + 2500.0 - 888.0
    # Editar un movimiento lo excluye de la cuenta
    sin = core.obtener_ventana_corte_fecha(cur, "2026-11-15", transaccion_id=gasto)
    assert sin["cargos_actuales"] == 100.0
    # El ingreso configurado manda si lo real es menor
    conn2 = _bd()
    _config(conn2, 10000, "mensual", 10)
    _mov(conn2, "2026-11-12", "ingreso", 4000)
    assert core.obtener_ventana_corte_fecha(conn2.cursor(), "2026-11-15")["ingreso_ventana"] == 10000.0


def test_un_remanente_negativo_no_resta_capacidad():
    conn = _bd()
    _config(conn, 10000, "mensual", 1)
    _mov(conn, "2026-10-20", "gasto", 500)                                       # se gastó sin ingresos antes de la ventana
    v = core.obtener_ventana_corte_fecha(conn.cursor(), "2026-11-15")
    assert v["remanente_disponible"] == 0.0 and v["capacidad_total"] == 10000.0


def test_el_limite_de_cargos_de_la_ventana_se_cumple_al_centavo_con_su_tolerancia():
    conn = _bd()
    cur = conn.cursor()
    _config(conn, 10000, "mensual", None)
    _mov(conn, "2026-11-02", "gasto", 4000)                                      # capacidad 10000, cargos 4000: caben 6000 más
    core.validar_limite_cargos_ventana(cur, "2026-11-15", 6000.0)
    core.validar_limite_cargos_ventana(cur, "2026-11-15", 6000.01)               # 1 centavo de más se tolera (redondeo)
    _falla(core.validar_limite_cargos_ventana, cur, "2026-11-15", 6000.02, texto="ha sido superado")
    _falla(core.validar_limite_cargos_ventana, cur, "2026-11-30", 7000.0, texto="2026-11-01 al 2026-11-30")
    core.validar_limite_cargos_ventana(cur, "2026-12-01", 9999.0)                # otra ventana: sin cargos
    core.validar_limite_cargos_ventana(cur, "2026-11-15", 999999.0, transaccion_id=1)   # una edición no se revalida aquí (lo hace validar_edicion)


def test_con_ingreso_fijo_la_ventana_es_el_periodo_de_cobro_que_contiene_la_fecha():
    conn = _bd()
    cur = conn.cursor()
    conn.execute("INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, categoria_id, activo) "
                 "VALUES ('Nomina', 'ingreso', ?, 'mensual', 5, '2026-12-05', 1, 1)", (a_centavos(10000),))
    conn.commit()
    for fecha, inicio, fin in (("2026-11-20", "2026-11-05", "2026-12-04"), ("2026-11-05", "2026-11-05", "2026-12-04"), ("2026-11-04", "2026-10-05", "2026-11-04")):
        v = core.obtener_ventana_corte_fecha(cur, fecha)
        assert (v["inicio"], v["fin"], v["ingreso_ventana"]) == (inicio, fin, 10000.0), (fecha, v)


def test_un_gasto_fijo_nuevo_cabe_en_la_capacidad_al_centavo_con_su_tolerancia():
    conn = _bd()
    cur = conn.cursor()
    _fijo(conn, "ingreso", 10000)
    _fijo(conn, "gasto", 4000)                                                   # capacidad que queda: 6000 al mes
    core.validar_sustentabilidad_gasto_fijo(cur, "gasto", 6000.0, "mensual")
    core.validar_sustentabilidad_gasto_fijo(cur, "gasto", 6000.01, "mensual")    # un centavo de más se tolera
    _falla(core.validar_sustentabilidad_gasto_fijo, cur, "gasto", 6000.02, "mensual", texto="Gasto Fijo")
    core.validar_sustentabilidad_gasto_fijo(cur, "gasto", 3000.0, "quincenal")     # 6000 al mes
    _falla(core.validar_sustentabilidad_gasto_fijo, cur, "gasto", 3001.0, "quincenal", texto="Gasto Fijo")
    core.validar_sustentabilidad_gasto_fijo(cur, "ingreso", 999999.0, "mensual")   # un ingreso nunca se rechaza por capacidad


# ---------------------------------------------------------------- inversiones y monedas

_POSICIONES = [
    {"ticker": "AAA", "cantidad": 2, "precio_promedio": 100.0, "precio_actual": 150.0, "moneda": "USD"},
    {"ticker": "BBB", "cantidad": 10, "precio_promedio": 50.0, "precio_actual": 40.0, "moneda": "MXN"},
    {"ticker": "CCC", "cantidad": 1, "precio_promedio": 90.0, "precio_actual": 99.0, "moneda": "EUR"},
]


def test_valorar_posiciones_convierte_cada_moneda_y_suma_el_portafolio():
    """Tasas: 20 MXN y 0.9 EUR por USD. Valores calculados a mano."""
    r = core.valorar_posiciones(_POSICIONES, "MXN", 20.0, 0.9)
    a, b, c = r["posiciones"]
    assert (a["capital_invertido"], a["valor_actual"], a["pnl"], a["pnl_porcentaje"]) == (4000.0, 6000.0, 2000.0, 50.0)
    assert (b["capital_invertido"], b["valor_actual"], b["pnl"], b["pnl_porcentaje"]) == (500.0, 400.0, -100.0, -20.0)
    assert (c["capital_invertido"], c["valor_actual"], c["pnl"], c["pnl_porcentaje"]) == (2000.0, 2200.0, 200.0, 10.0)
    assert (a["capital_invertido_orig"], a["valor_actual_orig"], c["capital_invertido_orig"], c["valor_actual_orig"]) == (200.0, 300.0, 90.0, 99.0)
    assert (a["factor_conversion"], b["factor_conversion"]) == (20.0, 1.0) and round(c["factor_conversion"], 6) == round(20 / 0.9, 6)
    assert r["resumen"] == {"total_invertido": 6500.0, "total_valor_actual": 8600.0, "total_pnl": 2100.0, "total_pnl_porcentaje": 32.31}
    assert [p["peso_porcentaje"] for p in (a, b, c)] == [69.77, 4.65, 25.58] and r["moneda_base"] == "MXN"
    u = core.valorar_posiciones(_POSICIONES, "usd", 20.0, 0.9)
    assert (u["moneda_base"], u["resumen"]) == ("USD", {"total_invertido": 325.0, "total_valor_actual": 430.0, "total_pnl": 105.0, "total_pnl_porcentaje": 32.31})
    assert [p["pnl"] for p in u["posiciones"]] == [100.0, -5.0, 10.0]


def test_valorar_posiciones_casos_borde():
    vacio = core.valorar_posiciones([], "MXN", 20.0)
    assert vacio["resumen"] == {"total_invertido": 0.0, "total_valor_actual": 0.0, "total_pnl": 0.0, "total_pnl_porcentaje": 0.0} and vacio["posiciones"] == []
    sin_costo = core.valorar_posiciones([{"ticker": "X", "cantidad": 3, "precio_promedio": 0.0, "precio_actual": 10.0, "moneda": "USD"}], "USD", 20.0)
    assert sin_costo["posiciones"][0]["pnl_porcentaje"] == 0.0 and sin_costo["resumen"]["total_pnl_porcentaje"] == 0.0   # sin costo no hay porcentaje: nunca divide entre cero
    sin_valor = core.valorar_posiciones([{"ticker": "X", "cantidad": 1, "precio_promedio": 5.0, "precio_actual": 0.0, "moneda": "USD"}], "USD", 20.0)
    assert sin_valor["posiciones"][0]["peso_porcentaje"] == 0.0 and sin_valor["posiciones"][0]["pnl_porcentaje"] == -100.0
    por_defecto = core.valorar_posiciones([{"ticker": "X", "cantidad": 1, "precio_promedio": 10.0, "precio_actual": 11.0, "moneda": None}], "MXN", 20.0)
    assert por_defecto["posiciones"][0]["moneda"] == "USD" and por_defecto["posiciones"][0]["valor_actual"] == 220.0   # sin moneda se asume USD
    _falla(core.valorar_posiciones, [{"ticker": "JPY1", "cantidad": 1, "precio_promedio": 1.0, "precio_actual": 1.0, "moneda": "JPY"}], "MXN", 20.0, texto="JPY1")
    _falla(core.valorar_posiciones, [_POSICIONES[2]], "MXN", 20.0)                  # EUR sin tasa: no se valúa a valor nominal
    # Redondeo por posición y no solo al final
    r = core.valorar_posiciones([{"ticker": "R", "cantidad": 3, "precio_promedio": 0.333, "precio_actual": 0.337, "moneda": "USD"}], "USD", 20.0)
    assert r["posiciones"][0]["capital_invertido"] == 1.0 and r["posiciones"][0]["valor_actual"] == 1.01


def test_precio_en_la_moneda_de_la_posicion():
    f = core.precio_en_moneda_de_la_posicion
    assert f(100.0, "USD", "USD", 20.0) == 100.0
    assert f(100.0, "usd", "mxn", 20.0) == 2000.0
    assert f(100.0, "MXN", "USD", 20.0) == 5.0
    assert f(90.0, "EUR", "USD", 20.0, 0.9) == 100.0
    assert f(1.0, "USD", "MXN", 18.123456) == 18.1235                              # a 4 decimales
    assert f(None, "USD", "MXN", 20.0) is None and f(0, "USD", "MXN", 20.0) is None and f(-5.0, "USD", "MXN", 20.0) is None
    assert f(10.0, "JPY", "MXN", 20.0) is None and f(10.0, "USD", "JPY", 20.0) is None and f(10.0, None, "USD", 20.0) is None and f(10.0, "USD", None, 20.0) is None
    assert f(10.0, "EUR", "MXN", 20.0) is None                                      # sin tasa EUR no se usa el precio
    assert f(0.00000001, "MXN", "USD", 20.0) is None                               # se redondea a cero: no es un precio


def test_convertir_frecuencias_y_de_mensual():
    assert core.convertir_frecuencias(1000.0, "mensual") == {"mensual": 1000.0, "quincenal": 500.0, "semanal": 230.77}
    assert core.convertir_frecuencias(500.0, "quincenal") == {"mensual": 1000.0, "quincenal": 500.0, "semanal": 230.77}
    assert core.convertir_frecuencias(120.0, "semanal") == {"mensual": 520.0, "quincenal": 260.0, "semanal": 120.0}
    assert (core.convertir_de_mensual(1000.0, "mensual"), core.convertir_de_mensual(1000.0, "quincenal"), core.convertir_de_mensual(1000.0, "semanal")) == (1000.0, 500.0, 230.77)
    _falla(core.convertir_de_mensual, 1000.0, "anual", texto="anual")
    _falla(core.convertir_frecuencias, 1000.0, "anual", texto="anual")
    assert core.mensualizar(100.0, "semanal") == 433.33 and core.mensualizar(100.0, "quincenal") == 200.0 and core.mensualizar(100.0, "mensual") == 100.0


def _todas():
    return [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]


if __name__ == "__main__":
    fallos = 0
    for nombre, prueba in _todas():
        try:
            prueba()
            print(f"  ok     {nombre}")
        except Exception as error:      # noqa: BLE001
            fallos += 1
            print(f"  FALLA  {nombre}: {type(error).__name__}: {str(error)[:500]}")
    if fallos:
        print(f"[test_core_casos] {fallos} FALLA(S)")
        sys.exit(1)
    print(f"[test_core_casos] OK: {len(_todas())} pruebas")
