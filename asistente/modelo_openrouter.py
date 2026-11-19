"""asistente/modelo_openrouter.py - Adaptador de OpenRouter (API compatible con OpenAI, `/chat/completions`) al modelo neutro del motor.

Una sola clave (`OPENROUTER_API_KEY`) da acceso a muchos modelos. Aquí se usan dos, según lo que traiga el mensaje:
- `OPENROUTER_MODEL` (por defecto `anthropic/claude-haiku-5.5`): todo lo que es solo texto. Es mucho más barato, pero no lee audio.
- `OPENROUTER_MODEL_MULTIMEDIA` (por defecto `google/gemini-3.8-flash`): cuando el mensaje de la persona trae una foto o una nota de voz.
Si `OPENROUTER_MODEL_MULTIMEDIA` está vacío, el de texto contesta todo (y un audio fallaría: Haiku no lo admite).
El modelo se elige por turno, no por ronda: mientras el mensaje conserva su archivo (todas las rondas de una consulta con herramientas), contesta
el mismo modelo, así que sus firmas de razonamiento (`reasoning_details`) siempre vuelven al modelo que las escribió.

Otros ajustes (todos opcionales): `OPENROUTER_URL` (siempre https), `OPENROUTER_TIMEOUT` (segundos) y `OPENROUTER_DATA_COLLECTION`
(`deny` por defecto: OpenRouter solo enruta a proveedores que no guardan ni entrenan con tus datos; con `allow` se quita esa restricción
si ningún proveedor del modelo la cumple).

Privacidad: las preguntas y los datos de la cuenta viajan a OpenRouter y al proveedor del modelo. No se envía ninguna cabecera que
identifique la app (`HTTP-Referer`, `X-Title` son opcionales). Los errores nunca llevan la clave, la URL ni el cuerpo de la respuesta.

Comprobar la configuración sin gastar casi nada: `python3 -m asistente.modelo_openrouter` (una llamada mínima a cada modelo).
"""

import base64
import json
import os
import re
import sys
import time

from asistente.modelo_ollama import SinSoporte
from asistente.motor import Respuesta

URL_POR_DEFECTO = "https://openrouter.ai/api/v1"
MODELO_TEXTO = "anthropic/claude-haiku-5.5"
MODELO_MULTIMEDIA = "google/gemini-3.8-flash"
SEGUNDOS_POR_DEFECTO = 30          # el turno del motor corta a los 60 s: un modelo lento no debe agotarlo solo
PRESUPUESTO_REINTENTO = 12         # solo se reintenta si el primer intento falló rápido: una llamada + pausa + reintento no pasa de ~45 s
REINTENTAR = (429, 502, 503, 504)  # una sola vez, tras una pausa corta: suelen ser picos de demanda o límites por minuto
PAUSA_REINTENTO = 1.5
_ID_VALIDO = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # lo que aceptan Anthropic y Google en el id de una llamada
FORMATO_AUDIO = {"audio/wav": "wav", "audio/mp3": "mp3", "audio/mpeg": "mp3", "audio/ogg": "ogg", "audio/flac": "flac"}
# Qué le decimos a quien administra cuando OpenRouter rechaza la llamada (nunca el cuerpo de la respuesta: podría traer datos)
PISTAS = {
    400: "la petición no es válida para ese modelo (¿admite herramientas y ese tipo de archivo?)",
    401: "la clave OPENROUTER_API_KEY no es válida",
    402: "la cuenta de OpenRouter no tiene crédito",
    403: "la petición fue rechazada (moderación o clave sin permiso)",
    404: "ningún proveedor acepta lo que se pide: revisa el nombre del modelo, que no se mande un parámetro que ese modelo no soporte, o prueba OPENROUTER_DATA_COLLECTION=allow",
    408: "el proveedor tardó demasiado",
    429: "se superó un límite de uso; espera un momento",
    502: "el proveedor del modelo falló",
    503: "ningún proveedor del modelo está disponible ahora",
}


class ErrorDeOpenRouter(Exception):
    """Fallo al hablar con OpenRouter. `estado` es el código HTTP si lo hubo. El texto nunca lleva la clave, la URL ni el cuerpo."""

    def __init__(self, mensaje, estado=None):
        super().__init__(mensaje)
        self.estado = estado


