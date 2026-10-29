/**
 * static/js/presupuesto.js - Presupuesto por pagos del periodo
 * Cada pago (semana, quincena o mes, según la frecuencia del ingreso) tiene su día de cobro,
 * su distribución del ingreso y sus tarjetas. Al exportar, cada tarjeta de un pago es un fijo mensual en su día.
 */

import { apiFetch, escapeHTML, fmt, formatErrorDetail, toast } from './core.js';

let cachePresupuesto = null;
// Pago al que pertenece la tarjeta del modal (null cuando se edita fuera de un pago)
let pagoTarjetaEditando = null;
let idTarjetaEditando = null;
let guardandoTarjeta = false;

const ICONOS_SVG = {
    'home': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>`,
    'trending-up': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>`,
    'shield': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
    'credit-card': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/></svg>`,
    'smile': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>`,
    'book': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`,
    'heart': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>`,
    'shopping-bag': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>`,
    'wallet': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a8 8 0 0 1-8 8H5a2 2 0 0 1-2-2V7"/><circle cx="18" cy="14" r="1"/></svg>`
};

const FRECUENCIA_LABEL = {
    'mensual': 'al mes',
    'quincenal': 'por quincena',
    'semanal': 'por semana'
};


export async function cargarPresupuesto() {
    try {
        const res = await apiFetch('/api/presupuesto');
        if (!res.ok) return;
        const data = await res.json();
        cachePresupuesto = data;
        renderizarVistaPresupuesto(data);
    } catch (e) {
        console.error('Error al cargar presupuesto:', e);
    }
}


function renderizarVistaPresupuesto(data) {
    const cfg = data.config;
    const res = data.resumen;
    const tarjetas = data.tarjetas || [];
    const fijos = data.fijos_detectados || {};

    // 1. Inputs de configuración de ingreso base y frecuencia
    const inpIngreso = document.getElementById('pres-ingreso-base');
    const selFrecuencia = document.getElementById('pres-frecuencia');
    if (inpIngreso && document.activeElement !== inpIngreso) inpIngreso.value = cfg.ingreso_base || '';
    if (selFrecuencia && document.activeElement !== selFrecuencia) selFrecuencia.value = cfg.frecuencia || 'mensual';

    // Equivalencias de ingreso en el subtítulo
    const lblEq = document.getElementById('pres-ingreso-equivalentes');
    if (lblEq && cfg.equivalentes) {
        const eq = cfg.equivalentes;
        lblEq.textContent = `Equivalente: ${fmt(eq.mensual)} mensual · ${fmt(eq.quincenal)} quincenal · ${fmt(eq.semanal)} semanal`;
    }

    // Alerta contextual de datos detectados en Fijos y MSI
    const boxFijos = document.getElementById('pres-fijos-alert');
    if (boxFijos) {
        const hayFijos = (fijos.ingreso_fijo_periodo > 0 || fijos.gasto_fijo_periodo > 0 || fijos.cuota_msi_periodo > 0);
        const yaSincronizadoIngreso = (cfg.ingreso_base > 0 && Math.abs(cfg.ingreso_base - fijos.ingreso_fijo_periodo) < 1.0);
        const bannerDescartado = (localStorage.getItem('presupuesto_fijos_banner_dismissed') === 'true' || sessionStorage.getItem('presupuesto_fijos_banner_dismissed') === 'true');
        const yaTieneTarjetas = (tarjetas && tarjetas.length > 0);
        const mostrar = hayFijos && !bannerDescartado && (!yaSincronizadoIngreso || !yaTieneTarjetas);
        boxFijos.classList.toggle('hidden', !mostrar);
        if (mostrar) {
            const lblTxt = document.getElementById('pres-fijos-alert-text');
            if (lblTxt) {
                lblTxt.innerHTML = `Detectado en Fijos y MSI: Nómina de <strong>${fmt(fijos.ingreso_fijo_periodo)}</strong> y gastos/MSI de <strong>${fmt(fijos.gasto_fijo_periodo + fijos.cuota_msi_periodo)}</strong> (${cfg.frecuencia}).`;
            }
        }
    }

    // 2. Totales del plan en una línea y bloques de pago con su distribución
    renderTotales(res);
    renderPagos(data);
}

