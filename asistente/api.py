"""asistente/api.py - Cómo llegan las herramientas a la API: con un token de corta vida de la cuenta, por la ruta HTTP normal."""

from asistente.tokens import emitir_token


def obtener_con_cliente(cliente, user_id):
    """`cliente` es cualquier cliente estilo httpx (httpx.Client o fastapi.testclient.TestClient).
    Cada llamada lleva un token nuevo, así que nunca se usa uno vencido."""
    def obtener(ruta, params):
        r = cliente.get(ruta, params=params, headers={"Authorization": f"Bearer {emitir_token(user_id)}"})
        try:
            cuerpo = r.json()
        except ValueError:
            cuerpo = {"detail": "respuesta sin formato"}
        return r.status_code, cuerpo
    return obtener


def ejecutar_propuesta(cliente, propuesta):
    """Ejecuta una propuesta ya confirmada con un token de escritura de un minuto. Devuelve `(ok, texto)`; el texto lo arma el
    código con el resumen y lo que respondió la API, nunca el modelo."""
    r = cliente.request(propuesta.metodo, propuesta.ruta, json=propuesta.cuerpo,
                        headers={"Authorization": f"Bearer {emitir_token(propuesta.user_id, escritura=True)}"})
    if r.status_code < 300:
        return True, f"Listo: {propuesta.resumen}."
    try:
        detalle = r.json().get("detail")
    except ValueError:
        detalle = None
    return False, f"No se pudo: {detalle if isinstance(detalle, str) else 'la plataforma rechazó la operación'}"
