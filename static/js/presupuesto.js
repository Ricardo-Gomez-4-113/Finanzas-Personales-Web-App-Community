/**
 * static/js/presupuesto.js - Organizador de Presupuesto por Tarjetas y Porcentajes
 * Permite dividir el ingreso fijo (semanal, quincenal, mensual) en tarjetas con sincronización $ ⇄ %,
 * y sub-dividir el presupuesto en 2 quincenas, 4 semanas o de manera personalizada.
 */

import { apiFetch, escapeHTML, fmt, toast } from './core.js';

let cachePresupuesto = null;
let subperiodoActivoId = 'total';
let editorSubperiodosTemp = [];

const ICONOS_SVG = {
    'home': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>`,
    'trending-up': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>`,
    'shield': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
    'credit-card': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/></svg>`,
    'smile': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>`,
    'book': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`,
    'heart': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>`,
    'shopping-bag': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>`,
    'wallet': `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a8 8 0 0 1-8 8H5a2 2 0 0 1-2-2V7"/><circle cx="18" cy="14" r="1"/></svg>`
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

function actualizarOpcionesSubdivisionSelect(frecuencia, subTipoActual) {
    const sel = document.getElementById('pres-subdivision-tipo');
    const wrap = document.getElementById('wrap-pres-subdivision');
    const btnGear = document.getElementById('btn-custom-subdivision');
    if (!sel) return;

    if (frecuencia === 'semanal') {
        if (wrap) wrap.classList.add('hidden');
        sel.innerHTML = `<option value="ninguna" selected>Sin división (Semanal)</option>`;
        if (btnGear) btnGear.classList.add('hidden');
        return;
    }

    if (wrap) wrap.classList.remove('hidden');

    let optionsHtml = `<option value="ninguna">Sin división (Periodo completo)</option>`;
    if (frecuencia === 'mensual') {
        optionsHtml += `
            <option value="quincenas">2 Quincenas</option>
            <option value="semanas">4 Semanas</option>
            <option value="personalizada">Personalizada ⚙️</option>
        `;
    } else if (frecuencia === 'quincenal') {
        optionsHtml += `
            <option value="semanas">2 Semanas</option>
            <option value="personalizada">Personalizada ⚙️</option>
        `;
    }

    sel.innerHTML = optionsHtml;
    // Si el tipo actual existe en las opciones, seleccionarlo; de lo contrario 'ninguna'
    const existe = Array.from(sel.options).some(o => o.value === subTipoActual);
    sel.value = existe ? subTipoActual : 'ninguna';

    if (btnGear) {
        btnGear.classList.toggle('hidden', sel.value === 'ninguna');
    }
}

function renderizarVistaPresupuesto(data) {
    const cfg = data.config;
    const res = data.resumen;
    const tarjetas = data.tarjetas || [];
    const fijos = data.fijos_detectados || {};

    // 1. Inputs de configuración de ingreso base y sub-división
    const inpIngreso = document.getElementById('pres-ingreso-base');
    const selFrecuencia = document.getElementById('pres-frecuencia');
    if (inpIngreso && document.activeElement !== inpIngreso) inpIngreso.value = cfg.ingreso_base || '';
    if (selFrecuencia && document.activeElement !== selFrecuencia) selFrecuencia.value = cfg.frecuencia || 'mensual';

    actualizarOpcionesSubdivisionSelect(cfg.frecuencia || 'mensual', cfg.subdivision_tipo || 'ninguna');

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

    // 2. Barra de Navegación por Sub-periodos
    renderBarraSubperiodos(cfg);

    // 3. Tarjetas KPI de Resumen (adaptativas al sub-periodo activo)
    renderKPIs(cfg, res, tarjetas);

    // 4. Barra visual segmentada de asignación (100% allocation track)
    renderBarraDistribucion(tarjetas, res, cfg);

    // 5. Grid de Tarjetas de Presupuesto
    renderGridTarjetas(tarjetas, cfg);
}

function renderBarraSubperiodos(cfg) {
    const navBar = document.getElementById('pres-subperiodos-bar');
    const pillsWrap = document.getElementById('pres-subperiodos-pills');
    const badgeDesc = document.getElementById('pres-subperiodo-active-badge');
    if (!navBar || !pillsWrap) return;

    const subperiodos = cfg.subdivision_config || [];
    const activo = (cfg.subdivision_tipo !== 'ninguna' && subperiodos.length > 0);

    if (!activo) {
        navBar.classList.add('hidden');
        subperiodoActivoId = 'total';
        return;
    }

    navBar.classList.remove('hidden');

    // Validar que el subperiodoActivoId siga existiendo en la lista
    if (subperiodoActivoId !== 'total' && !subperiodos.some(sp => sp.id === subperiodoActivoId)) {
        subperiodoActivoId = 'total';
    }

    pillsWrap.innerHTML = '';

    // Pill: Periodo Completo
    const pillTotal = document.createElement('button');
    pillTotal.type = 'button';
    pillTotal.className = `pres-subperiodo-pill ${subperiodoActivoId === 'total' ? 'active' : ''}`;
    pillTotal.innerHTML = `<span>🌐 Periodo Completo (${fmt(cfg.ingreso_base)})</span>`;
    pillTotal.onclick = () => {
        subperiodoActivoId = 'total';
        renderizarVistaPresupuesto(cachePresupuesto);
    };
    pillsWrap.appendChild(pillTotal);

    // Pills de cada sub-periodo
    subperiodos.forEach(sp => {
        const pill = document.createElement('button');
        pill.type = 'button';
        pill.className = `pres-subperiodo-pill ${subperiodoActivoId === sp.id ? 'active' : ''}`;
        pill.innerHTML = `<span>📅 ${escapeHTML(sp.nombre)} (${sp.porcentaje}% · ${fmt(sp.monto)})</span>`;
        pill.onclick = () => {
            subperiodoActivoId = sp.id;
            renderizarVistaPresupuesto(cachePresupuesto);
        };
        pillsWrap.appendChild(pill);
    });

    if (badgeDesc) {
        if (subperiodoActivoId === 'total') {
            badgeDesc.textContent = `Mostrando asignación general (${cfg.frecuencia})`;
        } else {
            const spActual = subperiodos.find(sp => sp.id === subperiodoActivoId);
            badgeDesc.innerHTML = spActual ? `Mostrando flujo de <strong>${escapeHTML(spActual.nombre)}</strong>` : '';
        }
    }
}

function renderKPIs(cfg, res, tarjetas) {
    const setEl = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
    const subperiodos = cfg.subdivision_config || [];
    const spActivo = (subperiodoActivoId !== 'total') ? subperiodos.find(sp => sp.id === subperiodoActivoId) : null;

    let ingresoMostrar = cfg.ingreso_base;
    let asignadoMostrar = res.total_asignado_monto;
    let disponibleMostrar = res.disponible_monto;
    let tagSub = `${cfg.frecuencia.toUpperCase()} · 100.0%`;

    if (spActivo) {
        const factor = (spActivo.porcentaje / 100.0);
        ingresoMostrar = spActivo.monto;
        asignadoMostrar = Math.round(res.total_asignado_monto * factor * 100) / 100;
        disponibleMostrar = Math.max(0, Math.round((ingresoMostrar - asignadoMostrar) * 100) / 100);
        tagSub = `${spActivo.nombre.toUpperCase()} · ${spActivo.porcentaje}%`;
    }

    setEl('lbl-pres-kpi-ingreso', fmt(ingresoMostrar));
    setEl('lbl-pres-kpi-ingreso-sub', tagSub);

    setEl('lbl-pres-kpi-asignado', fmt(asignadoMostrar));
    const badgeAsig = document.getElementById('badge-pres-asignado');
    if (badgeAsig) {
        badgeAsig.textContent = `${res.total_asignado_pct.toFixed(1)}% del total`;
        badgeAsig.className = `pnl-badge ${res.excedido ? 'badge-danger' : 'badge-gold'}`;
    }

    setEl('lbl-pres-kpi-disponible', fmt(disponibleMostrar));
    const badgeDisp = document.getElementById('badge-pres-disponible');
    if (badgeDisp) {
        if (res.excedido) {
            badgeDisp.textContent = `Excedido en ${(res.total_asignado_pct - 100).toFixed(1)}%`;
            badgeDisp.className = 'pnl-badge badge-danger';
        } else {
            badgeDisp.textContent = `${res.disponible_pct.toFixed(1)}% libre`;
            badgeDisp.className = 'pnl-badge badge-success';
        }
    }

    // Calcular ahorro / inversión proyectada
    let ahorroInvMonto = 0;
    let ahorroInvPct = 0;
    tarjetas.forEach(t => {
        const n = t.nombre.toLowerCase();
        if (n.includes('inversión') || n.includes('inversion') || n.includes('etf') || n.includes('ahorro') || n.includes('cetes') || n.includes('fondo') || n.includes('renta fija')) {
            const montoT = spActivo ? (t.monto * (spActivo.porcentaje / 100.0)) : t.monto;
            ahorroInvMonto += montoT;
            ahorroInvPct += t.porcentaje;
        }
    });
    setEl('lbl-pres-kpi-inv', fmt(ahorroInvMonto));
    const badgeInv = document.getElementById('badge-pres-inv');
    if (badgeInv) {
        badgeInv.textContent = `${ahorroInvPct.toFixed(1)}% tasa ahorro/inv.`;
        badgeInv.className = 'pnl-badge badge-success';
    }
}

function renderBarraDistribucion(tarjetas, res, cfg) {
    const track = document.getElementById('presupuesto-alloc-track');
    const legend = document.getElementById('presupuesto-alloc-legend');
    if (!track) return;

    track.innerHTML = '';
    if (legend) legend.innerHTML = '';

    if (tarjetas.length === 0) {
        track.innerHTML = `<div class="pres-alloc-slice pres-slice-empty" style="width: 100%;" title="Sin tarjetas configuradas (100% sin asignar)"></div>`;
        return;
    }

    tarjetas.forEach(t => {
        const pct = Math.max(0, Math.min(100, t.porcentaje));
        if (pct <= 0) return;
        const slice = document.createElement('div');
        slice.className = `pres-alloc-slice pres-color-${t.color}`;
        slice.style.width = `${pct}%`;
        slice.title = `${t.nombre}: ${pct.toFixed(1)}% (${fmt(t.monto)})`;
        track.appendChild(slice);

        if (legend) {
            const item = document.createElement('div');
            item.className = 'pres-legend-item';
            item.innerHTML = `<span class="pres-legend-dot pres-color-${t.color}"></span><span>${escapeHTML(t.nombre)}: <strong>${pct.toFixed(1)}%</strong></span>`;
            legend.appendChild(item);
        }
    });

    if (res.disponible_pct > 0 && !res.excedido) {
        const sliceDisp = document.createElement('div');
        sliceDisp.className = 'pres-alloc-slice pres-slice-libre';
        sliceDisp.style.width = `${res.disponible_pct}%`;
        sliceDisp.title = `Disponible sin asignar: ${res.disponible_pct.toFixed(1)}% (${fmt(res.disponible_monto)})`;
        track.appendChild(sliceDisp);

        if (legend) {
            const item = document.createElement('div');
            item.className = 'pres-legend-item pres-legend-libre';
            item.innerHTML = `<span class="pres-legend-dot pres-color-libre"></span><span>Por asignar: <strong>${res.disponible_pct.toFixed(1)}%</strong></span>`;
            legend.appendChild(item);
        }
    }
}

function renderGridTarjetas(tarjetas, cfg) {
    const grid = document.getElementById('grid-tarjetas-presupuesto');
    if (!grid) return;

    if (tarjetas.length === 0) {
        grid.innerHTML = `
            <div class="pres-empty-state">
                <svg viewBox="0 0 24 24" width="44" height="44" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/>
                </svg>
                <h3>Aún no has creado tarjetas de presupuesto</h3>
                <p>Divide tu dinero para saber con exactitud cuánto destinas a gastos fijos, inversión en ETFs, ahorro y diversión.</p>
                <div class="pres-empty-actions">
                    <button type="button" class="btn-primary" onclick="abrirModalCrearTarjeta()">+ Crear Primera Tarjeta</button>
                    <button type="button" class="btn-secondary" onclick="aplicarPlantillaPresupuesto('50-30-20')">Plantilla Regla 50/30/20</button>
                    <button type="button" class="btn-secondary" onclick="aplicarPlantillaPresupuesto('inversionista')">Plantilla Perfil Inversionista</button>
                </div>
            </div>`;
        return;
    }

    const subperiodos = cfg.subdivision_config || [];
    const esSubperiodo = (subperiodoActivoId !== 'total');
    const spActivo = esSubperiodo ? subperiodos.find(sp => sp.id === subperiodoActivoId) : null;

    grid.innerHTML = '';
    tarjetas.forEach(t => {
        const card = document.createElement('div');
        card.className = `card pres-card pres-card-accent-${t.color}`;
        const iconSvg = ICONOS_SVG[t.icono] || ICONOS_SVG['wallet'];
        const eq = t.equivalentes || {};

        let montoMostrar = t.monto;
        let tagFreq = FRECUENCIA_LABEL[cfg.frecuencia] || '';
        let badgePctTxt = `${t.porcentaje.toFixed(1)}%`;

        if (spActivo) {
            const spItem = (t.subdivision || []).find(s => s.id === subperiodoActivoId);
            montoMostrar = spItem ? spItem.monto : Math.round(t.monto * (spActivo.porcentaje / 100.0) * 100) / 100;
            tagFreq = `en ${spActivo.nombre}`;
            badgePctTxt = `${t.porcentaje.toFixed(1)}% (${spActivo.porcentaje}% de periodo)`;
        }

        // Mini fila de desglose de sub-periodos si está en vista 'total' y hay subdivisión activa
        let miniDesgloseHtml = '';
        if (!esSubperiodo && t.subdivision && t.subdivision.length > 0) {
            miniDesgloseHtml = `
                <div class="pres-card-subperiodo-breakdown">
                    <div class="pres-card-sub-pills-row">
                        ${t.subdivision.map(sp => `
                            <span class="pres-card-sub-pill" title="${escapeHTML(sp.nombre)}: ${sp.porcentaje}%">
                                ${escapeHTML(sp.nombre)}: <strong>${fmt(sp.monto)}</strong>
                            </span>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        card.innerHTML = `
            <div class="pres-card-header">
                <div class="pres-card-icon-wrap pres-icon-${t.color}">
                    ${iconSvg}
                </div>
                <div class="pres-card-titles">
                    <h4 class="pres-card-name">${escapeHTML(t.nombre)}</h4>
                    ${t.notas ? `<p class="pres-card-notes">${escapeHTML(t.notas)}</p>` : ''}
                </div>
                <div class="pres-card-actions">
                    <button type="button" class="btn-icon-edit" data-id="${t.id}" title="Editar tarjeta">
                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                    </button>
                    <button type="button" class="btn-icon-del" data-id="${t.id}" title="Eliminar tarjeta">
                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </div>
            </div>

            <div class="pres-card-metric-block">
                <div class="pres-card-pct-badge pres-badge-${t.color}">${badgePctTxt}</div>
                <div class="pres-card-amount">${fmt(montoMostrar)}</div>
                <div class="pres-card-freq-tag">${tagFreq}</div>
            </div>

            <div class="pres-card-progress-bar">
                <div class="pres-card-progress-fill pres-color-${t.color}" style="width: ${Math.min(100, t.porcentaje)}%"></div>
            </div>

            ${miniDesgloseHtml}

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

// -----------------------------------------------------------------------------
// Sincronización de Ingreso Base, Frecuencia y Subdivisión
// -----------------------------------------------------------------------------

export async function guardarConfigPresupuesto(e) {
    if (e && e.preventDefault) e.preventDefault();
    const inp = document.getElementById('pres-ingreso-base');
    const selFrec = document.getElementById('pres-frecuencia');
    const selSub = document.getElementById('pres-subdivision-tipo');
    if (!inp || !selFrec) return;

    const ingreso_base = parseFloat(inp.value) || 0;
    const frecuencia = selFrec.value;
    const subdivision_tipo = selSub ? selSub.value : 'ninguna';

    // Si seleccionó personalizada pero aún no tiene periodos, abrir el modal
    if (subdivision_tipo === 'personalizada' && (!cachePresupuesto?.config?.subdivision_config?.length || cachePresupuesto.config.subdivision_tipo !== 'personalizada')) {
        abrirModalSubdivisionPersonalizada();
        return;
    }

    try {
        const res = await apiFetch('/api/presupuesto/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ingreso_base, frecuencia, subdivision_tipo })
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

// Manejador del cambio de frecuencia en el selector
export function alCambiarFrecuenciaPresupuesto() {
    const selFrec = document.getElementById('pres-frecuencia');
    if (!selFrec) return;
    actualizarOpcionesSubdivisionSelect(selFrec.value, 'ninguna');
    guardarConfigPresupuesto();
}

// Manejador del cambio de subdivisión en el selector
export function alCambiarSubdivisionSelect() {
    const selSub = document.getElementById('pres-subdivision-tipo');
    if (!selSub) return;
    if (selSub.value === 'personalizada') {
        abrirModalSubdivisionPersonalizada();
    } else {
        guardarConfigPresupuesto();
    }
}

export function abrirModalImportarFijos() {
    const modal = document.getElementById('modal-importar-fijos-presupuesto');
    if (!modal) return;

    if (!cachePresupuesto) {
        cargarPresupuesto().then(() => abrirModalImportarFijos());
        return;
    }

    const cfg = cachePresupuesto.config || {};
    const fijos = cachePresupuesto.fijos_detectados || {};
    const tarjetas = cachePresupuesto.tarjetas || [];

    // 1. Bloque de nómina / ingreso base
    const boxIngreso = document.getElementById('importar-fijos-ingreso-box');
    const txtIngreso = document.getElementById('importar-fijos-ingreso-txt');
    const chkIngreso = document.getElementById('chk-importar-ingreso');

    if (fijos.ingreso_fijo_periodo > 0) {
        if (boxIngreso) boxIngreso.classList.remove('hidden');
        if (chkIngreso) chkIngreso.checked = true;
        if (txtIngreso) {
            txtIngreso.innerHTML = `<strong>${fmt(fijos.ingreso_fijo_periodo)}</strong> ${FRECUENCIA_LABEL[cfg.frecuencia] || ''} <span style="opacity: 0.85;">(Equivalente mensual: ${fmt(fijos.ingreso_fijo_mensual)})</span>`;
        }
    } else {
        if (boxIngreso) boxIngreso.classList.add('hidden');
        if (chkIngreso) chkIngreso.checked = false;
    }

    // 2. Modo individual por defecto
    const radioInd = document.querySelector('input[name="import-fijos-modo"][value="individual"]');
    if (radioInd) radioInd.checked = true;

    // 3. Renderizar items con checkboxes
    const contItems = document.getElementById('lista-items-importar-fijos');
    const itemsGasto = fijos.items_gasto || [];

    if (contItems) {
        if (itemsGasto.length === 0) {
            contItems.innerHTML = `<div style="padding: 12px; text-align: center; color: var(--text-muted); font-size: 0.85rem;">No se detectaron cargos periódicos ni compras a meses activas.</div>`;
        } else {
            const ingresoReferencia = (fijos.ingreso_fijo_periodo > 0 ? fijos.ingreso_fijo_periodo : (cfg.ingreso_base || 1));
            contItems.innerHTML = itemsGasto.map(item => {
                const iconSvg = ICONOS_SVG[item.icono] || ICONOS_SVG['wallet'];
                const pct = ingresoReferencia > 0 ? ((item.monto_periodo / ingresoReferencia) * 100) : 0;

                const yaExiste = tarjetas.find(t => {
                    const tNom = t.nombre.toLowerCase().trim();
                    const iNom = item.descripcion.toLowerCase().trim();
                    return tNom === iNom || (iNom.length >= 3 && tNom.includes(iNom)) || (tNom.length >= 3 && iNom.includes(tNom));
                });

                return `
                    <div class="pres-import-item-row" style="display: flex; align-items: center; justify-content: space-between; padding: 8px 10px; border-bottom: 1px solid var(--border-color); gap: 10px;">
                        <label style="display: flex; align-items: center; gap: 10px; cursor: pointer; flex: 1; min-width: 0; margin-bottom: 0;">
                            <input type="checkbox" class="chk-item-fijo" data-id="${item.id}" checked style="cursor: pointer; width: 16px; height: 16px;">
                            <div class="pres-card-icon-wrap pres-icon-${item.color}" style="width: 28px; height: 28px; border-radius: 6px; display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;">
                                ${iconSvg}
                            </div>
                            <div style="min-width: 0;">
                                <div style="font-weight: 600; font-size: 0.88rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--text-main);">
                                    ${escapeHTML(item.descripcion)}
                                    ${item.es_msi ? `<span style="font-size: 0.72rem; color: #f59e0b; background: rgba(245, 158, 11, 0.15); padding: 1px 6px; border-radius: 4px; margin-left: 4px;">MSI ${item.cuotas_info || ''}</span>` : ''}
                                </div>
                                <div style="font-size: 0.75rem; color: var(--text-muted);">
                                    ${escapeHTML(item.categoria_nombre)}
                                    ${yaExiste ? `<span style="color: #3b82f6; font-weight: 500;"> · (Sincronizará con "${escapeHTML(yaExiste.nombre)}")</span>` : ''}
                                </div>
                            </div>
                        </label>
                        <div style="text-align: right; flex-shrink: 0;">
                            <div style="font-weight: 700; font-size: 0.9rem; color: var(--text-main);">${fmt(item.monto_periodo)}</div>
                            <div style="font-size: 0.75rem; color: var(--text-muted);">${pct.toFixed(1)}%</div>
                        </div>
                    </div>
                `;
            }).join('');
        }
    }

    const chkReemp = document.getElementById('chk-importar-reemplazar');
    if (chkReemp) chkReemp.checked = false;

    modal.classList.remove('hidden');
}

export function cerrarModalImportarFijos() {
    const modal = document.getElementById('modal-importar-fijos-presupuesto');
    if (modal) modal.classList.add('hidden');
}

export function toggleSeleccionarTodosFijos() {
    const checkboxes = document.querySelectorAll('.chk-item-fijo');
    const btn = document.getElementById('btn-toggle-todos-fijos');
    const algunoDesmarcado = Array.from(checkboxes).some(c => !c.checked);
    checkboxes.forEach(c => c.checked = algunoDesmarcado);
    if (btn) btn.textContent = algunoDesmarcado ? 'Deseleccionar todos' : 'Seleccionar todos';
}

export async function ejecutarImportarFijos() {
    const btn = document.getElementById('btn-confirmar-importar-fijos');
    if (btn) btn.disabled = true;

    try {
        const modoRadio = document.querySelector('input[name="import-fijos-modo"]:checked');
        const modo = modoRadio ? modoRadio.value : 'individual';

        const chkIngreso = document.getElementById('chk-importar-ingreso');
        const actualizar_ingreso = chkIngreso ? chkIngreso.checked : true;

        const chkReemplazar = document.getElementById('chk-importar-reemplazar');
        const reemplazar = chkReemplazar ? chkReemplazar.checked : false;

        const checkboxes = document.querySelectorAll('.chk-item-fijo:checked');
        const recurrentes_ids = Array.from(checkboxes).map(c => parseInt(c.dataset.id, 10));

        const res = await apiFetch('/api/presupuesto/importar-fijos', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                modo,
                recurrentes_ids,
                actualizar_ingreso,
                reemplazar
            })
        });

        if (!res.ok) {
            toast('No se pudieron importar los datos.', 'error');
            return;
        }

        const data = await res.json();
        cachePresupuesto = data;
        renderizarVistaPresupuesto(data);
        cerrarModalImportarFijos();
        cerrarBannerFijosPresupuesto();
        toast('Presupuesto sincronizado con éxito desde Fijos y MSI.', 'ok');
    } catch (err) {
        toast('Error al sincronizar datos.', 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

export const importarFijosPresupuesto = abrirModalImportarFijos;

export function abrirModalExportarPresupuesto() {
    const modal = document.getElementById('modal-exportar-presupuesto');
    if (!modal) return;
    if (!cachePresupuesto) {
        cargarPresupuesto().then(() => abrirModalExportarPresupuesto());
        return;
    }
    const cfg = cachePresupuesto.config || {};
    const tarjetas = cachePresupuesto.tarjetas || [];

    const badgeFrec = document.getElementById('exportar-frecuencia-badge');
    if (badgeFrec) {
        badgeFrec.textContent = (FRECUENCIA_LABEL[cfg.frecuencia] || cfg.frecuencia || 'MENSUAL').toUpperCase();
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
                            <div class="pres-card-icon-wrap pres-icon-${t.color}" style="width: 28px; height: 28px; border-radius: 6px; display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;">
                                ${iconSvg}
                            </div>
                            <div style="min-width: 0;">
                                <div style="font-weight: 600; font-size: 0.88rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--text-main);">
                                    ${escapeHTML(t.nombre)}
                                </div>
                                <div style="font-size: 0.75rem; color: var(--text-muted);">
                                    ${t.notas ? escapeHTML(t.notas) + ' · ' : ''}Periodicidad: <strong>${FRECUENCIA_LABEL[cfg.frecuencia] || cfg.frecuencia}</strong>
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

    modal.classList.remove('hidden');
}

export function cerrarModalExportarPresupuesto() {
    const modal = document.getElementById('modal-exportar-presupuesto');
    if (modal) modal.classList.add('hidden');
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

        const chkActualizar = document.getElementById('chk-exportar-actualizar');
        const actualizar_existentes = chkActualizar ? chkActualizar.checked : true;

        const checkboxes = document.querySelectorAll('.chk-item-exportar:checked');
        const tarjetas_ids = Array.from(checkboxes).map(c => parseInt(c.dataset.id, 10));

        if (tarjetas_ids.length === 0 && !incluir_ingreso) {
            toast('Selecciona al menos un elemento para exportar.', 'error');
            return;
        }

        const res = await apiFetch('/api/presupuesto/exportar-a-fijos', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tarjetas_ids,
                incluir_ingreso,
                actualizar_existentes
            })
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            toast(err.detail || 'No se pudo exportar el presupuesto.', 'error');
            return;
        }

        const data = await res.json();
        cerrarModalExportarPresupuesto();
        toast(`Exportación exitosa: ${data.total_procesados} movimientos/fijos sincronizados con periodicidad ${data.frecuencia}.`, 'ok');

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

export async function aplicarPlantillaPresupuesto(plantillaNombre) {
    if (!confirm(`¿Deseas aplicar la plantilla "${plantillaNombre === '50-30-20' ? 'Regla 50/30/20' : 'Perfil Inversionista'}"? Se reemplazarán las tarjetas existentes.`)) {
        return;
    }
    try {
        const res = await apiFetch('/api/presupuesto/plantilla', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plantilla: plantillaNombre })
        });
        if (!res.ok) {
            toast('No se pudo aplicar la plantilla.', 'error');
            return;
        }
        toast('Plantilla aplicada con éxito.', 'ok');
        cargarPresupuesto();
    } catch (err) {
        toast('Error al aplicar plantilla.', 'error');
    }
}

// -----------------------------------------------------------------------------
// Modal Crear / Editar Tarjeta con sincronización $ ⇄ %
// -----------------------------------------------------------------------------

let idTarjetaEditando = null;

export function abrirModalCrearTarjeta() {
    idTarjetaEditando = null;
    const modal = document.getElementById('modal-tarjeta-presupuesto');
    const form = document.getElementById('form-tarjeta-presupuesto');
    const titulo = document.getElementById('modal-tarjeta-pres-title');
    if (!modal || !form) return;

    form.reset();
    document.getElementById('tarjeta-pres-icono').value = 'wallet';
    document.getElementById('tarjeta-pres-color').value = 'gold';
    if (titulo) titulo.textContent = 'Nueva Tarjeta de Presupuesto';
    modal.classList.remove('hidden');

    const inpNombre = document.getElementById('tarjeta-pres-nombre');
    if (inpNombre) setTimeout(() => inpNombre.focus(), 60);
}

export function abrirModalEditarTarjeta(t) {
    idTarjetaEditando = t.id;
    const modal = document.getElementById('modal-tarjeta-presupuesto');
    const titulo = document.getElementById('modal-tarjeta-pres-title');
    if (!modal) return;

    document.getElementById('tarjeta-pres-nombre').value = t.nombre;
    document.getElementById('tarjeta-pres-monto').value = t.monto;
    document.getElementById('tarjeta-pres-pct').value = t.porcentaje;
    document.getElementById('tarjeta-pres-icono').value = t.icono || 'wallet';
    document.getElementById('tarjeta-pres-color').value = t.color || 'gold';
    document.getElementById('tarjeta-pres-notas').value = t.notas || '';

    if (titulo) titulo.textContent = 'Editar Tarjeta de Presupuesto';
    modal.classList.remove('hidden');
}

export function cerrarModalTarjetaPresupuesto() {
    const modal = document.getElementById('modal-tarjeta-presupuesto');
    if (modal) modal.classList.add('hidden');
    idTarjetaEditando = null;
}

function sincronizarInputsModal(origen) {
    if (!cachePresupuesto) return;
    const ingreso = Number(cachePresupuesto.config?.ingreso_base) || 0;
    const inpMonto = document.getElementById('tarjeta-pres-monto');
    const inpPct = document.getElementById('tarjeta-pres-pct');
    if (!inpMonto || !inpPct) return;

    if (origen === 'monto') {
        const monto = parseFloat(inpMonto.value);
        if (isNaN(monto) || monto <= 0 || ingreso <= 0) {
            inpPct.value = '';
        } else {
            inpPct.value = (Math.round((monto / ingreso) * 1000) / 10).toFixed(1);
        }
    } else if (origen === 'pct') {
        const pct = parseFloat(inpPct.value);
        if (isNaN(pct) || pct <= 0 || ingreso <= 0) {
            inpMonto.value = '';
        } else {
            inpMonto.value = (Math.round(ingreso * (pct / 100) * 100) / 100).toFixed(2);
        }
    }
}

export function aplicarPresetChip(nombre, icono, color, notasSugeridas, pctSugerido) {
    document.getElementById('tarjeta-pres-nombre').value = nombre;
    document.getElementById('tarjeta-pres-icono').value = icono;
    document.getElementById('tarjeta-pres-color').value = color;
    document.getElementById('tarjeta-pres-notas').value = notasSugeridas;
    if (pctSugerido) {
        document.getElementById('tarjeta-pres-pct').value = pctSugerido;
        sincronizarInputsModal('pct');
    }
}

export async function guardarTarjetaPresupuesto(e) {
    e.preventDefault();
    const nombre = document.getElementById('tarjeta-pres-nombre').value.trim();
    const monto = parseFloat(document.getElementById('tarjeta-pres-monto').value) || 0;
    const porcentaje = parseFloat(document.getElementById('tarjeta-pres-pct').value) || 0;
    const icono = document.getElementById('tarjeta-pres-icono').value;
    const color = document.getElementById('tarjeta-pres-color').value;
    const notas = document.getElementById('tarjeta-pres-notas').value.trim();

    if (!nombre) {
        toast('Ingresa un nombre para la tarjeta.', 'error');
        return;
    }

    const body = { nombre, monto, porcentaje, icono, color, notas };

    try {
        let res;
        if (idTarjetaEditando) {
            res = await apiFetch(`/api/presupuesto/tarjetas/${idTarjetaEditando}`, {
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
            toast('No se pudo guardar la tarjeta.', 'error');
            return;
        }

        toast(idTarjetaEditando ? 'Tarjeta actualizada.' : 'Tarjeta agregada al presupuesto.', 'ok');
        cerrarModalTarjetaPresupuesto();
        cargarPresupuesto();
    } catch (err) {
        toast('Error de conexión.', 'error');
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

// -----------------------------------------------------------------------------
// Modal: Editor de Sub-periodos Personalizados
// -----------------------------------------------------------------------------

export function abrirModalSubdivisionPersonalizada() {
    const modal = document.getElementById('modal-subdivision-personalizada');
    if (!modal) return;

    const cfg = cachePresupuesto?.config || {};
    const subperiodos = cfg.subdivision_config || [];

    if (subperiodos.length > 0) {
        editorSubperiodosTemp = subperiodos.map(sp => ({
            id: sp.id || ('sp_' + Math.random().toString(36).substr(2, 6)),
            nombre: sp.nombre,
            porcentaje: sp.porcentaje
        }));
    } else {
        // Pre-cargar por defecto según la frecuencia
        if (cfg.frecuencia === 'quincenal') {
            editorSubperiodosTemp = [
                { id: 's1', nombre: 'Semana 1', porcentaje: 50.0 },
                { id: 's2', nombre: 'Semana 2', porcentaje: 50.0 }
            ];
        } else {
            editorSubperiodosTemp = [
                { id: 'q1', nombre: 'Quincena 1', porcentaje: 50.0 },
                { id: 'q2', nombre: 'Quincena 2', porcentaje: 50.0 }
            ];
        }
    }

    renderFilasEditorSubperiodos();
    modal.classList.remove('hidden');
}

export function cerrarModalSubdivisionPersonalizada() {
    const modal = document.getElementById('modal-subdivision-personalizada');
    if (modal) modal.classList.add('hidden');
}

function renderFilasEditorSubperiodos() {
    const contenedor = document.getElementById('lista-subperiodos-editor');
    const badgeSuma = document.getElementById('subperiodos-suma-badge');
    if (!contenedor) return;

    contenedor.innerHTML = '';
    let suma = 0;

    editorSubperiodosTemp.forEach((sp, idx) => {
        suma += Number(sp.porcentaje) || 0;
        const row = document.createElement('div');
        row.className = 'subperiodo-editor-row';
        row.innerHTML = `
            <input type="text" class="subperiodo-editor-input-name" value="${escapeHTML(sp.nombre)}" placeholder="Nombre del periodo" data-idx="${idx}">
            <div class="calc-input-adornment" style="width: 100px;">
                <input type="number" min="0" max="100" step="any" class="subperiodo-editor-input-pct" value="${sp.porcentaje}" data-idx="${idx}">
                <span class="calc-adornment-suffix">%</span>
            </div>
            ${editorSubperiodosTemp.length > 2 ? `
                <button type="button" class="btn-remove-subperiodo" data-idx="${idx}" title="Eliminar periodo">&times;</button>
            ` : '<div style="width: 32px;"></div>'}
        `;

        row.querySelector('.subperiodo-editor-input-name').addEventListener('input', (e) => {
            editorSubperiodosTemp[idx].nombre = e.target.value.trim() || `Periodo ${idx + 1}`;
        });

        row.querySelector('.subperiodo-editor-input-pct').addEventListener('input', (e) => {
            editorSubperiodosTemp[idx].porcentaje = parseFloat(e.target.value) || 0;
            actualizarSumaBadgeEditor();
        });

        const btnDel = row.querySelector('.btn-remove-subperiodo');
        if (btnDel) {
            btnDel.addEventListener('click', () => {
                editorSubperiodosTemp.splice(idx, 1);
                renderFilasEditorSubperiodos();
            });
        }

        contenedor.appendChild(row);
    });

    actualizarSumaBadgeEditor();
}

function actualizarSumaBadgeEditor() {
    const badgeSuma = document.getElementById('subperiodos-suma-badge');
    if (!badgeSuma) return;

    const suma = editorSubperiodosTemp.reduce((acc, cur) => acc + (Number(cur.porcentaje) || 0), 0);
    const sumaRed = Math.round(suma * 10) / 10;
    const esValido = Math.abs(sumaRed - 100.0) <= 0.1;

    badgeSuma.textContent = `Suma: ${sumaRed}% ${esValido ? '✓' : '(Debe ser 100%)'}`;
    badgeSuma.className = `pnl-badge ${esValido ? 'badge-success' : 'badge-danger'}`;
}

export function agregarPeriodoEditor() {
    if (editorSubperiodosTemp.length >= 8) {
        toast('Máximo 8 sub-periodos permitidos.', 'error');
        return;
    }
    const n = editorSubperiodosTemp.length + 1;
    editorSubperiodosTemp.push({
        id: 'sp_' + Date.now() + '_' + n,
        nombre: `Periodo ${n}`,
        porcentaje: 0.0
    });
    renderFilasEditorSubperiodos();
}

export function equilibrarPeriodosEditor() {
    const count = editorSubperiodosTemp.length;
    if (count <= 0) return;

    const basePct = Math.floor((100.0 / count) * 10) / 10;
    let sumaAcum = 0;
    editorSubperiodosTemp.forEach((sp, i) => {
        if (i === count - 1) {
            sp.porcentaje = Math.round((100.0 - sumaAcum) * 10) / 10;
        } else {
            sp.porcentaje = basePct;
            sumaAcum += basePct;
        }
    });
    renderFilasEditorSubperiodos();
}

export async function guardarSubdivisionPersonalizada() {
    const suma = editorSubperiodosTemp.reduce((acc, cur) => acc + (Number(cur.porcentaje) || 0), 0);
    if (Math.abs(suma - 100.0) > 0.2) {
        toast(`La suma de los periodos debe ser exactamente 100% (suma actual: ${Math.round(suma * 10) / 10}%).`, 'error');
        return;
    }

    const cfg = cachePresupuesto?.config || {};
    try {
        const res = await apiFetch('/api/presupuesto/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ingreso_base: cfg.ingreso_base,
                frecuencia: cfg.frecuencia,
                subdivision_tipo: 'personalizada',
                subdivision_config: editorSubperiodosTemp
            })
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            toast(err.detail || 'Error al guardar la subdivisión.', 'error');
            return;
        }

        toast('Sub-división personalizada aplicada.', 'ok');
        cerrarModalSubdivisionPersonalizada();
        cargarPresupuesto();
    } catch (e) {
        toast('Error de conexión al guardar subdivisión.', 'error');
    }
}

// -----------------------------------------------------------------------------
// Inicialización de Listeners y Window Exports
// -----------------------------------------------------------------------------

export function inicializarPresupuesto() {
    const formConfig = document.getElementById('form-presupuesto-config');
    if (formConfig) formConfig.addEventListener('submit', guardarConfigPresupuesto);

    const selFrec = document.getElementById('pres-frecuencia');
    if (selFrec) selFrec.addEventListener('change', alCambiarFrecuenciaPresupuesto);

    const selSub = document.getElementById('pres-subdivision-tipo');
    if (selSub) selSub.addEventListener('change', alCambiarSubdivisionSelect);

    const btnImportar = document.getElementById('btn-importar-fijos-pres');
    if (btnImportar) btnImportar.addEventListener('click', importarFijosPresupuesto);

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
    abrirModalSubdivisionPersonalizada,
    cerrarModalSubdivisionPersonalizada,
    agregarPeriodoEditor,
    equilibrarPeriodosEditor,
    guardarSubdivisionPersonalizada,
    aplicarPresetChip,
    aplicarPlantillaPresupuesto,
    abrirModalImportarFijos,
    cerrarModalImportarFijos,
    toggleSeleccionarTodosFijos,
    ejecutarImportarFijos,
    importarFijosPresupuesto,
    abrirModalExportarPresupuesto,
    cerrarModalExportarPresupuesto,
    toggleSeleccionarTodosExportar,
    ejecutarExportarPresupuesto,
    cerrarBannerFijosPresupuesto,
    guardarConfigPresupuesto
});
