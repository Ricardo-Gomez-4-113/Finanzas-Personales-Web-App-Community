"""dinero.py - El dinero se guarda como centavos enteros y se usa como pesos con decimales.

La base guarda cada monto en centavos (INTEGER): sumar centavos no acumula el error de los decimales
binarios. El resto del código (cálculos, API, pantalla) sigue en pesos. La frontera son dos puntos:

- Al escribir, `a_centavos` convierte los pesos del parámetro.
- Al leer, `fabrica_filas` devuelve en pesos las columnas de COLUMNAS_DINERO.

Los totales calculados en SQL (SUM, CASE...) no pasan por la fábrica: se dividen entre 100.0 en la consulta.
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

# Columnas que guardan dinero en centavos. Cantidades y precios de la bolsa, porcentajes y tasas no.
COLUMNAS_DINERO = frozenset({
    "monto", "monto_actual", "meta_monto", "monto_total", "saldo_inicial", "ingreso_base", "capital",
})  # `monto` también es la columna de transacciones_canceladas


def a_centavos(valor: Any) -> Optional[int]:
    """Pesos a centavos enteros, redondeando la media fracción hacia arriba. None se queda en None."""
    if valor is None:
        return None
    pesos = Decimal(str(valor))
    if not pesos.is_finite():
        raise ValueError(f"Monto no válido: {valor!r}")
    return int((pesos * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def de_centavos(centavos: Optional[int]) -> Optional[float]:
    """Centavos enteros a pesos. None se queda en None."""
    return None if centavos is None else centavos / 100


class FilaSQL:
    """Fila de resultados que se lee como sqlite3.Row (por nombre o posición) con los montos ya en pesos."""

    __slots__ = ("_claves", "_valores", "_indice")

    def __init__(self, claves, valores):
        self._claves = claves
        self._valores = valores
        self._indice = {c.lower(): i for i, c in enumerate(claves)}

    def __getitem__(self, clave):
        if isinstance(clave, str):
            try:
                return self._valores[self._indice[clave.lower()]]
            except KeyError:
                raise IndexError("No item with that key") from None
        return self._valores[clave]

    def keys(self):
        return list(self._claves)

    def __iter__(self):
        return iter(self._valores)

    def __len__(self):
        return len(self._valores)

    def __eq__(self, otra):
        return tuple(self) == tuple(otra) if isinstance(otra, (FilaSQL, tuple)) else NotImplemented

    def __hash__(self):
        return hash(self._valores)

    def __repr__(self):
        return f"FilaSQL({dict(zip(self._claves, self._valores))})"


def fabrica_filas(cursor, fila):
    """row_factory de las conexiones de un tenant: filas por nombre y dinero en pesos."""
    claves = tuple(d[0] for d in cursor.description)
    valores = list(fila)
    for i, clave in enumerate(claves):
        if clave.lower() in COLUMNAS_DINERO and valores[i] is not None:
            if not isinstance(valores[i], int):
                # Un decimal en una columna de centavos es una escritura que no pasó por a_centavos
                raise TypeError(f"La columna {clave} debe guardar centavos enteros, no {valores[i]!r}")
            valores[i] = valores[i] / 100
    return FilaSQL(claves, tuple(valores))
