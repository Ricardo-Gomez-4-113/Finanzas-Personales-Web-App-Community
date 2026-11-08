#!/usr/bin/env python3
"""tests/auditoria/informe.py - Informe en Markdown de las corridas de la auditoría por uso real.

Lee `auditoria/corridas/*/` (bitacora.jsonl, tiempos.jsonl, logs/app.log y fallos/*/fallo.json) y escribe un informe con:
- una fila por corrida (sección, capa, persona, episodios, pasos, violaciones y estado),
- las violaciones agrupadas por causa probable, con el comando exacto para reproducir un caso de cada grupo,
- los 10 pasos más lentos (marcando los que se midieron con la máquina cargada),
- lo que registró el servidor: eventos, rechazos de negocio por código, respuestas lentas y 5xx,
- los errores de consola del navegador.

Por defecto solo cuenta la corrida más reciente de cada sección, capa y persona (las anteriores suelen ser de antes de un
arreglo); `--todas` las incluye.

Uso: python3 tests/auditoria/informe.py [--corridas auditoria/corridas] [--salida auditoria/informe.md] [--solo texto] [--todas]
Solo usa la biblioteca estándar y no toca ninguna base ni ninguna corrida.
"""

import argparse
import collections
import glob
import json
import os
import re
import sys

NOMBRE = re.compile(r"^(?P<seccion>[a-z]+)-(?P<capa>api|escritorio|celular)(?:-(?P<persona>[a-z_]+))?-s(?P<semilla>\d+)-n(?P<n>\d+)$")
CARGA_ALTA = 2.0  # por encima, un paso lento puede ser de la máquina y no de la app
UMBRAL_LENTO_MS = 1500
# 5xx que el servidor devuelve a propósito y no son un fallo: sin correo configurado, «olvidé mi contraseña» responde 503
ESPERADOS_5XX = {("POST", "/auth/forgot-password", 503): "sin correo configurado en las pruebas (previsto)"}


def _lineas(ruta):
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8") as archivo:
        for linea in archivo:
            linea = linea.strip()
            if not linea:
                continue
            try:
                yield json.loads(linea)
            except ValueError:
                continue


def causa(mensaje):
    """Mensaje de una violación sin lo que cambia entre casos (números, nombres entre «», ids), para agrupar."""
    texto = re.sub(r"«[^»]*»", "«…»", mensaje)
    texto = re.sub(r"\b\d+(?:\.\d+)?\b", "N", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:150]


def comando(corrida, episodio):
    """Línea para reproducir un episodio: el episodio k usa la semilla s + k - 1."""
    partes = ["tests/auditoria/run.sh", f"--seccion {corrida['seccion']}", f"--capa {corrida['capa']}"]
    if corrida["persona"]:
        partes.append(f"--persona {corrida['persona']}")
    partes += [f"--semilla {corrida['semilla'] + episodio - 1}", "--episodios 1"]
    return " ".join(partes)


def leer_corrida(carpeta):
    base = os.path.basename(carpeta.rstrip("/"))
    m = NOMBRE.match(base)
    if not m:
        return None
    corrida = {
        "nombre": base, "ruta": carpeta, "seccion": m["seccion"], "capa": m["capa"], "persona": m["persona"] or "",
        "semilla": int(m["semilla"]), "n": int(m["n"]), "pasos": 0, "episodios": set(), "violaciones": [],
        "omitidas": 0, "tiempos": {}, "servidor": [], "consola": [],
    }
    for fila in _lineas(os.path.join(carpeta, "bitacora.jsonl")):
        corrida["pasos"] += 1
        corrida["episodios"].add(fila.get("episodio"))
        if fila.get("nota"):
            corrida["omitidas"] += 1
        for texto in fila.get("invariantes_rotas") or []:
            corrida["violaciones"].append({"episodio": fila.get("episodio"), "paso": fila.get("paso"),
                                           "accion": fila.get("accion"), "texto": texto})
    for fila in _lineas(os.path.join(carpeta, "tiempos.jsonl")):
        corrida["tiempos"][(fila.get("episodio"), fila.get("paso"))] = (fila.get("duracion_ms") or 0, fila.get("carga_1m") or 0)
    for ruta in glob.glob(os.path.join(carpeta, "logs", "app.log*")):
        corrida["servidor"].extend(_lineas(ruta))
    for ruta in glob.glob(os.path.join(carpeta, "fallos", "*", "fallo.json")):
        try:
            with open(ruta, encoding="utf-8") as archivo:
                datos = json.load(archivo)
        except ValueError:
            continue
        for error in datos.get("errores_navegador") or []:
            corrida["consola"].append((os.path.basename(os.path.dirname(ruta)), error.splitlines()[0][:160]))
    return corrida


