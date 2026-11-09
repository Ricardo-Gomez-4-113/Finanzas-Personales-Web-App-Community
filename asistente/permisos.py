"""asistente/permisos.py - Qué rutas acepta cada token del asistente.

Hay dos tokens, los dos de un minuto:
- `uso = "asistente"` (lectura): lo usan las herramientas que el modelo pide. Solo GET de consulta.
- `uso = "asistente_escritura"`: solo lo emite el código al confirmar una propuesta (nunca está al alcance del modelo) y
  solo abre las tres rutas que el asistente puede ejecutar.

`security.verify_token` rechaza con 403 un token con `uso` en toda ruta que no esté en su lista, y también cualquier `uso`
desconocido. Se compara con la plantilla de la ruta (`/api/apartados/{apartado_id}/movimiento`), no con la URL real.
Fuera de las listas, siempre: autenticación, administración, respaldo, perfil, ediciones y borrados.
"""

USO_ASISTENTE = "asistente"
USO_ESCRITURA = "asistente_escritura"

RUTAS_LECTURA = frozenset({
    ("GET", "/api/resumen"),
    ("GET", "/api/transacciones"),
    ("GET", "/api/gastos-por-categoria"),
    ("GET", "/api/apartados"),
    ("GET", "/api/recurrentes"),
    ("GET", "/api/recurrentes/periodos"),
    ("GET", "/api/presupuesto"),
    ("GET", "/api/inversiones"),
    ("GET", "/api/rendimiento"),
    ("GET", "/api/categorias"),
    ("GET", "/api/tipo-cambio"),
})

RUTAS_ESCRITURA = frozenset({
    ("POST", "/api/transacciones"),
    ("POST", "/api/apartados/{apartado_id}/movimiento"),
    ("POST", "/api/recurrentes/{recurrente_id}/saltar"),
})

RUTAS_POR_USO = {USO_ASISTENTE: RUTAS_LECTURA, USO_ESCRITURA: RUTAS_ESCRITURA}


def permitido(uso, metodo, plantilla):
    """True si un token con ese `uso` puede llamar a esa ruta. Sin plantilla o con un uso desconocido siempre es False."""
    return bool(plantilla) and (str(metodo).upper(), plantilla) in RUTAS_POR_USO.get(uso, frozenset())
