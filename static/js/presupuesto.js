/**
 * static/js/presupuesto.js - Organizador de Presupuesto por Tarjetas y Porcentajes
 * Permite dividir el ingreso fijo (semanal, quincenal, mensual) en tarjetas con sincronización $ ⇄ %.
 */

import { apiFetch, escapeHTML, estado, fmt, toast } from './core.js';

let cachePresupuesto = null;

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

function renderizarVistaPresupuesto(data) {
    const cfg = data.config;
    const res = data.resumen;
    const tarjetas = data.tarjetas || [];
    const fijos = data.fijos_detectados || {};

    // 1. Inputs de configuración de ingreso base
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

    // Alerta o botón para importar desde Fijos y MSI
    const boxFijos = document.getElementById('pres-fijos-alert');
    if (boxFijos) {
        const hayFijos = (fijos.ingreso_fijo_periodo > 0 || fijos.gasto_fijo_periodo > 0 || fijos.cuota_msi_periodo > 0);
        boxFijos.classList.toggle('hidden', !hayFijos);
        if (hayFijos) {
            const lblTxt = document.getElementById('pres-fijos-alert-text');
            if (lblTxt) {
                lblTxt.innerHTML = `Detectado en Fijos y MSI: Nómina de <strong>${fmt(fijos.ingreso_fijo_periodo)}</strong> y gastos/MSI de <strong>${fmt(fijos.gasto_fijo_periodo + fijos.cuota_msi_periodo)}</strong> (${cfg.frecuencia}).`;
            }
        }
    }

    // 2. Tarjetas KPI de Resumen
    const setEl = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
    const frecTxt = FRECUENCIA_LABEL[cfg.frecuencia] || 'por periodo';

    setEl('lbl-pres-kpi-ingreso', fmt(cfg.ingreso_base));
    setEl('lbl-pres-kpi-ingreso-sub', `${cfg.frecuencia.toUpperCase()} · 100.0%`);

    setEl('lbl-pres-kpi-asignado', fmt(res.total_asignado_monto));
    const badgeAsig = document.getElementById('badge-pres-asignado');
    if (badgeAsig) {
        badgeAsig.textContent = `${res.total_asignado_pct.toFixed(1)}% del total`;
        badgeAsig.className = `pnl-badge ${res.excedido ? 'badge-danger' : 'badge-gold'}`;
    }

    setEl('lbl-pres-kpi-disponible', fmt(res.disponible_monto));
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
            ahorroInvMonto += t.monto;
            ahorroInvPct += t.porcentaje;
        }
    });
    setEl('lbl-pres-kpi-inv', fmt(ahorroInvMonto));
    const badgeInv = document.getElementById('badge-pres-inv');
    if (badgeInv) {
        badgeInv.textContent = `${ahorroInvPct.toFixed(1)}% tasa ahorro/inv.`;
        badgeInv.className = 'pnl-badge badge-success';
    }

    // 3. Barra visual segmentada de asignación (100% allocation track)
    renderBarraDistribucion(tarjetas, res, cfg);

    // 4. Grid de Tarjetas de Presupuesto
    renderGridTarjetas(tarjetas, cfg);
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

    // Porción restante sin asignar (o aviso de sobreasignación)
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

    grid.innerHTML = '';
    tarjetas.forEach(t => {
        const card = document.createElement('div');
        card.className = `card pres-card pres-card-accent-${t.color}`;
        const iconSvg = ICONOS_SVG[t.icono] || ICONOS_SVG['wallet'];
        const eq = t.equivalentes || {};

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
                <div class="pres-card-pct-badge pres-badge-${t.color}">${t.porcentaje.toFixed(1)}%</div>
                <div class="pres-card-amount">${fmt(t.monto)}</div>
                <div class="pres-card-freq-tag">${FRECUENCIA_LABEL[cfg.frecuencia] || ''}</div>
            </div>

            <div class="pres-card-progress-bar">
                <div class="pres-card-progress-fill pres-color-${t.color}" style="width: ${Math.min(100, t.porcentaje)}%"></div>
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

// -----------------------------------------------------------------------------
// Sincronización de Ingreso Base y Frecuencia
// -----------------------------------------------------------------------------

export async function guardarConfigPresupuesto(e) {
    if (e) e.preventDefault();
    const inp = document.getElementById('pres-ingreso-base');
    const sel = document.getElementById('pres-frecuencia');
    if (!inp || !sel) return;

    const ingreso_base = parseFloat(inp.value) || 0;
    const frecuencia = sel.value;

    try {
        const res = await apiFetch('/api/presupuesto/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ingreso_base, frecuencia })
        });
        if (!res.ok) {
            toast('No se pudo guardar la configuración.', 'error');
            return;
        }
        toast('Ingreso y frecuencia actualizados.', 'ok');
        cargarPresupuesto();
    } catch (err) {
        toast('Error de conexión al guardar configuración.', 'error');
    }
}

export async function importarFijosPresupuesto() {
    try {
        const res = await apiFetch('/api/presupuesto/importar-fijos', { method: 'POST' });
        if (!res.ok) {
            toast('No se pudieron importar los datos.', 'error');
            return;
        }
        toast('Datos importados de Fijos y MSI con éxito.', 'ok');
        cargarPresupuesto();
    } catch (err) {
        toast('Error al importar datos.', 'error');
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

// Sincronización bidireccional en el formulario del modal
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

// Chips sugeridos de categorías rápidas
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
// Inicialización de Listeners y Window Exports
// -----------------------------------------------------------------------------

export function inicializarPresupuesto() {
    const formConfig = document.getElementById('form-presupuesto-config');
    if (formConfig) formConfig.addEventListener('submit', guardarConfigPresupuesto);

    const selFrec = document.getElementById('pres-frecuencia');
    if (selFrec) selFrec.addEventListener('change', guardarConfigPresupuesto);

    const btnImportar = document.getElementById('btn-importar-fijos-pres');
    if (btnImportar) btnImportar.addEventListener('click', importarFijosPresupuesto);

    const formTarjeta = document.getElementById('form-tarjeta-presupuesto');
    if (formTarjeta) formTarjeta.addEventListener('submit', guardarTarjetaPresupuesto);

    const inpMonto = document.getElementById('tarjeta-pres-monto');
    const inpPct = document.getElementById('tarjeta-pres-pct');
    if (inpMonto) inpMonto.addEventListener('input', () => sincronizarInputsModal('monto'));
    if (inpPct) inpPct.addEventListener('input', () => sincronizarInputsModal('pct'));
}

Object.assign(window, {
    abrirModalCrearTarjeta,
    cerrarModalTarjetaPresupuesto,
    aplicarPresetChip,
    aplicarPlantillaPresupuesto,
    importarFijosPresupuesto,
    guardarConfigPresupuesto
});
