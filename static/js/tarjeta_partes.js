/**
 * static/js/tarjeta_partes.js - Bloque «Tarjeta de presupuesto» de los modales de edición: una tarjeta dividida
 * se compone de cargos independientes, y aquí se ven juntos. Lo comparten movimientos y fijos.
 */

import { escapeHTML, montoMxnHTML } from './core.js';

const ICONO_TARJETA = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>';

/**
 * @param {object} datos
 * @param {string} datos.nombre Nombre de la tarjeta.
 * @param {number} datos.total Total de la tarjeta, en MXN.
 * @param {string} datos.etiquetaTotal «Total», «Total mensual»…
 * @param {string} datos.aviso Texto explicativo bajo el encabezado.
 * @param {{id: *, descripcion: string, monto: number, detalle: string}[]} datos.partes
 * @param {*} datos.actualId id de la parte que se está editando.
 */
export function bloqueTarjetaPartes({ nombre, total, etiquetaTotal, aviso, partes, actualId }) {
    const filas = partes.map(p => {
        const actual = p.id === actualId;
        return `
            <div class="tarjeta-parte${actual ? ' tarjeta-parte--actual' : ''}">
                <span class="tarjeta-parte-nombre">${escapeHTML(p.descripcion)}${actual ? ' ← (editando)' : ''}</span>
                <span class="tarjeta-parte-monto">${montoMxnHTML(p.monto)} · ${escapeHTML(p.detalle)}</span>
            </div>`;
    }).join('');
    return `
        <div class="tarjeta-partes">
            <div class="tarjeta-partes-cabecera">
                <span class="tarjeta-partes-titulo">${ICONO_TARJETA} Tarjeta de Presupuesto: ${escapeHTML(nombre)}</span>
                <span class="tarjeta-partes-total">${escapeHTML(etiquetaTotal)}: ${montoMxnHTML(total)}</span>
            </div>
            <div class="tarjeta-partes-aviso">${escapeHTML(aviso)}</div>
            <div class="tarjeta-partes-lista">${filas}</div>
        </div>`;
}
