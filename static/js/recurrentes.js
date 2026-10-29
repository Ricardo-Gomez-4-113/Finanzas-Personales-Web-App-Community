/**
 * static/js/recurrentes.js - Pestaña "Fijos y MSI": nómina, gastos fijos y compras a meses.
 */

import { apiFetch, escapeHTML, estado, fmt, formatErrorDetail, recargar, toast } from './core.js';


const FRECUENCIA_TXT = { semanal: 'Semanal', quincenal: 'Quincenal', mensual: 'Mensual' };


function modoActual() {
    return document.querySelector('input[name="rec-modo"]:checked')?.value || 'gasto';
}

function mesesElegidos() {
    const sel = document.getElementById('rec-meses').value;
    if (sel === 'otro') return parseInt(document.getElementById('rec-meses-otro').value, 10) || 0;
    return parseInt(sel, 10);
}


// -----------------------------------------------------------------------------
// Formulario
// -----------------------------------------------------------------------------

function llenarCategoriasRec() {
    const sel = document.getElementById('rec-categoria');
    if (!sel) return;
    const modo = modoActual();
    const tipo = modo === 'ingreso' ? 'ingreso' : 'gasto';
    const actual = sel.value;
    const cats = (estado.cache.categorias || []).filter(c => c.tipo === tipo);
    sel.innerHTML = cats.map(c => `<option value="${c.id}">${escapeHTML(c.nombre)}</option>`).join('');
    if (actual && [...sel.options].some(o => o.value === actual)) sel.value = actual;
}

function llenarApartadosRec() {
    const apartados = estado.cache.apartados || [];
    const opts = ['<option value="">-- Ninguno (gasto común) --</option>']
        .concat(apartados.map(a => `<option value="${a.id}">${escapeHTML(a.nombre)} (${fmt(a.monto_actual)}${a.meta_monto ? ' / ' + fmt(a.meta_monto) : ''})</option>`))
        .join('');

    const selForm = document.getElementById('rec-apartado-id');
    if (selForm) {
        const val = selForm.value;
        selForm.innerHTML = opts;
        if (val && [...selForm.options].some(o => o.value === val)) selForm.value = val;
    }

    const selModal = document.getElementById('modal-edit-rec-apartado');
    if (selModal) {
        selModal.innerHTML = opts;
    }
}

function actualizarVistaModo() {
    const msi = modoActual() === 'msi';
    const ingreso = modoActual() === 'ingreso';
    document.getElementById('rec-campos-fijo').classList.toggle('hidden', msi);
    document.getElementById('rec-campos-msi').classList.toggle('hidden', !msi);
    const grpApartado = document.getElementById('rec-apartado-group');
    if (grpApartado) grpApartado.classList.toggle('hidden', msi || ingreso);
    document.getElementById('rec-monto').required = !msi;
    document.getElementById('rec-total').required = msi;
    document.getElementById('rec-desc').placeholder = msi ? 'Ej. Laptop, Celular' : 'Ej. Nómina, Renta, Netflix';
    llenarCategoriasRec();
    llenarApartadosRec();
    actualizarPreview();
}

// Remanente del periodo más ajustado entre los próximos: una cuota mayor provocaría déficit en ese periodo
function margenPeriodos() {
    const ps = estado.resumen && Array.isArray(estado.resumen.periodos) ? estado.resumen.periodos : [];
    return ps.length ? Math.min(...ps.map(p => Number(p.remanente))) : null;
}

function actualizarPreview() {
    const prev = document.getElementById('rec-preview');
    const otro = document.getElementById('rec-meses-otro-wrap');
    const sel = document.getElementById('rec-meses').value;
    otro.classList.toggle('hidden', sel !== 'otro');
    if (modoActual() !== 'msi' || !prev) return;
    const total = parseFloat(document.getElementById('rec-total').value);
    const meses = mesesElegidos();
    if (!total || total <= 0 || !meses || meses < 2) {
        prev.textContent = '';
        return;
    }
    const cuota = Math.round((total / meses) * 100) / 100;
    const libre = margenPeriodos();
    const baseTxt = `${meses} pagos de ${fmt(cuota)} al mes`;
    if (libre !== null && cuota > libre) {
        prev.innerHTML = `${baseTxt}<br><span class="preview-warning"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg> Atención: la cuota (${fmt(cuota)}) supera el libre de tu periodo más ajustado (${fmt(libre)}). Ese periodo quedaría en déficit.</span>`;
    } else {
        prev.textContent = baseTxt;
    }
}

