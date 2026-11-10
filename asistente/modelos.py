"""asistente/modelos.py - Qué modelo usa el asistente: Gemini, Ollama o uno con el otro de respaldo.

Se elige por entorno, igual para el bot de Telegram, el servicio web y la línea de comandos:
- `ASISTENTE_MODELO`: `gemini` (por defecto) u `ollama`.
- `ASISTENTE_MODELO_RESPALDO` (opcional): el otro. Si el principal falla (red caída, tiempo agotado, error) o no puede con el mensaje (una
  foto o un audio con un modelo de solo texto), se intenta una vez con el de respaldo.
Gemini se configura con `GEMINI_API_KEY`/`GEMINI_MODEL`/`GEMINI_MODEL_RESPALDO`; Ollama con `OLLAMA_URL`/`OLLAMA_MODEL` (ver `modelo_ollama.py`).
"""

import os

PROVEEDORES = ("gemini", "ollama")


class ModeloConRespaldo:
    """Prueba el principal y, si falla, una vez el de respaldo. Los dos hablan el mismo lenguaje (`generar`), así que el motor no se entera."""

    def __init__(self, principal, respaldo, registrar=None):
        self.principal, self.respaldo, self._registrar = principal, respaldo, registrar
        self.modelo = getattr(principal, "modelo", "")

    def generar(self, instrucciones, mensajes, declaraciones):
        try:
            return self.principal.generar(instrucciones, mensajes, declaraciones)
        except Exception as error:
            if self._registrar:
                try:
                    self._registrar("modelo_respaldo", tipo=type(error).__name__)   # solo el tipo: el texto del error puede traer datos
                except Exception:
                    pass
            return self.respaldo.generar(instrucciones, mensajes, declaraciones)


def _proveedor(nombre):
    nombre = (nombre or "").strip().lower()
    if nombre not in PROVEEDORES:
        raise ValueError(f"proveedor de modelo desconocido: {nombre!r} (usa {' u '.join(PROVEEDORES)})")
    return nombre


def crear_uno(nombre, entorno=None):
    nombre = _proveedor(nombre)
    entorno = os.environ if entorno is None else entorno
    if nombre == "ollama":
        from asistente.modelo_ollama import ModeloOllama
        return ModeloOllama(url=entorno.get("OLLAMA_URL"), modelo=entorno.get("OLLAMA_MODEL"))
    from asistente.modelo_gemini import ModeloGemini
    return ModeloGemini()


def crear_modelo(entorno=None, registrar=None):
    """El modelo que dice el entorno. Falla al arrancar (no a mitad de una conversación) si el proveedor no está bien configurado."""
    entorno = os.environ if entorno is None else entorno
    principal = _proveedor(entorno.get("ASISTENTE_MODELO") or "gemini")
    respaldo = (entorno.get("ASISTENTE_MODELO_RESPALDO") or "").strip()
    modelo = crear_uno(principal, entorno)
    if not respaldo:
        return modelo
    if _proveedor(respaldo) == principal:
        raise ValueError("el modelo de respaldo debe ser distinto del principal")
    return ModeloConRespaldo(modelo, crear_uno(respaldo, entorno), registrar)
