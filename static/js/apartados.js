/**
 * static/js/apartados.js - Apartados y metas de ahorro.
 */

import { apiFetch, escapeHTML, fmt, formatErrorDetail, recargar, toast } from './core.js';


// =============================================================================
// 6. APARTADOS Y METAS DE AHORRO
// =============================================================================

export function toggleMetaInput() {
    const tipo = document.getElementById('tipo-apartado').value;
    const metaGroup = document.getElementById('group-meta-monto');
    if (metaGroup) metaGroup.classList.toggle('hidden', !(tipo === 'meta'));
}


export async function cargarApartados() {
    try {
        const res = await apiFetch('/api/apartados');
        const apartados = await res.json();
        const contenedor = document.getElementById('contenedor-apartados');
        if (!contenedor) return;
        contenedor.innerHTML = '';

        if (!apartados || apartados.length === 0) {
            contenedor.innerHTML = '<div class="empty-state">No hay apartados o metas aún. <br><button type="button" class="btn-primary btn-sm" data-on-click="[[&quot;abrirModalCrearApartado&quot;]]">+ Crear primer apartado</button></div>';
            return;
        }

        apartados.forEach(item => {
            const card = document.createElement('div');
            card.className = 'card-apartado';
            
            let contenidoMeta = '';
            if (item.tipo === 'meta') {
                const porcentaje = Math.min(item.porcentaje || 0, 100);
                contenidoMeta = `
                    <div class="apartado-meta">
                        <span>${item.porcentaje}% de ${fmt(item.meta_monto)}</span>
                        <span>Faltan ${fmt(Math.max(0, item.meta_monto - item.monto_actual))}</span>
                    </div>
                    <div class="progress-bar-bg" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${porcentaje}" aria-label="Avance de ${escapeHTML(item.nombre)}">
                        <div class="progress-bar-fill" style="width: ${porcentaje}%;"></div>
                    </div>
                `;
            }

            card.innerHTML = `
                <button type="button" class="btn-eliminar-apartado" data-on-click="${escapeHTML(JSON.stringify([["eliminarApartado", item.id]]))}" title="Eliminar" aria-label="Eliminar ${escapeHTML(item.nombre)}">×</button>
                <div class="apartado-cabecera">
                    <h3 class="apartado-nombre">${escapeHTML(item.nombre)}</h3>
                    <span class="badge-tag ${item.tipo === 'meta' ? 'badge-fijo' : ''}">${item.tipo === 'meta' ? 'Meta' : 'Sobre libre'}</span>
                </div>
                <p class="apartado-saldo">${fmt(item.monto_actual)}</p>
                ${contenidoMeta}
                <div class="acciones-apartado">
                    <input type="number" id="monto-acc-${item.id}" step="0.01" min="0.01" placeholder="Monto">
                    <button type="button" class="btn-secondary" data-on-click="${escapeHTML(JSON.stringify([["realizarMovimiento", item.id, "depositar"]]))}">Depositar</button>
                    <button type="button" class="btn-secondary" data-on-click="${escapeHTML(JSON.stringify([["realizarMovimiento", item.id, "retirar"]]))}">Retirar</button>
                </div>
            `;
            contenedor.appendChild(card);
        });
    } catch (error) {
        console.error("Error al cargar apartados:", error);
    }
}


export let guardandoApartado = false;


export async function guardarNuevoApartado(e) {
    if (e) e.preventDefault();
    if (guardandoApartado) return;

    const nombreInput = document.getElementById('nombre-apartado');
    const tipoInput = document.getElementById('tipo-apartado');
    const metaInput = document.getElementById('meta-monto');

    const nombre = nombreInput ? nombreInput.value.trim() : '';
    const tipo = tipoInput ? tipoInput.value : 'libre';
    const meta_monto = (tipo === 'meta' && metaInput && metaInput.value) ? parseFloat(metaInput.value) : null;

    if (!nombre) {
        toast("Ingresa un nombre para el apartado.", 'error');
        return;
    }

    if (tipo === 'meta' && (meta_monto === null || isNaN(meta_monto) || meta_monto <= 0)) {
        toast("Una meta requiere un monto objetivo válido mayor a cero.", 'error');
        return;
    }

    guardandoApartado = true;
    const btnSubmit = document.querySelector('#form-apartado button[type="submit"]');
    const origHTML = btnSubmit ? btnSubmit.innerHTML : '';
    if (btnSubmit) {
        btnSubmit.disabled = true;
        btnSubmit.innerHTML = '<span>Creando...</span>';
    }

    try {
        const res = await apiFetch('/api/apartados', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nombre, tipo, meta_monto })
        });

        if (res.ok) {
            cerrarModalCrearApartado();
            await cargarApartados();
            recargar();
        } else {
            const error = await res.json();
            toast(error.detail || 'Error al crear apartado', 'error');
        }
    } catch (err) {
        toast("Error al conectar con el servidor.", 'error');
    } finally {
        guardandoApartado = false;
        if (btnSubmit) {
            btnSubmit.disabled = false;
            btnSubmit.innerHTML = origHTML || '<span>Crear Apartado</span>';
        }
    }
}


