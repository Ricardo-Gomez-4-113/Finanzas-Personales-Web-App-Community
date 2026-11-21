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
