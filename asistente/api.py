"""asistente/api.py - Cómo llegan las herramientas a la API: con un token de corta vida de la cuenta, por la ruta HTTP normal."""

from asistente.tokens import emitir_token


def obtener_con_proveedor(cliente, proveedor_de_token):
    """Igual que `obtener_con_cliente`, pero el token de lectura lo da `proveedor_de_token()` (el bot lo pide a la app para el chat)."""
    def obtener(ruta, params):
        r = cliente.get(ruta, params=params, headers={"Authorization": f"Bearer {proveedor_de_token()}"})
        try:
            cuerpo = r.json()
        except ValueError:
            cuerpo = {"detail": "respuesta sin formato"}
        return r.status_code, cuerpo
    return obtener


def obtener_con_cliente(cliente, user_id):
    """`cliente` es cualquier cliente estilo httpx (httpx.Client o fastapi.testclient.TestClient).
    Cada llamada lleva un token nuevo, así que nunca se usa uno vencido."""
    return obtener_con_proveedor(cliente, lambda: emitir_token(user_id))


def ejecutar_propuesta(cliente, propuesta, token_de_escritura=None):
    """Ejecuta una propuesta ya confirmada con un token de escritura de pocos segundos (`token_de_escritura()` o, por defecto, uno
    emitido aquí). Devuelve `(ok, texto)`; el texto lo arma el código con el resumen y lo que respondió la API, nunca el modelo."""
    token = token_de_escritura() if token_de_escritura else emitir_token(propuesta.user_id, escritura=True)
    r = cliente.request(propuesta.metodo, propuesta.ruta, json=propuesta.cuerpo, headers={"Authorization": f"Bearer {token}"})
    if r.status_code < 300:
        return True, f"Listo: {propuesta.resumen}."
    try:
        detalle = r.json().get("detail")
    except ValueError:
        detalle = None
    return False, f"No se pudo: {detalle if isinstance(detalle, str) else 'la plataforma rechazó la operación'}"
