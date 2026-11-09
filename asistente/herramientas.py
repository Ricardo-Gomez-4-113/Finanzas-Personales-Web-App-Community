"""asistente/herramientas.py - Herramientas de lectura que el modelo puede pedir.

Cada una valida sus argumentos, llama a una ruta de la API con el token del asistente (nunca toca una base) y devuelve un
JSON reducido: lo justo para responder. Un rechazo de la API vuelve como `{"error": ..., "estado": ...}` para que el modelo
se lo explique a la persona. Las rutas que usan están en `permisos.RUTAS_PERMITIDAS`.
"""

import re
from datetime import date

from pydantic import ValidationError

from asistente.propuestas import LimiteDePropuestas

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


DECLARACIONES_ESCRITURA = [
    {"nombre": "registrar_movimiento",
     "descripcion": ("Prepara el registro de un gasto o un ingreso. NO lo registra: queda como propuesta que la persona debe confirmar. "
                     "Antes llama a `categorias` si no sabes cómo se llama la categoría."),
     "parametros": {"type": "object", "required": ["tipo", "monto", "categoria"], "properties": {
         "tipo": {"type": "string", "enum": ["gasto", "ingreso"]},
         "monto": {"type": "number", "description": "Monto en pesos mexicanos, mayor que cero."},
         "categoria": {"type": "string", "description": "Nombre exacto de una categoría que existe."},
         "descripcion": {"type": "string", "description": "Concepto breve (hasta 255 caracteres)."},
         "fecha": {"type": "string", "description": "AAAA-MM-DD. Si no se dice, hoy."}}}},
    {"nombre": "mover_a_meta",
     "descripcion": "Prepara un depósito o un retiro en una meta o apartado. NO lo hace: queda como propuesta que la persona debe confirmar.",
     "parametros": {"type": "object", "required": ["meta", "accion", "monto"], "properties": {
         "meta": {"type": "string", "description": "Nombre de la meta o apartado."},
         "accion": {"type": "string", "enum": ["depositar", "retirar"]},
         "monto": {"type": "number", "description": "Monto en pesos mexicanos, mayor que cero."}}}},
    {"nombre": "saltar_fijo",
     "descripcion": "Prepara saltar el próximo cobro de un gasto o ingreso fijo. NO lo salta: queda como propuesta que la persona debe confirmar.",
     "parametros": {"type": "object", "required": ["fijo"], "properties": {
         "fijo": {"type": "string", "description": "Descripción del fijo, como aparece en `fijos_proximos`."}}}},
]

MAX_TEXTO_RESUMEN = 60


def neutralizar(texto, maximo=MAX_TEXTO_RESUMEN):
    """Texto libre (descripciones, nombres) apto para el resumen que ve la persona: una sola línea, sin comillas angulares que
    imiten otro campo, sin caracteres de control ni formato, y corto. El cuerpo de la propuesta conserva el texto original."""
    limpio = "".join(" " if c.isspace() else c for c in str(texto) if c.isprintable() or c.isspace())
    limpio = re.sub(r"[«»<>&*_`\[\]]", "", limpio)
    limpio = re.sub(r" +", " ", limpio).strip()
    return limpio if len(limpio) <= maximo else limpio[:maximo].rstrip() + "…"