def limpiar_valor(valor):
    """El valor de una línea de .env: sin comentario al final (`... # solo texto`), sin comillas ni espacios."""
    valor = (valor or "").strip()
    if valor[:1] in ('"', "'"):
        cierre = valor.find(valor[0], 1)
        return valor[1:cierre] if cierre > 0 else valor[1:]
    return re.split(r"\s+#", valor, maxsplit=1)[0].strip()


def leer_clave(ruta_env=".env", entorno=None):
    """OPENROUTER_API_KEY del entorno o, si no está, de un archivo .env (con o sin comillas)."""
    entorno = os.environ if entorno is None else entorno
    clave = limpiar_valor(entorno.get("OPENROUTER_API_KEY", ""))
    if not clave and os.path.exists(ruta_env):
        with open(ruta_env, encoding="utf-8") as archivo:
            for linea in archivo:
                linea = linea.strip()
                if linea.startswith("export "):
                    linea = linea[len("export "):].lstrip()
                if linea.startswith("OPENROUTER_API_KEY="):
                    clave = limpiar_valor(linea.split("=", 1)[1])
    return clave


def normalizar_url(url):
    url = (url or "").strip().strip('"').strip("'").rstrip("/") or URL_POR_DEFECTO
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")].rstrip("/")
    if not url.lower().startswith("https://"):
        raise ValueError("OPENROUTER_URL debe empezar por https://: las conversaciones no viajan sin cifrar")
    return url


def _segundos():
    try:
        valor = int(os.environ.get("OPENROUTER_TIMEOUT", "").strip() or SEGUNDOS_POR_DEFECTO)
    except ValueError:
        return SEGUNDOS_POR_DEFECTO
    return valor if valor > 0 else SEGUNDOS_POR_DEFECTO


def _privacidad(entorno=None):
    entorno = os.environ if entorno is None else entorno
    valor = (entorno.get("OPENROUTER_DATA_COLLECTION") or "").strip().lower() or "deny"
    if valor not in ("deny", "allow"):
        raise ValueError("OPENROUTER_DATA_COLLECTION debe ser deny o allow")
    return valor


