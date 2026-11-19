"""tests/test_modelos.py - Proveedores del modelo del asistente: Ollama (con un servidor falso), el respaldo entre proveedores y la fábrica.

Sin red externa y sin costo: el «Ollama» es un servidor HTTP local de la prueba. Requiere httpx (se ejecuta en Docker, ver tests/run_tests.sh).
"""

import json
import os
import shutil
import sys
import tempfile
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import httpx
except ImportError:
    print("SKIP: requiere httpx (ejecutar con tests/run_tests.sh)")
    sys.exit(0)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="finanzas_test_")
shutil.copytree(RAIZ, TMP, dirs_exist_ok=True, ignore=shutil.ignore_patterns("data", ".git", "tests", "__pycache__", "*.db", "backups", "venv", ".venv", "node_modules", ".env", ".env.*"))
os.chdir(TMP)
sys.path.insert(0, TMP)
os.environ.setdefault("JWT_SECRET_KEY", "k" * 48)

from asistente.herramientas import Herramientas  # noqa: E402
from asistente.modelo_ollama import RECORDATORIO, ErrorDeOllama, ModeloOllama, SinSoporte, normalizar_url, revisar_cifrado  # noqa: E402
from asistente.modelo_falso import ModeloFalso  # noqa: E402
from asistente.modelos import ModeloConRespaldo, crear_modelo  # noqa: E402
import asistente.modelo_openrouter as orr  # noqa: E402
from asistente.modelo_openrouter import ErrorDeOpenRouter, ModeloOpenRouter, ModeloPorContenido  # noqa: E402
from asistente.motor import INSTRUCCIONES, Motor, Respuesta  # noqa: E402

DECLARACIONES = [{"nombre": "resumen", "descripcion": "Resumen", "parametros": {"type": "object", "properties": {}}},
                 {"nombre": "categorias", "descripcion": "Categorías", "parametros": {"type": "object", "properties": {"tipo": {"type": "string"}}}}]