export function abrirModalCrearApartado() {
    const modal = document.getElementById('modal-crear-apartado');
    if (modal) {
        modal.classList.remove('hidden');
        setTimeout(() => {
            const input = document.getElementById('nombre-apartado');
            if (input) input.focus();
        }, 50);
    }
}


export function cerrarModalCrearApartado() {
    const modal = document.getElementById('modal-crear-apartado');
    if (modal) modal.classList.add('hidden');
    const form = document.getElementById('form-apartado');
    if (form) form.reset();
    toggleMetaInput();
}


export let procesandoMovimientoApartado = false;

export async function realizarMovimiento(id, accion) {
    if (procesandoMovimientoApartado) return;
    const input = document.getElementById(`monto-acc-${id}`);
    const monto = parseFloat(input?.value);

    if (isNaN(monto) || monto <= 0) {
        toast("Ingresa un monto válido mayor a 0", 'error');
        return;
    }

    procesandoMovimientoApartado = true;
    const btnContainer = input ? input.parentElement : null;
    const btns = btnContainer ? btnContainer.querySelectorAll('button') : [];
    btns.forEach(b => b.disabled = true);

    try {
        const res = await apiFetch(`/api/apartados/${id}/movimiento`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ monto, accion })
        });

        if (res.ok) {
            if (input) input.value = '';
            // Se libera antes de repintar: si no, un clic rápido en otro botón se ignora en silencio
            procesandoMovimientoApartado = false;
            btns.forEach(b => b.disabled = false);
            await cargarApartados();
            recargar();
        } else {
            let errorMsg = 'Error en la operación';
            try {
                const error = await res.json();
                errorMsg = formatErrorDetail(error.detail, errorMsg);
            } catch (_) {}
            toast(errorMsg, 'error');
        }
    } catch (err) {
        console.error("Error al realizar movimiento en apartado:", err);
        toast("Error de conexión al procesar el movimiento.", 'error');
    } finally {
        procesandoMovimientoApartado = false;
        btns.forEach(b => b.disabled = false);
    }
}


export async function eliminarApartado(id) {
    if (!confirm("¿Seguro que deseas eliminar este apartado? Se perderá el registro de este sobre.")) return;
    
    // Feedback visual inmediato en la tarjeta
    const btnEliminar = document.querySelector(`.card-apartado button[onclick*="eliminarApartado(${id})"]`);
    const card = btnEliminar ? btnEliminar.closest('.card-apartado') : null;
    if (card) {
        card.style.opacity = '0.3';
        card.style.pointerEvents = 'none';
        card.style.transition = 'opacity 0.2s ease, transform 0.2s ease';
    }

    try {
        const res = await apiFetch(`/api/apartados/${id}`, { method: 'DELETE' });
        if (res.ok) {
            if (card) card.remove();
            await cargarApartados();
            recargar();
        } else {
            if (card) {
                card.style.opacity = '1';
                card.style.pointerEvents = 'auto';
            }
            const error = await res.json();
            toast(error.detail || "No se pudo eliminar el apartado.", 'error');
        }
    } catch (error) {
        if (card) {
            card.style.opacity = '1';
            card.style.pointerEvents = 'auto';
        }
        console.error("Error al eliminar:", error);
        toast("Error de conexión al eliminar el apartado.", 'error');
    }
}

Object.assign(window, {
    abrirModalCrearApartado,
    cerrarModalCrearApartado,
    eliminarApartado,
    guardarNuevoApartado,
    realizarMovimiento,
    toggleMetaInput
});
