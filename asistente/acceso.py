"""asistente/acceso.py - Un solo lugar que decide si una cuenta puede usar el asistente.

Basta el interruptor del administrador (`users.asistente_activo`). Si una instalación necesita otra regla, se cambia aquí y en ningún otro
lugar: la web, Telegram, la emisión de tokens y la burbuja consultan esta función.
"""

from typing import Any, Dict


def asistente_habilitado(user: Dict[str, Any]) -> bool:
    return bool(user.get("asistente_activo", 1))
