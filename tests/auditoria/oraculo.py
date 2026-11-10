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

    def fijos_a_meta(self):
        """(id del fijo, id de la meta, activo) de los fijos que aportan a una meta."""
        return tuple(self._leer("SELECT id, apartado_id, activo FROM recurrentes WHERE apartado_id IS NOT NULL ORDER BY id"))

    def cuotas_de(self, recurrente_id):
        return self._leer("SELECT COUNT(*) FROM transacciones WHERE recurrente_id = ?", (recurrente_id,))[0][0]

    def config_presupuesto(self):
        """(ingreso base en centavos, frecuencia, día de pago) del presupuesto, o None si no hay fila."""
        filas = self._leer("SELECT ingreso_base, frecuencia, dia_pago FROM presupuesto_config WHERE id = 1")
        return filas[0] if filas else None

    def tarjetas_presupuesto(self):
        """(id, monto en centavos, pago al que pertenece) de cada tarjeta."""
        return tuple(self._leer("SELECT id, monto, unidad_orden FROM presupuesto_tarjetas ORDER BY id"))

    def recurrentes_contenido(self):
        """Contenido de los fijos sin ids: se comparan entre exportaciones, que pueden recrearlos."""
        return tuple(sorted(self._leer(
            "SELECT tipo, descripcion, monto, frecuencia, presupuesto_tarjeta_id, activo FROM recurrentes"
        ), key=lambda f: tuple("" if v is None else str(v) for v in f)))

    def apartados(self):
        """(id, nombre, tipo, monto_actual en centavos, meta_monto en centavos) de cada meta o sobre."""
        return tuple(self._leer("SELECT id, nombre, tipo, monto_actual, meta_monto FROM apartados ORDER BY id"))

    def neto_por_apartado(self):
        """{id de apartado: depósitos − retiros registrados como movimientos, en centavos}."""
        filas = self._leer(
            "SELECT apartado_id, SUM(CASE WHEN tipo = 'gasto' THEN monto ELSE -monto END) "
            "FROM transacciones WHERE apartado_id IS NOT NULL GROUP BY apartado_id"
        )
        return {apartado: neto for apartado, neto in filas}

    def saldo_hasta_c(self, fecha):
        """Disponible en centavos con los movimientos de esa fecha o anteriores (lo que la app llama «hoy»)."""
        return self._leer(
            "SELECT COALESCE(SUM(CASE WHEN tipo = 'ingreso' THEN monto ELSE -monto END), 0) "
            "FROM transacciones WHERE fecha <= ?", (fecha,)
        )[0][0]

    def posiciones(self):
        """(id, ticker, cantidad, precio_promedio, precio_actual, moneda) de cada posición bursátil."""
        return tuple(self._leer(
            "SELECT id, ticker, cantidad, precio_promedio, precio_actual, moneda FROM inversiones ORDER BY id"
        ))

    def cuentas_rendimiento(self):
        """(id, nombre, capital en centavos, tasa anual, fecha base) de cada cuenta con rendimiento."""
        return tuple(self._leer(
            "SELECT id, nombre, capital, tasa_anual, fecha_base FROM cuentas_rendimiento ORDER BY id"
        ))

    def volcado(self):
        """Contenido completo de cada tabla de la app, para comparar una base antes y después de restaurar un respaldo."""
        tablas = [t for (t,) in self._leer("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: tuple(self._leer(f'SELECT * FROM "{t}" ORDER BY rowid')) for t in tablas}

