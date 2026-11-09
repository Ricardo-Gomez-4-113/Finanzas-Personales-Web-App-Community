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
