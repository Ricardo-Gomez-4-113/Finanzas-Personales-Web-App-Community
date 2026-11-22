"""
tests/test_propiedades.py - Pruebas de propiedades del core financiero (Hypothesis).

En vez de ejemplos escritos a mano, Hypothesis genera miles de entradas al azar y busca una que rompa la regla.
Si encuentra una, la reduce al caso más pequeño y lo imprime. Sin red ni navegador.

Ejecutar (necesita `pip install hypothesis`): python3 tests/test_propiedades.py
"""

import calendar
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "k" * 48)

from hypothesis import HealthCheck, given, settings, strategies as st  # noqa: E402

import finanzas_core as core  # noqa: E402
from dinero import a_centavos  # noqa: E402

AJUSTES = settings(max_examples=int(os.environ.get("PROPIEDADES_EJEMPLOS", "1500")), deadline=None,
                   suppress_health_check=[HealthCheck.too_slow])

centavos = st.integers(min_value=0, max_value=10**12)          # hasta 10 mil millones de pesos
fechas = st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 12, 31))


@st.composite
def porcentajes_que_suman_100(draw):
    """De 1 a 12 porcentajes con dos decimales que suman exactamente 100."""
    n = draw(st.integers(min_value=1, max_value=12))
    cortes = sorted(draw(st.lists(st.integers(0, 10000), min_size=n - 1, max_size=n - 1)))
    puntos = [0] + cortes + [10000]
    return [(puntos[i + 1] - puntos[i]) / 100 for i in range(n)]


# ---------- dinero ----------

@AJUSTES
@given(centavos)
def propiedad_ida_y_vuelta_de_centavos(c):
    """Pesos con dos decimales -> centavos -> pesos no pierde nada."""
    assert a_centavos(c / 100) == c


@AJUSTES
@given(st.integers(min_value=1, max_value=10**11), porcentajes_que_suman_100())
def propiedad_el_reparto_suma_exactamente_el_total(c, pcts):
    partes = core.repartir_en_centavos(c / 100, pcts)
    assert len(partes) == len(pcts)
    assert sum(a_centavos(p) for p in partes) == c, (c, pcts, partes)
    assert all(a_centavos(p) >= 0 for p in partes)


@AJUSTES
@given(st.integers(min_value=1, max_value=10**11), porcentajes_que_suman_100())
def propiedad_el_reparto_no_se_desvia_mas_de_un_centavo_de_lo_exacto(c, pcts):
    partes = core.repartir_en_centavos(c / 100, pcts)
    for p, parte in zip(pcts, partes):
        assert abs(a_centavos(parte) - c * p / 100) <= 1 + 1e-6, (c, p, parte)


@AJUSTES
@given(st.integers(min_value=1, max_value=10**10), st.integers(min_value=1, max_value=60))
def propiedad_las_cuotas_a_meses_suman_el_total_o_se_rechazan(c, meses):
    total = c / 100
    try:
        core.validar_compra_msi(total, meses)
    except ValueError:
        return          # demasiado pequeña: rechazada, nunca guardada con una cuota en cero o negativa
    cuota = a_centavos(core.cuota_msi(total, meses))
    ultima = c - cuota * (meses - 1)
    assert cuota >= 1 and ultima >= 1, (c, meses, cuota, ultima)
    assert cuota * (meses - 1) + ultima == c
    assert abs(ultima - cuota) <= meses, (c, meses, cuota, ultima)   # el ajuste del redondeo es pequeño


# ---------- monedas ----------

tasas = st.floats(min_value=0.5, max_value=500, allow_nan=False, allow_infinity=False)


@AJUSTES
@given(st.integers(min_value=0, max_value=10**11), tasas, tasas,
       st.sampled_from(["MXN", "USD", "EUR"]), st.sampled_from(["MXN", "USD", "EUR"]))
def propiedad_convertir_ida_y_vuelta_conserva_el_monto(c, t_mxn, t_eur, a, b):
    monto = c / 100
    ida = core.convertir(monto, a, b, t_mxn, t_eur)
    vuelta = core.convertir(ida, b, a, t_mxn, t_eur)
    assert abs(vuelta - monto) <= max(1e-6, abs(monto) * 1e-9), (monto, a, b, ida, vuelta)


