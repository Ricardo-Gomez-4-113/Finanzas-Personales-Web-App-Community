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