def generar(corridas):
    salida = []
    p = salida.append
    total_pasos = sum(c["pasos"] for c in corridas)
    total_viol = sum(len(c["violaciones"]) for c in corridas)
    p("# Informe de la auditoría por uso real\n")
    p(f"{len(corridas)} corridas, {total_pasos} acciones, **{total_viol} violaciones**.\n")

    p("## Corridas\n")
    p("| Sección | Capa | Persona | Semilla | Episodios | Acciones | Omitidas | Violaciones | Estado |")
    p("|---|---|---|---|---|---|---|---|---|")
    for c in sorted(corridas, key=lambda c: (c["seccion"], c["capa"], c["persona"], c["semilla"], c["n"])):
        estado = "OK" if not c["violaciones"] else "FALLA"
        p(f"| {c['seccion']} | {c['capa']} | {c['persona'] or 'laura'} | {c['semilla']} | {len(c['episodios'])} de {c['n']} | "
          f"{c['pasos']} | {c['omitidas']} | {len(c['violaciones'])} | {estado} |")
    p("")

    p("## Violaciones por causa probable\n")
    grupos = collections.defaultdict(list)
    for c in corridas:
        for v in c["violaciones"]:
            grupos[(c["seccion"], c["capa"], v["accion"], causa(v["texto"]))].append((c, v))
    if not grupos:
        p("Ninguna.\n")
    for (seccion, capa, accion, texto), casos in sorted(grupos.items(), key=lambda kv: -len(kv[1])):
        c, v = casos[0]
        p(f"- **{len(casos)}×** {seccion} / {capa} / `{accion}`: {texto}")
        p(f"  - Ejemplo: episodio {v['episodio']} paso {v['paso']} de `{c['nombre']}`")
        p(f"  - Reproducir: `{comando(c, v['episodio'])}`")
    p("")

    p("## Los 10 pasos más lentos\n")
    lentos = []
    for c in corridas:
        for (episodio, paso), (ms, carga) in c["tiempos"].items():
            lentos.append((ms, carga, c["nombre"], episodio, paso))
    lentos.sort(reverse=True)
    if not lentos:
        p("Sin tiempos registrados.\n")
    else:
        p("| ms | Carga | Corrida | Episodio | Paso | |")
        p("|---|---|---|---|---|---|")
        for ms, carga, nombre, episodio, paso in lentos[:10]:
            nota = "máquina cargada: puede no ser de la app" if carga >= CARGA_ALTA else ("lento" if ms > UMBRAL_LENTO_MS else "")
            p(f"| {ms} | {carga} | {nombre} | {episodio} | {paso} | {nota} |")
        p("")

    p("## Lo que registró el servidor\n")
    eventos, codigos, lentas, cincox, esperados = collections.Counter(), collections.Counter(), [], [], collections.Counter()
    for c in corridas:
        for e in c["servidor"]:
            eventos[e.get("evento", "?")] += 1
            if e.get("evento") == "rechazo_negocio":
                codigos[(e.get("codigo") or "sin código", e.get("estado"))] += 1
            if e.get("evento") == "lenta":
                lentas.append((e.get("duracion_ms") or 0, e.get("metodo"), e.get("ruta"), c["nombre"]))
            if (e.get("estado") or 0) >= 500:
                clave = (e.get("metodo"), e.get("ruta"), e.get("estado"))
                if clave in ESPERADOS_5XX:
                    esperados[clave] += 1
                else:
                    cincox.append((c["nombre"], e.get("estado"), e.get("metodo"), e.get("ruta"), e.get("id_peticion")))
    if eventos:
        p("Eventos: " + ", ".join(f"{k} {v}" for k, v in eventos.most_common()) + "\n")
    if codigos:
        p("Rechazos de negocio por código:\n")
        for (codigo, estado), n in codigos.most_common(15):
            p(f"- `{codigo}` ({estado}): {n}")
        p("")
    if lentas:
        p("Respuestas lentas del servidor (las 5 peores):\n")
        for ms, metodo, ruta, nombre in sorted(lentas, reverse=True)[:5]:
            p(f"- {ms} ms {metodo} {ruta} ({nombre})")
        p("")
    p(f"Respuestas 5xx sin explicar: **{len(cincox)}**" + ("" if not cincox else " (explicar cada una)") + "\n")
    for (metodo, ruta, estado), n in esperados.items():
        p(f"- Previstas, {n}× {estado} {metodo} {ruta}: {ESPERADOS_5XX[(metodo, ruta, estado)]}")
    if esperados:
        p("")
    for nombre, estado, metodo, ruta, id_peticion in cincox[:20]:
        p(f"- {estado} {metodo} {ruta} id `{id_peticion}` en {nombre}")
    if cincox:
        p("")

    p("## Errores de consola del navegador\n")
    vistos = collections.Counter(texto for c in corridas for _, texto in c["consola"])
    if not vistos:
        p("Ninguno en los casos que fallaron.\n")
    for texto, n in vistos.most_common(10):
        p(f"- {n}× {texto}")
    return "\n".join(salida) + "\n"


def main():
    raiz = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser = argparse.ArgumentParser(description="Informe de las corridas de la auditoría.")
    parser.add_argument("--corridas", default=os.path.join(raiz, "auditoria", "corridas"))
    parser.add_argument("--salida", default=os.path.join(raiz, "auditoria", "informe.md"))
    parser.add_argument("--solo", default="", help="solo las corridas cuyo nombre contiene este texto")
    parser.add_argument("--todas", action="store_true", help="incluir también las corridas anteriores de la misma sección, capa y persona")
    args = parser.parse_args()
    corridas = [c for c in (leer_corrida(d) for d in sorted(glob.glob(os.path.join(args.corridas, "*")))) if c]
    if args.solo:
        corridas = [c for c in corridas if args.solo in c["nombre"]]
    if not args.todas:
        recientes = {}
        for c in corridas:
            c["mtime"] = os.path.getmtime(os.path.join(c["ruta"], "bitacora.jsonl")) if os.path.exists(os.path.join(c["ruta"], "bitacora.jsonl")) else 0
            clave = (c["seccion"], c["capa"], c["persona"])
            if clave not in recientes or c["mtime"] > recientes[clave]["mtime"]:
                recientes[clave] = c
        corridas = list(recientes.values())
    if not corridas:
        print("No hay corridas que informar.", file=sys.stderr)
        return 1
    texto = generar(corridas)
    with open(args.salida, "w", encoding="utf-8") as archivo:
        archivo.write(texto)
    print(f"Informe: {args.salida} ({len(corridas)} corridas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
