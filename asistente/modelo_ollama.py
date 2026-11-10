"""asistente/modelo_ollama.py - Adaptador de Ollama (API nativa `/api/chat`) al modelo neutro del motor.

Sirve para desarrollar sin cuota ni costo con un modelo propio (por ejemplo `qwen2.5:7b`) en la red local. Usa `httpx`, que el bot y la
app ya traen. Es solo texto: un modelo sin visión no lee fotos ni audios, así que ante un archivo lanza `SinSoporte` y quien arma la
cadena de modelos (`asistente/modelos.py`) puede pasárselo al de respaldo.

Configuración (variables de entorno): `OLLAMA_URL` (con o sin `/api` o `/v1` al final), `OLLAMA_MODEL` (por defecto `qwen2.5:7b`),
`OLLAMA_NUM_CTX` (ventana de contexto; las instrucciones y las herramientas ocupan ~4 000 tokens y el valor por defecto de Ollama puede
cortarlas), `OLLAMA_TIMEOUT` (segundos) y `OLLAMA_API_KEY` (solo si hay un proxy que la pida; Ollama no la necesita).

Cifrado: las preguntas y los datos de la cuenta viajan a ese servidor. Con `https://` siempre; con `http://` solo si el destino es la propia
máquina, una red privada o Tailscale (100.64.0.0/10, que ya va cifrado por WireGuard) o un nombre de red interna (`.ts.net`, `.local`, `.lan`,
`.internal` o sin puntos). Un `http://` hacia una dirección pública se rechaza al arrancar, salvo `OLLAMA_PERMITIR_HTTP=1`.
"""

import ipaddress
import json
import os
import re
from urllib.parse import urlsplit

from asistente.motor import Respuesta

MODELO_POR_DEFECTO = "qwen2.5:7b"
NUM_CTX_POR_DEFECTO = 8192
# Se añade al final del último mensaje de la persona (solo en lo que se envía, no en el historial): un modelo pequeño se olvida de las reglas de las
# instrucciones cuando la conversación se alarga y empieza a contestar de memoria o a narrar lo que haría; repetirlas junto a la pregunta lo evita.
RECORDATORIO = "\n\n(Recuerda: los datos de mi cuenta solo salen de una herramienta; llámala antes de contestar y no describas que vas a llamarla.)"
SEGUNDOS_POR_DEFECTO = 50   # el turno del motor corta a los 60 s: este modelo no debe agotarlo solo


class SinSoporte(Exception):
    """Este modelo no puede con el mensaje (por ejemplo, trae una foto y es de solo texto). Se pasa al de respaldo, si lo hay."""


class ErrorDeOllama(Exception):
    """Fallo al hablar con Ollama. El texto nunca lleva la URL ni el cuerpo de la respuesta: podrían traer datos."""


def normalizar_url(url):
    """`http://host:11435`, sin barra final ni `/api` ni `/v1`: lo que el usuario pegue de la documentación vale."""
    url = (url or "").strip().strip('"').strip("'").rstrip("/")
    url = re.sub(r"/(api|v1)(/chat)?$", "", url).rstrip("/")
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("OLLAMA_URL debe empezar por http:// o https://")
    return url


_TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
_SUFIJOS_INTERNOS = (".ts.net", ".local", ".lan", ".internal", ".home.arpa")


def _destino_protegido(url):
    """True si hablar por http con este destino no sale a una red pública."""
    host = (urlsplit(url).hostname or "").lower()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host or host.endswith(_SUFIJOS_INTERNOS)
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip in _TAILSCALE


def revisar_cifrado(url):
    if url.lower().startswith("https://") or _destino_protegido(url) or os.environ.get("OLLAMA_PERMITIR_HTTP", "").strip() == "1":
        return
    raise ValueError("OLLAMA_URL usa http:// hacia una dirección pública: el contenido de las conversaciones viajaría sin cifrar. "
                     "Usa https://, una red privada o Tailscale (o, bajo tu responsabilidad, OLLAMA_PERMITIR_HTTP=1)")


def _entero(nombre, defecto):
    try:
        valor = int(os.environ.get(nombre, "").strip() or defecto)
    except ValueError:
        return defecto
    return valor if valor > 0 else defecto


# Algunos modelos pequeños escriben la llamada a la herramienta como texto JSON en vez de usar `tool_calls`
_LLAMADA_EN_TEXTO = re.compile(r"^\s*(?:<tool_call>)?\s*(\{.*\})\s*(?:</tool_call>)?\s*$", re.S)


def _llamada_en_texto(texto, conocidas):
    encontrada = _LLAMADA_EN_TEXTO.match(texto or "")
    if not encontrada:
        return None
    try:
        dato = json.loads(encontrada.group(1))
    except ValueError:
        return None
    if not isinstance(dato, dict) or dato.get("name") not in conocidas:
        return None
    args = dato.get("arguments", dato.get("parameters", {}))
    return {"nombre": dato["name"], "args": args if isinstance(args, dict) else {}}


