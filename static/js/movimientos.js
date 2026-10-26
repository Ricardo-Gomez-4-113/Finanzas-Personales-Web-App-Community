/**
 * static/js/movimientos.js - Formulario, categorías, tabla con filtros y edición de movimientos de caja.
 */

import { apiFetch, escapeHTML, estado, fmt, formatErrorDetail, recargar, toast } from './core.js';


// Valor especial del select de categoría para crear una nueva
const NUEVA_CATEGORIA = '__nueva__';


// =============================================================================
// 5. CARGA DE DATOS FINANCIEROS DEL TENANT ACTIVO
// =============================================================================

export function obtenerFechaLocalISO() {
    const d = new Date();
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}


export const inputFecha = document.getElementById('fecha');

if (inputFecha) inputFecha.value = obtenerFechaLocalISO();


// =============================================================================
// 5.1 CATEGORÍAS
// =============================================================================

/** Llena un select de categorías filtrando por tipo. `conNueva` agrega la opción de crear. */
function llenarSelect(id, tipo, conNueva) {
    const sel = document.getElementById(id);
    if (!sel) return;
    const actual = sel.value;
    const opciones = (estado.cache.categorias || []).filter(c => !tipo || c.tipo === tipo);
    sel.innerHTML = opciones.map(c => `<option value="${c.id}">${escapeHTML(c.nombre)}</option>`).join('');
    if (conNueva) sel.insertAdjacentHTML('beforeend', `<option value="${NUEVA_CATEGORIA}">+ Nueva categoría…</option>`);
    if (actual && [...sel.options].some(o => o.value === actual)) sel.value = actual;
}

function llenarFiltroCategorias() {
    const sel = document.getElementById('filtro-categoria');
    if (!sel) return;
    const actual = sel.value;
    const opciones = (estado.cache.categorias || []).map(c => `<option value="${c.id}">${escapeHTML(c.nombre)}</option>`);
    sel.innerHTML = '<option value="">Todas las categorías</option>' + opciones.join('');
    if (actual) sel.value = actual;
}

function llenarSelectsCategoria() {
    llenarSelect('categoria', document.getElementById('tipo')?.value || 'gasto', true);
    llenarSelect('modal-edit-trans-categoria', document.getElementById('modal-edit-trans-tipo')?.value || 'gasto', true);
    llenarFiltroCategorias();
}

export async function cargarCategorias() {
    try {
        const res = await apiFetch('/api/categorias');
        if (!res.ok) return;
        estado.cache.categorias = await res.json();
        llenarSelectsCategoria();
    } catch (error) {
        console.error('No se pudieron cargar las categorías:', error);
    }
}

/** Maneja la opción "+ Nueva categoría…" de un select. */
async function manejarNuevaCategoria(select) {
    if (select.value !== NUEVA_CATEGORIA) return;
    const tipo = select.id === 'categoria'
        ? (document.getElementById('tipo')?.value || 'gasto')
        : (document.getElementById('modal-edit-trans-tipo')?.value || 'gasto');
    const nombre = (prompt('Nombre de la nueva categoría:') || '').trim();
    if (!nombre) {
        select.selectedIndex = 0;
        return;
    }
    try {
        const res = await apiFetch('/api/categorias', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nombre, tipo })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            toast(formatErrorDetail(err.detail, 'No se pudo crear la categoría.'), 'error');
            select.selectedIndex = 0;
            return;
        }
        const creada = await res.json();
        await cargarCategorias();
        select.value = String(creada.id);
    } catch (error) {
        toast('Error de conexión al crear la categoría.', 'error');
        select.selectedIndex = 0;
    }
}


// =============================================================================
// 5.2 TABLA DE MOVIMIENTOS CON FILTROS EN CLIENTE
// =============================================================================

/** Badges de origen: fijo, cuota MSI (n/N) y ahorro de apartado. */
function badgesMovimiento(t) {
    const badges = [];
    if (t.apartado_id) badges.push('<span class="badge-tag badge-ahorro">Ahorro</span>');
    if (t.recurrente_id) {
        const m = (t.descripcion || '').match(/\((\d+)\/(\d+)\)$/);
        badges.push(m
            ? `<span class="badge-tag badge-msi">MSI ${m[1]}/${m[2]}</span>`
            : '<span class="badge-tag badge-fijo">Fijo</span>');
    }
    if (t.tarjeta_nombre) {
        badges.push(`<span class="badge-tag badge-presupuesto" title="Vinculado al presupuesto: ${escapeHTML(t.tarjeta_nombre)}">Presupuesto: ${escapeHTML(t.tarjeta_nombre)}</span>`);
    }
    return badges.join(' ');
}

