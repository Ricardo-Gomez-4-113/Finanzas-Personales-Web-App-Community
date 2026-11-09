"""tests/auditoria/run_api.py - Capa A de la auditoría: episodios de uso de Movimientos por API.

Cada episodio es una persona (Laura) que hace entre 5 y 30 acciones elegidas con la semilla del episodio.
Después de cada acción se comprueban las invariantes (tests/auditoria/acciones.py). Lo que falla queda en
fallos/<episodio>-<paso>/ dentro de la carpeta de la corrida, con la base de la persona copiada.

Uso:
    python3 tests/auditoria/run_api.py                          # 100 episodios desde la semilla 1
    python3 tests/auditoria/run_api.py --semilla 48213 --episodios 1   # repetir un episodio concreto
    python3 tests/auditoria/run_api.py --fuente /ruta/a/otra/copia    # correr contra otro código (p. ej. uno con un error)

Se corre en una copia temporal de la app: el repo real no se toca ni se escribe en su data/.
Requiere el reloj de prueba (FINANZAS_MODO_PRUEBA=1 y FINANZAS_HOY, que pone este mismo script).
Sale con código 1 si alguna invariante se rompió.
"""

import argparse
import os
import random
import shutil
import sys
import tempfile
import time
import datetime
import types

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import acceso  # noqa: E402
import acciones  # noqa: E402
import administracion  # noqa: E402
import arnes_asistente  # noqa: E402
import copias  # noqa: E402
import fijos  # noqa: E402
import metas  # noqa: E402
import portafolio  # noqa: E402
import presupuesto  # noqa: E402
import personas  # noqa: E402
from bitacora import Bitacora, copiar_base  # noqa: E402
from oraculo import Oraculo  # noqa: E402

# Nunca se copian secretos a la copia de prueba: un .env con la clave de correo haría que «olvidé mi contraseña» mandara
# correos reales a las cuentas de prueba
SIN_SECRETOS = ".env", ".env.*", "*.pem", "*.key"
IGNORAR_EN_COPIA = shutil.ignore_patterns(
    "data", ".git", "tests", "auditoria", "__pycache__", "*.db", "backups", "venv", ".venv",
    "node_modules", "graphify-out", "docs", *SIN_SECRETOS,
)
HOY_INICIAL = datetime.date(2026, 10, 1)
PASOS_MIN, PASOS_MAX = 5, 30


COTIZACIONES = {
    "USDMXN=X": (19.5, "USD"), "EURUSD=X": (1.08, "USD"), "AAPL": (190.0, "USD"), "MSFT": (410.0, "USD"),
    "VOO": (480.0, "USD"), "WALMEX.MX": (65.0, "MXN"), "SAP.DE": (150.0, "EUR"),
}


def _cotizaciones_fijas():
    """El mercado no se consulta por internet: tasas y precios fijos. Se pone antes de importar la app,
    porque los routers toman la función por su nombre al importarse."""
    import mercado

    def consulta(ticker):
        limpio = ticker.strip().upper()
        return COTIZACIONES.get(limpio, (None, "MXN" if limpio.endswith(".MX") else "USD"))

    mercado.consultar_precio_y_moneda_mercado = consulta


def _preparar_app(fuente, carpeta_logs):
    """Copia el código a una carpeta temporal y carga la app desde ahí, con reloj de prueba."""
    tmp = tempfile.mkdtemp(prefix="auditoria_")
    shutil.copytree(fuente, tmp, dirs_exist_ok=True, ignore=IGNORAR_EN_COPIA)
    os.chdir(tmp)
    sys.path.insert(0, tmp)
    os.environ.setdefault("JWT_SECRET_KEY", "k" * 48)
    os.environ["DEFAULT_ADMIN_PASSWORD"] = "AdminAuditoria#12345"
    os.environ["FINANZAS_MODO_PRUEBA"] = "1"
    for variable in ("RESEND_API_KEY", "SMTP_USER", "SMTP_PASSWORD", "SMTP_HOST"):
        os.environ[variable] = ""  # sin correo en las pruebas, pase lo que pase con el entorno de quien las lanza
    os.environ["FINANZAS_LOG_DIR"] = carpeta_logs
    _cotizaciones_fijas()
    import database
    import main
    import master_db
    import security
    return types.SimpleNamespace(app=main.app, database=database, master_db=master_db, security=security), tmp


def _detalle(respuesta):
    try:
        cuerpo = respuesta.json()
        return str(cuerpo.get("detail", cuerpo))[:300]
    except ValueError:
        return respuesta.text[:300]


SECCIONES = {"movimientos": acciones, "fijos": fijos, "presupuesto": presupuesto, "metas": metas, "inversiones": portafolio, "respaldo": copias, "admin": administracion, "login": acceso, "asistente": arnes_asistente}


