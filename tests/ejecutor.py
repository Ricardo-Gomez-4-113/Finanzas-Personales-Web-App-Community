"""tests/ejecutor.py - Ejecutor común de las suites del proyecto (sin pytest).

- Descubre las funciones `test_*` de un módulo en el orden en que están definidas.
- Corre todas aunque una falle, para ver el panorama completo en una sola corrida.
- Sale con código 1 si alguna falló, para que run_tests.sh y cualquier CI lo detecten.
"""

import sys
import time
import traceback


def descubrir(modulo):
    """Devuelve [(nombre, función)] de las pruebas del módulo, en orden de definición."""
    return [(n, f) for n, f in vars(modulo).items() if n.startswith("test_") and callable(f)]


def ejecutar(pruebas, titulo):
    """Corre cada prueba. `pruebas` es una lista de (nombre, llamable sin argumentos)."""
    fallos = []
    inicio_total = time.perf_counter()
    for nombre, prueba in pruebas:
        inicio = time.perf_counter()
        try:
            prueba()
        except Exception:
            fallos.append(nombre)
            print(f"  FALLO  {nombre}")
            traceback.print_exc()
            continue
        print(f"  ok     {nombre} ({time.perf_counter() - inicio:.2f}s)")

    total = len(pruebas)
    duracion = time.perf_counter() - inicio_total
    if fallos:
        print(f"[{titulo}] FALLO: {len(fallos)} de {total} pruebas fallaron ({duracion:.1f}s): {', '.join(fallos)}")
        return 1
    print(f"[{titulo}] OK: {total} pruebas ({duracion:.1f}s)")
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