export function renderTablaMovimientos() {
    const tbody = document.getElementById('tabla-movimientos');
    if (!tbody) return;

    const mes = document.getElementById('filtro-mes')?.value || '';
    const tipo = document.getElementById('filtro-tipo')?.value || '';
    const categoria = document.getElementById('filtro-categoria')?.value || '';
    const texto = (document.getElementById('filtro-buscar')?.value || '').trim().toLowerCase();

    const lista = (estado.cache.listaTransacciones || []).filter(t => {
        if (mes && !String(t.fecha).startsWith(mes)) return false;
        if (tipo && t.tipo !== tipo) return false;
        if (categoria && String(t.categoria_id) !== categoria) return false;
        if (texto && !(t.descripcion || '').toLowerCase().includes(texto)) return false;
        return true;
    });

    tbody.innerHTML = '';
    if (lista.length === 0) {
        const mensaje = (estado.cache.listaTransacciones || []).length === 0
            ? 'No hay movimientos. Registra tu primer movimiento o agrega tu nómina en Fijos y MSI.'
            : 'Ningún movimiento coincide con los filtros.';
        tbody.innerHTML = `<tr><td colspan="7" class="empty-state">${mensaje}</td></tr>`;
        actualizarSeleccionMovimientos();
        return;
    }

    lista.forEach(t => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td style="text-align: center;">
                <input type="checkbox" class="chk-mov-item" value="${t.id}" data-id="${t.id}" aria-label="Seleccionar movimiento">
            </td>
            <td>${escapeHTML(t.fecha)}</td>
            <td>${escapeHTML(t.descripcion || '-')} ${badgesMovimiento(t)}</td>
            <td>${escapeHTML(t.categoria || 'Sin categoría')}</td>
            <td class="${t.tipo === 'gasto' ? 'text-gasto' : 'text-ingreso'}">${escapeHTML(t.tipo.toUpperCase())}</td>
            <td>${fmt(t.monto)}</td>
            <td class="col-acciones">
                <button type="button" class="btn-accion btn-editar" onclick="abrirModalEditarTransaccionPorId(${t.id})" title="Editar movimiento"><svg class="action-svg-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg></button>
                <button type="button" class="btn-accion btn-eliminar" onclick="eliminarTransaccion(${t.id})" title="Eliminar movimiento"><svg class="action-svg-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
            </td>
        `;
        tbody.appendChild(tr);
    });
    actualizarSeleccionMovimientos();
}


// =============================================================================
// Formulario de registro
// =============================================================================

export let guardandoTransaccion = false;

export const formTrans = document.getElementById('transaccion-form');

if (formTrans) {
    formTrans.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (guardandoTransaccion) return;

        const mensajeDiv = document.getElementById('mensaje');
        const btnSubmit = formTrans.querySelector('button[type="submit"]');
        const origHTML = btnSubmit ? btnSubmit.innerHTML : '';

        const montoVal = parseFloat(document.getElementById('monto').value);
        if (isNaN(montoVal) || montoVal <= 0) {
            if (mensajeDiv) {
                mensajeDiv.style.color = 'var(--danger-color)';
                mensajeDiv.textContent = 'Ingresa un monto válido mayor a 0.';
            }
            return;
        }

        const datos = {
            fecha: document.getElementById('fecha').value,
            monto: montoVal,
            tipo: document.getElementById('tipo').value,
            descripcion: document.getElementById('descripcion').value.trim(),
            categoria_id: Number(document.getElementById('categoria').value) || 1
        };

        guardandoTransaccion = true;
        if (btnSubmit) {
            btnSubmit.disabled = true;
            btnSubmit.innerHTML = '<span>Guardando...</span>';
        }

        try {
            const response = await apiFetch('/api/transacciones', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(datos)
            });

            if (response.ok) {
                if (mensajeDiv) {
                    mensajeDiv.style.color = 'var(--success-color)';
                    mensajeDiv.textContent = '¡Movimiento guardado!';
                }
                formTrans.reset();
                document.getElementById('fecha').value = obtenerFechaLocalISO();
                llenarSelectsCategoria();
                recargar();
            } else {
                let errText = 'Error al guardar.';
                try {
                    const err = await response.json();
                    errText = formatErrorDetail(err.detail, errText);
                } catch (_) {}
                if (mensajeDiv) {
                    mensajeDiv.style.color = 'var(--danger-color)';
                    mensajeDiv.textContent = errText;
                }
            }
        } catch (error) {
            if (mensajeDiv) {
                mensajeDiv.style.color = 'var(--danger-color)';
                mensajeDiv.textContent = 'Error de conexión.';
            }
        } finally {
            guardandoTransaccion = false;
            if (btnSubmit) {
                btnSubmit.disabled = false;
                btnSubmit.innerHTML = origHTML || '<span>Guardar Transacción</span>';
            }
        }
    });
}


export function actualizarSeleccionMovimientos() {
    const checkboxes = Array.from(document.querySelectorAll('#tabla-movimientos .chk-mov-item'));
    const seleccionados = checkboxes.filter(cb => cb.checked);
    const barra = document.getElementById('barra-lote-movimientos');
    const lbl = document.getElementById('lbl-mov-seleccionados');
    const chkTodos = document.getElementById('chk-mov-todos');

    if (lbl) {
        lbl.textContent = `${seleccionados.length} seleccionado${seleccionados.length === 1 ? '' : 's'}`;
    }

    if (barra) {
        barra.style.display = seleccionados.length > 0 ? 'flex' : 'none';
    }

    if (chkTodos) {
        chkTodos.disabled = checkboxes.length === 0;
        if (checkboxes.length === 0) {
            chkTodos.checked = false;
            chkTodos.indeterminate = false;
        } else if (seleccionados.length === checkboxes.length) {
            chkTodos.checked = true;
            chkTodos.indeterminate = false;
        } else if (seleccionados.length > 0) {
            chkTodos.checked = false;
            chkTodos.indeterminate = true;
        } else {
            chkTodos.checked = false;
            chkTodos.indeterminate = false;
        }
    }
}

export function toggleSeleccionarTodosMovimientos(checked) {
    const checkboxes = document.querySelectorAll('#tabla-movimientos .chk-mov-item');
    checkboxes.forEach(cb => { cb.checked = checked; });
    actualizarSeleccionMovimientos();
}

export async function eliminarTransaccion(id, forzar = false) {
    const t = (estado.cache.listaTransacciones || []).find(item => item.id === id);
    let advertencias = [];
    if (t) {
        if (t.apartado_id) {
            if (t.tipo === 'gasto') {
                advertencias.push('• Está ligado a una meta de ahorro (su saldo acumulado se descontará).');
            } else {
                advertencias.push('• Corresponde a un retiro de meta de ahorro (el monto no volverá a la meta).');
            }
        }
        if (t.recurrente_id) {
            advertencias.push('• Corresponde a un cargo o ingreso fijo (se restará 1 cuota generada).');
        }
    }

    let pregunta = "¿Eliminar este movimiento?";
    if (advertencias.length > 0) {
        pregunta = `⚠️ Advertencia:\n${advertencias.join('\n')}\n\n${pregunta}`;
    }

    if (!forzar && !confirm(pregunta)) return;

    try {
        const url = `/api/transacciones/${id}${forzar ? '?forzar=true' : ''}`;
        const response = await apiFetch(url, { method: 'DELETE' });
        if (response.ok) {
            toast("Movimiento eliminado con éxito.", 'success');
            recargar();
        } else if (response.status === 422) {
            const err = await response.json().catch(() => ({}));
            const msg = (formatErrorDetail(err.detail, "Este ingreso respalda gastos posteriores y provocaría saldo negativo.")) +
                        "\n\n¿Deseas FORZAR la eliminación de este movimiento?";
            if (confirm(msg)) {
                await eliminarTransaccion(id, true);
            }
        } else {
            const err = await response.json().catch(() => ({}));
            toast(formatErrorDetail(err.detail, "No se pudo eliminar el movimiento."), 'error');
        }
    } catch (error) {
        console.error("Error al eliminar:", error);
        toast("Error de red al intentar eliminar el movimiento.", 'error');
    }
}

export async function eliminarMovimientosLote(forzar = false) {
    const checkboxes = Array.from(document.querySelectorAll('#tabla-movimientos .chk-mov-item:checked'));
    const ids = checkboxes.map(cb => Number(cb.value)).filter(Boolean);
    if (ids.length === 0) {
        toast("No hay movimientos seleccionados.", "info");
        return;
    }

    const items = (estado.cache.listaTransacciones || []).filter(t => ids.includes(t.id));
    const ligadosMeta = items.filter(t => t.apartado_id).length;
    const ligadosFijo = items.filter(t => t.recurrente_id).length;

    let advertencias = [];
    if (ligadosMeta > 0) {
        advertencias.push(`• ${ligadosMeta} movimiento(s) ligado(s) a metas (los saldos se reajustarán).`);
    }
    if (ligadosFijo > 0) {
        advertencias.push(`• ${ligadosFijo} movimiento(s) ligado(s) a fijos o compras a meses (se restarán sus cuotas).`);
    }

    let pregunta = `¿Deseas eliminar los ${ids.length} movimientos seleccionados?`;
    if (advertencias.length > 0) {
        pregunta = `⚠️ Advertencia previa:\n${advertencias.join('\n')}\n\n${pregunta}`;
    }

    if (!forzar && !confirm(pregunta)) return;

    try {
        const response = await apiFetch('/api/transacciones/eliminar-lote', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: ids, todos: false, forzar: forzar })
        });

        if (response.ok) {
            const res = await response.json().catch(() => ({}));
            toast(res.mensaje || `Se eliminaron ${ids.length} movimientos.`, 'success');
            recargar();
        } else if (response.status === 422) {
            const err = await response.json().catch(() => ({}));
            const msg = (formatErrorDetail(err.detail, "La eliminación provocaría un saldo operativo negativo.")) +
                        "\n\n¿Deseas FORZAR la eliminación de estos movimientos seleccionados?";
            if (confirm(msg)) {
                await eliminarMovimientosLote(true);
            }
        } else {
            const err = await response.json().catch(() => ({}));
            toast(formatErrorDetail(err.detail, "No se pudieron eliminar los movimientos."), 'error');
        }
    } catch (error) {
        console.error("Error al eliminar por lote:", error);
        toast("Error de red al intentar eliminar movimientos.", 'error');
    }
}

export async function vaciarTodosLosMovimientos(forzar = false) {
    const items = estado.cache.listaTransacciones || [];
    if (items.length === 0) {
        toast("No hay movimientos para vaciar.", "info");
        return;
    }

    const ligadosMeta = items.filter(t => t.apartado_id).length;
    const ligadosFijo = items.filter(t => t.recurrente_id).length;

    let advertencias = [];
    if (ligadosMeta > 0) {
        advertencias.push(`• ${ligadosMeta} movimiento(s) ligado(s) a metas de ahorro (se revertirá su saldo acumulado).`);
    }
    if (ligadosFijo > 0) {
        advertencias.push(`• ${ligadosFijo} movimiento(s) correspondientes a fijos o MSI (se ajustarán sus cuotas generadas).`);
    }

    let pregunta = `⚠️ ACCIÓN IRREVERSIBLE: Se eliminarán TODOS los ${items.length} movimientos registrados.`;
    if (advertencias.length > 0) {
        pregunta += `\n\n${advertencias.join('\n')}`;
    }
    pregunta += "\n\n¿Estás completamente seguro de vaciar todos los movimientos?";

    if (!forzar && !confirm(pregunta)) return;

    try {
        const response = await apiFetch('/api/transacciones/eliminar-lote', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: null, todos: true, forzar: forzar })
        });

        if (response.ok) {
            const res = await response.json().catch(() => ({}));
            toast(res.mensaje || "Todos los movimientos han sido eliminados.", 'success');
            recargar();
        } else if (response.status === 422) {
            const err = await response.json().catch(() => ({}));
            const msg = (formatErrorDetail(err.detail, "La eliminación provocaría un saldo operativo negativo.")) +
                        "\n\n¿Deseas FORZAR el vaciado de todas formas?";
            if (confirm(msg)) {
                await vaciarTodosLosMovimientos(true);
            }
        } else {
            const err = await response.json().catch(() => ({}));
            toast(formatErrorDetail(err.detail, "No se pudieron vaciar los movimientos."), 'error');
        }
    } catch (error) {
        console.error("Error al vaciar movimientos:", error);
        toast("Error de red al intentar vaciar movimientos.", 'error');
    }
}


// =============================================================================
// Modal de edición
// =============================================================================

export function abrirModalEditarTransaccion(id, fecha, monto, tipo, descripcion, categoriaId, itemCompleto = null) {
    const modal = document.getElementById('modal-editar-transaccion');
    if (!modal) return;

    if (!itemCompleto && typeof estado !== 'undefined' && estado.cache) {
        itemCompleto = (estado.cache.transacciones && estado.cache.transacciones[id]) || (estado.cache.listaTransacciones || []).find(item => item.id === id);
    }

    document.getElementById('modal-edit-trans-id').value = id;
    document.getElementById('modal-edit-trans-fecha').value = fecha || '';
    document.getElementById('modal-edit-trans-tipo').value = tipo || 'gasto';
    document.getElementById('modal-edit-trans-monto').value = (monto !== undefined && monto !== null) ? monto : '';
    document.getElementById('modal-edit-trans-desc').value = descripcion || '';
    llenarSelect('modal-edit-trans-categoria', tipo || 'gasto', true);
    if (categoriaId) document.getElementById('modal-edit-trans-categoria').value = String(categoriaId);

    const subInfoEl = document.getElementById('modal-edit-trans-subperiodos-info');
    if (subInfoEl) {
        if (itemCompleto && itemCompleto.presupuesto_tarjeta_id) {
            const hermanos = (estado.cache?.listaTransacciones || []).filter(h => h.presupuesto_tarjeta_id === itemCompleto.presupuesto_tarjeta_id);
            const totalMostrar = itemCompleto.tarjeta_monto_total || hermanos.reduce((acc, h) => acc + (parseFloat(h.monto) || 0), 0);

            subInfoEl.innerHTML = `
                <div style="background: rgba(6, 182, 212, 0.08); border: 1px solid rgba(6, 182, 212, 0.25); border-radius: 8px; padding: 10px 12px; margin-bottom: 14px;">
                    <div style="display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px;">
                        <span style="font-weight: 600; font-size: 0.85rem; color: #22d3ee; display: inline-flex; align-items: center; gap: 5px;">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>
                            Tarjeta de Presupuesto: ${escapeHTML(itemCompleto.tarjeta_nombre || 'Dividida')}
                        </span>
                        <span style="font-size: 0.8rem; font-weight: 600; color: var(--text-main); font-variant-numeric: tabular-nums;">
                            Total mensual: ${fmt(totalMostrar)}
                        </span>
                    </div>
                    <div style="font-size: 0.76rem; color: var(--text-muted); margin-bottom: 6px;">
                        Esta tarjeta se compone de cargos independientes por sub-período. Puedes ver las partes juntas, aunque cada una se procesa por separado:
                    </div>
                    <div style="display: flex; flex-direction: column; gap: 4px;">
                        ${hermanos.map(h => `
                            <div style="display: flex; align-items: center; justify-content: space-between; padding: 5px 8px; border-radius: 4px; font-size: 0.78rem; background: ${h.id === itemCompleto.id ? 'rgba(6, 182, 212, 0.15)' : 'rgba(255, 255, 255, 0.03)'}; border: ${h.id === itemCompleto.id ? '1px solid rgba(6, 182, 212, 0.4)' : '1px solid transparent'};">
                                <span style="font-weight: ${h.id === itemCompleto.id ? '600' : '400'}; color: ${h.id === itemCompleto.id ? '#22d3ee' : 'var(--text-main)'};">
                                    ${escapeHTML(h.descripcion || 'Movimiento')} ${h.id === itemCompleto.id ? '← (editando)' : ''}
                                </span>
                                <span style="font-variant-numeric: tabular-nums; font-weight: 600; color: var(--text-main);">
                                    ${fmt(h.monto)} · ${escapeHTML(h.fecha)}
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

    modal.classList.remove('hidden');
    setTimeout(() => {
        const inputMonto = document.getElementById('modal-edit-trans-monto');
        if (inputMonto) {
            inputMonto.focus();
            inputMonto.select();
        }
    }, 50);
}


export function cerrarModalEditarTransaccion() {
    const subInfoEl = document.getElementById('modal-edit-trans-subperiodos-info');
    if (subInfoEl) {
        subInfoEl.innerHTML = '';
        subInfoEl.classList.add('hidden');
    }
    const modal = document.getElementById('modal-editar-transaccion');
    if (modal) modal.classList.add('hidden');
}


export async function guardarEdicionTransaccion(e) {
    if (e) {
        e.preventDefault();
        e.stopPropagation();
    }

    const id = document.getElementById('modal-edit-trans-id').value;
    const fecha = document.getElementById('modal-edit-trans-fecha').value;
    const tipo = document.getElementById('modal-edit-trans-tipo').value;
    const monto = parseFloat(document.getElementById('modal-edit-trans-monto').value);
    const descripcion = document.getElementById('modal-edit-trans-desc').value.trim();
    const categoriaId = Number(document.getElementById('modal-edit-trans-categoria').value) || undefined;

    if (!fecha || isNaN(monto) || monto <= 0 || !tipo) {
        toast("Por favor ingresa una fecha, tipo y monto numérico válido mayor a cero.", 'error');
        return;
    }

    try {
        const res = await apiFetch(`/api/transacciones/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fecha, tipo, monto, descripcion, categoria_id: categoriaId })
        });

        if (res.ok) {
            cerrarModalEditarTransaccion();
            await recargar();
        } else {
            const err = await res.json();
            toast(formatErrorDetail(err.detail, "Error al actualizar el movimiento."), 'error');
        }
    } catch (err) {
        toast("Error de red al intentar guardar los cambios.", 'error');
    }
}


