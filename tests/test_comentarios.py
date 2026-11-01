"""tests/test_comentarios.py - Comentarios del usuario: sesión, validación, límite de envíos, autor y registro.

Requiere fastapi + httpx (se ejecuta en Docker, ver tests/run_tests.sh).
Copia el código a un directorio temporal: nunca toca 'data/' del proyecto.
"""

import os
import shutil
import sys
import tempfile

try:
    import httpx  # noqa: F401
    import fastapi  # noqa: F401
except ImportError:
    print("SKIP: requiere fastapi y httpx (ejecutar con tests/run_tests.sh)")
    sys.exit(0)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="finanzas_test_")
LOGS = tempfile.mkdtemp(prefix="finanzas_logs_")
shutil.copytree(
    RAIZ, TMP, dirs_exist_ok=True,
    ignore=shutil.ignore_patterns("data", ".git", "tests", "__pycache__", "*.db", "backups", "venv", ".venv", "node_modules"),
)
os.chdir(TMP)
sys.path.insert(0, TMP)
os.environ.setdefault("JWT_SECRET_KEY", "k" * 48)
os.environ["DEFAULT_ADMIN_PASSWORD"] = "AdminTest#12345"
os.environ["FINANZAS_LOG_DIR"] = LOGS

from fastapi.testclient import TestClient  # noqa: E402

import master_db  # noqa: E402
import security  # noqa: E402
from main import app  # noqa: E402

PASS = "Passw0rd#Test1"


def _crear_usuario(nombre):
    return master_db.create_user_entry(nombre, security.hash_password(PASS), email=f"{nombre}@test.local")


def _login(client, usuario):
    r = client.post("/auth/login", json={"username": usuario, "password": PASS})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _fila_en_base(id_comentario):
    conn = master_db.get_master_conn()
    try:
        fila = conn.execute("SELECT user_id, tipo, mensaje, contexto FROM comentarios WHERE id = ?", (id_comentario,)).fetchone()
        return tuple(fila) if fila else None
    finally:
        conn.close()


def test_comentario_exige_sesion(client):
    r = client.post("/api/comentarios", json={"tipo": "error", "mensaje": "sin sesion"})
    assert r.status_code == 401, r.text


def test_validacion_rechaza_tipo_longitud_y_no_repite_el_texto(client):
    _crear_usuario("com_valida")
    h = _login(client, "com_valida")
    assert client.post("/api/comentarios", json={"tipo": "otra", "mensaje": "hola"}, headers=h).status_code == 422
    assert client.post("/api/comentarios", json={"tipo": "error", "mensaje": ""}, headers=h).status_code == 422
    assert client.post("/api/comentarios", json={"tipo": "error", "mensaje": "   "}, headers=h).status_code == 422
    assert client.post("/api/comentarios", json={"tipo": "error", "mensaje": "x" * 1001}, headers=h).status_code == 422
    assert client.post("/api/comentarios", json={"tipo": "error", "mensaje": "ok", "contexto": "c" * 121}, headers=h).status_code == 422
    r = client.post("/api/comentarios", json={"tipo": "error", "mensaje": "y" * 1001}, headers=h)
    assert len(r.content) < 2000, "el 422 no debe repetir el comentario enviado"


def test_comentario_se_guarda_con_su_autor_y_contexto(client):
    usuario = _crear_usuario("com_autor")
    h = _login(client, "com_autor")
    r = client.post("/api/comentarios", json={"tipo": "sugerencia", "mensaje": "  Me gustaría exportar en CSV  ", "contexto": "tab-movimientos"}, headers=h)
    assert r.status_code == 201, r.text
    fila = _fila_en_base(r.json()["id"])
    assert fila == (usuario["id"], "sugerencia", "Me gustaría exportar en CSV", "tab-movimientos"), fila


def test_limite_de_cinco_comentarios_por_hora_por_usuario(client):
    _crear_usuario("com_limite")
    _crear_usuario("com_otro")
    h = _login(client, "com_limite")
    codigos = [client.post("/api/comentarios", json={"tipo": "otro", "mensaje": f"n{i}"}, headers=h).status_code for i in range(6)]
    assert codigos[:5] == [201] * 5 and codigos[5] == 429, codigos
    ho = _login(client, "com_otro")
    assert client.post("/api/comentarios", json={"tipo": "otro", "mensaje": "otro usuario"}, headers=ho).status_code == 201


