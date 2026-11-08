#!/usr/bin/env python3
"""tests/auditoria/misiones.py - Misiones por sección y mensaje de ayuda de cada episodio (plan, sección 7).

Sirve para que quien audita actúe como una persona y no como programador: cada episodio trae una misión con su persona,
su día simulado y lo que la persona quiere lograr. La misión sale de la semilla, así que el mismo episodio siempre trae
la misma.

Uso:
  python3 tests/auditoria/misiones.py --seccion movimientos --semilla 37             # el mensaje de ayuda del episodio
  python3 tests/auditoria/misiones.py --seccion metas --lista                        # las 10 misiones de una sección
  python3 tests/auditoria/misiones.py --plantilla                                    # la plantilla de hallazgo
Solo usa la biblioteca estándar.
"""

import argparse
import random
import sys

# Las personas del arnés (tests/auditoria/personas.py), con lo que cada una tiene al empezar
PERSONAS = {
    "laura": "Laura cobra 14,000 cada quincena y tiene una meta («Fondo propio») con 50,000 de ahorro que nunca pasó por la cuenta.",
    "beto": "Beto cobra 3,500 cada viernes y gasta poco y a diario: ya tiene más de cien movimientos.",
    "mara": "Mara cobra 18,500 el día 1, paga 6,500 de renta y una laptop a 12 meses y un celular a 24.",
    "ivan": "Iván vive de honorarios y tiene posiciones en dólares, pesos y euros, y dos cuentas con rendimiento.",
    "sofia": "Sofía acaba de abrir su cuenta: no tiene ni un movimiento.",
}

