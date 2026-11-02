"""tests/test_mercado.py - Caché de tipos de cambio y codificación de tickers hacia Yahoo."""

import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mercado  # noqa: E402


def _vaciar_cache():
    mercado._CACHE_TASAS["datos"] = None
    mercado._CACHE_TASAS["expira_en"] = 0
    mercado._CACHE_TASAS["respaldo"] = ["MXN", "EUR"]


def _cotizaciones(mxn, eur):
    def _fake(ticker):
        return {"USDMXN=X": mxn, "EURUSD=X": eur}[ticker]
    return _fake


def test_tasas_sin_cotizacion_no_se_cachean_300_segundos():
    _vaciar_cache()
    with mock.patch.object(mercado, "consultar_precio_mercado", _cotizaciones(None, None)):
        mercado._refrescar_tasas_cambio()
    assert mercado._CACHE_TASAS["expira_en"] - time.time() <= 61


def test_tasas_completas_se_cachean_300_segundos():
    _vaciar_cache()
    with mock.patch.object(mercado, "consultar_precio_mercado", _cotizaciones(20.0, 1.25)):
        tasas = mercado._refrescar_tasas_cambio()
    assert tasas["MXN"] == 20.0 and tasas["EUR"] == 0.8
    assert 290 <= mercado._CACHE_TASAS["expira_en"] - time.time() <= 300


def test_tasas_sin_cotizacion_conservan_la_ultima_buena():
    _vaciar_cache()
    with mock.patch.object(mercado, "consultar_precio_mercado", _cotizaciones(20.0, 1.25)):
        mercado._refrescar_tasas_cambio()
    mercado._CACHE_TASAS["expira_en"] = 0
    with mock.patch.object(mercado, "consultar_precio_mercado", _cotizaciones(None, None)):
        tasas = mercado._refrescar_tasas_cambio()
    assert tasas["MXN"] == 20.0 and tasas["EUR"] == 0.8


def test_si_solo_llega_el_peso_el_euro_conserva_su_ultimo_valor_bueno():
    # Antes el euro volvía al valor fijo de respaldo aunque ya se hubiera cotizado bien
    _vaciar_cache()
    with mock.patch.object(mercado, "consultar_precio_mercado", _cotizaciones(20.0, 1.25)):
        mercado._refrescar_tasas_cambio()
    assert mercado.tasas_en_respaldo() == []
    mercado._CACHE_TASAS["expira_en"] = 0
    with mock.patch.object(mercado, "consultar_precio_mercado", _cotizaciones(25.0, None)):
        tasas = mercado._refrescar_tasas_cambio()
    assert tasas["MXN"] == 25.0 and tasas["EUR"] == 0.8, tasas
    assert mercado.tasas_en_respaldo() == []
    assert mercado._CACHE_TASAS["expira_en"] - time.time() <= 61  # al faltar una, se reintenta pronto


def test_tasas_en_respaldo_se_distinguen_de_las_cotizadas():
    _vaciar_cache()
    with mock.patch.object(mercado, "consultar_precio_mercado", _cotizaciones(None, None)):
        tasas = mercado._refrescar_tasas_cambio()
    assert tasas["MXN"] == mercado.TASA_MXN_RESPALDO and tasas["EUR"] == mercado.TASA_EUR_RESPALDO
    assert mercado.tasas_en_respaldo() == ["MXN", "EUR"]

    _vaciar_cache()
    with mock.patch.object(mercado, "consultar_precio_mercado", _cotizaciones(20.0, None)):
        tasas = mercado._refrescar_tasas_cambio()
    assert tasas["MXN"] == 20.0 and tasas["EUR"] == mercado.TASA_EUR_RESPALDO
    assert mercado.tasas_en_respaldo() == ["EUR"]

    mercado._CACHE_TASAS["expira_en"] = 0
    with mock.patch.object(mercado, "consultar_precio_mercado", _cotizaciones(20.0, 1.25)):
        mercado._refrescar_tasas_cambio()
    assert mercado.tasas_en_respaldo() == []


def test_ticker_se_codifica_en_la_ruta_de_yahoo():
    urls = []

    def _urlopen(req, timeout=None):
        urls.append(req.full_url)
        raise OSError("sin red en la prueba")

    with mock.patch.object(mercado.urllib.request, "urlopen", _urlopen):
        mercado.consultar_precio_y_moneda_mercado("a/b?x")
    assert urls and all("/chart/A%2FB%3FX?" in u for u in urls), urls


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import ejecutar_modulo
    sys.exit(ejecutar_modulo(sys.modules[__name__], "test_mercado"))
