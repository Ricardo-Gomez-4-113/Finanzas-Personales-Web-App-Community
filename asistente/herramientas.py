"""asistente/herramientas.py - Herramientas de lectura que el modelo puede pedir.

Cada una valida sus argumentos, llama a una ruta de la API con el token del asistente (nunca toca una base) y devuelve un
JSON reducido: lo justo para responder. Un rechazo de la API vuelve como `{"error": ..., "estado": ...}` para que el modelo
se lo explique a la persona. Las rutas que usan están en `permisos.RUTAS_PERMITIDAS`.
"""

from datetime import date

MONEDAS = ("MXN", "USD", "EUR")
MAX_ELEMENTOS = 40
MAX_TEXTO = 300
MAX_MOVIMIENTOS = 50

_MONEDA = {"type": "string", "enum": list(MONEDAS), "description": "Moneda en que se muestran las cifras. Por defecto MXN."}

DECLARACIONES = [
    {"nombre": "resumen",
     "descripcion": "Saldo, ingresos y gastos del periodo, libre hasta el cobro y patrimonio de la cuenta. Úsala para «¿cuánto me queda?».",
     "parametros": {"type": "object", "properties": {"moneda_base": _MONEDA}}},
    {"nombre": "buscar_movimientos",
     "descripcion": "Movimientos de la cuenta (gastos e ingresos), del más reciente al más antiguo. Devuelve hasta 50, el total encontrado y la suma.",
     "parametros": {"type": "object", "properties": {
         "desde": {"type": "string", "description": "Fecha inicial AAAA-MM-DD, inclusive."},
         "hasta": {"type": "string", "description": "Fecha final AAAA-MM-DD, inclusive."},
         "tipo": {"type": "string", "enum": ["gasto", "ingreso"]},
         "categoria": {"type": "string", "description": "Nombre de la categoría (sin distinguir mayúsculas)."},
         "texto": {"type": "string", "description": "Texto que debe aparecer en la descripción."},
         "limite": {"type": "integer", "description": "Cuántos devolver, de 1 a 50. Por defecto 20."}}}},
    {"nombre": "gastos_por_categoria",
     "descripcion": "Gastos del periodo de cobro actual agrupados por categoría.",
     "parametros": {"type": "object", "properties": {"moneda_base": _MONEDA}}},
    {"nombre": "metas",
     "descripcion": "Metas de ahorro y apartados: nombre, monto objetivo, monto guardado y porcentaje.",
     "parametros": {"type": "object", "properties": {}}},
    {"nombre": "fijos_proximos",
     "descripcion": "Gastos fijos, ingresos fijos y compras a meses agrupados por periodo, con sus próximas fechas.",
     "parametros": {"type": "object", "properties": {}}},
    {"nombre": "presupuesto",
     "descripcion": "Presupuesto: ingreso fijo base, frecuencia de cobro y tarjetas con su monto.",
     "parametros": {"type": "object", "properties": {}}},
    {"nombre": "inversiones",
     "descripcion": "Posiciones bursátiles y cuentas con rendimiento (solo consulta; no sincroniza cotizaciones).",
     "parametros": {"type": "object", "properties": {"moneda_base": _MONEDA}}},
    {"nombre": "categorias",
     "descripcion": "Lista de categorías de movimientos que existen en la cuenta.",
     "parametros": {"type": "object", "properties": {}}},
    {"nombre": "tipo_de_cambio",
     "descripcion": "Tipos de cambio vigentes entre MXN, USD y EUR.",
     "parametros": {"type": "object", "properties": {}}},
]


def acotar(valor, max_elementos=MAX_ELEMENTOS, max_texto=MAX_TEXTO):
    """Recorta listas y textos largos para no mandar la base entera al modelo; avisa cuánto omitió."""
    if isinstance(valor, list):
        recorte = [acotar(v, max_elementos, max_texto) for v in valor[:max_elementos]]
        if len(valor) > max_elementos:
            recorte.append({"_omitidos": len(valor) - max_elementos})
        return recorte
    if isinstance(valor, dict):
        return {k: acotar(v, max_elementos, max_texto) for k, v in valor.items()}
    if isinstance(valor, str) and len(valor) > max_texto:
        return valor[:max_texto] + "…"
    return valor