@AJUSTES
@given(st.integers(min_value=1, max_value=10**9), tasas, st.sampled_from(["MXN", "USD"]))
def propiedad_un_monto_en_euros_sin_tasa_no_se_valua_a_valor_nominal(c, t_mxn, otra):
    for tasa in (None, 0, -1, float("nan")):
        for par in ((("EUR", otra)), ((otra, "EUR"))):
            try:
                core.convertir(c / 100, par[0], par[1], t_mxn, tasa)
            except ValueError:
                continue
            raise AssertionError(f"convirtió {par} con tasa EUR {tasa!r} sin avisar")


# ---------- fechas ----------

def _es_quincena(f):
    return f.day == 15 or f.day == calendar.monthrange(f.year, f.month)[1]


@AJUSTES
@given(fechas)
def propiedad_primera_fecha_quincenal(desde):
    f = core.primera_fecha_quincenal(desde)
    assert f >= desde and _es_quincena(f)
    assert (f - desde).days <= 16
    # es la primera: ninguna quincena en [desde, f)
    d = desde
    while d < f:
        assert not _es_quincena(d), (desde, f, d)
        d += timedelta(days=1)


@AJUSTES
@given(fechas, st.sampled_from(["semanal", "quincenal", "mensual"]), st.integers(min_value=1, max_value=31))
def propiedad_siguiente_fecha_avanza_sin_saltarse_ocurrencias(f, frecuencia, dia):
    if frecuencia == "mensual":
        f = f.replace(day=min(dia, calendar.monthrange(f.year, f.month)[1]))
    sig = core.siguiente_fecha(f, frecuencia, dia)
    assert sig > f
    dias = (sig - f).days
    if frecuencia == "semanal":
        assert dias == 7
    elif frecuencia == "quincenal":
        assert 1 <= dias <= 16 and _es_quincena(sig)
        assert not any(_es_quincena(f + timedelta(days=k)) for k in range(1, dias)), (f, sig)
    else:
        assert 28 <= dias <= 62, (f, sig, dia)
        meses = (sig.year - f.year) * 12 + sig.month - f.month
        assert meses == 1, (f, sig, dia)
        assert sig.day == min(dia, calendar.monthrange(sig.year, sig.month)[1]), (f, sig, dia)


@AJUSTES
@given(fechas, st.sampled_from(["semanal", "quincenal", "mensual"]), st.integers(min_value=1, max_value=31),
       st.integers(min_value=0, max_value=800))
def propiedad_proxima_fecha_vigente_no_cobra_en_el_pasado_ni_se_salta_una_fecha(f, frecuencia, dia, atraso):
    if frecuencia == "mensual":
        f = f.replace(day=min(dia, calendar.monthrange(f.year, f.month)[1]))
    hoy = f + timedelta(days=atraso)
    prox = core.proxima_fecha_vigente(f, frecuencia, dia, hoy)
    assert prox >= hoy
    if f >= hoy:
        assert prox == f                         # ya vigente: no se mueve
        return
    previa = f                                   # la ocurrencia anterior a prox sigue la cadena y es < hoy
    while True:
        sig = core.siguiente_fecha(previa, frecuencia, dia)
        if sig >= hoy:
            assert sig == prox
            break
        previa = sig


# ---------- con base de datos (migraciones reales, en memoria) ----------

import sqlite3  # noqa: E402

from database import apply_lazy_migrations, configure_sqlite_pragmas  # noqa: E402
from dinero import fabrica_filas  # noqa: E402

FRECUENCIAS = ["semanal", "quincenal", "mensual"]


def _bd():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = fabrica_filas
    configure_sqlite_pragmas(conn)
    apply_lazy_migrations(conn)
    return conn


def _alta_fijo(conn, tipo, c, frecuencia, dia, proxima, total=None, monto_total=None):
    conn.execute(
        "INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, total_cuotas, monto_total, categoria_id, activo) "
        "VALUES ('f', ?, ?, ?, ?, ?, ?, ?, 1, 1)",
        (tipo, c, frecuencia, dia, proxima.isoformat(), total, None if monto_total is None else monto_total))


fijo = st.fixed_dictionaries({
    "tipo": st.sampled_from(["ingreso", "gasto"]),
    "c": st.integers(min_value=1, max_value=10**8),
    "frecuencia": st.sampled_from(FRECUENCIAS),
    "dia": st.integers(min_value=1, max_value=31),
    "inicio": st.dates(min_value=date(2025, 1, 1), max_value=date(2026, 12, 31)),
})