async function guardarRecurrente(e) {
    e.preventDefault();
    const btn = document.getElementById('btn-guardar-recurrente');
    const modo = modoActual();
    const descripcion = document.getElementById('rec-desc').value.trim();
    const fecha = document.getElementById('rec-fecha').value;
    const categoriaId = Number(document.getElementById('rec-categoria').value) || undefined;
    const selApa = document.getElementById('rec-apartado-id');
    const apartadoId = (modo === 'gasto' && selApa && selApa.value) ? Number(selApa.value) : null;

    let body;
    if (modo === 'msi') {
        const total = parseFloat(document.getElementById('rec-total').value);
        const meses = mesesElegidos();
        if (!total || total <= 0) return toast('Ingresa el monto total de la compra.', 'error');
        if (!meses || meses < 2 || meses > 60) return toast('Los meses deben estar entre 2 y 60.', 'error');
        const cuota = Math.round((total / meses) * 100) / 100;
        const libre = margenPeriodos();
        if (libre !== null && cuota > libre) {
            const confirmar = confirm(`Esta compra a meses generará una cuota de ${fmt(cuota)} al mes, que supera el libre de tu periodo más ajustado (${fmt(libre)}) y lo dejaría en déficit.\n\n¿Deseas registrarla de todos modos?`);
            if (!confirmar) return;
        }
        body = { descripcion, monto_total: total, meses, fecha_inicio: fecha, categoria_id: categoriaId };
    } else {
        const monto = parseFloat(document.getElementById('rec-monto').value);
        if (!monto || monto <= 0) return toast('Ingresa un monto mayor a 0.', 'error');
        body = {
            descripcion,
            tipo: modo === 'ingreso' ? 'ingreso' : 'gasto',
            frecuencia: document.getElementById('rec-frecuencia').value,
            monto,
            fecha_inicio: fecha,
            categoria_id: categoriaId,
            apartado_id: apartadoId
        };
    }

    btn.disabled = true;
    try {
        const res = await apiFetch('/api/recurrentes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            toast(formatErrorDetail(err.detail, 'No se pudo guardar el registro.'), 'error');
            return;
        }
        toast(modo === 'msi' ? 'Compra a meses registrada.' : 'Registro fijo guardado.', 'ok');
        document.getElementById('form-recurrente').reset();
        document.getElementById('rec-frecuencia').value = 'mensual';
        document.getElementById('rec-meses').value = '12';
        restaurarFechaHoy();
        actualizarVistaModo();
        recargar();
    } catch (error) {
        toast('Error de conexión al guardar.', 'error');
    } finally {
        btn.disabled = false;
    }
}


// -----------------------------------------------------------------------------
// Listas y tarjetas
// -----------------------------------------------------------------------------

// Fijos y compras a meses por periodo de cobro: el backend decide periodos, estados y cuotas
const ESTADO_FIJO_TXT = { aplicado: 'Aplicado', programado: 'Programado', pausado: 'Pausado' };

function fechaPeriodo(iso) {
    return new Date(`${iso}T12:00:00`).toLocaleDateString('es-MX', { day: 'numeric', month: 'short' });
}

function etiquetaFijo(i) {
    if (i.es_msi) return i.cuota ? `Cuota ${i.cuota} de ${i.total_cuotas}` : 'Cuota aplicada';
    return i.tipo === 'ingreso' ? 'Ingreso fijo' : 'Gasto fijo';
}

function accionesFijo(i) {
    const boton = (accion, texto, extra = '') => `<button type="button" class="btn-secondary btn-sm" data-accion="${accion}" data-id="${i.id}" ${extra}>${texto}</button>`;
    if (i.estado === 'aplicado') return '';
    // Saltar, pausar y eliminar actúan sobre el fijo entero: solo se ofrecen en su primera fila
    const primera = i.estado === 'pausado' || i.primera_de_su_fijo;
    if (!primera) return boton('editar', 'Editar');
    const saltar = i.estado === 'programado' ? boton('saltar', 'Saltar', `title="Saltar próximo cobro (${escapeHTML(i.fecha)})"`) : '';
    const pausa = i.estado === 'pausado' ? boton('reanudar', 'Reanudar') : boton('pausar', 'Pausar');
    return boton('editar', 'Editar') + saltar + pausa + boton('eliminar', 'Eliminar');
}