def _episodio(semilla, numero, cliente, mods, bit, fallos, catalogo, persona="laura", base_dueno=None):
    rng = random.Random(semilla)
    usuario = f"aud{semilla}"
    # Cada episodio es una persona nueva: los accesos fallidos de uno (a propósito, en Admin) no bloquean al siguiente
    mods.security._intentos_fallidos_login.clear()
    os.environ["FINANZAS_HOY"] = f"{HOY_INICIAL.isoformat()}T10:00:00"
    cabecera, ruta_db, hoy_inicial = personas.PERSONAS[persona](cliente, usuario, mods, base_dueno=base_dueno)
    os.environ["FINANZAS_HOY"] = f"{hoy_inicial.isoformat()}T10:00:00"
    oraculo = Oraculo(ruta_db)
    # Ahorro propio de cada meta: lo guardado que no viene de movimientos. Las metas nuevas del episodio empiezan en 0.
    neto = oraculo.neto_por_apartado()
    oraculo.offsets = {a[0]: a[3] - neto.get(a[0], 0) for a in oraculo.apartados()}
    metas_previas = cliente.get("/api/apartados", headers=cabecera).json()
    meta_id = metas_previas[0]["id"] if metas_previas else cliente.post(
        "/api/apartados", json={"nombre": "Meta de prueba", "tipo": "meta", "meta_monto": 10000}, headers=cabecera).json()["id"]
    ctx = catalogo.Contexto(rng, cliente, cabecera, oraculo, hoy_inicial, meta_id)
    ctx.fijar_hoy()
    if hasattr(catalogo, "preparar"):  # ajustes iniciales de la sección (p. ej. el ingreso base del presupuesto)
        catalogo.preparar(cliente, cabecera)

    pasos = rng.randint(PASOS_MIN, PASOS_MAX)
    for paso in range(1, pasos + 1):
        nombre = catalogo.elegir(rng)
        if nombre in ("cancelar", "cancelar_cuota", "eliminar", "editar", "borrar_cancelado", "saltar"):
            # Estado antes de la operación: si falla, el fallo muestra qué había antes, no solo lo que quedó
            copiar_base(ruta_db, os.path.join(bit.carpeta, "antes.db"))
        inicio = time.perf_counter()
        res = catalogo.FUNCIONES[nombre](ctx)
        if res is None:  # no había movimientos que tocar: se anota y se sigue
            res = {"entrada": {}, "respuesta": None, "pre": None, "fecha_ref": None, "candidato": None, "apartado": False}
            nota = "omitida: no había movimientos"
        else:
            nota = None
        duracion_ms = round((time.perf_counter() - inicio) * 1000)

        saldo_api = cliente.get("/api/resumen?moneda_base=MXN", headers=cabecera).json()["balance"]
        problemas = catalogo.verificar(nombre, res, oraculo, saldo_api)
        respuesta = res["respuesta"]
        datos = {
            "episodio": numero, "semilla": semilla, "persona": usuario, "paso": paso,
            "fecha_simulada": ctx.hoy.isoformat(), "accion": nombre, "entrada": res["entrada"],
            "http": respuesta.status_code if respuesta is not None else None,
            "detalle": _detalle(respuesta) if respuesta is not None and respuesta.status_code >= 300 else None,
            "saldo_api": saldo_api, "saldo_base_c": oraculo.saldo_c(),
            "invariantes_rotas": problemas, "nota": nota,
        }
        tiempos = {
            "episodio": numero, "paso": paso, "duracion_ms": duracion_ms,
            "id_peticion": respuesta.headers.get("X-Request-Id") if respuesta is not None else None,
            "carga_1m": round(os.getloadavg()[0], 2),
        }
        bit.paso(datos, tiempos)

        if problemas:
            fallos.append((numero, paso, nombre, problemas))
            carpeta_fallo = bit.fallo(f"{numero:03d}-{paso:02d}-{semilla}", datos, ruta_db)
            antes = os.path.join(bit.carpeta, "antes.db")
            if os.path.exists(antes):
                shutil.copy(antes, os.path.join(carpeta_fallo, "antes.db"))
    return pasos


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--semilla", type=int, default=1, help="semilla del primer episodio; el episodio k usa semilla+k-1")
    parser.add_argument("--episodios", type=int, default=100)
    parser.add_argument("--fuente", default=RAIZ, help="carpeta del código de la app a probar")
    parser.add_argument("--carpeta", default=None, help="donde dejar la bitácora (por defecto auditoria/corridas/...)")
    parser.add_argument("--seccion", choices=sorted(SECCIONES), default="movimientos")
    parser.add_argument("--persona", choices=sorted(personas.PERSONAS), default="laura")
    parser.add_argument("--base-dueno", default=None, help="base real del dueño que se copia en las personas dueno_* (por defecto data/tenants/user_1.db de la fuente)")
    args = parser.parse_args()

    catalogo = SECCIONES[args.seccion]
    sufijo = "" if args.persona == "laura" else f"-{args.persona}"
    nombre_corrida = f"{args.seccion}-api{sufijo}-s{args.semilla}-n{args.episodios}"
    base_dueno = args.base_dueno or os.path.join(os.path.abspath(args.fuente), "data", "tenants", "user_1.db")
    carpeta = args.carpeta or os.path.join(RAIZ, "auditoria", "corridas", nombre_corrida)
    carpeta = os.path.abspath(carpeta)
    shutil.rmtree(carpeta, ignore_errors=True)
    os.makedirs(os.path.join(carpeta, "logs"), exist_ok=True)

    mods, tmp = _preparar_app(os.path.abspath(args.fuente), os.path.join(carpeta, "logs"))
    from fastapi.testclient import TestClient
    bit = Bitacora(carpeta)
    fallos = []
    pasos_totales = 0
    try:
        with TestClient(mods.app) as cliente:
            for numero in range(1, args.episodios + 1):
                pasos_totales += _episodio(args.semilla + numero - 1, numero, cliente, mods, bit, fallos, catalogo,
                                           args.persona, base_dueno)
    finally:
        bit.cerrar()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"Corrida {nombre_corrida}: {args.episodios} episodios, {pasos_totales} acciones, carpeta {carpeta}")
    if fallos:
        print(f"FALLO: {len(fallos)} acciones con invariantes rotas")
        for numero, paso, nombre, problemas in fallos[:20]:
            print(f"  episodio {numero} paso {paso} ({nombre}): {'; '.join(problemas)}")
        return 1
    print("OK: todas las invariantes se cumplieron")
    return 0


if __name__ == "__main__":
    sys.exit(main())
