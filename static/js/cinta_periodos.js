/**
 * static/js/cinta_periodos.js - Cinta de periodos de cobro: lo libre de cada periodo, lado a lado.
 *
 * Recibe los periodos de /api/recurrentes/periodos (totales ya calculados en finanzas_core.py).
 * Solo pinta: el libre, el saldo y los fijos vienen del backend.
 */

import { fmt } from './core.js';

const el = (tag, clase, texto) => {
    const nodo = document.createElement(tag);
    if (clase) nodo.className = clase;
    if (texto !== undefined) nodo.textContent = texto;
    return nodo;
};

const fechaCorta = iso => new Intl.DateTimeFormat('es-MX', { day: 'numeric', month: 'short', timeZone: 'UTC' })
    .format(new Date(`${iso}T00:00:00Z`)).replace('.', '');

const dias = p => Math.round((new Date(`${p.fin}T00:00:00Z`) - new Date(`${p.inicio}T00:00:00Z`)) / 86400000) + 1;

// Ancho de cada segmento como parte del ingreso del periodo. Lo que no cubren fijos, MSI ni libre son metas.
const porcentaje = (monto, ingreso) => `${Math.min(100, Math.max(0, (monto / ingreso) * 100)).toFixed(2)}%`;

function barra(t) {
    const caja = el('span', 'cinta-barra');
    const segmentos = [
        ['cinta-seg-fijos', Math.max(0, t.fijos)],
        ['cinta-seg-msi', Math.max(0, t.msi)],
        ['cinta-seg-libre', Math.max(0, t.libre)],
    ];
    segmentos.forEach(([clase, monto], i) => {
        const seg = el('span', `cinta-seg ${clase}`);
        seg.style.width = porcentaje(monto, t.ingresos);
        seg.style.animationDelay = `${i * 90}ms`;
        caja.appendChild(seg);
    });
    return caja;
}

function celda(p, indice, seleccionado, onSeleccionar) {
    const t = p.totales;
    const boton = el('button', 'cinta-periodo');
    boton.type = 'button';
    boton.style.setProperty('--dias', String(dias(p)));
    boton.dataset.indice = String(indice);
    boton.setAttribute('aria-pressed', String(seleccionado));
    if (p.en_curso) boton.classList.add('cinta-periodo-actual');
    if (!p.en_curso) boton.classList.add('cinta-periodo-futuro');
    if (t.saldo_al_cierre < 0) boton.classList.add('cinta-periodo-riesgo');

    boton.append(
        el('span', 'cinta-periodo-rango', `${fechaCorta(p.inicio)} – ${fechaCorta(p.fin)}`),
        el('span', 'cinta-periodo-estado', p.en_curso ? 'En curso' : 'Proyectado'),
        el('strong', 'cinta-periodo-libre', fmt(t.saldo_al_cierre)),
        el('span', 'cinta-periodo-etiqueta', 'Saldo al cierre'),
    );
    if (t.ingresos > 0) {
        boton.append(barra(t));
    } else {
        boton.append(el('span', 'cinta-periodo-sin-ingreso', 'Sin cobro en este periodo'));
    }
    boton.append(el('span', 'cinta-periodo-ingreso', t.ingresos > 0 ? `Ingreso ${fmt(t.ingresos)}` : ''));

    boton.addEventListener('click', () => onSeleccionar(indice));
    return boton;
}

/**
 * Pinta la cinta dentro de `raiz`.
 * opciones: { seleccionado: índice activo, onSeleccionar(indice) }
 */
export function renderCintaPeriodos(raiz, periodos, opciones = {}) {
    if (!raiz) return;
    const { seleccionado = periodos.findIndex(p => p.en_curso), onSeleccionar = () => {} } = opciones;
    raiz.replaceChildren();
    if (!periodos.length) return;

    const pista = el('div', 'cinta-periodos');
    pista.setAttribute('role', 'group');
    pista.setAttribute('aria-label', 'Periodos de cobro');
    periodos.forEach((p, i) => pista.appendChild(celda(p, i, i === seleccionado, onSeleccionar)));
    // La entrada de las barras corre una sola vez; los cambios de periodo no la repiten
    if (!raiz.dataset.animada) {
        pista.classList.add('cinta-animar');
        raiz.dataset.animada = '1';
    }

    // Las flechas recorren los periodos, como pide el patrón de botones en grupo
    pista.addEventListener('keydown', e => {
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
        const botones = [...pista.querySelectorAll('.cinta-periodo')];
        const actual = botones.indexOf(document.activeElement);
        if (actual < 0) return;
        const siguiente = botones[(actual + (e.key === 'ArrowRight' ? 1 : -1) + botones.length) % botones.length];
        siguiente.focus();
        e.preventDefault();
    });

    raiz.appendChild(pista);
}