function filaFijo(i) {
    const origen = [
        i.meta ? `Aporta a ${i.apartado_nombre || 'una meta'}` : '',
        i.tarjeta_nombre ? `Presupuesto: ${i.tarjeta_nombre}` : '',
    ].filter(Boolean).map(t => `<span class="fijo-origen">${escapeHTML(t)}</span>`).join('');
    const signo = i.tipo === 'ingreso' ? '+' : '';
    const acciones = accionesFijo(i);
    return `
        <div class="fijo-fila fijo-${i.estado}">
            <div class="fijo-fila-main">
                <div class="fijo-fila-titulo">
                    <strong>${escapeHTML(i.descripcion)}</strong>
                    <span class="badge-tag ${i.estado === 'programado' ? 'badge-fijo' : ''}">${ESTADO_FIJO_TXT[i.estado]}</span>
                </div>
                <div class="fijo-fila-meta">
                    <span>${escapeHTML(etiquetaFijo(i))}</span>
                    ${origen}
                </div>
            </div>
            <div class="fijo-fila-monto">
                <strong>${signo}${fmt(i.monto)}</strong>
                <span>${i.estado === 'pausado' ? 'Próxima fecha ' : ''}${escapeHTML(i.fecha)}</span>
            </div>
            ${acciones ? `<div class="rec-item-actions">${acciones}</div>` : ''}
        </div>`;
}

function bloqueFijos(titulo, items) {
    if (items.length === 0) return '';
    return `<div class="fijos-bloque"><h4>${titulo}</h4>${items.map(filaFijo).join('')}</div>`;
}

function renderFijosPorPeriodo(data) {
    const cont = document.getElementById('fijos-periodos');
    const bloqueP = document.getElementById('fijos-pausados-bloque');
    const contP = document.getElementById('fijos-pausados');
    if (!cont) return;
    const periodos = data.periodos || [];
    if (periodos.every(p => p.items.length === 0) && !(data.pausados || []).length) {
        cont.innerHTML = '<p class="empty-state">Aún no tienes ingresos ni gastos fijos. Agrega tu nómina o tu renta para verlos por periodo.</p>';
    } else {
        cont.innerHTML = periodos.map(p => {
            const t = p.totales;
            const ingresos = p.items.filter(i => i.tipo === 'ingreso');
            const fijos = p.items.filter(i => i.tipo !== 'ingreso' && !i.es_msi);
            const msi = p.items.filter(i => i.tipo !== 'ingreso' && i.es_msi);
            return `
                <section class="fijos-periodo${p.en_curso ? ' fijos-periodo-actual' : ''}">
                    <header class="fijos-periodo-head">
                        <h3>${fechaPeriodo(p.inicio)} – ${fechaPeriodo(p.fin)}</h3>
                        ${p.en_curso ? '<span class="badge-tag badge-fijo">En curso</span>' : ''}
                    </header>
                    <p class="fijos-periodo-totales">Ingresos ${fmt(t.ingresos)} · Gastos fijos ${fmt(t.fijos)} · Compras a meses ${fmt(t.msi)} · Por pagar ${fmt(t.por_pagar)}</p>
                    ${bloqueFijos('Ingresos y gastos fijos', [...ingresos, ...fijos])}
                    ${bloqueFijos('Compras a meses', msi)}
                    ${p.items.length === 0 ? '<p class="fijos-bloque-ayuda">Sin cargos en este periodo.</p>' : ''}
                </section>`;
        }).join('');
    }
    const pausados = data.pausados || [];
    if (bloqueP && contP) {
        bloqueP.classList.toggle('hidden', pausados.length === 0);
        contP.innerHTML = pausados.map(filaFijo).join('');
    }
}

let recurrenteSeleccionadoParaSaltar = null;

export function abrirModalSaltarMsi(r) {
    recurrenteSeleccionadoParaSaltar = r;
    const modal = document.getElementById('modal-saltar-msi');
    if (!modal) return;
    const desc = document.getElementById('modal-saltar-msi-desc');
    const txtPagada = document.getElementById('modal-saltar-msi-pagada-txt');
    const proximaCuotaNum = (r.cuotas_generadas || 0) + 1;
    if (desc) {
        desc.innerHTML = `¿Cómo deseas proceder con el cobro de <strong>${escapeHTML(r.descripcion)}</strong> programado para el <strong>${escapeHTML(r.proxima_fecha)}</strong>?`;
    }
    if (txtPagada) {
        txtPagada.textContent = `Computa la cuota ${proximaCuotaNum} de ${r.total_cuotas} como liquidada fuera de la app y posterga la fecha al siguiente mes sin generar cobro en tus movimientos.`;
    }
    modal.classList.remove('hidden');
}