class Herramientas:
    """`obtener(ruta, params)` devuelve `(estado_http, json)`: en producción es la API real y en las pruebas un TestClient."""

    def __init__(self, obtener):
        self._obtener = obtener

    def ejecutar(self, nombre, argumentos):
        funcion = getattr(self, f"_{nombre}", None)
        if nombre not in {d["nombre"] for d in DECLARACIONES} or funcion is None:
            return {"error": f"No existe la herramienta «{nombre}»."}
        try:
            return acotar(funcion(**(argumentos or {})))
        except _ArgumentoInvalido as error:
            return {"error": str(error)}
        except TypeError:
            return {"error": "Argumentos no reconocidos para esa herramienta."}
        except Exception as error:   # un dato raro o la API caída no tumba el turno; solo se devuelve el tipo, nunca el texto
            return {"error": f"No se pudo completar la consulta ({type(error).__name__})."}

    # --- ayudas ---------------------------------------------------------------------------------------------------
    def _api(self, ruta, params=None):
        estado, cuerpo = self._obtener(ruta, params or {})
        if estado >= 400:
            detalle = cuerpo.get("detail") if isinstance(cuerpo, dict) else None
            raise _ArgumentoInvalido(f"La plataforma respondió {estado}: {detalle if isinstance(detalle, str) else 'no se pudo completar'}")
        return cuerpo

    @staticmethod
    def _moneda(moneda_base):
        moneda = moneda_base if moneda_base is not None else "MXN"
        moneda = moneda.upper() if isinstance(moneda, str) else None
        if moneda not in MONEDAS:
            raise _ArgumentoInvalido("moneda_base debe ser MXN, USD o EUR.")
        return moneda

    @staticmethod
    def _fecha(texto, nombre):
        if texto is None:
            return None
        if not isinstance(texto, str):
            raise _ArgumentoInvalido(f"{nombre} debe ser una fecha AAAA-MM-DD.")
        try:
            return date.fromisoformat(texto)
        except ValueError:
            raise _ArgumentoInvalido(f"{nombre} debe ser una fecha AAAA-MM-DD.")

    # --- herramientas ---------------------------------------------------------------------------------------------
    def _resumen(self, moneda_base=None):
        return self._api("/api/resumen", {"moneda_base": self._moneda(moneda_base)})

    def _gastos_por_categoria(self, moneda_base=None):
        return self._api("/api/gastos-por-categoria", {"moneda_base": self._moneda(moneda_base)})

    def _metas(self):
        return self._api("/api/apartados")

    def _fijos_proximos(self):
        return self._api("/api/recurrentes/periodos")

    def _presupuesto(self):
        return self._api("/api/presupuesto")

    def _categorias(self):
        return self._api("/api/categorias")

    def _tipo_de_cambio(self):
        return self._api("/api/tipo-cambio")

    def _inversiones(self, moneda_base=None):
        moneda = self._moneda(moneda_base)
        return {"posiciones": self._api("/api/inversiones", {"moneda_base": moneda}),
                "cuentas_con_rendimiento": self._api("/api/rendimiento", {"moneda_base": moneda})}

    def _buscar_movimientos(self, desde=None, hasta=None, tipo=None, categoria=None, texto=None, limite=20):
        d, h = self._fecha(desde, "desde"), self._fecha(hasta, "hasta")
        if tipo is not None and tipo not in ("gasto", "ingreso"):
            raise _ArgumentoInvalido("tipo debe ser «gasto» o «ingreso».")
        if not isinstance(limite, int) or isinstance(limite, bool) or not 1 <= limite <= MAX_MOVIMIENTOS:
            raise _ArgumentoInvalido(f"limite debe ser un entero de 1 a {MAX_MOVIMIENTOS}.")
        for nombre, valor in (("texto", texto), ("categoria", categoria)):
            if valor is not None and (not isinstance(valor, str) or len(valor) > 100):
                raise _ArgumentoInvalido(f"{nombre} debe ser una cadena de hasta 100 caracteres.")
        encontrados = []
        for m in self._api("/api/transacciones"):
            fecha = date.fromisoformat(m["fecha"])
            if d and fecha < d or h and fecha > h:
                continue
            if tipo and m["tipo"] != tipo:
                continue
            if categoria and (m.get("categoria") or "").lower() != categoria.lower():
                continue
            if texto and texto.lower() not in (m.get("descripcion") or "").lower():
                continue
            encontrados.append(m)
        suma_c = sum(round(m["monto"] * 100) for m in encontrados)
        return {"total_encontrados": len(encontrados), "suma": suma_c / 100,
                "movimientos": [{"id": m["id"], "fecha": m["fecha"], "tipo": m["tipo"], "monto": m["monto"],
                                 "descripcion": m.get("descripcion"), "categoria": m.get("categoria")}
                                for m in encontrados[:limite]]}


class _ArgumentoInvalido(Exception):
    pass
