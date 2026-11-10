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
from asistente.modelo_ollama import ErrorDeOllama, ModeloOllama, SinSoporte, normalizar_url  # noqa: E402
from asistente.modelo_falso import ModeloFalso  # noqa: E402
from asistente.modelos import ModeloConRespaldo, crear_modelo  # noqa: E402
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


def test_ollama_manda_instrucciones_herramientas_y_ventana_y_lee_texto_y_tokens():
    srv = OllamaFalso([_texto("Hola, ¿en qué te ayudo?", prompt_eval_count=120, eval_count=30)])
    try:
        m = ModeloOllama(srv.url, "qwen2.5:7b")
        r = m.generar("Eres un asistente.", [{"rol": "usuario", "texto": "hola"}], DECLARACIONES)
        assert r.texto == "Hola, ¿en qué te ayudo?" and r.llamadas == [] and r.tokens == 150, r
        p = srv.peticiones[0]
        assert p["ruta"] == "/api/chat" and p["cuerpo"]["model"] == "qwen2.5:7b" and p["cuerpo"]["stream"] is False
        assert p["cuerpo"]["messages"] == [{"role": "system", "content": "Eres un asistente."}, {"role": "user", "content": "hola"}]
        assert [t["function"]["name"] for t in p["cuerpo"]["tools"]] == ["resumen", "categorias"]
        assert p["cuerpo"]["options"] == {"temperature": 0.2, "num_ctx": 8192}, "pide una ventana que quepa el prompt de herramientas"
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


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import descubrir, ejecutar

    codigo = ejecutar(descubrir(sys.modules[__name__]), "test_modelos")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)