@settings(parent=AJUSTES, max_examples=300)
@given(st.lists(fijo, min_size=1, max_size=5), st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 31)))
def propiedad_materializar_es_idempotente_exacto_y_nunca_toca_el_futuro(fijos, hoy):
    conn = _bd()
    for f in fijos:
        inicio = f["inicio"]
        if f["frecuencia"] == "mensual":
            inicio = inicio.replace(day=min(f["dia"], calendar.monthrange(inicio.year, inicio.month)[1]))
        _alta_fijo(conn, f["tipo"], f["c"], f["frecuencia"], f["dia"], inicio)
    conn.commit()
    creadas = core.materializar_recurrentes(conn, hoy)
    conn.commit()
    filas = conn.execute("SELECT fecha, recurrente_id FROM transacciones").fetchall()
    assert creadas == len(filas)
    assert all(fila["fecha"] <= hoy.isoformat() for fila in filas)
    for rec in conn.execute("SELECT * FROM recurrentes").fetchall():
        assert rec["proxima_fecha"] > hoy.isoformat(), dict(rec)
    # segunda pasada: nada nuevo
    assert core.materializar_recurrentes(conn, hoy) == 0
    assert conn.execute("SELECT COUNT(*) FROM transacciones").fetchone()[0] == len(filas)
    # nunca dos cargos del mismo fijo en la misma fecha
    repetidos = conn.execute("SELECT recurrente_id, fecha, COUNT(*) n FROM transacciones GROUP BY recurrente_id, fecha HAVING n > 1").fetchall()
    assert not repetidos, [dict(r) for r in repetidos]


@settings(parent=AJUSTES, max_examples=300)
@given(st.integers(min_value=2, max_value=24), st.integers(min_value=200, max_value=10**8), st.sampled_from(FRECUENCIAS),
       st.dates(min_value=date(2025, 1, 1), max_value=date(2026, 1, 1)))
def propiedad_una_compra_a_meses_cobra_exactamente_el_total(meses, total_c, frecuencia, inicio):
    try:
        core.validar_compra_msi(total_c / 100, meses)
    except ValueError:
        return
    conn = _bd()
    cuota_c = a_centavos(core.cuota_msi(total_c / 100, meses))
    _alta_fijo(conn, "gasto", cuota_c, frecuencia, inicio.day, inicio, total=meses, monto_total=total_c)
    conn.commit()
    hoy = date(2100, 1, 1)
    core.materializar_recurrentes(conn, hoy)
    cobrado = conn.execute("SELECT COALESCE(SUM(monto), 0), COUNT(*) FROM transacciones").fetchone()
    assert cobrado[1] == meses, cobrado[1]
    assert cobrado[0] == total_c, (cobrado[0], total_c)


@st.composite
def movimientos_historia(draw):
    n = draw(st.integers(min_value=0, max_value=40))
    return [(draw(st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 31))),
             draw(st.sampled_from(["ingreso", "gasto"])), draw(st.integers(min_value=1, max_value=10**7))) for _ in range(n)]


@settings(parent=AJUSTES, max_examples=400)
@given(movimientos_historia(), st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 31)),
       st.one_of(st.none(), st.tuples(st.sampled_from(FRECUENCIAS), st.integers(min_value=1, max_value=31), st.dates(min_value=date(2026, 1, 1), max_value=date(2027, 3, 1)))))