# (persona, día simulado, misión). Cada misión se resuelve solo con lo que se ve en pantalla.
MISIONES = {
    "login": [
        ("laura", "2026-10-01", "Olvidaste si tu contraseña lleva mayúscula: prueba hasta entrar. Después de varios fallos, ¿la pantalla te explica qué pasa?"),
        ("beto", "2026-10-01", "Te prestaron el celular: entra, mira tu resumen y sal sin dejar tu sesión abierta. Comprueba que al volver a abrir te pida contraseña."),
        ("mara", "2026-10-02", "Escribe tu usuario con mayúsculas y un espacio al final, y luego tu contraseña pegada con un espacio. ¿Entras? ¿Te dice por qué no?"),
        ("ivan", "2026-10-01", "Cambia tu contraseña desde Mi perfil y entra de nuevo con la nueva. Intenta entrar con la vieja."),
        ("sofia", "2026-10-01", "Pulsa «¿Olvidaste tu contraseña?». ¿Entiendes qué pasa si el servidor no puede mandar correos?"),
        ("laura", "2026-10-03", "Cambia el correo de tu cuenta. ¿Te pide tu contraseña? ¿Entiendes por qué?"),
        ("beto", "2026-10-05", "Abre la app en una pestaña, cierra sesión en otra y vuelve a la primera. ¿Qué ves? ¿Se entiende?"),
        ("mara", "2026-10-01", "Pega una contraseña larguísima (más de 80 caracteres) al entrar. ¿Qué pasa?"),
        ("ivan", "2026-10-07", "Manda un comentario a quien hizo la app desde el menú. ¿Te confirma que lo recibió? ¿Puedes mandar el mismo dos veces?"),
        ("sofia", "2026-10-01", "Ve a eliminar tu cuenta. Prueba con un nombre parecido y luego una contraseña equivocada; ¿qué te dice cada vez?"),
    ],
    "resumen": [
        ("laura", "2026-10-01", "¿Cuánto te queda libre hasta tu próximo cobro? Respóndelo solo con la pantalla y comprueba con papel."),
        ("beto", "2026-10-08", "¿Cuánto gastaste esta semana en comida? Encuéntralo sin salir del resumen y de la lista de movimientos."),
        ("mara", "2026-10-15", "¿Cuándo vence el próximo pago de tu laptop y cuánto es? ¿Cuenta ya en lo que te queda libre?"),
        ("ivan", "2026-10-01", "Cambia la moneda de la esquina a euros y luego a dólares. ¿Todas las cifras cambian de forma coherente?"),
        ("sofia", "2026-10-01", "Acabas de empezar: ¿la pantalla te dice qué hacer primero? ¿Algún número engaña?"),
        ("laura", "2026-10-20", "Avanza a otro periodo con la cinta y regresa. ¿Cada periodo muestra lo que esperas?"),
        ("beto", "2026-10-31", "¿Cuánto tienes en metas más inversiones? Calcúlalo con papel y compáralo con el patrimonio."),
        ("mara", "2026-10-01", "Activa el modo discreción y recorre todas las pestañas. ¿Se esconde alguna cifra que no debería verse?"),
        ("ivan", "2026-10-12", "Mira el tipo de cambio que muestra la barra. ¿Dice si es un valor de respaldo? ¿Cuánto valen tus dólares en pesos?"),
        ("laura", "2026-10-01", "Cambia a modo oscuro y claro. ¿Algo se ve cortado, ilegible o fuera de lugar?"),
    ],
    "movimientos": [
        ("beto", "2026-10-04", "Registra 10 gastos del fin de semana lo más rápido que puedas. ¿Alguno queda con la fecha o el monto equivocado?"),
        ("laura", "2026-10-06", "Encuentra todo lo que cancelaste este mes y dime cuánto suman."),
        ("mara", "2026-10-02", "Registraste dos veces el súper de ayer. Quita el repetido sin perder el otro."),
        ("ivan", "2026-10-09", "Registra un gasto más grande que tu saldo. ¿El mensaje te explica cuánto te falta y por qué?"),
        ("sofia", "2026-10-01", "Registra tu primer ingreso y tu primer gasto. ¿Todo se entiende sin ayuda?"),
        ("laura", "2026-10-10", "Corrige el monto de un gasto de hace dos semanas. ¿Cambian el resumen y la tabla?"),
        ("beto", "2026-10-12", "Borra del todo un movimiento que ya cancelaste. ¿Entiendes la diferencia entre cancelar y eliminar?"),
        ("mara", "2026-10-15", "Filtra la tabla por gastos, luego por ingresos y por cancelados. ¿Cada lista cuadra con lo que ves?"),
        ("ivan", "2026-10-16", "Quita un ingreso del que dependen gastos posteriores. ¿Te avisa? ¿Con un mensaje claro?"),
        ("laura", "2026-10-18", "Abre dos pestañas, edita el mismo movimiento en ambas y guarda las dos. ¿Qué pasa con la segunda?"),
    ],
    "fijos": [
        ("mara", "2026-10-01", "Te subieron la renta desde el mes que viene. Cámbiala sin que el mes actual cambie."),
        ("laura", "2026-10-05", "Este mes no te cobraron la suscripción: sáltala solo este mes."),
        ("beto", "2026-10-01", "Crea un gasto fijo semanal. ¿Te deja crearlo sin un ingreso fijo? ¿El mensaje lo explica?"),
        ("ivan", "2026-10-03", "Registra una compra a 6 meses sin intereses. ¿Las cuotas suman exactamente el total?"),
        ("sofia", "2026-10-01", "Crea tu ingreso fijo y luego tu primer gasto fijo. ¿Qué te dice la pantalla si lo haces al revés?"),
        ("mara", "2026-10-10", "Pausa y reactiva un fijo. ¿Cambian bien los próximos cobros?"),
        ("laura", "2026-10-15", "Elimina tu ingreso fijo teniendo gastos fijos. ¿Te lo impide? ¿Entiendes por qué?"),
        ("beto", "2026-10-20", "Cambia la fecha de inicio de un fijo al pasado. ¿Aparecen cobros que ya debieron pasar?"),
        ("ivan", "2026-10-25", "Paga una compra a meses por adelantado. ¿Cuánto queda por pagar?"),
        ("mara", "2026-10-31", "Mira tus fijos del mes y suma lo que falta por cobrarte. ¿Cuadra con el resumen?"),
    ],
    "presupuesto": [
        ("laura", "2026-10-01", "Reparte tu quincena en tarjetas y pásala a fijos; luego te arrepientes y lo vuelves a hacer."),
        ("mara", "2026-10-01", "Crea una tarjeta que supere tu ingreso. ¿Qué te dice?"),
        ("beto", "2026-10-05", "Marca una tarjeta como pagada y desmárcala. ¿Se mueve bien el resumen?"),
        ("ivan", "2026-10-08", "Exporta el presupuesto a fijos y revisa la vista previa. ¿Entiendes qué se creará, cambiará y quitará?"),
        ("sofia", "2026-10-01", "Es tu primer presupuesto: ¿puedes armarlo sin ayuda? ¿Qué te confunde?"),
        ("laura", "2026-10-10", "Renombra una tarjeta que ya exportaste y exporta otra vez. ¿Se duplica el cargo del mes?"),
        ("mara", "2026-10-12", "Divide una tarjeta en partes por quincena. ¿Suman exactamente el monto?"),
        ("beto", "2026-10-15", "Cancela la exportación en la vista previa. ¿No se tocó nada?"),
        ("ivan", "2026-10-20", "Cambia el ingreso base del presupuesto. ¿Se recalculan las tarjetas de forma entendible?"),
        ("laura", "2026-10-28", "Exporta, ve al resumen y compáralo con papel. ¿Se actualizó sin recargar la página?"),
    ],
    "metas": [
        ("laura", "2026-10-05", "Saca 2,000 del viaje para una emergencia y vuelve a meterlos el viernes."),
        ("beto", "2026-10-02", "Crea una meta de 20,000 y deposita más de lo que tienes. ¿Qué te dice?"),
        ("mara", "2026-10-10", "Retira de una meta más de lo que tiene. ¿El mensaje es claro?"),
        ("ivan", "2026-10-12", "Elimina una meta con dinero adentro. ¿Qué pasa con ese dinero?"),
        ("sofia", "2026-10-01", "Crea tu primera meta y un sobre libre. ¿Entiendes la diferencia?"),
        ("laura", "2026-10-15", "Tienes 50,000 de ahorro propio: gasta más de lo que ves en cuenta. ¿Te lo permite? ¿Debería?"),
        ("beto", "2026-10-18", "Deposita a una meta con fecha de la semana que viene. ¿Cambia lo que te queda libre hoy?"),
        ("mara", "2026-10-20", "Quita el depósito que hiciste ayer. ¿Vuelve el dinero a tu cuenta?"),
        ("ivan", "2026-10-22", "Alcanza la meta justo al centavo. ¿La pantalla lo celebra o lo maneja raro?"),
        ("laura", "2026-10-30", "Suma lo que tienes en metas con papel y compáralo con el patrimonio."),
    ],
    "inversiones": [
        ("ivan", "2026-10-01", "Agrega tus acciones en dólares y dime cuánto valen en pesos."),
        ("beto", "2026-10-03", "Registra una posición con un ticker que ya tienes. ¿Te explica que debes editarla?"),
        ("mara", "2026-10-05", "Registra una acción mexicana (.MX) sin escribir la moneda. ¿En qué moneda queda?"),
        ("laura", "2026-10-08", "Cambia la cantidad de una posición y mira la ganancia. Calcúlala con papel."),
        ("sofia", "2026-10-01", "Sin posiciones, ¿la pantalla te dice qué hacer? ¿Algo se ve roto?"),
        ("ivan", "2026-10-10", "Cambia la moneda base a euros. ¿Cada posición y el total cambian de forma coherente?"),
        ("beto", "2026-10-12", "Pulsa «Actualizar cotizaciones» sin internet. ¿Qué te dice?"),
        ("mara", "2026-10-15", "Elimina una posición y mira el peso porcentual de las demás. ¿Suman 100?"),
        ("laura", "2026-10-20", "Aporta y retira de una cuenta con rendimiento. ¿Cuánto vale hoy y cuánto ganó?"),
        ("ivan", "2026-10-31", "¿Las inversiones mueven tu dinero disponible? Compruébalo antes y después de registrar una."),
    ],
    "respaldo": [
        ("mara", "2026-10-10", "Cambias de celular: guarda todo y déjalo igual en la cuenta nueva."),
        ("beto", "2026-10-12", "Restaura una copia de hace una semana. ¿Qué pasó con lo que hiciste después?"),
        ("laura", "2026-10-15", "Intenta restaurar un archivo que no es una copia (una foto, un PDF). ¿Qué te dice? ¿Se perdió algo?"),
        ("ivan", "2026-10-18", "Exporta a Excel en pesos, dólares y euros. ¿Cada archivo lleva la moneda que pediste?"),
        ("sofia", "2026-10-01", "Descarga una copia de una cuenta vacía y restáurala. ¿Todo sigue funcionando?"),
        ("mara", "2026-10-20", "Abre el Excel: ¿los movimientos y el balance cuadran con la app?"),
        ("beto", "2026-10-22", "Escribe en una descripción «=1+1» y exporta. ¿Cómo se ve en el Excel?"),
        ("laura", "2026-10-25", "Restaura y revisa tus metas, inversiones y fijos. ¿Está todo?"),
        ("ivan", "2026-10-27", "Descarga dos copias seguidas. ¿Son iguales? ¿Cambió algo entre una y otra?"),
        ("mara", "2026-10-30", "Restaura la copia de otra persona (un archivo ajeno). ¿Te deja? ¿Debería?"),
    ],
    "admin": [
        ("laura", "2026-10-01", "Da de baja a un usuario que dejó de pagar y luego reactívalo."),
        ("beto", "2026-10-03", "Crea un usuario con una contraseña corta. ¿Qué dice? Luego con una válida."),
        ("mara", "2026-10-05", "Busca a un usuario por parte de su nombre y filtra por suspendidos."),
        ("ivan", "2026-10-07", "Cámbiale la contraseña a alguien. ¿Te pide la tuya? ¿Por qué?"),
        ("sofia", "2026-10-01", "Elimina a un usuario: escribe su nombre a medias, luego completo, luego tu contraseña mal y bien."),
        ("laura", "2026-10-10", "Crea otro administrador. ¿Qué te pide? Luego intenta suspenderlo."),
        ("beto", "2026-10-12", "Intenta suspender o borrar la cuenta principal. ¿Te explica por qué no?"),
        ("mara", "2026-10-15", "Cambia el correo de un usuario. ¿Te pide tu contraseña?"),
        ("ivan", "2026-10-18", "Mira las cifras de arriba (usuarios, suspendidos, almacenamiento). ¿Cuadran con la lista?"),
        ("sofia", "2026-10-20", "Entra como usuario normal y trata de abrir la administración. ¿Qué ves?"),
    ],
}

