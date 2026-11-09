#!/usr/bin/env python3
"""Quita declaraciones CSS muertas: las que una regla posterior con el mismo selector (o con `:root ` delante) y el mismo
contexto (@media...) ya sobrescribe siempre. Solo entre reglas distintas: los respaldos de una misma regla se conservan.

Uso: css_muerto.py <carpeta_css> [--aplicar]   (orden de carga: tokens, layout, componentes, vistas, tema)
"""
import re
import sys
import collections

ORDEN = ["tokens", "layout", "componentes", "vistas", "tema"]


def norm(s):
    return re.sub(r"\s+", " ", s.strip())


def saltar_cadena(t, i):
    q = t[i]
    i += 1
    while i < len(t) and t[i] != q:
        i += 2 if t[i] == "\\" else 1
    return i + 1


def parsear(t):
    """Devuelve reglas [(contexto, [selectores], [decls], inicio_cuerpo, fin_cuerpo, inicio_regla, fin_regla)]."""
    reglas = []

    def bloque(i, fin, ctx):
        while i < fin:
            # saltar espacios y comentarios
            m = re.compile(r"\s+|/\*.*?\*/", re.S).match(t, i)
            if m:
                i = m.end()
                continue
            ini = i
            j = i
            while j < fin and t[j] not in "{;":
                if t[j] in "\"'":
                    j = saltar_cadena(t, j)
                elif t[j] == "(":
                    d = 1
                    j += 1
                    while j < fin and d:
                        if t[j] in "\"'":
                            j = saltar_cadena(t, j)
                            continue
                        d += (t[j] == "(") - (t[j] == ")")
                        j += 1
                else:
                    j += 1
            if j >= fin or t[j] == ";":
                i = j + 1
                continue
            prelude = norm(re.sub(r"/\*.*?\*/", "", t[i:j], flags=re.S))
            # cuerpo balanceado
            k = j + 1
            d = 1
            while k < fin and d:
                c = t[k]
                if c in "\"'":
                    k = saltar_cadena(t, k)
                    continue
                if c == "/" and t[k:k + 2] == "/*":
                    k = t.index("*/", k) + 2
                    continue
                d += (c == "{") - (c == "}")
                k += 1
            cuerpo_ini, cuerpo_fin = j + 1, k - 1
            if prelude.startswith("@"):
                nombre = prelude.split()[0].lower()
                if nombre in ("@media", "@supports", "@layer", "@container"):
                    bloque(cuerpo_ini, cuerpo_fin, ctx + (prelude,))
                # @keyframes, @font-face, etc.: se dejan intactos
            else:
                sels = [norm(s) for s in split_top(prelude)]
                reglas.append((ctx, sels, decls(cuerpo_ini, cuerpo_fin), cuerpo_ini, cuerpo_fin, ini, k))
            i = k

    def split_top(s):
        partes, d, ult = [], 0, 0
        for n, c in enumerate(s):
            if c in "([":
                d += 1
            elif c in ")]":
                d -= 1
            elif c == "," and d == 0:
                partes.append(s[ult:n])
                ult = n + 1
        partes.append(s[ult:])
        return partes

    def decls(ini, fin):
        out = []
        i = ini
        while i < fin:
            m = re.compile(r"\s+|/\*.*?\*/", re.S).match(t, i)
            if m:
                i = m.end()
                continue
            j = i
            while j < fin and t[j] != ";":
                if t[j] in "\"'":
                    j = saltar_cadena(t, j)
                elif t[j] == "(":
                    d = 1
                    j += 1
                    while j < fin and d:
                        if t[j] in "\"'":
                            j = saltar_cadena(t, j)
                            continue
                        d += (t[j] == "(") - (t[j] == ")")
                        j += 1
                else:
                    j += 1
            fin_d = min(j + 1, fin)
            txt = t[i:j]
            if ":" in txt:
                prop, _, val = txt.partition(":")
                imp = bool(re.search(r"!\s*important\s*$", val))
                out.append((prop.strip().lower(), imp, i, fin_d))
            i = fin_d
        return out

    bloque(0, len(t), ())
    return reglas


def main():
    carpeta = sys.argv[1]
    aplicar = "--aplicar" in sys.argv
    textos = {n: open(f"{carpeta}/{n}.css", encoding="utf-8").read() for n in ORDEN}
    todas = []  # (orden global, archivo, regla)
    for n in ORDEN:
        for r in parsear(textos[n]):
            todas.append((n, r))
    # índice: (selector, prop) -> lista de (posición, contexto, importante, regla_idx)
    indice = collections.defaultdict(list)
    for pos, (n, (ctx, sels, ds, *_)) in enumerate(todas):
        for s in sels:
            for prop, imp, *_ in ds:
                indice[(s, prop)].append((pos, ctx, imp))

    def domina(pos, ctx, sel, prop, imp):
        """¿Hay una declaración posterior que gana siempre a esta?"""
        # `:root X` solo domina a `X` si el primer elemento de la cadena no puede ser <html> (lleva data-theme)
        puede_ser_html = sel[:1] in "[:*" or sel.lower().startswith("html")
        for objetivo in ((sel,) if puede_ser_html else (sel, ":root " + sel)):
            for pos2, ctx2, imp2 in indice.get((objetivo, prop), ()):
                if pos2 <= pos:
                    continue
                if ctx2 not in ((), ctx):
                    continue
                if imp and not imp2:
                    continue
                return True
        return False

    quitar = collections.defaultdict(list)
    cuenta = collections.Counter()
    for pos, (n, (ctx, sels, ds, *_)) in enumerate(todas):
        for prop, imp, a, b in ds:
            # propiedad repetida dentro de la misma regla = respaldo: se conserva
            if sum(1 for d in ds if d[0] == prop) > 1:
                continue
            if all(domina(pos, ctx, s, prop, imp) for s in sels):
                quitar[n].append((a, b))
                cuenta[n] += 1
    print("declaraciones muertas:", dict(cuenta), "total", sum(cuenta.values()))
    if not aplicar:
        return
    for n, spans in quitar.items():
        t = textos[n]
        for a, b in sorted(spans, reverse=True):
            # quitar también el salto de línea y la sangría que quedan
            ini = a
            while ini > 0 and t[ini - 1] in " \t":
                ini -= 1
            fin = b
            if fin < len(t) and t[fin] == "\n" and ini > 0 and t[ini - 1] == "\n":
                fin += 1
            t = t[:ini] + t[fin:]
        # reglas que quedaron vacías
        t = re.sub(r"[^{}]*\{\s*\}\n?", lambda m: "" if "@" not in m.group(0) else m.group(0), t)
        open(f"{carpeta}/{n}.css", "w", encoding="utf-8").write(t)


main()