def propiedad_el_historial_cubre_el_tiempo_sin_huecos_y_suma_lo_mismo_que_los_movimientos(movs, hoy, ingreso_fijo):
    conn = _bd()
    for f, tipo, c in movs:
        conn.execute("INSERT INTO transacciones (fecha, monto, tipo, categoria_id) VALUES (?, ?, ?, 1)", (f.isoformat(), c, tipo))
    if ingreso_fijo:
        frecuencia, dia, prox = ingreso_fijo
        if frecuencia == "mensual":
            prox = prox.replace(day=min(dia, calendar.monthrange(prox.year, prox.month)[1]))
        _alta_fijo(conn, "ingreso", 100000, frecuencia, dia, prox)
    conn.commit()
    h = core.historial_por_periodos(conn, hoy, maximo=10**6)
    periodos = list(reversed(h["periodos"]))                      # cronológico
    if not movs:
        assert periodos == []
        return
    for a, b in zip(periodos, periodos[1:]):                      # sin huecos ni traslapes
        assert date.fromisoformat(b["inicio"]) == date.fromisoformat(a["fin"]) + timedelta(days=1), (a, b)
    assert all(p["inicio"] <= p["fin"] for p in periodos)
    assert sum(1 for p in periodos if p["en_curso"]) <= 1
    for tipo, clave in (("ingreso", "ingresos"), ("gasto", "gastos")):
        esperado = sum(c for _, t, c in movs if t == tipo) / 100
        assert abs(sum(p[clave] for p in periodos) - esperado) < 0.005, (tipo, esperado, h)
    for f, tipo, c in movs:                                       # cada movimiento cae en un periodo
        assert any(p["inicio"] <= f.isoformat() <= p["fin"] for p in periodos), f


@st.composite
def operaciones(draw):
    return draw(st.lists(st.tuples(
        st.sampled_from(["ingreso", "gasto", "borrar", "editar"]),
        st.integers(min_value=0, max_value=60),                    # dia (desplazamiento desde el 1 de octubre)
        st.integers(min_value=1, max_value=500000),                 # centavos
        st.integers(min_value=0, max_value=1000),                   # elige un movimiento existente
        st.integers(min_value=0, max_value=60)), min_size=1, max_size=40))


@settings(parent=AJUSTES, max_examples=400)
@given(operaciones())
def propiedad_ninguna_operacion_validada_deja_el_saldo_negativo_en_ninguna_fecha(ops):
    conn = _bd()
    cur = conn.cursor()
    base = date(2026, 10, 1)

    def revisar():
        fechas = [r[0] for r in cur.execute("SELECT DISTINCT fecha FROM transacciones").fetchall()]
        for f in fechas:
            saldo = core.balance_disponible_a_fecha(cur, f) - core.obtener_total_apartados(cur, f)
            assert saldo >= -0.005, f"saldo negativo el {f}: {saldo}"

    for tipo, d, c, quien, d2 in ops:
        fecha = (base + timedelta(days=d)).isoformat()
        ids = [r[0] for r in cur.execute("SELECT id FROM transacciones ORDER BY id").fetchall()]
        try:
            if tipo == "ingreso":
                cur.execute("INSERT INTO transacciones (fecha, monto, tipo, categoria_id) VALUES (?, ?, 'ingreso', 1)", (fecha, c))
            elif tipo == "gasto":
                core.validar_saldo_disponible_fecha(cur, fecha, c / 100, "gasto")
                cur.execute("INSERT INTO transacciones (fecha, monto, tipo, categoria_id) VALUES (?, ?, 'gasto', 1)", (fecha, c))
            elif tipo == "borrar" and ids:
                i = ids[quien % len(ids)]
                core.validar_eliminacion_transaccion(cur, i)
                cur.execute("DELETE FROM transacciones WHERE id = ?", (i,))
            elif tipo == "editar" and ids:
                i = ids[quien % len(ids)]
                fila = cur.execute("SELECT tipo FROM transacciones WHERE id = ?", (i,)).fetchone()
                nueva = (base + timedelta(days=d2)).isoformat()
                core.validar_edicion_transaccion(cur, i, nueva, c / 100, fila["tipo"])
                cur.execute("UPDATE transacciones SET fecha = ?, monto = ? WHERE id = ?", (nueva, c, i))
        except ValueError:
            pass                                                   # rechazada: el estado no cambia
        revisar()


def _todas():
    return [(n, f) for n, f in sorted(globals().items()) if n.startswith("propiedad_") and callable(f)]


if __name__ == "__main__":
    fallos = 0
    t0 = time.time()
    for nombre, prueba in _todas():
        t = time.time()
        try:
            prueba()
            print(f"  ok     {nombre} ({time.time() - t:.2f}s)")
        except Exception as error:      # noqa: BLE001
            fallos += 1
            print(f"  FALLA  {nombre}: {type(error).__name__}: {str(error)[:600]}")
    if fallos:
        print(f"[test_propiedades] {fallos} FALLA(S)")
        sys.exit(1)
    print(f"[test_propiedades] OK: {len(_todas())} propiedades ({time.time() - t0:.1f}s)")