PLANTILLA = """### H-<número> · <título corto, en palabras de quien usa la app>
- Sección / capa / pantalla: <p. ej. Movimientos · celular · 390×844>
- Persona, día simulado, semilla, paso: <Mara · 2026-10-15 · 48213 · 14>
- Qué hice: …
- Qué esperaba: …
- Qué pasó: …
- Gravedad: bloqueante | cifra incorrecta | mensaje falso | confuso | visual | lento
- Evidencia: auditoria/corridas/<corrida>/fallos/<episodio>-<paso>-<semilla>/ (captura, base, consola) · id_peticion …
- Reproducir: tests/auditoria/run.sh --seccion <s> --capa <c> --semilla <semilla> --episodios 1
"""

REGLAS = """Recuerda: solo lo que ves en pantalla. No leas la base ni el código para decidir qué hacer.
No son errores (solo reporta si el mensaje que los explica es confuso): gastos fijos exigen ingreso fijo; no hay
frecuencia anual; el efectivo no cuenta en el patrimonio; inversiones y cuentas con rendimiento no mueven el disponible;
el ahorro propio en metas no bloquea gastos. Si dudas, eso es un hallazgo. No arregles nada durante la campaña."""


def elegir(seccion, semilla):
    """La misión del episodio: sale de la semilla, así que el mismo episodio trae siempre la misma."""
    return random.Random(f"{seccion}:{semilla}").choice(MISIONES[seccion])