class ModeloOllama:
    def __init__(self, url=None, modelo=None, cliente=None):
        self._base = normalizar_url(url or os.environ.get("OLLAMA_URL", ""))
        revisar_cifrado(self._base)
        self.modelo = modelo or os.environ.get("OLLAMA_MODEL", "").strip() or MODELO_POR_DEFECTO
        self._num_ctx = _entero("OLLAMA_NUM_CTX", NUM_CTX_POR_DEFECTO)
        segundos = _entero("OLLAMA_TIMEOUT", SEGUNDOS_POR_DEFECTO)
        clave = os.environ.get("OLLAMA_API_KEY", "").strip()
        self._cabeceras = {"Authorization": f"Bearer {clave}"} if clave else {}
        if cliente is None:
            import httpx
            cliente = httpx.Client(timeout=segundos)
        self._cliente = cliente

    def _mensajes(self, instrucciones, mensajes):
        salida = [{"role": "system", "content": instrucciones}]
        for m in mensajes:
            if m["rol"] == "usuario":
                if m.get("adjuntos"):
                    raise SinSoporte("este modelo solo lee texto")
                salida.append({"role": "user", "content": m["texto"] or ""})
            elif m["rol"] == "modelo":
                if m.get("consultas") and not m.get("llamadas"):
                    # Un turno anterior que consultó herramientas: se le muestra al modelo CON sus llamadas. Si solo viera respuestas con cifras y
                    # ninguna llamada, un modelo pequeño aprende a contestar de memoria (se vio con qwen2.5:7b a partir de la sexta pregunta).
                    # Los resultados no se repiten (los datos cambian y ocupan contexto): una nota dice que son de antes.
                    salida.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": c["nombre"], "arguments": c.get("args") or {}}} for c in m["consultas"]]})
                    for c in m["consultas"]:
                        salida.append({"role": "tool", "tool_name": c["nombre"], "content": json.dumps({"nota": "resultado de una consulta anterior; si preguntan de nuevo, vuelve a llamar a la herramienta"})})
                mensaje = {"role": "assistant", "content": m.get("texto") or ""}
                if m.get("llamadas"):
                    mensaje["tool_calls"] = [{"function": {"name": c["nombre"], "arguments": c.get("args") or {}}} for c in m["llamadas"]]
                salida.append(mensaje)
            else:
                for r in m["resultados"]:
                    salida.append({"role": "tool", "tool_name": r["nombre"], "content": json.dumps(r["resultado"], ensure_ascii=False, default=str)})
        if salida[-1]["role"] == "user":
            salida[-1] = {**salida[-1], "content": salida[-1]["content"] + RECORDATORIO}
        return salida

    def generar(self, instrucciones, mensajes, declaraciones):
        cuerpo = {
            "model": self.modelo,
            "messages": self._mensajes(instrucciones, mensajes),
            "tools": [{"type": "function", "function": {"name": d["nombre"], "description": d["descripcion"], "parameters": d["parametros"]}}
                      for d in declaraciones],
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": self._num_ctx},
        }
        try:
            r = self._cliente.post(f"{self._base}/api/chat", json=cuerpo, headers=self._cabeceras)
            if r.status_code != 200:
                raise ErrorDeOllama(f"Ollama respondió {r.status_code}")
            datos = r.json()
            mensaje = datos["message"]
        except ErrorDeOllama:
            raise
        except Exception as error:   # red, tiempo agotado, JSON roto: el texto original podría traer la URL, no se propaga
            raise ErrorDeOllama(f"falló la llamada a Ollama ({type(error).__name__})") from None
        conocidas = {d["nombre"] for d in declaraciones}
        llamadas = []
        for c in (mensaje.get("tool_calls") or []) if isinstance(mensaje, dict) else []:
            funcion = c.get("function") if isinstance(c, dict) else None
            if not isinstance(funcion, dict) or not isinstance(funcion.get("name"), str):
                continue
            args = funcion.get("arguments")
            if isinstance(args, str):   # algunos servidores compatibles lo mandan como texto JSON
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            llamadas.append({"nombre": funcion["name"], "args": args if isinstance(args, dict) else {}})
        texto = mensaje.get("content") if isinstance(mensaje, dict) and isinstance(mensaje.get("content"), str) else ""
        if not llamadas:
            en_texto = _llamada_en_texto(texto, conocidas)
            if en_texto:
                llamadas, texto = [en_texto], ""
        entrada = int(datos.get("prompt_eval_count") or 0)
        if entrada >= self._num_ctx - 16:
            # Ollama recorta el principio cuando el prompt no cabe: se pierden las instrucciones y las herramientas y el modelo contesta cualquier
            # cosa sin avisar. Mejor fallar (el motor lo dice, o responde el modelo de respaldo) que mostrar una respuesta sin fundamento.
            raise ErrorDeOllama(f"el contexto se llenó ({entrada} tokens de {self._num_ctx}): sube OLLAMA_NUM_CTX o acorta la conversación")
        tokens = entrada + int(datos.get("eval_count") or 0)
        if tokens <= 0:   # sin cifras del servidor, el tope de tokens no puede quedar ciego: se estima por longitud
            tokens = max(1, (len(instrucciones) + len(str(mensajes)) + len(texto) + len(str(llamadas))) // 3)
        return Respuesta(texto=texto if not llamadas else "", llamadas=llamadas, tokens=tokens)