// Totales del plan: suman todos los pagos activos y se muestran en una sola línea
function renderTotales(res) {
    const el = document.getElementById('lbl-pres-totales');
    if (!el) return;
    el.textContent = `Ingreso ${fmt(res.ingreso_total)} · Asignado ${fmt(res.total_asignado_monto)} · Sin asignar ${fmt(res.disponible_monto)}`;
    el.classList.toggle('pres-totales-excedido', Boolean(res.excedido));
}

function nombrePago(orden, frecuencia) {
    if (frecuencia === 'semanal') return `Semana ${orden}`;
    if (frecuencia === 'quincenal') return `Quincena ${orden}`;
    return 'Pago mensual';
}

// Distribución del ingreso de un pago: cada tarjeta es un tramo, y lo que queda es el libre
function renderDistribucionPago(u) {
    if (u.ingreso <= 0) {
        return '<p class="pres-pago-vacio">Define tu ingreso fijo base para repartirlo entre tus tarjetas.</p>';
    }
    const tramos = u.tarjetas.filter(t => t.monto > 0).map(t => ({ t, pct: (t.monto / u.ingreso) * 100 }));
    const sumaPct = tramos.reduce((acc, tr) => acc + tr.pct, 0);
    // Si el pago está excedido, los tramos se reescalan para que quepan en la barra
    const escala = sumaPct > 100 ? 100 / sumaPct : 1;
    const librePct = Math.max(0, 100 - sumaPct);
    const libreMonto = Math.max(0, u.ingreso - u.total_asignado);

    const slices = tramos.map(({ t, pct }) => `
        <div class="pres-alloc-slice pres-color-${escapeHTML(t.color)}" style="width: ${(pct * escala).toFixed(2)}%" title="${escapeHTML(t.nombre)}: ${pct.toFixed(1)}% (${fmt(t.monto)})"></div>`).join('');
    const leyenda = tramos.map(({ t, pct }) => `
        <span class="pres-legend-item"><span class="pres-legend-dot pres-color-${escapeHTML(t.color)}"></span><span>${escapeHTML(t.nombre)}: <strong>${pct.toFixed(1)}%</strong> (${fmt(t.monto)})</span></span>`).join('');
    const sliceLibre = librePct > 0 ? `<div class="pres-alloc-slice pres-slice-libre" style="width: ${librePct.toFixed(2)}%" title="Por asignar: ${librePct.toFixed(1)}% (${fmt(libreMonto)})"></div>` : '';
    const legendLibre = librePct > 0 ? `<span class="pres-legend-item pres-legend-libre"><span class="pres-legend-dot pres-color-libre"></span><span>Por asignar: <strong>${librePct.toFixed(1)}%</strong> (${fmt(libreMonto)})</span></span>` : '';

    return `
        <div class="pres-pago-dist">
            <div class="pres-pago-dist-head"><span>Distribución del ingreso</span>${u.excedido ? '<span class="pres-pago-excedido">Excedido</span>' : ''}</div>
            <div class="pres-alloc-track-wrap"><div class="pres-alloc-track">${slices}${sliceLibre}</div></div>
            <div class="pres-alloc-legend">${leyenda}${legendLibre}</div>
        </div>`;
}

