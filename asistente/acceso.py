"""asistente/acceso.py - Un solo lugar que decide si una cuenta puede usar el asistente.

En esta edición basta el interruptor del administrador (`users.asistente_activo`). La edición con planes de pago lo sustituye por su
propia versión, que además exige que el plan de la cuenta incluya la IA.
"""

from typing import Any, Dict


def asistente_habilitado(user: Dict[str, Any]) -> bool:
    return bool(user.get("asistente_activo", 1))
