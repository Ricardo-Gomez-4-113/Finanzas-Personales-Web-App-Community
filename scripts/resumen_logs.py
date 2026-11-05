"""
scripts/resumen_logs.py - Resume el registro de fallos: cuántos hay, de qué tipo, en qué rutas y los últimos,
con su id de petición para cruzarlos con la respuesta que vio el usuario (cabecera X-Request-Id).

Uso:
    python3 scripts/resumen_logs.py                 # data/logs
    python3 scripts/resumen_logs.py ruta/a/carpeta  # o un archivo concreto
    python3 scripts/resumen_logs.py --ultimos 20
"""
import argparse
import collections
import glob
import json
import os
import sys

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CARPETA_POR_DEFECTO = os.path.join(RAIZ, "data", "logs")


def archivos_en_orden(origen):
    """Del más antiguo al más reciente: app.log.5 … app.log.1, app.log."""
    if os.path.isfile(origen):
        return [origen]
    def numero(ruta):
        sufijo = ruta.rsplit(".", 1)[-1]
        return int(sufijo) if sufijo.isdigit() else 0
    rutas = glob.glob(os.path.join(origen, "app.log*"))
    return sorted(rutas, key=numero, reverse=True)


def resumir(origen, ultimos=10):
    total = malformadas = 0
    por_nivel = collections.Counter()
    por_grupo = collections.Counter()
    por_codigo = collections.Counter()
    lentas = 0
    recientes = collections.deque(maxlen=ultimos)
    for ruta in archivos_en_orden(origen):
        with open(ruta, encoding="utf-8", errors="replace") as archivo:
            for linea in archivo:
                if not linea.strip():
                    continue
                total += 1
                try:
                    registro = json.loads(linea)
                except json.JSONDecodeError:
                    malformadas += 1
                    continue
                por_nivel[registro.get("nivel", "?")] += 1
                if registro.get("evento") == "rechazo_negocio":
                    por_codigo[registro.get("codigo", "otro")] += 1
                elif registro.get("evento") == "lenta":
                    lentas += 1
                if registro.get("nivel") in ("WARNING", "ERROR", "CRITICAL"):
                    por_grupo[(registro.get("evento"), registro.get("metodo"), registro.get("ruta"), registro.get("estado"))] += 1
                    recientes.append(registro)
    return {
        "total": total,
        "malformadas": malformadas,
        "por_nivel": dict(por_nivel),
        "por_grupo": por_grupo.most_common(),
        "por_codigo": por_codigo.most_common(),
        "lentas": lentas,
        "ultimos": list(recientes),
    }


def imprimir(resultado):
    print(f"Líneas: {resultado['total']} (malformadas: {resultado['malformadas']})")
    print("Por nivel: " + ", ".join(f"{nivel} {cuenta}" for nivel, cuenta in sorted(resultado["por_nivel"].items())))
    print("\nFallos por evento, método, ruta y estado:")
    for (evento, metodo, ruta, estado), cuenta in resultado["por_grupo"] or [(("ninguno", None, None, None), 0)]:
        print(f"  {cuenta:>5}  {evento or '-':<18} {metodo or '-':<6} {ruta or '-':<40} {estado or '-'}")
    if resultado["por_codigo"] or resultado["lentas"]:
        print("\nRechazos por reglas, por código (no son fallos: la app dijo que no a propósito):")
        for codigo, cuenta in resultado["por_codigo"]:
            print(f"  {cuenta:>5}  {codigo}")
        print(f"  Respuestas lentas (más de 1.5 s): {resultado['lentas']}")
    print("\nÚltimos fallos:")
    for registro in resultado["ultimos"]:
        detalle = registro.get("excepcion") or registro.get("mensaje")
        print(f"  {registro.get('ts', '-')}  {registro.get('id_peticion', '-')}  {registro.get('evento', '-'):<15} {detalle}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resumen del registro de fallos de la aplicación.")
    parser.add_argument("origen", nargs="?", default=CARPETA_POR_DEFECTO)
    parser.add_argument("--ultimos", type=int, default=10)
    args = parser.parse_args()
    if not os.path.exists(args.origen):
        print(f"No hay registro en {args.origen}. Aún no se ha producido ningún fallo, o el servidor no ha arrancado.")
        sys.exit(0)
    imprimir(resumir(args.origen, args.ultimos))