def ayuda(seccion, semilla, capa="escritorio", numero=None, total=None):
    persona, dia, mision = elegir(seccion, semilla)
    cabecera = f"── Episodio {numero}/{total} · " if numero and total else "── Episodio · "
    cabecera += f"{seccion.capitalize()} · {capa} · semilla {semilla} "
    return "\n".join([cabecera.ljust(76, "─"), f"Eres {persona.upper()}. {PERSONAS[persona]}", f"Hoy es {dia}.",
                      f"Misión: {mision}", REGLAS, "─" * 76])


def main():
    parser = argparse.ArgumentParser(description="Misiones y mensaje de ayuda de la auditoría por uso real.")
    parser.add_argument("--seccion", choices=sorted(MISIONES))
    parser.add_argument("--semilla", type=int, default=1)
    parser.add_argument("--capa", choices=("api", "escritorio", "celular"), default="escritorio")
    parser.add_argument("--lista", action="store_true", help="las 10 misiones de la sección")
    parser.add_argument("--plantilla", action="store_true", help="la plantilla de hallazgo")
    args = parser.parse_args()
    if args.plantilla:
        print(PLANTILLA)
        return 0
    if not args.seccion:
        parser.error("indica --seccion (o --plantilla)")
    if args.lista:
        for i, (persona, dia, mision) in enumerate(MISIONES[args.seccion], 1):
            print(f"{i:>2}. {persona.capitalize():<6} {dia}  {mision}")
        return 0
    print(ayuda(args.seccion, args.semilla, args.capa))
    return 0


if __name__ == "__main__":
    sys.exit(main())