class ModeloOpenRouter:
    def __init__(self, modelo, clave=None, url=None, cliente=None, privacidad=None):
        self.modelo = (modelo or "").strip()
        if not self.modelo:
            raise ValueError("falta el nombre del modelo de OpenRouter")
        self._clave = clave or leer_clave()
        if not self._clave:
            raise RuntimeError("Falta OPENROUTER_API_KEY")
        self._base = normalizar_url(url or os.environ.get("OPENROUTER_URL", ""))
        self._privacidad = privacidad or _privacidad()
        if cliente is None:
            import httpx
            cliente = httpx.Client(timeout=_segundos())
        self._cliente = cliente

    def _mensajes(self, instrucciones, mensajes):
        salida = [{"role": "system", "content": instrucciones}]
        contador = 0
        usados = set()
        pendientes = []   # ids de las llamadas de la última ronda del modelo, en orden: cada resultado vuelve con el suyo

        def nuevo_id(preferido=None):
            """El id que dio OpenRouter si es válido y no se repite (las firmas de razonamiento de Gemini se ligan a él); si no, uno nuestro."""
            nonlocal contador
            if isinstance(preferido, str) and _ID_VALIDO.match(preferido) and preferido not in usados:
                usados.add(preferido)
                return preferido
            while True:
                contador += 1
                candidato = f"call_{contador}"
                if candidato not in usados:
                    usados.add(candidato)
                    return candidato

        for m in mensajes:
            if m["rol"] == "usuario":
                adjuntos = m.get("adjuntos") or []
                if not adjuntos:
                    salida.append({"role": "user", "content": m["texto"] or "(sin texto)"})   # Anthropic rechaza un bloque de texto vacío
                    continue
                partes = [{"type": "text", "text": m["texto"] or "(sin texto)"}]
                for a in adjuntos:
                    mime = a["mime"]
                    codificado = base64.b64encode(a["datos"]).decode("ascii")
                    if mime.startswith("image/"):
                        partes.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{codificado}"}})
                    elif mime in FORMATO_AUDIO:
                        partes.append({"type": "input_audio", "input_audio": {"data": codificado, "format": FORMATO_AUDIO[mime]}})
                    else:
                        raise SinSoporte(f"formato {mime} sin soporte")
                salida.append({"role": "user", "content": partes})
            elif m["rol"] == "modelo":
                if m.get("consultas") and not m.get("llamadas"):
                    # Un turno anterior que consultó herramientas: se le muestra al modelo CON sus llamadas (los resultados no se repiten: los datos
                    # cambian y ocupan contexto; una nota dice que son de antes). Así sigue consultando en vez de contestar de memoria.
                    ids = [nuevo_id() for _ in m["consultas"]]
                    salida.append({"role": "assistant", "content": None, "tool_calls": [
                        {"id": i, "type": "function", "function": {"name": c["nombre"], "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False)}}
                        for i, c in zip(ids, m["consultas"])]})
                    for i in ids:
                        salida.append({"role": "tool", "tool_call_id": i, "content": json.dumps(
                            {"nota": "resultado de una consulta anterior; si preguntan de nuevo, vuelve a llamar a la herramienta"})})
                mensaje = {"role": "assistant", "content": m.get("texto") or None}
                llamadas = m.get("llamadas") or []
                if llamadas:
                    pendientes = [nuevo_id(c.get("id")) for c in llamadas]
                    mensaje["tool_calls"] = [
                        {"id": i, "type": "function", "function": {"name": c["nombre"], "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False)}}
                        for i, c in zip(pendientes, llamadas)]
                    razonamiento = next((c["razonamiento"] for c in llamadas if c.get("razonamiento")), None)
                    if razonamiento:
                        mensaje["reasoning_details"] = razonamiento   # un modelo de razonamiento lo necesita de vuelta para seguir tras la herramienta
                elif not mensaje["content"]:
                    mensaje["content"] = "(sin texto)"
                salida.append(mensaje)
            else:
                for r in m["resultados"]:
                    identificador = pendientes.pop(0) if pendientes else nuevo_id()
                    salida.append({"role": "tool", "tool_call_id": identificador,
                                   "content": json.dumps(r["resultado"], ensure_ascii=False, default=str)})
        return salida

    def _pedir(self, cuerpo):
        cabeceras = {"Authorization": f"Bearer {self._clave}", "Content-Type": "application/json"}
        inicio = time.monotonic()
        for intento in range(2):
            queda_tiempo = time.monotonic() - inicio < PRESUPUESTO_REINTENTO
            try:
                r = self._cliente.post(f"{self._base}/chat/completions", json=cuerpo, headers=cabeceras)
            except Exception as error:   # red, tiempo agotado: el texto original podría traer la URL, no se propaga
                raise ErrorDeOpenRouter(f"falló la llamada a OpenRouter ({type(error).__name__})") from None
            if r.status_code == 200:
                try:
                    datos = r.json()
                except ValueError:
                    raise ErrorDeOpenRouter("OpenRouter devolvió una respuesta que no es JSON", 200) from None
                # OpenRouter a veces responde 200 con un error dentro (el proveedor falló a mitad de camino)
                if isinstance(datos, dict) and isinstance(datos.get("error"), dict) and not datos.get("choices"):
                    codigo = datos["error"].get("code")
                    codigo = codigo if isinstance(codigo, int) else None
                    if intento == 0 and codigo in REINTENTAR and queda_tiempo:
                        time.sleep(PAUSA_REINTENTO)
                        continue
                    raise ErrorDeOpenRouter(f"OpenRouter devolvió un error ({codigo}): {PISTAS.get(codigo, 'sin detalle')}", codigo)
                return datos
            if intento == 0 and r.status_code in REINTENTAR and queda_tiempo:
                time.sleep(PAUSA_REINTENTO)
                continue
            raise ErrorDeOpenRouter(f"OpenRouter respondió {r.status_code}: {PISTAS.get(r.status_code, 'error del servicio')}", r.status_code)
        raise ErrorDeOpenRouter("OpenRouter no respondió", None)   # inalcanzable: el bucle siempre devuelve o lanza

    def generar(self, instrucciones, mensajes, declaraciones):
        cuerpo = {
            "model": self.modelo,
            "messages": self._mensajes(instrucciones, mensajes),
            "stream": False,   # sin `temperature`: Haiku 5.5 no la acepta y, con require_parameters, OpenRouter responde 404 (sin proveedor)
        }
        if declaraciones:   # una lista de herramientas vacía la rechazan algunos proveedores
            cuerpo["tools"] = [{"type": "function", "function": {"name": d["nombre"], "description": d["descripcion"], "parameters": d["parametros"]}}
                               for d in declaraciones]
        # Solo proveedores que cumplan lo que se pide (herramientas, archivos): uno que las ignorara contestaría sin consultar nada
        cuerpo["provider"] = {"require_parameters": True, **({"data_collection": "deny"} if self._privacidad == "deny" else {})}
        datos = self._pedir(cuerpo)
        try:
            mensaje = datos["choices"][0]["message"]
            if not isinstance(mensaje, dict):
                raise TypeError
        except (KeyError, IndexError, TypeError):
            raise ErrorDeOpenRouter("OpenRouter devolvió una respuesta sin mensaje") from None
        razonamiento = mensaje.get("reasoning_details") if isinstance(mensaje.get("reasoning_details"), list) else None
        llamadas = []
        for c in mensaje.get("tool_calls") or []:
            funcion = c.get("function") if isinstance(c, dict) else None
            if not isinstance(funcion, dict) or not isinstance(funcion.get("name"), str):
                continue
            args = funcion.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except ValueError:
                    args = {}
            llamada = {"nombre": funcion["name"], "args": args if isinstance(args, dict) else {}}
            if isinstance(c.get("id"), str):
                llamada["id"] = c["id"]
            if razonamiento:
                llamada["razonamiento"] = razonamiento
            llamadas.append(llamada)
        contenido = mensaje.get("content")
        if isinstance(contenido, list):   # algunos proveedores devuelven partes de texto
            contenido = "".join(p.get("text", "") for p in contenido if isinstance(p, dict))
        texto = contenido if isinstance(contenido, str) else ""
        uso = datos.get("usage") if isinstance(datos.get("usage"), dict) else {}
        try:
            tokens = int(uso.get("total_tokens") or 0)
        except (TypeError, ValueError):
            tokens = 0
        if tokens <= 0:   # sin cifras del servicio, el tope de tokens no puede quedar ciego: se estima por longitud
            tokens = max(1, (len(instrucciones) + len(str(mensajes)) + len(texto) + len(str(llamadas))) // 3)
        return Respuesta(texto=texto if not llamadas else "", llamadas=llamadas, tokens=tokens)


class ModeloPorContenido:
    """Reparte cada turno entre dos modelos: el de texto (barato) y el multimedia (el único que lee fotos y audios). Se decide con el
    mensaje de la persona: si conserva `adjuntos`, todas las rondas de ese turno van al multimedia."""

    def __init__(self, texto, multimedia=None):
        self.texto, self.multimedia = texto, multimedia
        self.modelo = getattr(texto, "modelo", "")

    def generar(self, instrucciones, mensajes, declaraciones):
        con_archivo = any(m.get("adjuntos") for m in mensajes if m.get("rol") == "usuario")
        elegido = self.multimedia if (con_archivo and self.multimedia is not None) else self.texto
        return elegido.generar(instrucciones, mensajes, declaraciones)


def crear(entorno=None, cliente=None):
    """El par de modelos que dice el entorno. Falla al arrancar si falta la clave o algo está mal escrito."""
    entorno = os.environ if entorno is None else entorno
    texto = (entorno.get("OPENROUTER_MODEL") or "").strip() or MODELO_TEXTO
    # Vacío a propósito = sin modelo multimedia; no definida = el de por defecto
    crudo = entorno.get("OPENROUTER_MODEL_MULTIMEDIA")
    multimedia = MODELO_MULTIMEDIA if crudo is None else crudo.strip()
    ajustes = dict(clave=leer_clave(entorno=entorno), url=entorno.get("OPENROUTER_URL"), privacidad=_privacidad(entorno), cliente=cliente)
    principal = ModeloOpenRouter(texto, **ajustes)
    if not multimedia or multimedia == texto:
        return principal
    return ModeloPorContenido(principal, ModeloOpenRouter(multimedia, **ajustes))


def _comprobar():
    """`python3 -m asistente.modelo_openrouter`: una llamada mínima a cada modelo configurado y qué hacer si falla."""
    modelo = crear()
    pares = [("texto", getattr(modelo, "texto", modelo))] + ([("multimedia", modelo.multimedia)] if isinstance(modelo, ModeloPorContenido) and modelo.multimedia else [])
    fallos = 0
    for etiqueta, m in pares:
        inicio = time.time()
        try:
            r = m.generar("Responde solo: ok", [{"rol": "usuario", "texto": "ok"}], [])
            print(f"OK    {etiqueta:<10} {m.modelo}  ({time.time() - inicio:.1f}s, {r.tokens} tokens)")
        except Exception as error:
            fallos += 1
            print(f"FALLA {etiqueta:<10} {m.modelo}: {error}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(_comprobar())
