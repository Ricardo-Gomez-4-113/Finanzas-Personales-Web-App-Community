"""asistente/permisos.py - Qué rutas acepta un token del asistente.

El token lleva `uso = "asistente"` y `security.verify_token` lo rechaza con 403 en toda ruta que no esté aquí. Se
compara con la plantilla de la ruta (`/api/apartados/{apartado_id}/movimiento`), no con la URL real.
Fuera de la lista, siempre: autenticación, administración, respaldo, perfil y borrados.
"""

USO_ASISTENTE = "asistente"

RUTAS_PERMITIDAS = frozenset({
    ("GET", "/api/resumen"),
    ("GET", "/api/transacciones"),
    ("GET", "/api/gastos-por-categoria"),
    ("GET", "/api/apartados"),
    ("GET", "/api/recurrentes/periodos"),
    ("GET", "/api/presupuesto"),
    ("GET", "/api/inversiones"),
    ("GET", "/api/rendimiento"),
    ("GET", "/api/categorias"),
    ("GET", "/api/tipo-cambio"),
})


def permitido(metodo, plantilla):
    """True si un token del asistente puede llamar a esa ruta. Sin plantilla (ruta desconocida) siempre es False."""
    return bool(plantilla) and (str(metodo).upper(), plantilla) in RUTAS_PERMITIDAS