// Un bloque por pago activo: día de cobro, distribución del ingreso y tarjetas de ese pago
function renderPagos(data) {
    const lista = document.getElementById('pres-pagos-lista');
    if (!lista) return;
    const cfg = data.config;
    const activos = (data.unidades || []).filter(u => u.activa);
    lista.innerHTML = '';

    activos.forEach(u => {
        const nombre = nombrePago(u.orden, cfg.frecuencia);
        const opcionesDia = ['<option value="">Sin fecha</option>']
            .concat(Array.from({ length: 31 }, (_, i) => `<option value="${i + 1}" ${u.dia_pago === i + 1 ? 'selected' : ''}>Día ${i + 1}</option>`))
            .join('');
        const bloque = document.createElement('section');
        bloque.className = 'card pres-pago';
        bloque.innerHTML = `
            <div class="pres-pago-head">
                <div class="pres-pago-titulo">
                    <h4>${escapeHTML(nombre)}</h4>
                    <span>Ingreso de este pago: ${fmt(u.ingreso)}</span>
                </div>
                <label class="pres-pago-dia-label">Día de cobro
                    <select class="select-calc pres-pago-dia" aria-label="Día de cobro de ${escapeHTML(nombre)}">${opcionesDia}</select>
                </label>
            </div>
            ${renderDistribucionPago(u)}
            <div class="pres-pago-stats">
                <span>Asignado: <strong>${fmt(u.total_asignado)}</strong></span>
                <span>Libre: <strong>${fmt(u.libre)}</strong></span>
            </div>
            <div class="grid-presupuesto pres-pago-grid"></div>
            <button type="button" class="btn-secondary pres-pago-add">Añadir tarjeta a ${escapeHTML(nombre)}</button>
        `;
        bloque.querySelector('.pres-pago-dia').addEventListener('change', (e) => guardarDiaPago(u.orden, e.target.value));
        bloque.querySelector('.pres-pago-add').addEventListener('click', () => abrirModalCrearTarjeta(u.orden));
        renderGridTarjetas(u, bloque.querySelector('.pres-pago-grid'));
        lista.appendChild(bloque);
    });
}

// Tarjetas de un pago: el monto se compara con el ingreso de ese pago y el día es el suyo
function renderGridTarjetas(pago, grid) {
    if (pago.tarjetas.length === 0) {
        grid.innerHTML = `<p class="pres-pago-vacio">Sin tarjetas en este pago.</p>`;
        return;
    }

    grid.innerHTML = '';
    pago.tarjetas.forEach(t => {
        const card = document.createElement('div');
        card.className = `card pres-card pres-card-accent-${escapeHTML(t.color)}`;
        const iconSvg = ICONOS_SVG[t.icono] || ICONOS_SVG['wallet'];
        const eq = t.equivalentes || {};
        const pctPago = pago.ingreso > 0 ? (t.monto / pago.ingreso) * 100 : 0;
        const diaBadge = pago.dia_pago ? `
            <span class="pres-card-dia-badge" title="Día de cargo de este pago: ${pago.dia_pago}">
                <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                Día ${pago.dia_pago}
            </span>` : '';

        card.innerHTML = `
            <div class="pres-card-header">
                <div class="pres-card-icon-wrap pres-icon-${escapeHTML(t.color)}">
                    ${iconSvg}
                </div>
                <div class="pres-card-titles">
                    <div class="pres-card-nombre-fila">
                        <h4 class="pres-card-name">${escapeHTML(t.nombre)}</h4>
                        ${diaBadge}
                    </div>
                    ${t.notas ? `<p class="pres-card-notes">${escapeHTML(t.notas)}</p>` : ''}
                </div>
                <div class="pres-card-actions">
                    <button type="button" class="btn-icon-edit" title="Editar tarjeta">
                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                    </button>
                    <button type="button" class="btn-icon-del" title="Eliminar tarjeta">
                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </div>
            </div>

            <div class="pres-card-metric-block">
                <div class="pres-card-pct-badge pres-badge-${escapeHTML(t.color)}">${pctPago.toFixed(1)}% de este pago</div>
                <div class="pres-card-amount">${fmt(t.monto)}</div>
                <div class="pres-card-freq-tag">${t.tipo === 'msi' ? `cuota mensual · ${t.msi_cuotas} meses` : 'en este pago'}</div>
            </div>

            <div class="pres-card-progress-bar">
                <div class="pres-card-progress-fill pres-color-${escapeHTML(t.color)}" style="width: ${Math.min(100, pctPago).toFixed(2)}%"></div>
            </div>

            <div class="pres-card-equivalences">
                <span>Mensual: <strong>${fmt(eq.mensual)}</strong></span>
                <span>Quincenal: <strong>${fmt(eq.quincenal)}</strong></span>
                <span>Semanal: <strong>${fmt(eq.semanal)}</strong></span>
            </div>
        `;

        card.querySelector('.btn-icon-edit').addEventListener('click', () => abrirModalEditarTarjeta(t));
        card.querySelector('.btn-icon-del').addEventListener('click', () => eliminarTarjetaPresupuesto(t.id, t.nombre));
        grid.appendChild(card);
    });
}

