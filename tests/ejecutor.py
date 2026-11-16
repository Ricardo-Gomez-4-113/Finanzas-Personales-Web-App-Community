"""tests/ejecutor.py - Ejecutor común de las suites del proyecto (sin pytest).

- Descubre las funciones `test_*` de un módulo en el orden en que están definidas.
- Corre todas aunque una falle, para ver el panorama completo en una sola corrida.
- Sale con código 1 si alguna falló, para que run_tests.sh y cualquier CI lo detecten.
"""

import os
import sys
import time
import traceback


def descubrir(modulo):
    """Devuelve [(nombre, función)] de las pruebas del módulo, en orden de definición."""
    return [(n, f) for n, f in vars(modulo).items() if n.startswith("test_") and callable(f)]


def ejecutar(pruebas, titulo):
    """Corre cada prueba. `pruebas` es una lista de (nombre, llamable sin argumentos)."""
    solo = [t for t in os.environ.get("SOLO", "").split(",") if t]
    if solo:  # SOLO=texto1,texto2 corre únicamente las pruebas cuyo nombre contiene alguno
        pruebas = [(n, p) for n, p in pruebas if any(t in n for t in solo)]
    fallos, intermitentes = [], []
    reintentos = int(os.environ.get("REINTENTOS", "0") or 0)   # solo la suite del navegador los usa (run_frontend.sh)
    inicio_total = time.perf_counter()
    for nombre, prueba in pruebas:
        inicio = time.perf_counter()
        primer_error = None
        for intento in range(reintentos + 1):
            try:
                prueba()
            except Exception as error:
                if intento < reintentos:
                    primer_error = primer_error or f"{type(error).__name__}: {str(error).splitlines()[0][:120] if str(error) else ''}"
                    print(f"  REINTENTO  {nombre} (falló: {primer_error})")
                    continue
                fallos.append(nombre)
                print(f"  FALLO  {nombre}")
                traceback.print_exc()
                break
            else:
                if primer_error:
                    intermitentes.append(f"{nombre} [{primer_error}]")
                    print(f"  INTERMITENTE  {nombre} (falló una vez y pasó al repetirse) ({time.perf_counter() - inicio:.2f}s)")
                else:
                    print(f"  ok     {nombre} ({time.perf_counter() - inicio:.2f}s)")
                break

    total = len(pruebas)
    duracion = time.perf_counter() - inicio_total
    if fallos:
        print(f"[{titulo}] FALLO: {len(fallos)} de {total} pruebas fallaron ({duracion:.1f}s): {', '.join(fallos)}")
        return 1
    extra = f" | INTERMITENTES: {len(intermitentes)}" if intermitentes else ""
    print(f"[{titulo}] OK: {total} pruebas ({duracion:.1f}s){extra}")
    for i in intermitentes:
        print(f"  ! intermitente: {i}")
    return 0


def ejecutar_modulo(modulo, titulo):
    """Atajo para módulos cuyas pruebas no necesitan argumentos."""
    return ejecutar(descubrir(modulo), titulo)


def verificar_cobertura(modulo, registradas):
    """Falla si el módulo define una función test_* que no está registrada.
    `registradas` es una lista de (función_de_prueba, llamable_sin_argumentos): así una prueba
    nueva no se queda sin ejecutar por olvidarse de agregarla a la lista."""
    definidas = {n for n, _ in descubrir(modulo)}
    usadas = {fn.__name__ for fn, _ in registradas}
    faltan = sorted(definidas - usadas)
    if faltan:
        print(f"ERROR: pruebas definidas pero no registradas para ejecutarse: {', '.join(faltan)}")
        sys.exit(1)
    return [(fn.__name__, llamable) for fn, llamable in registradas]
