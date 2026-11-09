"""asistente/modelo_gemini.py - Adaptador de Gemini (SDK `google-genai`) al modelo neutro del motor.

El SDK se importa al crear el adaptador: la app y las pruebas no lo necesitan (`requirements-asistente.txt`).
"""

import os

from asistente.motor import Respuesta

MODELO_POR_DEFECTO = "gemini-3.8-flash"
MODELO_DE_RESPALDO = "gemini-3.1-flash-lite"   # si el principal está saturado (503/429), tarda de más (504) o ya no existe (404)
CODIGOS_DE_RESPALDO = (404, 429, 500, 503, 504)
SEGUNDOS_MAXIMOS = 30


def _es_transitorio(error):
    """Saturación, tiempo agotado o red caída: vale la pena probar con el modelo de respaldo."""
    codigo = getattr(error, "code", None)
    if isinstance(codigo, int):
        return codigo in CODIGOS_DE_RESPALDO
    return any(palabra in type(error).__name__ for palabra in ("Timeout", "ConnectError", "ReadError", "RemoteProtocol"))


def leer_clave(ruta_env=".env"):
    """GEMINI_API_KEY del entorno o, si no está, de un archivo .env (con o sin comillas)."""
    clave = os.environ.get("GEMINI_API_KEY", "")
    if not clave and os.path.exists(ruta_env):
        with open(ruta_env, encoding="utf-8") as archivo:
            for linea in archivo:
                if linea.startswith("GEMINI_API_KEY="):
                    clave = linea.split("=", 1)[1]
    return clave.strip().strip('"').strip("'").strip()


class ModeloGemini:
    def __init__(self, clave=None, modelo=None):
        from google import genai
        from google.genai import errors, types
        self._types = types
        self._errores = errors
        clave = clave or leer_clave()
        if not clave:
            raise RuntimeError("Falta GEMINI_API_KEY")
        self._cliente = genai.Client(api_key=clave, http_options=types.HttpOptions(timeout=SEGUNDOS_MAXIMOS * 1000))
        self.modelo = modelo or os.environ.get("GEMINI_MODEL") or MODELO_POR_DEFECTO
        self.respaldo = os.environ.get("GEMINI_MODEL_RESPALDO") or MODELO_DE_RESPALDO

    def _contenidos(self, mensajes):
        t = self._types
        salida = []
        for m in mensajes:
            if m["rol"] == "usuario":
                salida.append(t.Content(role="user", parts=[t.Part(text=m["texto"])]))
            elif m["rol"] == "modelo":
                partes = [t.Part(text=m["texto"])] if m.get("texto") else []
                # Gemini 3 exige devolver la firma de pensamiento de cada llamada a función; es un dato opaco que se guarda tal cual
                partes += [t.Part(function_call=t.FunctionCall(name=c["nombre"], args=c.get("args") or {}), thought_signature=c.get("firma"))
                           for c in m.get("llamadas", [])]
                salida.append(t.Content(role="model", parts=partes))
            else:
                salida.append(t.Content(role="user", parts=[
                    t.Part(function_response=t.FunctionResponse(name=r["nombre"], response={"resultado": r["resultado"]}))
                    for r in m["resultados"]]))
        return salida

    def generar(self, instrucciones, mensajes, declaraciones):
        t = self._types
        herramientas = [t.Tool(function_declarations=[
            t.FunctionDeclaration(name=d["nombre"], description=d["descripcion"],
                                  parameters_json_schema=d["parametros"]) for d in declaraciones])]
        config = t.GenerateContentConfig(system_instruction=instrucciones, tools=herramientas,
                                         automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
                                         temperature=0.2)
        contenidos = self._contenidos(mensajes)
        try:
            respuesta = self._cliente.models.generate_content(model=self.modelo, contents=contenidos, config=config)
        except Exception as error:
            if not _es_transitorio(error) or self.respaldo == self.modelo:
                raise
            respuesta = self._cliente.models.generate_content(model=self.respaldo, contents=contenidos, config=config)
        llamadas = []
        for candidato in (respuesta.candidates or [])[:1]:
            for parte in (candidato.content.parts if candidato.content and candidato.content.parts else []):
                if parte.function_call:
                    llamadas.append({"nombre": parte.function_call.name, "args": dict(parte.function_call.args or {}),
                                     "firma": parte.thought_signature})
        texto = ""
        if not llamadas:
            try:
                texto = respuesta.text or ""
            except Exception:
                texto = ""
        uso = getattr(respuesta, "usage_metadata", None)
        return Respuesta(texto=texto, llamadas=llamadas, tokens=int(getattr(uso, "total_token_count", 0) or 0))