def test_al_eliminar_la_cuenta_el_comentario_se_conserva_sin_autor(client):
    _crear_usuario("com_borrar")
    h = _login(client, "com_borrar")
    r = client.post("/api/comentarios", json={"tipo": "error", "mensaje": "el boton de guardar falla"}, headers=h)
    assert r.status_code == 201, r.text
    assert client.delete("/auth/delete-account", headers=h).status_code == 200
    fila = _fila_en_base(r.json()["id"])
    assert fila is not None and fila[0] is None and fila[2] == "el boton de guardar falla", fila


def test_el_texto_del_comentario_no_llega_al_registro(client):
    _crear_usuario("com_registro")
    h = _login(client, "com_registro")
    secreto = "comentario-confidencial-93817"
    assert client.post("/api/comentarios", json={"tipo": "otro", "mensaje": secreto}, headers=h).status_code == 201
    client.post("/api/comentarios", json={"tipo": "otro", "mensaje": secreto + "-invalido", "contexto": "x" * 200}, headers=h)
    registro = os.path.join(LOGS, "app.log")
    texto = open(registro, encoding="utf-8").read() if os.path.exists(registro) else ""
    assert secreto not in texto, "el registro de fallos guardó el texto de un comentario"


def test_script_lista_nuevos_y_marca_leidos(client):
    import importlib.util
    _crear_usuario("com_script")
    h = _login(client, "com_script")
    client.post("/api/comentarios", json={"tipo": "otro", "mensaje": "para-el-script-1"}, headers=h)
    client.post("/api/comentarios", json={"tipo": "error", "mensaje": "para-el-script-2"}, headers=h)
    spec = importlib.util.spec_from_file_location("comentarios_script", os.path.join(TMP, "scripts", "comentarios.py"))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    conn = master_db.get_master_conn()
    try:
        todos = [f["mensaje"] for f in modulo.listar(conn)]
        assert "para-el-script-1" in todos and "para-el-script-2" in todos, todos
        assert todos[0] == "para-el-script-2", todos  # más reciente primero
        modulo.marcar_leidos(conn, [f["id"] for f in modulo.listar(conn) if f["mensaje"] == "para-el-script-1"])
        nuevos = [f["mensaje"] for f in modulo.listar(conn, solo_nuevos=True)]
        assert "para-el-script-1" not in nuevos and "para-el-script-2" in nuevos, nuevos
    finally:
        conn.close()


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import ejecutar, verificar_cobertura

    with TestClient(app) as client:  # arranque de la app: crea la base maestra antes de las pruebas
        registradas = [
            (test_comentario_exige_sesion, lambda: test_comentario_exige_sesion(client)),
            (test_validacion_rechaza_tipo_longitud_y_no_repite_el_texto, lambda: test_validacion_rechaza_tipo_longitud_y_no_repite_el_texto(client)),
            (test_comentario_se_guarda_con_su_autor_y_contexto, lambda: test_comentario_se_guarda_con_su_autor_y_contexto(client)),
            (test_limite_de_cinco_comentarios_por_hora_por_usuario, lambda: test_limite_de_cinco_comentarios_por_hora_por_usuario(client)),
            (test_al_eliminar_la_cuenta_el_comentario_se_conserva_sin_autor, lambda: test_al_eliminar_la_cuenta_el_comentario_se_conserva_sin_autor(client)),
            (test_el_texto_del_comentario_no_llega_al_registro, lambda: test_el_texto_del_comentario_no_llega_al_registro(client)),
            (test_script_lista_nuevos_y_marca_leidos, lambda: test_script_lista_nuevos_y_marca_leidos(client)),
        ]
        pruebas = verificar_cobertura(sys.modules[__name__], registradas)
        codigo = ejecutar(pruebas, "test_comentarios")
    shutil.rmtree(TMP, ignore_errors=True)
    shutil.rmtree(LOGS, ignore_errors=True)
    sys.exit(codigo)
