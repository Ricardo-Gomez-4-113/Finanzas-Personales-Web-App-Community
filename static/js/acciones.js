// Acciones declaradas en el DOM con data-on-<evento>, sin atributos on* inline (CSP sin unsafe-inline).
// El valor es una lista JSON de llamadas: [["abrirModalLegal", "terminos"], ["toggleSidebar"]].
// Los argumentos "$this", "$event", "$value" y "$checked" se sustituyen por el elemento, el evento
// y su valor o estado. Flags: data-prevent (preventDefault) y data-solo-fondo (solo si el clic es en el propio elemento).

const EVENTOS = ['click', 'change', 'submit', 'input'];

function resolverArgumento(valor, el, ev) {
    switch (valor) {
        case '$this': return el;
        case '$event': return ev;
        case '$value': return el.value;
        case '$checked': return el.checked;
        default: return valor;
    }
}

function ejecutarAccion(el, ev, atributo) {
    let llamadas;
    try {
        llamadas = JSON.parse(el.getAttribute(atributo));
    } catch (_) {
        return;
    }
    for (const [nombre, ...args] of llamadas) {
        const fn = window[nombre];
        if (typeof fn === 'function') fn(...args.map((a) => resolverArgumento(a, el, ev)));
    }
}

// Funciones que antes eran código inline de un solo uso.
Object.assign(window, {
    volverArriba() {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    enfocarTickerInversion() {
        document.getElementById('inv-ticker')?.focus();
    }
});

export function activarAccionesDelegadas() {
    for (const tipo of EVENTOS) {
        const atributo = `data-on-${tipo}`;
        document.addEventListener(tipo, (ev) => {
            // composedPath recorre desde el objetivo hacia arriba, igual que el burbujeo de los atributos inline
            for (const el of ev.composedPath()) {
                if (!(el instanceof Element) || !el.hasAttribute(atributo)) continue;
                if (el.hasAttribute('data-solo-fondo') && ev.target !== el) continue;
                if (el.hasAttribute('data-prevent')) ev.preventDefault();
                ejecutarAccion(el, ev, atributo);
            }
        });
    }
}
