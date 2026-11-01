"""tests/test_limite_cuerpo.py - Tope del tamaño del cuerpo: 413 antes de leer de más, y la restauración con su propio tope.

Requiere fastapi + httpx (se ejecuta en Docker, ver tests/run_tests.sh).
Copia el código a un directorio temporal: nunca toca 'data/' del proyecto.
"""

import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

try:
    import httpx  # noqa: F401
    import fastapi  # noqa: F401
except ImportError:
    print("SKIP: requiere fastapi y httpx (ejecutar con tests/run_tests.sh)")
    sys.exit(0)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="finanzas_test_")
shutil.copytree(
    RAIZ, TMP, dirs_exist_ok=True,
    ignore=shutil.ignore_patterns("data", ".git", "tests", "__pycache__", "*.db", "backups", "venv", ".venv", "node_modules"),
)
os.chdir(TMP)
sys.path.insert(0, TMP)
os.environ.setdefault("JWT_SECRET_KEY", "k" * 48)
os.environ["DEFAULT_ADMIN_PASSWORD"] = "AdminTest#12345"

from fastapi.testclient import TestClient  # noqa: E402

import limite_cuerpo  # noqa: E402
import master_db  # noqa: E402
import security  # noqa: E402
from main import app  # noqa: E402

PASS = "Passw0rd#Test1"
KB = 1024
MB = 1024 * KB


def _crear_usuario(nombre):
    return master_db.create_user_entry(nombre, security.hash_password(PASS), email=f"{nombre}@test.local")


def _login(client, usuario, clave=PASS):
    r = client.post("/auth/login", json={"username": usuario, "password": clave})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _trozos(total, tamano=64 * KB):
    """Cuerpo que llega por partes y sin Content-Length (transferencia chunked)."""
    restante = total
    while restante > 0:
        n = min(tamano, restante)
        restante -= n
        yield b"a" * n


def _multipart_trozos(total):
    """Archivo multipart sin Content-Length: la ruta de restauración lo lee por partes."""
    yield b'--limite\r\nContent-Disposition: form-data; name="file"; filename="respaldo.db"\r\nContent-Type: application/octet-stream\r\n\r\n'
    yield from _trozos(total)
    yield b"\r\n--limite--\r\n"


@contextmanager
def _limite_restauracion(bytes_):
    original = limite_cuerpo.LIMITE_RESTAURACION_BYTES
    limite_cuerpo.LIMITE_RESTAURACION_BYTES = bytes_
    try:
        yield
    finally:
        limite_cuerpo.LIMITE_RESTAURACION_BYTES = original


def test_cuerpo_con_content_length_excesivo_se_rechaza_sin_leerlo(client):
    """Un cuerpo declarado por encima del tope recibe 413 sin pasar por la ruta."""
    r = client.post("/auth/login", content=b"x" * (300 * KB), headers={"Content-Type": "application/json"})
    assert r.status_code == 413, r.text
    assert r.json() == {"detail": limite_cuerpo.MENSAJE_EXCEDIDO}, r.text


def test_cuerpo_chunked_que_supera_el_tope_recibe_413(client):
    """Sin Content-Length, el tope se aplica a los bytes que llegan."""
    r = client.post("/auth/login", content=_trozos(300 * KB), headers={"Content-Type": "application/json"})
    assert r.status_code == 413, r.text


def test_peticion_normal_no_se_ve_afectada(client):
    _crear_usuario("limite_normal")
    r = client.post("/auth/login", json={"username": "limite_normal", "password": PASS})
    assert r.status_code == 200, r.text
    # Cuerpo de 200 KB: entra en el tope general y lo rechaza la validación del campo (máximo 72), no el tamaño
    r = client.post("/auth/login", json={"username": "limite_normal", "password": "x" * (200 * KB)})
    assert r.status_code == 422, r.text


def test_restauracion_acepta_un_archivo_mayor_que_el_tope_general(client):
    """/api/restore admite más que el tope general: un archivo de 1 MB llega a la validación del respaldo."""
    _crear_usuario("limite_restaura")
    h = _login(client, "limite_restaura")
    r = client.post("/api/restore", headers=h, files={"file": ("respaldo.db", b"x" * MB, "application/octet-stream")})
    assert r.status_code == 400, r.text  # no es SQLite: lo rechaza la validación, no el tope de tamaño
    assert "SQLite" in r.json()["detail"], r.text


def test_restauracion_por_encima_de_su_tope_recibe_413(client):
    _crear_usuario("limite_restaura_grande")
    h = _login(client, "limite_restaura_grande")
    with _limite_restauracion(2 * MB):
        r = client.post("/api/restore", headers={**h, "Content-Type": "multipart/form-data; boundary=limite"}, content=_multipart_trozos(3 * MB))
    assert r.status_code == 413, r.text


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import ejecutar, verificar_cobertura

    registradas = [
        (test_cuerpo_con_content_length_excesivo_se_rechaza_sin_leerlo, lambda: test_cuerpo_con_content_length_excesivo_se_rechaza_sin_leerlo(client)),
        (test_cuerpo_chunked_que_supera_el_tope_recibe_413, lambda: test_cuerpo_chunked_que_supera_el_tope_recibe_413(client)),
        (test_peticion_normal_no_se_ve_afectada, lambda: test_peticion_normal_no_se_ve_afectada(client)),
        (test_restauracion_acepta_un_archivo_mayor_que_el_tope_general, lambda: test_restauracion_acepta_un_archivo_mayor_que_el_tope_general(client)),
        (test_restauracion_por_encima_de_su_tope_recibe_413, lambda: test_restauracion_por_encima_de_su_tope_recibe_413(client)),
    ]
    with TestClient(app) as client:  # arranque de la app: crea la base maestra antes de las pruebas
        pruebas = verificar_cobertura(sys.modules[__name__], registradas)
        codigo = ejecutar(pruebas, "test_limite_cuerpo")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)
