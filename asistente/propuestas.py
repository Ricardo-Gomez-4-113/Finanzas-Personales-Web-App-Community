"""asistente/propuestas.py - Escrituras que el asistente prepara y la persona confirma.

El modelo nunca ejecuta una escritura: sus herramientas solo crean una propuesta (la ruta y el cuerpo exactos, ya validados) y el
canal (CLI, Telegram) se la muestra a la persona con un resumen escrito por el código. Solo `confirmar` la ejecuta, una vez,
si no venció y es de la misma cuenta. Viven en memoria: un reinicio las pierde, y es lo correcto.
"""

import secrets
import threading
import time
from dataclasses import dataclass, replace

VIDA_SEGUNDOS = 600
MAX_PENDIENTES = 5   # por cuenta: un modelo desbocado no llena el chat de propuestas

PENDIENTE, EJECUTADA, FALLIDA, CANCELADA, VENCIDA = "pendiente", "ejecutada", "fallida", "cancelada", "vencida"


@dataclass
class Propuesta:
    id: str
    user_id: int
    accion: str      # registrar_movimiento | mover_a_meta | saltar_fijo
    metodo: str
    ruta: str        # con el id ya puesto: /api/apartados/3/movimiento
    cuerpo: dict
    resumen: str     # lo escribe el código, no el modelo
    vence: float
    estado: str = PENDIENTE


class LimiteDePropuestas(Exception):
    pass


MENSAJE_INCIERTO = "No pude confirmar si se aplicó; revisa tus movimientos antes de repetirlo."


class Propuestas:
    def __init__(self, ahora=time.monotonic, vida=VIDA_SEGUNDOS):
        self._ahora = ahora
        self._vida = vida
        self._por_id = {}
        self._candado = threading.Lock()

    def _vencer(self):
        ahora = self._ahora()
        for p in self._por_id.values():
            if p.estado == PENDIENTE and ahora > p.vence:
                p.estado = VENCIDA
        for id_ in [i for i, p in self._por_id.items() if p.estado != PENDIENTE and ahora > p.vence + self._vida]:
            del self._por_id[id_]   # se olvidan las ya resueltas para no acumular memoria

    def crear(self, user_id, accion, metodo, ruta, cuerpo, resumen):
        with self._candado:
            self._vencer()
            pendientes = [p for p in self._por_id.values() if p.user_id == int(user_id) and p.estado == PENDIENTE]
            if len(pendientes) >= MAX_PENDIENTES:
                raise LimiteDePropuestas(f"Ya hay {MAX_PENDIENTES} propuestas esperando confirmación; confirma o cancela alguna.")
            if any((p.metodo, p.ruta, p.cuerpo) == (metodo, ruta, dict(cuerpo)) for p in pendientes):
                raise LimiteDePropuestas("Ya hay una propuesta igual esperando confirmación.")
            propuesta = Propuesta(secrets.token_urlsafe(9), int(user_id), accion, metodo, ruta, dict(cuerpo), resumen, self._ahora() + self._vida)
            self._por_id[propuesta.id] = propuesta
            return propuesta

    def obtener(self, id_, user_id):
        """Una copia de la propuesta si existe, es de esa cuenta y sigue pendiente; si no, None. Es copia: cambiarla no afecta a la guardada."""
        with self._candado:
            self._vencer()
            p = self._por_id.get(id_)
            return replace(p, cuerpo=dict(p.cuerpo)) if p and p.user_id == int(user_id) and p.estado == PENDIENTE else None

    def cancelar(self, id_, user_id):
        with self._candado:
            self._vencer()
            p = self._por_id.get(id_)
            if p and p.user_id == int(user_id) and p.estado == PENDIENTE:
                p.estado = CANCELADA
                return True
            return False

    def confirmar(self, id_, user_id, ejecutor):
        """Ejecuta la propuesta una sola vez. `ejecutor(propuesta) -> (ok, texto)`. Devuelve `(ok, texto)` para mostrar."""
        with self._candado:
            self._vencer()
            p = self._por_id.get(id_)
            if not p or p.user_id != int(user_id):
                return False, "No encuentro esa propuesta."
            if p.estado != PENDIENTE:
                return False, {EJECUTADA: "Esa propuesta ya se ejecutó.", CANCELADA: "Esa propuesta ya se canceló.",
                               VENCIDA: "Esa propuesta venció; pídemela de nuevo.", FALLIDA: "Esa propuesta ya se intentó."}[p.estado]
            p.estado = EJECUTADA   # se reclama antes de ejecutar: un doble toque no la ejecuta dos veces
        ok, texto = False, MENSAJE_INCIERTO   # si el ejecutor revienta (corte de red) la API pudo haber aplicado el cambio
        try:
            ok, texto = ejecutor(replace(p, cuerpo=dict(p.cuerpo)))
        except Exception:
            pass
        finally:
            with self._candado:
                if not ok:
                    p.estado = FALLIDA
        return ok, texto
