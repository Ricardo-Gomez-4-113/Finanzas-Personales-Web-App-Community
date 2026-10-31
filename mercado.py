import threading
"""
mercado.py - Cotizaciones de Yahoo Finance y tipos de cambio (con caché).

Consultas en paralelo con cotizar_varios para no sumar los timeouts de cada ticker.
"""

import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple


def consultar_precio_y_moneda_mercado(ticker: str):
    ticker_clean = ticker.strip().upper()
    ticker_ruta = urllib.parse.quote(ticker_clean, safe="=^-.")
    urls = [
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_ruta}?interval=1d&range=1d",
        f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker_ruta}?interval=1d&range=1d"
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    for url in urls:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    result = data.get("chart", {}).get("result", [])
                    if result:
                        meta = result[0].get("meta", {})
                        precio = meta.get("regularMarketPrice")
                        moneda = (meta.get("currency") or ("MXN" if ticker_clean.endswith(".MX") else "USD")).upper()
                        if precio is not None:
                            return round(float(precio), 2), moneda
        except Exception:
            continue
    moneda_def = "MXN" if ticker_clean.endswith(".MX") else "USD"
    return None, moneda_def

def consultar_precio_mercado(ticker: str) -> Optional[float]:
    precio, _ = consultar_precio_y_moneda_mercado(ticker)
    return precio


def buscar_tickers_mercado(query: str):
    q = urllib.parse.quote(query.strip())
    url = f"https://query1.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=8&newsCount=0"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=4) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                quotes = data.get("quotes", [])
                resultados = []
                for item in quotes:
                    simbolo = item.get("symbol")
                    if not simbolo:
                        continue
                    nombre = item.get("shortname") or item.get("longname") or simbolo
                    tipo = item.get("quoteType") or item.get("typeDisp") or "ACTIVO"
                    bolsa = item.get("exchDisp") or item.get("exchange") or ""
                    resultados.append({
                        "symbol": simbolo,
                        "nombre": nombre,
                        "tipo": tipo,
                        "bolsa": bolsa
                    })
                return resultados
    except Exception:
        pass
    return []


_CACHE_TASAS = {
    "datos": None,
    "expira_en": 0.0
}

_LOCK_TASAS = threading.Lock()

# Tipos de cambio de respaldo cuando no hay cotización en vivo ni caché (unidades por USD)
TASA_MXN_RESPALDO = 18.40
TASA_EUR_RESPALDO = 0.89

def obtener_tasas_cambio():
    ahora = time.time()
    if _CACHE_TASAS["datos"] is not None and ahora < _CACHE_TASAS["expira_en"]:
        return _CACHE_TASAS["datos"]
    # Un solo refresco a la vez: las peticiones concurrentes esperan y reusan el resultado
    with _LOCK_TASAS:
        ahora = time.time()
        if _CACHE_TASAS["datos"] is not None and ahora < _CACHE_TASAS["expira_en"]:
            return _CACHE_TASAS["datos"]
        return _refrescar_tasas_cambio()

def _refrescar_tasas_cambio():
    ahora = time.time()

    tasas = {
        "USD": 1.0,
        "MXN": TASA_MXN_RESPALDO,
        "EUR": TASA_EUR_RESPALDO
    }
    try:
        precio_mxn = consultar_precio_mercado("USDMXN=X")
        precio_eur = consultar_precio_mercado("EURUSD=X")
    except Exception:
        precio_mxn = precio_eur = None

    obtuvo_mxn = bool(precio_mxn and precio_mxn > 0)
    obtuvo_eur = bool(precio_eur and precio_eur > 0)
    if obtuvo_mxn:
        tasas["MXN"] = round(precio_mxn, 4)
    if obtuvo_eur:
        tasas["EUR"] = round(1.0 / precio_eur, 4)

    # Sin ninguna cotización nueva, se conserva lo último bueno y se reintenta pronto.
    # Solo se cachean 300 s cuando llegaron las dos tasas; los valores por defecto caducan en 60 s.
    if not (obtuvo_mxn or obtuvo_eur) and _CACHE_TASAS["datos"] is not None:
        _CACHE_TASAS["expira_en"] = ahora + 60
        return _CACHE_TASAS["datos"]

    completas = obtuvo_mxn and obtuvo_eur
    _CACHE_TASAS["datos"] = tasas
    _CACHE_TASAS["expira_en"] = ahora + (300 if completas else 60)
    return _CACHE_TASAS["datos"]


def cotizar_varios(tickers: List[str]) -> Dict[str, Tuple[Optional[float], str]]:
    """Cotiza varios tickers en paralelo. Devuelve {ticker: (precio, moneda)}."""
    unicos = list(dict.fromkeys(tickers))
    if not unicos:
        return {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        resultados = list(pool.map(consultar_precio_y_moneda_mercado, unicos))
    return dict(zip(unicos, resultados))