export function cerrarModalSaltarMsi() {
    const modal = document.getElementById('modal-saltar-msi');
    if (modal) modal.classList.add('hidden');
    recurrenteSeleccionadoParaSaltar = null;
}

export async function ejecutarSaltarRecurrente(id, contabilizar_cuota = false) {
    try {
        const res = await apiFetch(`/api/recurrentes/${id}/saltar`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ contabilizar_cuota })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            toast(err.detail || 'No se pudo saltar el pago.', 'error');
            return;
        }
        const data = await res.json();
        const nuevaFecha = data.proxima_fecha;
        if (data.es_msi && contabilizar_cuota) {
            if (data.cuotas_generadas >= data.total_cuotas) {
                toast(`¡Compra liquidada! Cuota ${data.cuotas_generadas}/${data.total_cuotas} registrada por fuera.`, 'ok');
            } else {
                toast(`Cuota ${data.cuotas_generadas}/${data.total_cuotas} marcada como pagada. Próximo cobro: ${nuevaFecha}`, 'ok');
            }
        } else {
            toast(`Pago saltado con éxito. Próximo ciclo: ${nuevaFecha}`, 'ok');
        }
        recargar();
    } catch (e) {
        console.error('Error al saltar pago:', e);
        toast('Error de red al saltar el pago.', 'error');
    }
}

