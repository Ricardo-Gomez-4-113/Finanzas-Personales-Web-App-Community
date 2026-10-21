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
    const libre = (estado.resumen && estado.resumen.libre_al_mes !== undefined) ? Number(estado.resumen.libre_al_mes) : null;
    const baseTxt = `${meses} pagos de ${fmt(cuota)} al mes`;
    if (libre !== null && cuota > libre) {
        prev.innerHTML = `${baseTxt}<br><span class="preview-warning"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg> Atención: La cuota mensual (${fmt(cuota)}) excede tu margen libre actual (${fmt(libre)}). Te dejará saldo negativo.</span>`;
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
        const libre = (estado.resumen && estado.resumen.libre_al_mes !== undefined) ? Number(estado.resumen.libre_al_mes) : null;
        if (libre !== null && cuota > libre) {
            const confirmar = confirm(`Esta compra a meses generará una cuota de ${fmt(cuota)} al mes, la cual supera tu remanente libre actual (${fmt(libre)} al mes) y causará déficit mensual.\n\n¿Deseas registrarla de todos modos?`);
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

function renderListaMsi(items) {
    const cont = document.getElementById('lista-msi');
    if (!cont) return;
    if (items.length === 0) {
        cont.innerHTML = '<p class="empty-state">No tienes compras a meses. Registra una laptop, celular o cualquier compra en cuotas.</p>';
        return;
    }
    cont.innerHTML = '';
    items.forEach(r => {
        const card = document.createElement('div');
        card.className = 'rec-item';
        const terminado = r.cuotas_generadas >= r.total_cuotas;
        card.innerHTML = `
            <div class="rec-item-head">
                <strong>${escapeHTML(r.descripcion)}</strong>
                <span class="badge-tag badge-msi">${terminado ? 'Terminado' : `Cuota ${r.cuotas_generadas} de ${r.total_cuotas}`}</span>
            </div>
            <div class="progress-bar-bg"><div class="progress-bar-fill"></div></div>
            <div class="rec-item-meta">
                <span>Pendiente: <strong>${fmt(r.deuda_pendiente)}</strong></span>
                <span>${terminado ? 'Compra liquidada' : `Próximo pago: <strong>${escapeHTML(r.proxima_fecha)}</strong>`}</span>
            </div>
            <div class="rec-item-actions">
                <button type="button" class="btn-secondary btn-sm" data-accion="editar" data-id="${r.id}">Editar</button>
                <button type="button" class="btn-secondary btn-sm" data-accion="eliminar" data-id="${r.id}">Eliminar</button>
            </div>`;
        card.querySelector('.progress-bar-fill').style.width = `${Math.min(100, r.progreso_pct || 0)}%`;
        cont.appendChild(card);
    });
}

function renderListaFijos(items) {
    const cont = document.getElementById('lista-fijos');
    if (!cont) return;
    if (items.length === 0) {
        cont.innerHTML = '<p class="empty-state">Aún no tienes ingresos ni gastos fijos. Agrega tu nómina o tu renta para ver tu libre al mes.</p>';
        return;
    }
    cont.innerHTML = '';
    items.forEach(r => {
        const card = document.createElement('div');
        card.className = 'rec-item';
        const esIngreso = r.tipo === 'ingreso';
        const badgeMeta = r.apartado_nombre ? `<span class="badge-tag badge-apartado" style="background: rgba(147, 51, 234, 0.15); color: #c084fc; border: 1px solid rgba(147, 51, 234, 0.3); display: inline-flex; align-items: center; gap: 4px;"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/></svg> Aporta a: ${escapeHTML(r.apartado_nombre)}</span>` : '';
        card.innerHTML = `
            <div class="rec-item-head">
                <strong>${escapeHTML(r.descripcion)}</strong>
                <div class="s-display-flex s-align-items-center s-gap-8px">
                    ${badgeMeta}
                    <span class="badge-tag ${r.activo ? 'badge-fijo' : ''}">${r.activo ? (esIngreso ? 'Ingreso' : 'Gasto') : 'Pausado'}</span>
                </div>
            </div>
            <div class="rec-item-meta">
                <span>${fmt(r.monto)} · ${FRECUENCIA_TXT[r.frecuencia] || r.frecuencia}</span>
                <span>Próxima fecha: <strong>${escapeHTML(r.proxima_fecha)}</strong></span>
            </div>
            <div class="rec-item-actions">
                <button type="button" class="btn-secondary btn-sm" data-accion="editar" data-id="${r.id}">Editar</button>
                <button type="button" class="btn-secondary btn-sm" data-accion="${r.activo ? 'pausar' : 'reanudar'}" data-id="${r.id}">${r.activo ? 'Pausar' : 'Reanudar'}</button>
                <button type="button" class="btn-secondary btn-sm" data-accion="eliminar" data-id="${r.id}">Eliminar</button>
            </div>`;
        cont.appendChild(card);
    });
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
            tituloEl.textContent = 'Editar Compra a Meses (MSI)';
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

    document.getElementById('modal-editar-recurrente')?.classList.remove('hidden');
}

export function cerrarModalEditarRecurrente() {
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

export async function cargarRecurrentes() {
    try {
        const [resRec, resResumen, resApartados] = await Promise.all([
            apiFetch('/api/recurrentes'),
            apiFetch(`/api/resumen?moneda_base=${encodeURIComponent(estado.monedaGlobal)}`),
            apiFetch('/api/apartados')
        ]);
        const items = await resRec.json();
        const resumen = await resumenJson(resResumen);
        const apartados = resApartados.ok ? await resApartados.json() : [];
        estado.cache.apartados = apartados;
        estado.cache.recurrentes = items;

        const ingreso = Number(resumen.ingreso_fijo_mensual) || 0;
        const gastoFijo = Number(resumen.gasto_fijo_mensual) || 0;
        const cuotaMsi = Number(resumen.cuota_msi_mensual) || 0;
        const gastoTotal = Number(resumen.compromiso_mensual_total) || (gastoFijo + cuotaMsi);
        const libre = Number(resumen.libre_al_mes) !== undefined ? Number(resumen.libre_al_mes) : (ingreso - gastoTotal);
        estado.resumen = resumen;

        const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = fmt(v); };
        setTxt('lbl-rec-ingreso', ingreso);
        setTxt('lbl-rec-gasto', gastoTotal);
        setTxt('lbl-rec-deuda', resumen.deuda_msi || 0);
        setTxt('lbl-rec-libre', libre);

        // Alerta visual de déficit mensual si compromisos superan ingresos
        const elLibre = document.getElementById('lbl-rec-libre');
        if (elLibre) {
            const cardLibre = elLibre.closest('.card');
            if (cardLibre) {
                const subtext = cardLibre.querySelector('.card-subtext');
                if (libre < 0) {
                    cardLibre.classList.add('card-deficit');
                    if (subtext) subtext.textContent = 'Déficit: compromisos superan ingresos';
                } else {
                    cardLibre.classList.remove('card-deficit');
                    if (subtext) subtext.textContent = 'Remanente tras fijos y MSI';
                }
            }
        }

        // Sincronizar etiquetas de caja en la pestaña unificada
        if (resumen.balance !== undefined) {
            setTxt('lbl-balance', resumen.balance);
            const elBal = document.getElementById('lbl-balance');
            if (elBal) {
                const cardBal = elBal.closest('.card');
                if (cardBal) {
                    if (Number(resumen.balance) < 0) {
                        cardBal.classList.add('card-deficit');
                    } else {
                        cardBal.classList.remove('card-deficit');
                    }
                }
            }
        }
        if (resumen.ingresos !== undefined) setTxt('lbl-ingresos', resumen.ingresos);
        if (resumen.gastos !== undefined) setTxt('lbl-gastos', resumen.gastos);

        renderListaMsi(items.filter(r => r.es_msi && r.cuotas_generadas < r.total_cuotas));
        renderListaFijos(items.filter(r => !r.es_msi));
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
    const listas = ['lista-msi', 'lista-fijos'].map(id => document.getElementById(id)).filter(Boolean);
    listas.forEach(l => l.addEventListener('click', accionRecurrente));
    const fecha = document.getElementById('rec-fecha');
    restaurarFechaHoy();
    actualizarVistaModo();
}

Object.assign(window, {
    abrirModalEditarRecurrente,
    cerrarModalEditarRecurrente,
    guardarEdicionRecurrente
});

inicializarRecurrentes();
