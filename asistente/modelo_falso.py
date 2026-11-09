"""asistente/modelo_falso.py - Modelo guionizado para las pruebas: sin red, sin costo y determinista."""

from asistente.motor import Respuesta


class ModeloFalso:
    """`guion` es una lista de respuestas, una por llamada a `generar`: una `Respuesta` o una función
    `(instrucciones, mensajes) -> Respuesta`. Guarda cada llamada en `.llamadas` para que la prueba la revise."""

    def __init__(self, guion):
        self.guion = list(guion)
        self.llamadas = []

    def generar(self, instrucciones, mensajes, declaraciones):
        self.llamadas.append({"instrucciones": instrucciones, "mensajes": [dict(m) for m in mensajes]})
        if not self.guion:
            return Respuesta(texto="(el guion se acabó)")
        paso = self.guion.pop(0)
        return paso(instrucciones, mensajes) if callable(paso) else paso
