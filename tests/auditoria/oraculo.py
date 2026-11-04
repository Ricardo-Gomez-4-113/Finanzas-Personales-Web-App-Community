"""tests/auditoria/oraculo.py - Recalcula el saldo y los movimientos con SQL propio, sobre la base de la persona.

No llama a funciones de la app (finanzas_core ni routers): si las usara, un error en ellas pasaría como correcto.
Trabaja en centavos enteros para no mezclar redondeos. El dinero disponible es lo que la pantalla muestra:
ingresos menos gastos de todos los movimientos, los que van a metas incluidos (un depósito resta, un retiro suma).
"""

import sqlite3


class Oraculo:
    def __init__(self, ruta_db):
        self.ruta_db = ruta_db

    def _leer(self, consulta, parametros=()):
        con = sqlite3.connect(f"file:{self.ruta_db}?mode=ro", uri=True)
        try:
            return con.execute(consulta, parametros).fetchall()
        finally:
            con.close()

    def movimientos(self):
        return tuple(self._leer("SELECT id, fecha, monto, tipo, apartado_id FROM transacciones ORDER BY id"))

    def cancelados(self):
        return tuple(self._leer("SELECT id, id_original, fecha, monto, tipo FROM transacciones_canceladas ORDER BY id"))

    def saldo_c(self):
        """Disponible total en centavos."""
        return self._leer("SELECT COALESCE(SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END), 0) FROM transacciones")[0][0]

    def minimo_desde(self, fecha, delta=0):
        """Saldo más bajo en centavos que queda desde `fecha` en adelante, si se suma `delta` a todo ese tramo.

        Mira el saldo al cierre de `fecha` y de cada día posterior con movimientos. Con delta negativo sirve para
        preguntar: «si gasto esto hoy, ¿alguna fecha futura queda debajo de cero?»."""
        filas = self._leer(
            "SELECT fecha, SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END) "
            "FROM transacciones GROUP BY fecha ORDER BY fecha"
        )
        hasta_fecha = sum(delta_dia for dia, delta_dia in filas if dia <= fecha)
        saldos = [hasta_fecha]
        acumulado = hasta_fecha
        for dia, delta_dia in filas:
            if dia > fecha:
                acumulado += delta_dia
                saldos.append(acumulado)
        return min(saldos) + delta

    def recurrentes(self):
        """Fijos y compras a meses: (id, tipo, monto, frecuencia, total_cuotas, cuotas_generadas, monto_total, activo, proxima_fecha)."""
        return tuple(self._leer(
            "SELECT id, tipo, monto, frecuencia, total_cuotas, cuotas_generadas, monto_total, activo, proxima_fecha "
            "FROM recurrentes ORDER BY id"
        ))

    def filas_ligadas(self):
        """(recurrente, fecha, cuántas filas) de los movimientos que vienen de un fijo."""
        return tuple(self._leer(
            "SELECT recurrente_id, fecha, COUNT(*) FROM transacciones WHERE recurrente_id IS NOT NULL "
            "GROUP BY recurrente_id, fecha"
        ))

    def filas_ligadas_despues(self, fecha):
        """Movimientos de fijos con fecha posterior a `fecha`: no deberían existir todavía."""
        return tuple(self._leer(
            "SELECT recurrente_id, fecha FROM transacciones WHERE recurrente_id IS NOT NULL AND fecha > ?", (fecha,)
        ))

    def cuotas_de(self, recurrente_id):
        return self._leer("SELECT COUNT(*) FROM transacciones WHERE recurrente_id = ?", (recurrente_id,))[0][0]