async function accionRecurrente(e) {
    const btn = e.target.closest('button[data-accion]');
    if (!btn) return;
    const id = btn.dataset.id;
    const accion = btn.dataset.accion;
    if (accion === 'editar') {
        abrirModalEditarRecurrente(id);
        return;
    }
    if (accion === 'saltar') {
        const item = (estado.cache.recurrentes || []).find(r => r.id === parseInt(id, 10));
        if (!item) return;
        if (item.es_msi) {
            abrirModalSaltarMsi(item);
        } else {
            const textoTipo = item.tipo === 'ingreso' ? 'el próximo ingreso' : 'el próximo pago';
            const pregunta = `¿Saltar ${textoTipo} de "${item.descripcion}" (${item.proxima_fecha})?\n\nLa fecha avanzará al siguiente período (${FRECUENCIA_TXT[item.frecuencia] || item.frecuencia}) sin registrar movimientos en tus cuentas.`;
            if (confirm(pregunta)) {
                ejecutarSaltarRecurrente(id, false);
            }
        }
        return;
    }
    if (accion === 'eliminar') {
        if (!confirm('¿Eliminar este registro? Los movimientos ya generados se conservan.')) return;
        const res = await apiFetch(`/api/recurrentes/${id}`, { method: 'DELETE' });
        if (!res.ok) return toast('No se pudo eliminar el registro.', 'error');
    } else {
        const res = await apiFetch(`/api/recurrentes/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ activo: accion === 'reanudar' })
        });
        if (!res.ok) return toast('No se pudo actualizar el registro.', 'error');
    }
    recargar();
}


// -----------------------------------------------------------------------------
// Modal de Edición
// -----------------------------------------------------------------------------

export function abrirModalEditarRecurrente(id) {
    const item = (estado.cache.recurrentes || []).find(r => String(r.id) === String(id));
    if (!item) return toast('Registro no encontrado.', 'error');

    const inpId = document.getElementById('modal-edit-rec-id');
    const inpDesc = document.getElementById('modal-edit-rec-desc');
    const inpMonto = document.getElementById('modal-edit-rec-monto');
    const hintMonto = document.getElementById('modal-edit-rec-monto-hint');
    const inpFecha = document.getElementById('modal-edit-rec-fecha');
    const selFrec = document.getElementById('modal-edit-rec-frecuencia');
    const wrapFrec = document.getElementById('modal-edit-rec-frecuencia-wrap');
    const selCat = document.getElementById('modal-edit-rec-categoria');
    const selApa = document.getElementById('modal-edit-rec-apartado');
    const wrapApa = document.getElementById('modal-edit-rec-apartado-wrap');
    const tituloEl = document.getElementById('modal-editar-recurrente-titulo-txt');

    if (inpId) inpId.value = item.id;
    if (inpDesc) inpDesc.value = item.descripcion || '';

    if (inpMonto) {
        inpMonto.value = item.monto;
        inpMonto.disabled = !!item.es_msi;
    }
    if (hintMonto) {
        hintMonto.classList.toggle('hidden', !item.es_msi);
    }

    if (inpFecha) inpFecha.value = item.proxima_fecha || '';

    // Frecuencia
    if (wrapFrec) wrapFrec.classList.toggle('hidden', !!item.es_msi);
    if (selFrec) {
        selFrec.disabled = !!item.es_msi;
        selFrec.value = item.frecuencia || 'mensual';
    }

    // Título dinámico
    if (tituloEl) {
        if (item.es_msi) {
            tituloEl.textContent = 'Editar compra a meses';
        } else if (item.tipo === 'ingreso') {
            tituloEl.textContent = 'Editar Ingreso Fijo';
        } else {
            tituloEl.textContent = 'Editar Gasto Fijo';
        }
    }

    // Llenar categorías para el tipo correspondiente
    const tipo = item.tipo === 'ingreso' ? 'ingreso' : 'gasto';
    const cats = (estado.cache.categorias || []).filter(c => c.tipo === tipo);
    if (selCat) {
        selCat.innerHTML = cats.map(c => `<option value="${c.id}">${escapeHTML(c.nombre)}</option>`).join('');
        selCat.value = item.categoria_id || (cats[0]?.id || 1);
    }

    // Llenar apartados
    llenarApartadosRec();
    if (item.tipo === 'ingreso' || item.es_msi) {
        wrapApa?.classList.add('hidden');
        if (selApa) selApa.value = '';
    } else {
        wrapApa?.classList.remove('hidden');
        if (selApa) selApa.value = item.apartado_id || '';
    }

    // Información de sub-períodos si está vinculado a una tarjeta de presupuesto dividida
    const subInfoEl = document.getElementById('modal-edit-rec-subperiodos-info');
    if (subInfoEl) {
        if (item.presupuesto_tarjeta_id) {
            const hermanos = (estado.cache.recurrentes || []).filter(h => h.presupuesto_tarjeta_id === item.presupuesto_tarjeta_id);
            const totalHermanos = hermanos.reduce((acc, h) => acc + (parseFloat(h.monto) || 0), 0);
            const totalMostrar = item.tarjeta_monto_total ? item.tarjeta_monto_total : totalHermanos;

            subInfoEl.innerHTML = `
                <div style="background: rgba(6, 182, 212, 0.08); border: 1px solid rgba(6, 182, 212, 0.25); border-radius: 8px; padding: 10px 12px; margin-bottom: 14px;">
                    <div style="display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px;">
                        <span style="font-weight: 600; font-size: 0.85rem; color: #22d3ee; display: inline-flex; align-items: center; gap: 5px;">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>
                            Tarjeta de Presupuesto: ${escapeHTML(item.tarjeta_nombre || 'Dividida')}
                        </span>
                        <span style="font-size: 0.8rem; font-weight: 600; color: var(--text-main); font-variant-numeric: tabular-nums;">
                            Total: ${fmt(totalMostrar)}
                        </span>
                    </div>
                    <div style="font-size: 0.76rem; color: var(--text-muted); margin-bottom: 6px;">
                        Esta tarjeta se compone de cargos independientes. Puedes ver las partes juntas, aunque cada una se procesa por separado:
                    </div>
                    <div style="display: flex; flex-direction: column; gap: 4px;">
                        ${hermanos.map(h => `
                            <div style="display: flex; align-items: center; justify-content: space-between; padding: 5px 8px; border-radius: 4px; font-size: 0.78rem; background: ${h.id === item.id ? 'rgba(6, 182, 212, 0.15)' : 'rgba(255, 255, 255, 0.03)'}; border: ${h.id === item.id ? '1px solid rgba(6, 182, 212, 0.4)' : '1px solid transparent'};">
                                <span style="font-weight: ${h.id === item.id ? '600' : '400'}; color: ${h.id === item.id ? '#22d3ee' : 'var(--text-main)'};">
                                    ${escapeHTML(h.descripcion)} ${h.id === item.id ? '← (editando)' : ''}
                                </span>
                                <span style="font-variant-numeric: tabular-nums; font-weight: 600; color: var(--text-main);">
                                    ${fmt(h.monto)} · Día ${h.dia || '--'} (${escapeHTML(h.proxima_fecha)})
                                </span>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
            subInfoEl.classList.remove('hidden');
        } else {
            subInfoEl.innerHTML = '';
            subInfoEl.classList.add('hidden');
        }
    }

    document.getElementById('modal-editar-recurrente')?.classList.remove('hidden');
}

export function cerrarModalEditarRecurrente() {
    const subInfoEl = document.getElementById('modal-edit-rec-subperiodos-info');
    if (subInfoEl) {
        subInfoEl.innerHTML = '';
        subInfoEl.classList.add('hidden');
    }
    document.getElementById('modal-editar-recurrente')?.classList.add('hidden');
}

export async function guardarEdicionRecurrente(e) {
    if (e && e.preventDefault) e.preventDefault();
    const idEl = document.getElementById('modal-edit-rec-id');
    const descEl = document.getElementById('modal-edit-rec-desc');
    const fechaEl = document.getElementById('modal-edit-rec-fecha');
    const catEl = document.getElementById('modal-edit-rec-categoria');
    const inpMonto = document.getElementById('modal-edit-rec-monto');
    const selApa = document.getElementById('modal-edit-rec-apartado');
    const wrapApa = document.getElementById('modal-edit-rec-apartado-wrap');
    const selFrec = document.getElementById('modal-edit-rec-frecuencia');

    const id = idEl ? idEl.value : null;
    const desc = descEl ? descEl.value.trim() : '';
    const fecha = fechaEl ? fechaEl.value : '';
    const catId = catEl ? (Number(catEl.value) || undefined) : undefined;

    if (!id) return toast('Identificador de registro no válido.', 'error');
    if (!desc) return toast('Ingresa una descripción.', 'error');
    if (!fecha) return toast('Ingresa la próxima fecha de cargo.', 'error');

    const body = {
        descripcion: desc,
        proxima_fecha: fecha,
        categoria_id: catId
    };

    if (inpMonto && !inpMonto.disabled) {
        const monto = parseFloat(inpMonto.value);
        if (!monto || monto <= 0) return toast('Ingresa un monto válido.', 'error');
        body.monto = monto;
    }

    if (selFrec && !selFrec.disabled && selFrec.value) {
        body.frecuencia = selFrec.value;
    }

    const wrapApaActual = document.getElementById('modal-edit-rec-apartado-wrap');
    if (selApa && wrapApaActual && !wrapApaActual.classList.contains('hidden')) {
        body.apartado_id = selApa.value ? Number(selApa.value) : null;
    }

    const btn = document.getElementById('btn-modal-edit-rec-guardar');
    if (btn) btn.disabled = true;

    try {
        const res = await apiFetch(`/api/recurrentes/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            toast(formatErrorDetail(err.detail, 'No se pudo guardar la edición.'), 'error');
            return;
        }
        toast('Registro actualizado con éxito.', 'ok');
        cerrarModalEditarRecurrente();
        recargar();
    } catch (err) {
        toast('Error de conexión al actualizar.', 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}


// -----------------------------------------------------------------------------
// Carga
// -----------------------------------------------------------------------------

// Selector de periodo de las tarjetas de Movimientos. Los periodos vienen del resumen (empiezan igual que los de Fijos)
let periodosKpi = [];
let inicioKpiSeleccionado = null;

function llenarSelectorPeriodo(periodos) {
    periodosKpi = periodos;
    const sel = document.getElementById('sel-periodo-movimientos');
    if (!sel) return;
    const previo = inicioKpiSeleccionado;
    const enCurso = periodos.find(p => p.en_curso) || periodos[0];
    inicioKpiSeleccionado = periodos.some(p => p.inicio === previo) ? previo : (enCurso ? enCurso.inicio : null);
    sel.innerHTML = periodos.map(p => `<option value="${p.inicio}">${fechaPeriodo(p.inicio)} – ${fechaPeriodo(p.fin)}${p.en_curso ? ' (en curso)' : ''}</option>`).join('');
    sel.value = inicioKpiSeleccionado || '';
    if (!sel.dataset.listo) {
        sel.addEventListener('change', () => {
            inicioKpiSeleccionado = sel.value;
            pintarKpisPeriodo();
        });
        sel.dataset.listo = '1';
    }
}

function pintarKpisPeriodo() {
    const p = periodosKpi.find(x => x.inicio === inicioKpiSeleccionado);
    if (!p) return;
    const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = fmt(v); };
    setTxt('lbl-rec-ingreso', p.ingresos.total);
    setTxt('lbl-rec-gastos-normales', p.gastos.normales);
    setTxt('lbl-rec-fijos', p.gastos.fijos);
    setTxt('lbl-rec-msi', p.gastos.msi);
    const sub = document.getElementById('lbl-rec-ingreso-sub');
    if (sub) sub.textContent = `Recibido ${fmt(p.ingresos.recibido)} · por recibir ${fmt(p.ingresos.por_recibir)}`;
}

export async function cargarRecurrentes() {
    try {
        const [resRec, resResumen, resApartados, resPeriodos] = await Promise.all([
            apiFetch('/api/recurrentes'),
            apiFetch(`/api/resumen?moneda_base=${encodeURIComponent(estado.monedaGlobal)}`),
            apiFetch('/api/apartados'),
            apiFetch('/api/recurrentes/periodos?n=3')
        ]);
        const items = await resRec.json();
        const porPeriodo = resPeriodos.ok ? await resPeriodos.json() : { periodos: [], pausados: [] };
        const resumen = await resumenJson(resResumen);
        const apartados = resApartados.ok ? await resApartados.json() : [];
        estado.cache.apartados = apartados;
        estado.cache.recurrentes = items;

        estado.resumen = resumen;

        // Las cifras de arriba son las del periodo elegido en el selector (por defecto, el periodo en curso)
        const periodos = Array.isArray(resumen.periodos) ? resumen.periodos : [];
        llenarSelectorPeriodo(periodos);
        pintarKpisPeriodo();
        const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = fmt(v); };
        setTxt('lbl-fijos-deuda', resumen.deuda_msi || 0);


        renderFijosPorPeriodo(porPeriodo);
        llenarCategoriasRec();
        llenarApartadosRec();
    } catch (error) {
        console.error('No se pudieron cargar los fijos y MSI:', error);
    }
}

async function resumenJson(res) {
    return res.ok ? res.json() : {};
}


function restaurarFechaHoy() {
    const fecha = document.getElementById('rec-fecha');
    if (!fecha) return;
    const d = new Date();
    fecha.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function inicializarRecurrentes() {
    document.querySelectorAll('input[name="rec-modo"]').forEach(r => r.addEventListener('change', actualizarVistaModo));
    ['rec-total', 'rec-meses', 'rec-meses-otro'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', actualizarPreview);
        if (el) el.addEventListener('change', actualizarPreview);
    });
    const form = document.getElementById('form-recurrente');
    if (form) form.addEventListener('submit', guardarRecurrente);
    const formEdit = document.getElementById('form-editar-recurrente');
    if (formEdit) formEdit.addEventListener('submit', guardarEdicionRecurrente);
    const listas = ['fijos-periodos', 'fijos-pausados'].map(id => document.getElementById(id)).filter(Boolean);
    listas.forEach(l => l.addEventListener('click', accionRecurrente));
    const fecha = document.getElementById('rec-fecha');
    restaurarFechaHoy();
    actualizarVistaModo();

    const btnPagada = document.getElementById('btn-saltar-msi-pagada');
    if (btnPagada) {
        btnPagada.addEventListener('click', () => {
            if (recurrenteSeleccionadoParaSaltar) {
                const id = recurrenteSeleccionadoParaSaltar.id;
                cerrarModalSaltarMsi();
                ejecutarSaltarRecurrente(id, true);
            }
        });
    }
    const btnAplazar = document.getElementById('btn-saltar-msi-aplazar');
    if (btnAplazar) {
        btnAplazar.addEventListener('click', () => {
            if (recurrenteSeleccionadoParaSaltar) {
                const id = recurrenteSeleccionadoParaSaltar.id;
                cerrarModalSaltarMsi();
                ejecutarSaltarRecurrente(id, false);
            }
        });
    }
}

Object.assign(window, {
    abrirModalEditarRecurrente,
    cerrarModalEditarRecurrente,
    guardarEdicionRecurrente,
    abrirModalSaltarMsi,
    cerrarModalSaltarMsi,
    ejecutarSaltarRecurrente
});

inicializarRecurrentes();