async function guardarDiaPago(orden, valor) {
    const dia_pago = valor ? parseInt(valor, 10) : null;
    try {
        const res = await apiFetch(`/api/presupuesto/unidades/${orden}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dia_pago })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            toast(formatErrorDetail(err.detail, 'No se pudo guardar la fecha de pago.'), 'error');
            cargarPresupuesto();
            return;
        }
        cachePresupuesto = await res.json();
        renderizarVistaPresupuesto(cachePresupuesto);
    } catch (e) {
        toast('Error de conexión al guardar la fecha de pago.', 'error');
    }
}


export async function guardarConfigPresupuesto(e) {
    if (e && e.preventDefault) e.preventDefault();
    const inp = document.getElementById('pres-ingreso-base');
    const selFrec = document.getElementById('pres-frecuencia');
    if (!inp || !selFrec) return;

    const ingreso_base = parseFloat(inp.value) || 0;
    const frecuencia = selFrec.value;

    try {
        const res = await apiFetch('/api/presupuesto/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ingreso_base, frecuencia })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            toast(err.detail || 'No se pudo guardar la configuración.', 'error');
            return;
        }
        toast('Presupuesto y periodicidad actualizados.', 'ok');
        cargarPresupuesto();
    } catch (err) {
        toast('Error de conexión al guardar configuración.', 'error');
    }
}


export function alCambiarFrecuenciaPresupuesto() {
    if (!document.getElementById('pres-frecuencia')) return;
    guardarConfigPresupuesto();
}

export function abrirModalExportarPresupuesto() {
    const modal = document.getElementById('modal-exportar-presupuesto');
    if (!modal) return;
    if (!cachePresupuesto) {
        cargarPresupuesto().then(() => abrirModalExportarPresupuesto());
        return;
    }
    const cfg = cachePresupuesto.config || {};
    // Exportan el tablero general y las tarjetas de los pagos activos
    const tarjetas = [
        ...(cachePresupuesto.tarjetas || []),
        ...(cachePresupuesto.unidades || []).filter(u => u.activa).flatMap(u => u.tarjetas),
    ];

    const badgeFrec = document.getElementById('exportar-frecuencia-badge');
    if (badgeFrec) {
        badgeFrec.textContent = FRECUENCIA_LABEL[cfg.frecuencia] || cfg.frecuencia || 'Mensual';
    }

    const boxIngreso = document.getElementById('exportar-pres-ingreso-box');
    const txtIngreso = document.getElementById('exportar-pres-ingreso-txt');
    const chkIngreso = document.getElementById('chk-exportar-ingreso');
    if (cfg.ingreso_base > 0) {
        if (boxIngreso) boxIngreso.classList.remove('hidden');
        if (chkIngreso) chkIngreso.checked = true;
        if (txtIngreso) txtIngreso.textContent = `${fmt(cfg.ingreso_base)} (${FRECUENCIA_LABEL[cfg.frecuencia] || cfg.frecuencia})`;
    } else {
        if (boxIngreso) boxIngreso.classList.add('hidden');
        if (chkIngreso) chkIngreso.checked = false;
    }

    const contItems = document.getElementById('lista-items-exportar-presupuesto');
    if (contItems) {
        if (tarjetas.length === 0) {
            contItems.innerHTML = `<div style="padding: 14px; text-align: center; color: var(--text-muted); font-size: 0.85rem;">No tienes tarjetas de presupuesto para exportar. Crea tarjetas primero.</div>`;
        } else {
            contItems.innerHTML = tarjetas.map(t => {
                const iconSvg = ICONOS_SVG[t.icono] || ICONOS_SVG['wallet'];
                return `
                    <div class="pres-import-item-row" style="display: flex; align-items: center; justify-content: space-between; padding: 8px 10px; border-bottom: 1px solid var(--border-color); gap: 10px;">
                        <label style="display: flex; align-items: center; gap: 10px; cursor: pointer; flex: 1; min-width: 0; margin-bottom: 0;">
                            <input type="checkbox" class="chk-item-exportar" data-id="${t.id}" checked style="cursor: pointer; width: 16px; height: 16px;">
                            <div class="pres-card-icon-wrap pres-icon-${escapeHTML(t.color)}" style="width: 28px; height: 28px; border-radius: 6px; display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;">
                                ${iconSvg}
                            </div>
                            <div style="min-width: 0;">
                                <div style="font-weight: 600; font-size: 0.88rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--text-main);">
                                    ${escapeHTML(t.nombre)}
                                </div>
                                <div style="font-size: 0.75rem; color: var(--text-muted);">
                                    ${t.unidad_orden ? escapeHTML(nombrePago(t.unidad_orden, cfg.frecuencia)) + ' · ' : ''}${t.notas ? escapeHTML(t.notas) + ' · ' : ''}Periodicidad: <strong>${FRECUENCIA_LABEL[cfg.frecuencia] || cfg.frecuencia}</strong>
                                </div>
                            </div>
                        </label>
                        <div style="text-align: right; flex-shrink: 0;">
                            <div style="font-weight: 700; font-size: 0.9rem; color: var(--text-main); font-variant-numeric: tabular-nums;">${fmt(t.monto)}</div>
                            <div style="font-size: 0.75rem; color: var(--text-muted); font-variant-numeric: tabular-nums;">${t.porcentaje.toFixed(1)}%</div>
                        </div>
                    </div>
                `;
            }).join('');
        }
    }

    // Configurar selector de fecha de inicio de cargos
    const selInicio = document.getElementById('exportar-inicio-cargos');
    const boxCustom = document.getElementById('box-exportar-fecha-custom');
    const inputCustom = document.getElementById('exportar-fecha-personalizada');
    if (selInicio) selInicio.value = 'natural';
    if (boxCustom) boxCustom.classList.add('hidden');

    const dHoy = new Date();
    const hoyIso = `${dHoy.getFullYear()}-${String(dHoy.getMonth() + 1).padStart(2, '0')}-${String(dHoy.getDate()).padStart(2, '0')}`;
    if (inputCustom) inputCustom.value = hoyIso;

    const mesesAbr = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
    const diaHoy = dHoy.getDate();
    let sigQuincenaStr = '';
    if (diaHoy <= 15) {
        sigQuincenaStr = `16 de ${mesesAbr[dHoy.getMonth()]}`;
    } else {
        const proxMesIdx = (dHoy.getMonth() + 1) % 12;
        sigQuincenaStr = `1 de ${mesesAbr[proxMesIdx]}`;
    }
    const proxMesIdx = (dHoy.getMonth() + 1) % 12;
    const proxMesStr = `1 de ${mesesAbr[proxMesIdx]}`;

    const optNat = document.getElementById('opt-exportar-natural');
    if (optNat) optNat.textContent = `Próxima fecha natural (desde hoy, ${diaHoy} de ${mesesAbr[dHoy.getMonth()]})`;
    const optQuincena = document.getElementById('opt-exportar-sig-quincena');
    if (optQuincena) optQuincena.textContent = `A partir de la siguiente quincena (${sigQuincenaStr})`;
    const optMes = document.getElementById('opt-exportar-prox-mes');
    if (optMes) optMes.textContent = `A partir del 1er día del próximo mes (${proxMesStr})`;

    modal.classList.remove('hidden');
}


export function cerrarModalExportarPresupuesto() {
    const modal = document.getElementById('modal-exportar-presupuesto');
    if (modal) modal.classList.add('hidden');
}

export function cambiarInicioCargosExportar(val) {
    const boxCustom = document.getElementById('box-exportar-fecha-custom');
    if (boxCustom) {
        if (val === 'personalizada') {
            boxCustom.classList.remove('hidden');
        } else {
            boxCustom.classList.add('hidden');
        }
    }
}

export function toggleSeleccionarTodosExportar() {
    const checkboxes = document.querySelectorAll('.chk-item-exportar');
    const btn = document.getElementById('btn-toggle-todos-exportar');
    const algunoDesmarcado = Array.from(checkboxes).some(c => !c.checked);
    checkboxes.forEach(c => c.checked = algunoDesmarcado);
    if (btn) btn.textContent = algunoDesmarcado ? 'Deseleccionar todos' : 'Seleccionar todos';
}

export async function ejecutarExportarPresupuesto() {
    const btn = document.getElementById('btn-confirmar-exportar-pres');
    if (btn) btn.disabled = true;

    try {
        const chkIngreso = document.getElementById('chk-exportar-ingreso');
        const incluir_ingreso = chkIngreso ? chkIngreso.checked : true;

        const selInicio = document.getElementById('exportar-inicio-cargos');
        const inicio_cargos = selInicio ? selInicio.value : 'natural';
        let fecha_base = null;
        if (inicio_cargos === 'personalizada') {
            const inputFecha = document.getElementById('exportar-fecha-personalizada');
            fecha_base = inputFecha ? inputFecha.value : null;
            if (!fecha_base) {
                toast('Por favor selecciona una fecha base personalizada.', 'error');
                return;
            }
        }

        const checkboxes = document.querySelectorAll('.chk-item-exportar:checked');
        const tarjetas_ids = Array.from(checkboxes).map(c => parseInt(c.dataset.id, 10));

        if (tarjetas_ids.length === 0 && !incluir_ingreso) {
            toast('Selecciona al menos un elemento para exportar.', 'error');
            return;
        }

        const advertencia = 'Al exportar se sobrescribe todo: se borran todos los fijos (también los que creaste a mano y los aportes a metas) y se reemplazan por lo que marcaste.\n\nLas compras a meses también se borran, salvo las que tengas como tarjeta MSI del presupuesto.\n\n¿Continuar?';
        if (!confirm(advertencia)) {
            if (btn) btn.disabled = false;
            return;
        }

        const res = await apiFetch('/api/presupuesto/exportar-a-fijos', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tarjetas_ids,
                incluir_ingreso,
                inicio_cargos,
                fecha_base
            })
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            toast(err.detail || 'No se pudo exportar el presupuesto.', 'error');
            return;
        }

        const data = await res.json();
        cerrarModalExportarPresupuesto();
        const matTxt = data.transacciones_materializadas > 0
            ? ` (${data.transacciones_materializadas} generados hoy)`
            : ` (próximos cobros a partir del ${data.fecha_inicio_cargos})`;
        toast(`Exportación exitosa: ${data.total_procesados} movimientos/fijos sincronizados${matTxt}.`, 'ok');

        if (typeof window.cargarRecurrentes === 'function') {
            window.cargarRecurrentes();
        }
    } catch (e) {
        console.error('Error al exportar presupuesto:', e);
        toast('Error de red al exportar a Movimientos y Fijos.', 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}


// Los meses de la compra a MSI solo aparecen si la tarjeta es de ese tipo
function sincronizarCamposMsi() {
    const activo = document.getElementById('tarjeta-pres-msi')?.checked === true;
    document.getElementById('tarjeta-pres-msi-campos')?.classList.toggle('hidden', !activo);
}

// Una tarjeta de pago toma el día del pago: el campo de día no se muestra
function ajustarModalParaPago(esPago) {
    const campoDia = document.getElementById('tarjeta-pres-dia')?.closest('.form-group');
    if (campoDia) campoDia.classList.toggle('hidden', esPago);
}

export function abrirModalCrearTarjeta(pago = null) {
    idTarjetaEditando = null;
    pagoTarjetaEditando = Number.isInteger(pago) ? pago : null;
    const modal = document.getElementById('modal-tarjeta-presupuesto');
    const form = document.getElementById('form-tarjeta-presupuesto');
    const titulo = document.getElementById('modal-tarjeta-pres-title');
    if (!modal || !form) return;

    form.reset();
    document.getElementById('tarjeta-pres-icono').value = 'wallet';
    document.getElementById('tarjeta-pres-color').value = 'gold';
    document.getElementById('tarjeta-pres-msi').checked = false;
    document.getElementById('tarjeta-pres-msi-cuotas').value = '';
    sincronizarCamposMsi();
    const inpDia = document.getElementById('tarjeta-pres-dia');
    if (inpDia) inpDia.value = '';
    if (titulo) {
        titulo.textContent = pagoTarjetaEditando
            ? `Nueva tarjeta · ${nombrePago(pagoTarjetaEditando, cachePresupuesto?.config?.frecuencia)}`
            : 'Nueva tarjeta de presupuesto';
    }
    ajustarModalParaPago(pagoTarjetaEditando !== null);
    modal.classList.remove('hidden');

    const inpNombre = document.getElementById('tarjeta-pres-nombre');
    if (inpNombre) setTimeout(() => inpNombre.focus(), 60);
}

export function abrirModalEditarTarjeta(t) {
    idTarjetaEditando = t.id;
    pagoTarjetaEditando = t.unidad_orden ?? null;
    const modal = document.getElementById('modal-tarjeta-presupuesto');
    const titulo = document.getElementById('modal-tarjeta-pres-title');
    if (!modal) return;

    document.getElementById('tarjeta-pres-nombre').value = t.nombre;
    document.getElementById('tarjeta-pres-monto').value = t.monto;
    document.getElementById('tarjeta-pres-pct').value = t.porcentaje;
    document.getElementById('tarjeta-pres-icono').value = t.icono || 'wallet';
    document.getElementById('tarjeta-pres-color').value = t.color || 'gold';
    document.getElementById('tarjeta-pres-notas').value = t.notas || '';
    const inpDia = document.getElementById('tarjeta-pres-dia');
    if (inpDia) inpDia.value = (t.dia_pago !== undefined && t.dia_pago !== null) ? t.dia_pago : '';
    document.getElementById('tarjeta-pres-msi').checked = t.tipo === 'msi';
    document.getElementById('tarjeta-pres-msi-cuotas').value = t.msi_cuotas ?? '';
    sincronizarCamposMsi();

    if (titulo) titulo.textContent = 'Editar tarjeta de presupuesto';
    ajustarModalParaPago(pagoTarjetaEditando !== null);
    modal.classList.remove('hidden');
}

export function cerrarModalTarjetaPresupuesto() {
    const modal = document.getElementById('modal-tarjeta-presupuesto');
    if (modal) modal.classList.add('hidden');
    idTarjetaEditando = null;
    pagoTarjetaEditando = null;
}


function sincronizarInputsModal(origen) {
    if (!cachePresupuesto) return;
    const ingreso = Number(cachePresupuesto.config?.ingreso_base) || 0;
    const inpMonto = document.getElementById('tarjeta-pres-monto');
    const inpPct = document.getElementById('tarjeta-pres-pct');
    if (!inpMonto || !inpPct) return;

    let monto = 0;
    if (origen === 'monto') {
        monto = parseFloat(inpMonto.value);
        if (isNaN(monto) || monto <= 0 || ingreso <= 0) {
            inpPct.value = '';
            monto = 0;
        } else {
            inpPct.value = (Math.round((monto / ingreso) * 1000) / 10).toFixed(1);
        }
    } else if (origen === 'pct') {
        const pct = parseFloat(inpPct.value);
        if (isNaN(pct) || pct <= 0 || ingreso <= 0) {
            inpMonto.value = '';
            monto = 0;
        } else {
            monto = Math.round(ingreso * (pct / 100) * 100) / 100;
            inpMonto.value = monto.toFixed(2);
        }
    }
}


export async function guardarTarjetaPresupuesto(e) {
    e.preventDefault();
    // Un segundo envío mientras se guarda crearía otra tarjeta: se ignora
    if (guardandoTarjeta) return;
    // Se captura antes de cualquier await: al cerrar el modal la variable global pasa a null
    const idEditar = idTarjetaEditando;
    const pagoCrear = pagoTarjetaEditando;
    const nombre = document.getElementById('tarjeta-pres-nombre').value.trim();
    let monto = parseFloat(document.getElementById('tarjeta-pres-monto').value) || 0;
    let porcentaje = parseFloat(document.getElementById('tarjeta-pres-pct').value) || 0;
    const icono = document.getElementById('tarjeta-pres-icono').value;
    const color = document.getElementById('tarjeta-pres-color').value;
    const notas = document.getElementById('tarjeta-pres-notas').value.trim();

    if (!nombre) {
        toast('Ingresa un nombre para la tarjeta.', 'error');
        return;
    }

    const diaRaw = document.getElementById('tarjeta-pres-dia')?.value;
    const dia_pago = diaRaw ? parseInt(diaRaw, 10) : null;

    const ingreso = Number(cachePresupuesto?.config?.ingreso_base) || 0;
    if (porcentaje > 100) {
        toast('El porcentaje no puede pasar de 100%.', 'error');
        return;
    }
    if (ingreso <= 0 && porcentaje > 0 && monto === 0) {
        toast('Define primero tu ingreso fijo base para asignar tarjetas por porcentaje.', 'error');
        return;
    }
    const esMsi = document.getElementById('tarjeta-pres-msi').checked;
    const msiCuotas = parseInt(document.getElementById('tarjeta-pres-msi-cuotas').value, 10) || null;
    const body = { nombre, monto, porcentaje, icono, color, notas, dia_pago, tipo: esMsi ? 'msi' : 'normal', msi_cuotas: esMsi ? msiCuotas : null };
    if (!idEditar && pagoCrear !== null) body.unidad_orden = pagoCrear;

    guardandoTarjeta = true;
    try {
        let res;
        if (idEditar) {
            res = await apiFetch(`/api/presupuesto/tarjetas/${idEditar}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
        } else {
            res = await apiFetch('/api/presupuesto/tarjetas', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
        }

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            toast(formatErrorDetail(err.detail, 'No se pudo guardar la tarjeta.'), 'error');
            return;
        }

        toast(idEditar ? 'Tarjeta actualizada.' : 'Tarjeta agregada al presupuesto.', 'ok');
        cerrarModalTarjetaPresupuesto();
        cargarPresupuesto();
    } catch (err) {
        toast('Error de conexión.', 'error');
    } finally {
        guardandoTarjeta = false;
    }
}