class OllamaFalso:
    """Servidor local que hace de Ollama: guarda cada petición y responde lo que diga `respuestas` (códigos y cuerpos)."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.peticiones = []
        servidor = self

        class Manejador(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                cuerpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                servidor.peticiones.append({"ruta": self.path, "cuerpo": cuerpo, "cabeceras": dict(self.headers)})
                codigo, datos = servidor.respuestas.pop(0) if servidor.respuestas else (200, {"message": {"role": "assistant", "content": "(sin guion)"}})
                crudo = datos if isinstance(datos, bytes) else json.dumps(datos).encode()
                self.send_response(codigo)
                self.send_header("Content-Length", str(len(crudo)))
                self.end_headers()
                self.wfile.write(crudo)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def cerrar(self):
        self.http.shutdown()
        self.http.server_close()


def _texto(contenido, **extra):
    return 200, {"message": {"role": "assistant", "content": contenido}, **extra}


def _llamada(nombre, args, **extra):
    return 200, {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": nombre, "arguments": args}}]}, **extra}


def test_las_urls_de_la_documentacion_se_normalizan():
    for escrita in ("http://h:11435", "http://h:11435/", "http://h:11435/api", "http://h:11435/v1", "http://h:11435/v1/", " 'http://h:11435/api/chat' "):
        assert normalizar_url(escrita) == "http://h:11435", escrita
    for mala in ("", "h:11435", "ftp://h", None):
        try:
            normalizar_url(mala)
        except ValueError:
            pass
        else:
            raise AssertionError(f"debe rechazar {mala!r}")


def test_http_solo_hacia_destinos_privados_o_cifrados():
    for bien in ("https://ollama.ejemplo.com", "http://127.0.0.1:11435", "http://192.168.100.17:11435", "http://10.0.0.5", "http://172.20.1.2",
                 "http://100.93.94.85:11435", "http://ollama:11435", "http://pi.tail1234.ts.net:11435", "http://servidor.local", "http://[::1]:11435"):
        revisar_cifrado(bien)
    for mal in ("http://8.8.8.8:11435", "http://ollama.ejemplo.com", "http://100.128.0.1", "http://172.32.0.1", "http://[2606:4700::1111]"):
        try:
            revisar_cifrado(mal)
        except ValueError as error:
            assert "sin cifrar" in str(error) and mal not in str(error), mal
        else:
            raise AssertionError(f"debe rechazar {mal}")
    os.environ["OLLAMA_PERMITIR_HTTP"] = "1"
    try:
        revisar_cifrado("http://8.8.8.8:11435")
    finally:
        del os.environ["OLLAMA_PERMITIR_HTTP"]
    try:
        ModeloOllama(url="http://8.8.8.8:11435")
    except ValueError:
        pass
    else:
        raise AssertionError("el constructor también debe rechazarlo")


def test_ollama_manda_instrucciones_herramientas_y_ventana_y_lee_texto_y_tokens():
    srv = OllamaFalso([_texto("Hola, ¿en qué te ayudo?", prompt_eval_count=120, eval_count=30)])
    try:
        m = ModeloOllama(srv.url, "qwen2.5:7b")
        r = m.generar("Eres un asistente.", [{"rol": "usuario", "texto": "hola"}], DECLARACIONES)
        assert r.texto == "Hola, ¿en qué te ayudo?" and r.llamadas == [] and r.tokens == 150, r
        p = srv.peticiones[0]
        assert p["ruta"] == "/api/chat" and p["cuerpo"]["model"] == "qwen2.5:7b" and p["cuerpo"]["stream"] is False
        assert p["cuerpo"]["messages"] == [{"role": "system", "content": "Eres un asistente."}, {"role": "user", "content": "hola" + RECORDATORIO}], "la regla se repite junto a la pregunta"
        assert [t["function"]["name"] for t in p["cuerpo"]["tools"]] == ["resumen", "categorias"]
        assert p["cuerpo"]["options"] == {"temperature": 0.1, "num_ctx": 8192}, "pide una ventana que quepa el prompt de herramientas"
        assert "Authorization" not in p["cabeceras"], "Ollama no pide clave"
    finally:
        srv.cerrar()


def test_las_llamadas_a_herramientas_se_leen_y_los_resultados_vuelven_como_mensajes_de_herramienta():
    srv = OllamaFalso([_llamada("resumen", {}), _llamada("categorias", '{"tipo": "gasto"}'), _llamada("categorias", "esto no es json"), _llamada("resumen", [1, 2]), _texto("Listo.")])
    try:
        m = ModeloOllama(srv.url)
        assert m.generar("i", [{"rol": "usuario", "texto": "x"}], DECLARACIONES).llamadas == [{"nombre": "resumen", "args": {}}]
        assert m.generar("i", [{"rol": "usuario", "texto": "x"}], DECLARACIONES).llamadas == [{"nombre": "categorias", "args": {"tipo": "gasto"}}], "los argumentos en texto JSON se leen"
        assert m.generar("i", [{"rol": "usuario", "texto": "x"}], DECLARACIONES).llamadas == [{"nombre": "categorias", "args": {}}], "argumentos ilegibles: vacíos, nunca una excepción"
        assert m.generar("i", [{"rol": "usuario", "texto": "x"}], DECLARACIONES).llamadas == [{"nombre": "resumen", "args": {}}], "argumentos que no son un objeto: vacíos"
        historial = [{"rol": "usuario", "texto": "¿saldo?"}, {"rol": "modelo", "texto": "", "llamadas": [{"nombre": "resumen", "args": {}}]},
                     {"rol": "herramienta", "resultados": [{"nombre": "resumen", "resultado": {"balance": 123.5, "nota": "ñandú"}}]}]
        assert m.generar("i", historial, DECLARACIONES).texto == "Listo."
        enviados = srv.peticiones[-1]["cuerpo"]["messages"]
        assert enviados[2] == {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "resumen", "arguments": {}}}]}
        assert enviados[3]["role"] == "tool" and enviados[3]["tool_name"] == "resumen" and json.loads(enviados[3]["content"]) == {"balance": 123.5, "nota": "ñandú"}
    finally:
        srv.cerrar()


def test_los_turnos_anteriores_se_muestran_con_sus_llamadas_para_que_el_modelo_siga_consultando():
    srv = OllamaFalso([_texto("ok")])
    try:
        historial = [{"rol": "usuario", "texto": "¿saldo?"}, {"rol": "modelo", "texto": "Tu saldo es $10.", "consultas": [{"nombre": "resumen", "args": {}}]},
                     {"rol": "usuario", "texto": "gracias"}, {"rol": "modelo", "texto": "De nada."}, {"rol": "usuario", "texto": "¿y ahora?"}]
        ModeloOllama(srv.url).generar("i", historial, DECLARACIONES)
        roles = [(m["role"], m.get("tool_calls", [{}])[0].get("function", {}).get("name") if m.get("tool_calls") else m.get("content", "")[:12]) for m in srv.peticiones[0]["cuerpo"]["messages"]]
        assert roles == [("system", "i"), ("user", "¿saldo?"), ("assistant", "resumen"), ("tool", "{\"nota\": \"re"), ("assistant", "Tu saldo es "), ("user", "gracias"), ("assistant", "De nada."), ("user", "¿y ahora?\n\n(")], roles
        assert "vuelve a llamar" in srv.peticiones[0]["cuerpo"]["messages"][3]["content"], "los resultados viejos no se repiten: una nota dice que ya no valen"
    finally:
        srv.cerrar()


def test_una_llamada_escrita_como_texto_se_reconoce_solo_si_es_una_herramienta_conocida():
    srv = OllamaFalso([_texto('{"name": "categorias", "arguments": {"tipo": "gasto"}}'), _texto('<tool_call>{"name": "resumen", "arguments": {}}</tool_call>'),
                       _texto('{"name": "borrar_todo", "arguments": {}}'), _texto('Mi saldo {"a": 1} es alto')])
    try:
        m = ModeloOllama(srv.url)
        d = [{"rol": "usuario", "texto": "x"}]
        r = m.generar("i", d, DECLARACIONES)
        assert r.llamadas == [{"nombre": "categorias", "args": {"tipo": "gasto"}}] and r.texto == ""
        assert m.generar("i", d, DECLARACIONES).llamadas == [{"nombre": "resumen", "args": {}}]
        r = m.generar("i", d, DECLARACIONES)
        assert r.llamadas == [] and "borrar_todo" in r.texto, "una herramienta que no existe es texto, no una orden"
        assert m.generar("i", d, DECLARACIONES).llamadas == [], "un JSON dentro de una frase no es una llamada"
    finally:
        srv.cerrar()


def test_los_errores_de_ollama_no_filtran_la_url_ni_el_cuerpo_y_un_archivo_pasa_al_respaldo():
    srv = OllamaFalso([(500, {"error": "boom ñ SECRETO-DE-LA-RESPUESTA"}), (200, b"esto no es json"), (200, {"sin_message": 1})])
    try:
        m = ModeloOllama(srv.url)
        for _ in range(3):
            try:
                m.generar("i", [{"rol": "usuario", "texto": "x"}], DECLARACIONES)
            except ErrorDeOllama as error:
                assert srv.url not in str(error) and "SECRETO" not in str(error) and "127.0.0.1" not in str(error), str(error)
            else:
                raise AssertionError("debe fallar")
    finally:
        srv.cerrar()
    caido = ModeloOllama("http://127.0.0.1:9")   # nadie escucha: error de conexión
    try:
        caido.generar("i", [{"rol": "usuario", "texto": "x"}], DECLARACIONES)
    except ErrorDeOllama as error:
        assert "127.0.0.1" not in str(error) and "ConnectError" in str(error)
    else:
        raise AssertionError("debe fallar")
    try:   # un modelo de solo texto no puede leer una foto
        caido.generar("i", [{"rol": "usuario", "texto": "", "adjuntos": [{"mime": "image/jpeg", "datos": b"x"}]}], DECLARACIONES)
    except SinSoporte:
        pass
    else:
        raise AssertionError("debe avisar que no lee archivos")


def test_un_prompt_que_llena_la_ventana_falla_en_vez_de_contestar_sin_instrucciones():
    srv = OllamaFalso([_texto("respuesta sin fundamento", prompt_eval_count=8192, eval_count=20), _texto("bien", prompt_eval_count=8000, eval_count=5)])
    try:
        m = ModeloOllama(srv.url)
        try:
            m.generar("i", [{"rol": "usuario", "texto": "x"}], DECLARACIONES)
        except ErrorDeOllama as error:
            assert "contexto" in str(error) and "OLLAMA_NUM_CTX" in str(error)
        else:
            raise AssertionError("con la ventana llena Ollama recortó las instrucciones: no se debe usar esa respuesta")
        assert m.generar("i", [{"rol": "usuario", "texto": "x"}], DECLARACIONES).texto == "bien"
    finally:
        srv.cerrar()


def test_la_clave_opcional_y_los_ajustes_de_entorno_se_aplican_y_los_valores_malos_no_los_rompen():
    srv = OllamaFalso([_texto("ok")])
    previo = {k: os.environ.get(k) for k in ("OLLAMA_API_KEY", "OLLAMA_NUM_CTX", "OLLAMA_MODEL")}
    try:
        os.environ.update({"OLLAMA_API_KEY": "tok", "OLLAMA_NUM_CTX": "4096", "OLLAMA_MODEL": "otro:3b"})
        m = ModeloOllama(srv.url + "/v1")
        m.generar("i", [{"rol": "usuario", "texto": "x"}], DECLARACIONES)
        p = srv.peticiones[0]
        assert p["cabeceras"]["Authorization"] == "Bearer tok" and p["cuerpo"]["options"]["num_ctx"] == 4096 and p["cuerpo"]["model"] == "otro:3b"
        for malo in ("0", "-5", "abc", ""):
            os.environ["OLLAMA_NUM_CTX"] = malo
            srv.respuestas.append(_texto("ok"))
            ModeloOllama(srv.url).generar("i", [{"rol": "usuario", "texto": "x"}], DECLARACIONES)
            assert srv.peticiones[-1]["cuerpo"]["options"]["num_ctx"] == 8192, malo
    finally:
        for k, v in previo.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        srv.cerrar()


class _Cuenta:
    def __init__(self, resultados):
        self.resultados, self.llamadas = list(resultados), 0

    def generar(self, *a):
        self.llamadas += 1
        r = self.resultados.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_el_respaldo_solo_entra_cuando_el_principal_falla_y_solo_una_vez():
    eventos = []
    ok = Respuesta(texto="principal")
    alt = Respuesta(texto="respaldo")
    a, b = _Cuenta([ok]), _Cuenta([alt])
    assert ModeloConRespaldo(a, b).generar("i", [], []).texto == "principal" and b.llamadas == 0, "con el principal sano el respaldo no se toca"
    a, b = _Cuenta([ErrorDeOllama("SECRETO caído")]), _Cuenta([alt])
    m = ModeloConRespaldo(a, b, lambda nombre, **c: eventos.append((nombre, c)))
    assert m.generar("i", [], []).texto == "respaldo" and (a.llamadas, b.llamadas) == (1, 1)
    assert eventos == [("modelo_respaldo", {"tipo": "ErrorDeOllama"})] and "SECRETO" not in str(eventos), "el evento solo lleva el tipo del error"
    a, b = _Cuenta([SinSoporte("foto")]), _Cuenta([alt])
    assert ModeloConRespaldo(a, b).generar("i", [], []).texto == "respaldo", "lo que el principal no puede leer lo toma el respaldo"
    a, b = _Cuenta([RuntimeError("uno")]), _Cuenta([RuntimeError("dos")])
    try:
        ModeloConRespaldo(a, b).generar("i", [], [])
    except RuntimeError as error:
        assert str(error) == "dos" and (a.llamadas, b.llamadas) == (1, 1), "si fallan los dos se ve el error y no se reintenta en bucle"
    else:
        raise AssertionError("debe fallar")


def test_la_fabrica_elige_el_proveedor_por_entorno_y_falla_al_arrancar_si_esta_mal_configurado():
    srv = OllamaFalso([_texto("ok")])
    try:
        solo = crear_modelo({"ASISTENTE_MODELO": "ollama", "OLLAMA_URL": srv.url})
        assert isinstance(solo, ModeloOllama) and solo.modelo == "qwen2.5:7b"
        for entorno in ({"ASISTENTE_MODELO": "ollama"}, {"ASISTENTE_MODELO": "ollama", "OLLAMA_URL": "no-es-url"}, {"ASISTENTE_MODELO": "claude"},
                        {"ASISTENTE_MODELO": "ollama", "OLLAMA_URL": srv.url, "ASISTENTE_MODELO_RESPALDO": "ollama"},
                        {"ASISTENTE_MODELO": "ollama", "OLLAMA_URL": srv.url, "ASISTENTE_MODELO_RESPALDO": "otro"}):
            try:
                crear_modelo(entorno)
            except ValueError:
                pass
            else:
                raise AssertionError(f"debe fallar al arrancar: {entorno}")
        try:   # Gemini sin clave (o sin el SDK en esta imagen): también falla al arrancar, no a mitad de una conversación
            crear_modelo({"ASISTENTE_MODELO": "gemini"})
        except (RuntimeError, ImportError):
            pass
        else:
            raise AssertionError("gemini sin clave debe fallar al arrancar")
    finally:
        srv.cerrar()


def test_el_motor_resuelve_un_turno_completo_con_ollama_consultando_la_herramienta():
    """Ollama pide `resumen`, el motor la ejecuta de verdad y Ollama contesta con la cifra que devolvió."""
    srv = OllamaFalso([_llamada("resumen", {}), _texto("Tu saldo es $1,234.50.")])
    try:
        consultas = []

        def obtener(ruta, params):
            consultas.append(ruta)
            return 200, {"balance": 1234.5}

        motor = Motor(ModeloOllama(srv.url), Herramientas(obtener, None, None, lambda: date(2026, 11, 9)), lambda: date(2026, 11, 9))
        turno = motor.responder([], "¿cuánto tengo?")
        assert turno.texto == "Tu saldo es $1,234.50." and turno.herramientas_usadas == ["resumen"] and consultas, turno
        enviados = srv.peticiones[1]["cuerpo"]["messages"]
        assert enviados[0]["role"] == "system" and "asistente de una app de finanzas" in enviados[0]["content"]
        assert enviados[-1]["role"] == "tool" and "1234.5" in enviados[-1]["content"]
        # El texto de la persona y los datos de la plataforma viajan al servidor propio, no a un tercero: no se añade nada más
        assert set(srv.peticiones[1]["cuerpo"]) == {"model", "messages", "tools", "stream", "options"}
    finally:
        srv.cerrar()


def test_un_ollama_caido_no_tumba_el_turno_y_con_respaldo_responde_el_otro():
    caido = ModeloOllama("http://127.0.0.1:9")
    turno = Motor(caido, Herramientas(lambda r, p: (200, {}), None, None, lambda: date(2026, 11, 9)), lambda: date(2026, 11, 9)).responder([], "hola")
    assert turno.error == "ErrorDeOllama" and "No pude contestar" in turno.texto, turno
    con_respaldo = ModeloConRespaldo(caido, ModeloFalso([Respuesta(texto="Respondió el de respaldo.")]))
    turno = Motor(con_respaldo, Herramientas(lambda r, p: (200, {}), None, None, lambda: date(2026, 11, 9)), lambda: date(2026, 11, 9)).responder([], "hola")
    assert turno.texto == "Respondió el de respaldo." and not turno.error, turno



# ---------------------------------------------------------------------------------------------------------------------------------------------
# OpenRouter: un transporte HTTP simulado (sin red ni costo) que guarda cada petición y responde lo que diga el guion
# ---------------------------------------------------------------------------------------------------------------------------------------------
class OpenRouterFalso:
    def __init__(self, guion):
        self.guion = list(guion)
        self.peticiones = []

        def manejar(peticion):
            self.peticiones.append({"url": str(peticion.url), "cuerpo": json.loads(peticion.content or b"{}"), "cabeceras": dict(peticion.headers)})
            codigo, datos = self.guion.pop(0) if self.guion else (200, _or_texto("(sin guion)"))
            return httpx.Response(codigo, json=datos)

        self.cliente = httpx.Client(transport=httpx.MockTransport(manejar))


def _or_texto(texto, tokens=30, **extra):
    return {"choices": [{"message": {"role": "assistant", "content": texto, **extra}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": tokens - 5, "completion_tokens": 5, "total_tokens": tokens}}


def _or_llamada(nombre, args, id_="x1", **extra):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": id_, "type": "function", "function": {"name": nombre, "arguments": json.dumps(args)}}], **extra}, "finish_reason": "tool_calls"}],
        "usage": {"total_tokens": 80}}


def _con_clave(f):
    """Corre `f` con una clave de prueba en el entorno (y sin las demás variables de OpenRouter)."""
    anterior = {k: os.environ.pop(k, None) for k in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_MODEL_MULTIMEDIA", "OPENROUTER_URL",
                                                     "OPENROUTER_TIMEOUT", "OPENROUTER_DATA_COLLECTION", "ASISTENTE_MODELO", "ASISTENTE_MODELO_RESPALDO")}
    os.environ["OPENROUTER_API_KEY"] = '"sk-or-v1-clave-de-prueba"'   # con comillas, como en el .env
    try:
        return f()
    finally:
        for k in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_MODEL_MULTIMEDIA", "OPENROUTER_URL", "OPENROUTER_TIMEOUT", "OPENROUTER_DATA_COLLECTION"):
            os.environ.pop(k, None)
        for k, v in anterior.items():
            if v is not None:
                os.environ[k] = v


def test_openrouter_manda_modelo_herramientas_clave_y_privacidad_y_lee_texto_y_tokens():
    def prueba():
        falso = OpenRouterFalso([(200, _or_texto("Tienes $100.", tokens=42))])
        modelo = ModeloOpenRouter("anthropic/claude-haiku-5.5", cliente=falso.cliente)
        r = modelo.generar("INSTRUCCIONES", [{"rol": "usuario", "texto": "¿cuánto tengo?"}], DECLARACIONES)
        assert (r.texto, r.llamadas, r.tokens) == ("Tienes $100.", [], 42), r
        p = falso.peticiones[0]
        assert p["url"] == "https://openrouter.ai/api/v1/chat/completions", p["url"]
        assert p["cabeceras"]["authorization"] == "Bearer sk-or-v1-clave-de-prueba", "la clave se usa sin las comillas del .env"
        assert not any(c in p["cabeceras"] for c in ("http-referer", "x-title")), "no se manda ninguna cabecera que identifique la app"
        c = p["cuerpo"]
        assert c["model"] == "anthropic/claude-haiku-5.5" and c["provider"] == {"require_parameters": True, "data_collection": "deny"} and c["stream"] is False
        assert c["messages"] == [{"role": "system", "content": "INSTRUCCIONES"}, {"role": "user", "content": "¿cuánto tengo?"}]
        assert [t["function"]["name"] for t in c["tools"]] == ["resumen", "categorias"] and c["tools"][0]["type"] == "function"
        # Con OPENROUTER_DATA_COLLECTION=allow se quita la restricción de proveedores
        os.environ["OPENROUTER_DATA_COLLECTION"] = "allow"
        falso2 = OpenRouterFalso([])
        ModeloOpenRouter("anthropic/claude-haiku-5.5", cliente=falso2.cliente).generar("i", [{"rol": "usuario", "texto": "x"}], DECLARACIONES)
        assert falso2.peticiones[0]["cuerpo"]["provider"] == {"require_parameters": True}, "con allow se quita solo la restricción de datos"
        os.environ["OPENROUTER_DATA_COLLECTION"] = "quizá"
        try:
            ModeloOpenRouter("m", cliente=falso2.cliente)
        except ValueError:
            pass
        else:
            raise AssertionError("un valor raro de privacidad debe rechazarse al arrancar")
        # Sin herramientas no se manda la lista vacía
        falso3 = OpenRouterFalso([])
        os.environ["OPENROUTER_DATA_COLLECTION"] = "deny"
        ModeloOpenRouter("m", cliente=falso3.cliente).generar("i", [{"rol": "usuario", "texto": "x"}], [])
        assert "tools" not in falso3.peticiones[0]["cuerpo"]
    _con_clave(prueba)


def test_las_llamadas_a_herramientas_y_sus_resultados_van_con_el_mismo_id_y_el_razonamiento_vuelve():
    def prueba():
        falso = OpenRouterFalso([(200, _or_llamada("categorias", {"tipo": "gasto"}, reasoning_details=[{"type": "reasoning.encrypted", "data": "abc"}])),
                                 (200, _or_texto("Tienes Comida y Renta."))])
        modelo = ModeloOpenRouter("google/gemini-3.8-flash", cliente=falso.cliente)
        r1 = modelo.generar("I", [{"rol": "usuario", "texto": "¿qué categorías?"}], DECLARACIONES)
        assert r1.llamadas == [{"nombre": "categorias", "args": {"tipo": "gasto"}, "id": "x1", "razonamiento": [{"type": "reasoning.encrypted", "data": "abc"}]}], r1
        trabajo = [{"rol": "usuario", "texto": "¿qué categorías?"}, {"rol": "modelo", "texto": "", "llamadas": r1.llamadas},
                   {"rol": "herramienta", "resultados": [{"nombre": "categorias", "resultado": {"categorias": ["Comida", "Renta"]}}]}]
        r2 = modelo.generar("I", trabajo, DECLARACIONES)
        assert r2.texto == "Tienes Comida y Renta."
        m = falso.peticiones[1]["cuerpo"]["messages"]
        asistente = m[2]
        assert asistente["role"] == "assistant" and asistente["content"] is None and asistente["reasoning_details"] == [{"type": "reasoning.encrypted", "data": "abc"}]
        llamada = asistente["tool_calls"][0]
        assert llamada["type"] == "function" and llamada["function"] == {"name": "categorias", "arguments": json.dumps({"tipo": "gasto"})}
        assert llamada["id"] == "x1", "el id que dio OpenRouter se conserva (las firmas de razonamiento de Gemini se ligan a él)"
        assert m[3] == {"role": "tool", "tool_call_id": "x1", "content": json.dumps({"categorias": ["Comida", "Renta"]})}
        # Varias llamadas en una ronda: cada resultado vuelve con el id de SU llamada, en orden
        falso = OpenRouterFalso([])
        trabajo = [{"rol": "usuario", "texto": "x"},
                   {"rol": "modelo", "texto": "", "llamadas": [{"nombre": "resumen", "args": {}}, {"nombre": "categorias", "args": {}}]},
                   {"rol": "herramienta", "resultados": [{"nombre": "resumen", "resultado": {"a": 1}}, {"nombre": "categorias", "resultado": {"b": 2}}]}]
        ModeloOpenRouter("m", cliente=falso.cliente).generar("I", trabajo, DECLARACIONES)
        m = falso.peticiones[0]["cuerpo"]["messages"]
        ids = [c["id"] for c in m[2]["tool_calls"]]
        assert len(set(ids)) == 2 and [x["tool_call_id"] for x in m[3:5]] == ids
        assert all(ord(ch) < 128 and (ch.isalnum() or ch in "_-") for i in ids for ch in i), "los ids son seguros para Anthropic"
        # Los turnos anteriores que consultaron se muestran con sus llamadas y una nota (y ids que se corresponden)
        falso = OpenRouterFalso([])
        hist = [{"rol": "usuario", "texto": "a"}, {"rol": "modelo", "texto": "Tienes 3.", "consultas": [{"nombre": "resumen", "args": {}}]}, {"rol": "usuario", "texto": "b"}]
        ModeloOpenRouter("m", cliente=falso.cliente).generar("I", hist, DECLARACIONES)
        m = falso.peticiones[0]["cuerpo"]["messages"]
        assert [x["role"] for x in m] == ["system", "user", "assistant", "tool", "assistant", "user"], [x["role"] for x in m]
        assert m[2]["tool_calls"][0]["id"] == m[3]["tool_call_id"] and "consulta anterior" in m[3]["content"]
        # Una respuesta con argumentos rotos o una llamada sin nombre no revienta
        falso = OpenRouterFalso([(200, {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "resumen", "arguments": "{no es json"}}, {"id": "b", "type": "function", "function": {}}, "basura"]}}]})])
        r = ModeloOpenRouter("m", cliente=falso.cliente).generar("I", [{"rol": "usuario", "texto": "x"}], DECLARACIONES)
        assert r.llamadas == [{"nombre": "resumen", "args": {}, "id": "a"}] and r.tokens >= 1
        # Un id que el proveedor mande inválido o repetido se reemplaza por uno propio, sin repetir
        falso = OpenRouterFalso([])
        trabajo = [{"rol": "usuario", "texto": "x"},
                   {"rol": "modelo", "texto": "", "llamadas": [{"nombre": "resumen", "args": {}, "id": "dup"}, {"nombre": "categorias", "args": {}, "id": "dup"},
                                                              {"nombre": "resumen", "args": {}, "id": "con espacios/raros"}]},
                   {"rol": "herramienta", "resultados": [{"nombre": "resumen", "resultado": 1}, {"nombre": "categorias", "resultado": 2}, {"nombre": "resumen", "resultado": 3}]}]
        ModeloOpenRouter("m", cliente=falso.cliente).generar("I", trabajo, DECLARACIONES)
        m = falso.peticiones[0]["cuerpo"]["messages"]
        ids = [c["id"] for c in m[2]["tool_calls"]]
        assert ids[0] == "dup" and len(set(ids)) == 3 and all(i.replace("_", "").replace("-", "").isalnum() for i in ids), ids
        assert [x["tool_call_id"] for x in m[3:6]] == ids
        # Un mensaje de la persona vacío no va como bloque de texto vacío (Anthropic lo rechaza)
        falso = OpenRouterFalso([])
        ModeloOpenRouter("m", cliente=falso.cliente).generar("I", [{"rol": "usuario", "texto": ""}], DECLARACIONES)
        assert falso.peticiones[0]["cuerpo"]["messages"][1]["content"] == "(sin texto)"
    _con_clave(prueba)


def test_las_fotos_y_los_audios_se_mandan_en_el_formato_de_openrouter_y_lo_que_no_se_lee_se_rechaza():
    def prueba():
        falso = OpenRouterFalso([])
        modelo = ModeloOpenRouter("google/gemini-3.8-flash", cliente=falso.cliente)
        adjuntos = [{"mime": "image/jpeg", "datos": b"\xff\xd8\xffjpg"}, {"mime": "audio/ogg", "datos": b"OggSaudio"}, {"mime": "audio/mp3", "datos": b"mp3"}]
        modelo.generar("I", [{"rol": "usuario", "texto": "mira esto", "adjuntos": adjuntos}], DECLARACIONES)
        partes = falso.peticiones[0]["cuerpo"]["messages"][1]["content"]
        assert partes[0] == {"type": "text", "text": "mira esto"}
        assert partes[1]["type"] == "image_url" and partes[1]["image_url"]["url"].startswith("data:image/jpeg;base64,/9j/")
        assert partes[2] == {"type": "input_audio", "input_audio": {"data": "T2dnU2F1ZGlv", "format": "ogg"}}, "el audio va en base64 sin prefijo y con su formato"
        assert partes[3]["input_audio"]["format"] == "mp3"
        # Un archivo de otro tipo no se manda
        try:
            modelo.generar("I", [{"rol": "usuario", "texto": "x", "adjuntos": [{"mime": "application/pdf", "datos": b"%PDF"}]}], DECLARACIONES)
        except SinSoporte:
            pass
        else:
            raise AssertionError("un tipo de archivo sin soporte debe lanzar SinSoporte")
    _con_clave(prueba)


def test_el_modelo_barato_contesta_el_texto_y_el_multimedia_las_fotos_y_audios():
    def prueba():
        falso = OpenRouterFalso([])
        modelo = orr.crear(cliente=falso.cliente)
        assert isinstance(modelo, ModeloPorContenido) and modelo.texto.modelo == "anthropic/claude-haiku-5.5" and modelo.multimedia.modelo == "google/gemini-3.8-flash"
        modelo.generar("I", [{"rol": "usuario", "texto": "hola"}], DECLARACIONES)
        modelo.generar("I", [{"rol": "usuario", "texto": "una foto", "adjuntos": [{"mime": "image/png", "datos": b"\x89PNG"}]}], DECLARACIONES)
        # Las rondas de un turno con herramientas conservan el archivo en el mensaje: siguen con el multimedia
        modelo.generar("I", [{"rol": "usuario", "texto": "una nota", "adjuntos": [{"mime": "audio/wav", "datos": b"RIFF"}]},
                             {"rol": "modelo", "texto": "", "llamadas": [{"nombre": "resumen", "args": {}}]},
                             {"rol": "herramienta", "resultados": [{"nombre": "resumen", "resultado": {}}]}], DECLARACIONES)
        modelo.generar("I", [{"rol": "usuario", "texto": "ya sin archivo"}], DECLARACIONES)
        assert [p["cuerpo"]["model"] for p in falso.peticiones] == ["anthropic/claude-haiku-5.5", "google/gemini-3.8-flash", "google/gemini-3.8-flash", "anthropic/claude-haiku-5.5"]
        # Los nombres se cambian por entorno; un multimedia vacío a propósito deja todo al de texto; igual al de texto, también
        os.environ["OPENROUTER_MODEL"], os.environ["OPENROUTER_MODEL_MULTIMEDIA"] = "otro/modelo-de-texto", "otro/modelo-con-audio"
        m = orr.crear(cliente=falso.cliente)
        assert (m.texto.modelo, m.multimedia.modelo) == ("otro/modelo-de-texto", "otro/modelo-con-audio")
        os.environ["OPENROUTER_MODEL_MULTIMEDIA"] = ""
        assert isinstance(orr.crear(cliente=falso.cliente), ModeloOpenRouter)
        os.environ["OPENROUTER_MODEL_MULTIMEDIA"] = "otro/modelo-de-texto"
        assert isinstance(orr.crear(cliente=falso.cliente), ModeloOpenRouter)
    _con_clave(prueba)


def test_los_errores_de_openrouter_dicen_que_hacer_sin_filtrar_la_clave_ni_el_cuerpo_y_se_reintenta_una_vez():
    def prueba():
        orr.PAUSA_REINTENTO = 0
        secreto = {"error": {"code": 401, "message": "User asked: mi saldo es 12345 sk-or-v1-clave-de-prueba"}}
        for codigo, pista in ((401, "clave"), (402, "crédito"), (404, "política de datos"), (400, "herramientas")):
            falso = OpenRouterFalso([(codigo, secreto)])
            try:
                ModeloOpenRouter("m", cliente=falso.cliente).generar("I", [{"rol": "usuario", "texto": "x"}], DECLARACIONES)
            except ErrorDeOpenRouter as e:
                assert e.estado == codigo and pista in str(e), str(e)
                assert "12345" not in str(e) and "clave-de-prueba" not in str(e) and "openrouter.ai" not in str(e), "el texto no lleva datos ni URL"
                assert len(falso.peticiones) == 1, "un error de configuración no se reintenta"
            else:
                raise AssertionError(codigo)
        # 429 y 503 se reintentan una vez; si se arregla, responde
        falso = OpenRouterFalso([(429, {}), (200, _or_texto("ya"))])
        assert ModeloOpenRouter("m", cliente=falso.cliente).generar("I", [{"rol": "usuario", "texto": "x"}], DECLARACIONES).texto == "ya" and len(falso.peticiones) == 2
        falso = OpenRouterFalso([(503, {}), (503, {})])
        try:
            ModeloOpenRouter("m", cliente=falso.cliente).generar("I", [{"rol": "usuario", "texto": "x"}], DECLARACIONES)
        except ErrorDeOpenRouter as e:
            assert e.estado == 503 and len(falso.peticiones) == 2, "solo un reintento"
        # Si el primer intento ya gastó el presupuesto de tiempo, no se reintenta: el turno del motor corta a los 60 s
        orr.PRESUPUESTO_REINTENTO = 0
        falso = OpenRouterFalso([(503, {}), (200, _or_texto("tarde"))])
        try:
            ModeloOpenRouter("m", cliente=falso.cliente).generar("I", [{"rol": "usuario", "texto": "x"}], DECLARACIONES)
        except ErrorDeOpenRouter as e:
            assert e.estado == 503 and len(falso.peticiones) == 1, "sin presupuesto no hay reintento"
        orr.PRESUPUESTO_REINTENTO = 12
        # Un 200 con un error dentro (el proveedor falló a mitad) también es un error; con 5xx dentro se reintenta
        falso = OpenRouterFalso([(200, {"error": {"code": 502, "message": "x"}}), (200, _or_texto("bien"))])
        assert ModeloOpenRouter("m", cliente=falso.cliente).generar("I", [{"rol": "usuario", "texto": "x"}], DECLARACIONES).texto == "bien"
        for cuerpo in ({"error": {"code": 402, "message": "x"}}, {"choices": []}, {"choices": [{"message": "texto"}]}):
            try:
                ModeloOpenRouter("m", cliente=OpenRouterFalso([(200, cuerpo)]).cliente).generar("I", [{"rol": "usuario", "texto": "x"}], DECLARACIONES)
            except ErrorDeOpenRouter:
                pass
            else:
                raise AssertionError(cuerpo)
        # Una red caída no filtra nada
        def caida(peticion):
            raise httpx.ConnectError("no se pudo conectar a https://openrouter.ai con sk-or-v1-clave-de-prueba")
        try:
            ModeloOpenRouter("m", cliente=httpx.Client(transport=httpx.MockTransport(caida))).generar("I", [{"rol": "usuario", "texto": "x"}], DECLARACIONES)
        except ErrorDeOpenRouter as e:
            assert "ConnectError" in str(e) and "sk-or" not in str(e) and "openrouter.ai" not in str(e)
        orr.PAUSA_REINTENTO = 1.5
    _con_clave(prueba)


def test_la_configuracion_de_openrouter_falla_al_arrancar_si_falta_la_clave_o_la_url_no_es_https():
    def prueba():
        os.environ.pop("OPENROUTER_API_KEY")
        try:
            crear_modelo({"ASISTENTE_MODELO": "openrouter"})
        except RuntimeError as e:
            assert "OPENROUTER_API_KEY" in str(e)
        else:
            raise AssertionError("sin clave debe fallar al arrancar")
        os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-x"
        for url in ("http://openrouter.ai/api/v1", "ftp://x"):
            try:
                ModeloOpenRouter("m", url=url, cliente=OpenRouterFalso([]).cliente)
            except ValueError as e:
                assert "https" in str(e)
            else:
                raise AssertionError(url)
        assert orr.normalizar_url("https://openrouter.ai/api/v1/chat/completions/") == "https://openrouter.ai/api/v1"
        assert orr.normalizar_url("") == "https://openrouter.ai/api/v1"
        # La fábrica lo elige por entorno y admite combinarlo con otro proveedor de respaldo (pero no consigo mismo)
        base = {"ASISTENTE_MODELO": "openrouter", "OPENROUTER_API_KEY": "sk-or-v1-x"}
        modelo = crear_modelo(base)
        assert isinstance(modelo, ModeloPorContenido)
        # La fábrica usa el entorno que recibe, no solo el del proceso: la clave, los modelos y la privacidad salen de ahí
        os.environ.pop("OPENROUTER_API_KEY")
        otro = crear_modelo({**base, "OPENROUTER_API_KEY": "sk-or-v1-otra", "OPENROUTER_MODEL": "a/b", "OPENROUTER_MODEL_MULTIMEDIA": "c/d", "OPENROUTER_DATA_COLLECTION": "allow"})
        assert (otro.texto.modelo, otro.multimedia.modelo, otro.texto._clave, otro.texto._privacidad) == ("a/b", "c/d", "sk-or-v1-otra", "allow"), vars(otro.texto)
        os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-x"
        con_respaldo = crear_modelo({**base, "ASISTENTE_MODELO_RESPALDO": "ollama", "OLLAMA_URL": "http://localhost:11434"})
        assert isinstance(con_respaldo, ModeloConRespaldo) and isinstance(con_respaldo.principal, ModeloPorContenido)
        try:
            crear_modelo({**base, "ASISTENTE_MODELO_RESPALDO": "openrouter"})
        except ValueError:
            pass
        else:
            raise AssertionError("el respaldo no puede ser el mismo proveedor")
        # La clave puede venir de un .env: con comillas, con `export`, con un comentario al final
        os.environ.pop("OPENROUTER_API_KEY")
        for linea, esperada in (('OPENROUTER_API_KEY="sk-or-v1-del-env"', "sk-or-v1-del-env"), ("export OPENROUTER_API_KEY=sk-or-v1-b # mi clave", "sk-or-v1-b"),
                                ("OPENROUTER_API_KEY='sk-or-v1-c'  # con comentario", "sk-or-v1-c"), ("OPENROUTER_API_KEY=", "")):
            with open(".env", "w", encoding="utf-8") as f:
                f.write("OTRA=1\n" + linea + "\n")
            try:
                assert orr.leer_clave() == esperada, (linea, orr.leer_clave())
            finally:
                os.remove(".env")
        assert orr.limpiar_valor("anthropic/claude-haiku-5.5   # solo texto (no lee audio)") == "anthropic/claude-haiku-5.5"
        assert orr.limpiar_valor('"valor con # adentro"') == "valor con # adentro" and orr.limpiar_valor("a#b") == "a#b"
        os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-x"
    _con_clave(prueba)


def test_el_motor_resuelve_un_turno_completo_con_openrouter_y_una_foto_la_contesta_el_modelo_multimedia(client=None):
    def prueba():
        guion = [(200, _or_llamada("categorias", {})), (200, _or_texto("Tienes 2 categorías."))]
        falso = OpenRouterFalso(guion)
        modelo = orr.crear(cliente=falso.cliente)
        motor = Motor(modelo, _HerramientasDePrueba(), lambda: date(2026, 10, 20))
        turno = motor.responder([], "¿Qué categorías tengo?")
        assert turno.texto == "Tienes 2 categorías." and turno.herramientas_usadas == ["categorias"], turno
        assert [p["cuerpo"]["model"] for p in falso.peticiones] == ["anthropic/claude-haiku-5.5"] * 2
        falso = OpenRouterFalso([(200, _or_llamada("categorias", {})), (200, _or_texto("Es un ticket de $50."))])
        motor = Motor(orr.crear(cliente=falso.cliente), _HerramientasDePrueba(), lambda: date(2026, 10, 20))
        turno = motor.responder([], "¿qué es esto?", adjuntos=[{"mime": "image/jpeg", "datos": b"\xff\xd8\xffjpg"}])
        assert [p["cuerpo"]["model"] for p in falso.peticiones] == ["google/gemini-3.8-flash"] * 2, "las dos rondas del turno con foto van al multimedia"
    _con_clave(prueba)



def test_el_compose_trae_los_mismos_modelos_por_defecto_que_el_codigo_y_una_variable_no_definida_no_apaga_el_multimedia():
    """Docker Compose entrega al contenedor una variable no definida como cadena vacía: si el compose no lleva el valor por defecto,
    `crear()` cree que el multimedia se desactivó a propósito y un audio iría al modelo de texto (que no lo lee)."""
    import re as _re
    compose = open(os.path.join(RAIZ, "docker-compose.yml"), encoding="utf-8").read()
    multimedia = _re.findall(r"OPENROUTER_MODEL_MULTIMEDIA: \$\{OPENROUTER_MODEL_MULTIMEDIA-([^}]*)\}", compose)
    texto = _re.findall(r"OPENROUTER_MODEL: \$\{OPENROUTER_MODEL:-([^}]*)\}", compose)
    assert multimedia == [orr.MODELO_MULTIMEDIA] * 2, multimedia   # bot y servicio web
    assert texto == [orr.MODELO_TEXTO] * 2, texto


class _HerramientasDePrueba:
    escritura = False

    def declaraciones(self):
        return DECLARACIONES

    def ejecutar(self, nombre, args):
        return {"categorias": ["Comida", "Renta"]}


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import descubrir, ejecutar

    codigo = ejecutar(descubrir(sys.modules[__name__]), "test_modelos")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)