AVISO_PROPUESTA = "Quedó pendiente: la persona debe confirmarla. No digas que ya se hizo."


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

    def __init__(self, obtener, propuestas=None, user_id=None, hoy=None):
        """Con `propuestas` (un `Propuestas`) y `user_id` el modelo también puede preparar escrituras; sin ellos, solo consulta."""
        self._obtener = obtener
        self.propuestas = propuestas
        self._user_id = user_id
        self._hoy = hoy or date.today

    @property
    def escritura(self):
        return self.propuestas is not None and self._user_id is not None

    def declaraciones(self):
        return DECLARACIONES + (DECLARACIONES_ESCRITURA if self.escritura else [])

    def ejecutar(self, nombre, argumentos):
        funcion = getattr(self, f"_{nombre}", None)
        if nombre not in {d["nombre"] for d in self.declaraciones()} or funcion is None:
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

    # --- escrituras: solo crean una propuesta -----------------------------------------------------------------------
    @staticmethod
    def _monto(monto):
        if isinstance(monto, bool) or not isinstance(monto, (int, float)) or not 0 < monto <= 1e9:
            raise _ArgumentoInvalido("monto debe ser un número mayor que cero.")
        return round(float(monto), 2)

    @staticmethod
    def _por_nombre(lista, clave, nombre, que):
        if not isinstance(nombre, str) or not nombre.strip() or len(nombre) > 100:
            raise _ArgumentoInvalido(f"Indica el nombre de {que}.")
        buscado = nombre.strip().lower()
        coinciden = [x for x in lista if str(x.get(clave) or "").lower() == buscado]
        if len(coinciden) != 1:
            existentes = ", ".join(f"«{x.get(clave)}»" for x in lista[:30]) or "ninguno"
            raise _ArgumentoInvalido(f"No encuentro {que} «{nombre.strip()}» de forma única. Existen: {existentes}.")
        return coinciden[0]

    def _proponer(self, accion, metodo, ruta, cuerpo, resumen):
        try:
            p = self.propuestas.crear(self._user_id, accion, metodo, ruta, cuerpo, resumen)
        except LimiteDePropuestas as error:
            raise _ArgumentoInvalido(str(error))
        return {"propuesta_id": p.id, "resumen": p.resumen, "estado": "pendiente de confirmación", "aviso": AVISO_PROPUESTA}

    def _registrar_movimiento(self, tipo, monto, categoria, descripcion=None, fecha=None):
        from routers.movimientos import Transaccion
        if tipo not in ("gasto", "ingreso"):
            raise _ArgumentoInvalido("tipo debe ser «gasto» o «ingreso».")
        if descripcion is not None and (not isinstance(descripcion, str) or len(descripcion) > 255):
            raise _ArgumentoInvalido("descripcion debe ser una cadena de hasta 255 caracteres.")
        dia = self._fecha(fecha, "fecha") or self._hoy()
        categorias = [c for c in self._api("/api/categorias") if c["tipo"] == tipo]
        cat = self._por_nombre(categorias, "nombre", categoria, f"la categoría de {tipo}s")
        cuerpo = {"fecha": dia.isoformat(), "monto": self._monto(monto), "tipo": tipo, "categoria_id": cat["id"], "descripcion": descripcion or None}
        try:
            Transaccion(**cuerpo)
        except ValidationError as error:
            raise _ArgumentoInvalido("El movimiento no es válido: " + "; ".join(str(e["msg"]) for e in error.errors()))
        resumen = (f"{'Gasto' if tipo == 'gasto' else 'Ingreso'} de ${cuerpo['monto']:,.2f} MXN en «{neutralizar(cat['nombre'])}», "
                   f"el {dia.isoformat()}" + (f": «{neutralizar(descripcion)}»" if descripcion and neutralizar(descripcion) else ""))
        return self._proponer("registrar_movimiento", "POST", "/api/transacciones", cuerpo, resumen)

    def _mover_a_meta(self, meta, accion, monto):
        from routers.apartados import MovimientoApartado
        if accion not in ("depositar", "retirar"):
            raise _ArgumentoInvalido("accion debe ser «depositar» o «retirar».")
        apartado = self._por_nombre(self._api("/api/apartados"), "nombre", meta, "la meta")
        cuerpo = {"monto": self._monto(monto), "accion": accion}
        MovimientoApartado(**cuerpo)   # mismo modelo que valida la ruta
        resumen = f"{'Depositar' if accion == 'depositar' else 'Retirar'} ${cuerpo['monto']:,.2f} MXN {'en' if accion == 'depositar' else 'de'} la meta «{neutralizar(apartado['nombre'])}»"
        return self._proponer("mover_a_meta", "POST", f"/api/apartados/{int(apartado['id'])}/movimiento", cuerpo, resumen)

    def _saltar_fijo(self, fijo):
        activos = [r for r in self._api("/api/recurrentes") if r.get("activo")]
        rec = self._por_nombre(activos, "descripcion", fijo, "el fijo")
        resumen = f"Saltar el próximo cobro de «{neutralizar(rec['descripcion'])}»"
        return self._proponer("saltar_fijo", "POST", f"/api/recurrentes/{int(rec['id'])}/saltar", {"contabilizar_cuota": False}, resumen)


class _ArgumentoInvalido(Exception):
    pass