export async function eliminarTarjetaPresupuesto(id, nombre) {
    if (!confirm(`¿Eliminar la tarjeta "${nombre}" del presupuesto?`)) return;
    try {
        const res = await apiFetch(`/api/presupuesto/tarjetas/${id}`, { method: 'DELETE' });
        if (!res.ok) {
            toast('No se pudo eliminar la tarjeta.', 'error');
            return;
        }
        toast('Tarjeta eliminada.', 'ok');
        cargarPresupuesto();
    } catch (err) {
        toast('Error al eliminar tarjeta.', 'error');
    }
}


export function inicializarPresupuesto() {
    const formConfig = document.getElementById('form-presupuesto-config');
    if (formConfig) formConfig.addEventListener('submit', guardarConfigPresupuesto);

    document.getElementById('tarjeta-pres-msi')?.addEventListener('change', sincronizarCamposMsi);

    const selFrec = document.getElementById('pres-frecuencia');
    if (selFrec) selFrec.addEventListener('change', alCambiarFrecuenciaPresupuesto);

    const formTarjeta = document.getElementById('form-tarjeta-presupuesto');
    if (formTarjeta) formTarjeta.addEventListener('submit', guardarTarjetaPresupuesto);

    const inpMonto = document.getElementById('tarjeta-pres-monto');
    const inpPct = document.getElementById('tarjeta-pres-pct');
    if (inpMonto) inpMonto.addEventListener('input', () => sincronizarInputsModal('monto'));
    if (inpPct) inpPct.addEventListener('input', () => sincronizarInputsModal('pct'));
}

export function cerrarBannerFijosPresupuesto() {
    localStorage.setItem('presupuesto_fijos_banner_dismissed', 'true');
    sessionStorage.setItem('presupuesto_fijos_banner_dismissed', 'true');
    const boxFijos = document.getElementById('pres-fijos-alert');
    if (boxFijos) boxFijos.classList.add('hidden');
}


Object.assign(window, {
    abrirModalCrearTarjeta,
    cerrarModalTarjetaPresupuesto,
    abrirModalExportarPresupuesto,
    cerrarModalExportarPresupuesto,
    cambiarInicioCargosExportar,
    toggleSeleccionarTodosExportar,
    ejecutarExportarPresupuesto,
    cerrarBannerFijosPresupuesto,
    guardarConfigPresupuesto
});