// =============================================================================
// Inicialización de controles
// =============================================================================

function inicializarMovimientos() {
    const tipoForm = document.getElementById('tipo');
    if (tipoForm) tipoForm.addEventListener('change', llenarSelectsCategoria);

    const tipoModal = document.getElementById('modal-edit-trans-tipo');
    if (tipoModal) tipoModal.addEventListener('change', () => llenarSelect('modal-edit-trans-categoria', tipoModal.value, true));

    const catForm = document.getElementById('categoria');
    if (catForm) catForm.addEventListener('change', () => manejarNuevaCategoria(catForm));

    const catModal = document.getElementById('modal-edit-trans-categoria');
    if (catModal) catModal.addEventListener('change', () => manejarNuevaCategoria(catModal));

    ['filtro-mes', 'filtro-tipo', 'filtro-categoria', 'filtro-buscar'].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener(el.type === 'search' ? 'input' : 'change', renderTablaMovimientos);
        if (el.type === 'search') el.addEventListener('change', renderTablaMovimientos);
    });

    const chkTodos = document.getElementById('chk-mov-todos');
    if (chkTodos) {
        chkTodos.addEventListener('change', (e) => {
            toggleSeleccionarTodosMovimientos(e.target.checked);
        });
    }

    const tbody = document.getElementById('tabla-movimientos');
    if (tbody) {
        tbody.addEventListener('change', (e) => {
            if (e.target && e.target.classList.contains('chk-mov-item')) {
                actualizarSeleccionMovimientos();
            }
        });
    }

    const btnEliminarLote = document.getElementById('btn-eliminar-lote-mov');
    if (btnEliminarLote) {
        btnEliminarLote.addEventListener('click', () => eliminarMovimientosLote(false));
    }

    const btnVaciarTodos = document.getElementById('btn-vaciar-todos-mov');
    if (btnVaciarTodos) {
        btnVaciarTodos.addEventListener('click', () => vaciarTodosLosMovimientos(false));
    }
}

inicializarMovimientos();


// =============================================================================
// 13. EXPOSICIÓN GLOBAL DE FUNCIONES EN WINDOW
// =============================================================================

export function abrirModalEditarTransaccionPorId(id) {
    const t = (estado.cache?.transacciones && estado.cache.transacciones[id]) || (estado.cache?.listaTransacciones || []).find(item => item.id === id);
    if (!t) return;
    abrirModalEditarTransaccion(t.id, t.fecha, t.monto, t.tipo, t.descripcion, t.categoria_id, t);
}

Object.assign(window, {
    abrirModalEditarTransaccion,
    abrirModalEditarTransaccionPorId,
    cerrarModalEditarTransaccion,
    eliminarTransaccion,
    guardarEdicionTransaccion,
    actualizarSeleccionMovimientos,
    toggleSeleccionarTodosMovimientos,
    eliminarMovimientosLote,
    vaciarTodosLosMovimientos
});
