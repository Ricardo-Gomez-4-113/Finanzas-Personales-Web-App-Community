/**
 * static/js/admin.js - Panel de administración de usuarios (solo admin).
 */

import { apiFetch, escapeHTML, estado, formatErrorDetail, toast } from './core.js';


// =============================================================================
// 9. PANEL DE ADMINISTRACIÓN DE USUARIOS (SOLO ADMIN)
// =============================================================================

/**
 * Convierte una fecha/hora (string) a formato relativo legible en español ("hace X tiempo").
 */
export function formatoTiempoRelativo(fechaStr) {
    if (!fechaStr) return '<span style="color: var(--text-muted);">Nunca</span>';
    
    // Normalizar formato fecha si viene de SQLite (ej. YYYY-MM-DD HH:MM:SS)
    const fecha = new Date(fechaStr.replace(' ', 'T'));
    const ahora = new Date();
    const diffSegundos = Math.floor((ahora - fecha) / 1000);

    if (isNaN(diffSegundos) || diffSegundos < 0) {
        return fechaStr;
    }
    if (diffSegundos < 60) {
        return 'Hace unos instantes';
    }
    const diffMinutos = Math.floor(diffSegundos / 60);
    if (diffMinutos < 60) {
        return `Hace ${diffMinutos} min`;
    }
    const diffHoras = Math.floor(diffMinutos / 60);
    if (diffHoras < 24) {
        return `Hace ${diffHoras} ${diffHoras === 1 ? 'hora' : 'horas'}`;
    }
    const diffDias = Math.floor(diffHoras / 24);
    if (diffDias < 30) {
        return `Hace ${diffDias} ${diffDias === 1 ? 'día' : 'días'}`;
    }
    return fechaStr.split(' ')[0];
}


/**
 * Carga las métricas globales y la lista de usuarios del panel de administración.
 */
export async function cargarDatosAdmin() {
    if (!estado.usuarioActual || estado.usuarioActual.role !== 'admin') return;

    try {
        // 1. Cargar KPIs
        const resStats = await apiFetch('/admin/stats');
        if (resStats.ok) {
            const stats = await resStats.json();
            document.getElementById('lbl-kpi-total-users').textContent = stats.total_users;
            document.getElementById('lbl-kpi-active-today').textContent = stats.active_users_today;
            document.getElementById('lbl-kpi-active-7d').textContent = stats.active_users_7d;
            document.getElementById('lbl-kpi-inactive').textContent = stats.inactive_users;
            document.getElementById('lbl-kpi-disk-total').textContent = stats.total_disk_formatted;

            const bannerEmail = document.getElementById('admin-banner-email-warning');
            if (bannerEmail && stats.email_service_configured !== undefined) {
                const yaAceptado = sessionStorage.getItem('admin_email_warning_dismissed') === '1';
                bannerEmail.classList.toggle('hidden', !((!stats.email_service_configured && !yaAceptado)));
            }
        }

        // 2. Cargar Directorio de Usuarios
        const resUsers = await apiFetch('/admin/users');
        if (resUsers.ok) {
            usuariosAdmin = await resUsers.json();
            usuariosAdmin.forEach(u => { estado.cache.usuarios[u.id] = u; });
            renderizarUsuariosAdmin();
        }
        prepararControlesAdmin();
    } catch (error) {
        console.error("Error al cargar datos administrativos:", error);
    }
}


/**
 * Activa o suspende el acceso de un usuario tenant.
 */
export async function cambiarEstadoUsuario(userId, nuevoEstado) {
    try {
        const res = await apiFetch(`/admin/users/${userId}/status`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: nuevoEstado })
        });

        if (res.ok) {
            cargarDatosAdmin();
        } else {
            const err = await res.json();
            toast(err.detail || "Error al modificar el estado del usuario.", 'error');
        }
    } catch (e) {
        toast("Error de red al actualizar estado.", 'error');
    }
}


/**
 * Elimina un usuario tenant y borra su archivo de base de datos físico.
 */
export function abrirModalCrearUsuario() {
    const modal = document.getElementById('modal-crear-usuario');
    if (modal) {
        modal.classList.remove('hidden');
        setTimeout(() => {
            const input = document.getElementById('nuevo-user-username');
            if (input) input.focus();
        }, 50);
    }
}


export function cerrarModalCrearUsuario() {
    const modal = document.getElementById('modal-crear-usuario');
    if (modal) modal.classList.add('hidden');
    const form = document.getElementById('form-crear-usuario');
    if (form) form.reset();
}


export let guardandoUsuario = false;

export async function guardarNuevoUsuario(e) {
    e.preventDefault();
    if (guardandoUsuario) return;

    const username = document.getElementById('nuevo-user-username').value.trim();
    const email = (document.getElementById('nuevo-user-email')?.value || '').trim();
    const password = document.getElementById('nuevo-user-password').value;
    const role = document.getElementById('nuevo-user-role').value;

    if (!username || !password) return;

    guardandoUsuario = true;
    const btnSubmit = document.querySelector('#form-crear-usuario button[type="submit"]');
    const origHTML = btnSubmit ? btnSubmit.innerHTML : '';
    if (btnSubmit) {
        btnSubmit.disabled = true;
        btnSubmit.innerHTML = '<span>Creando...</span>';
    }

    try {
        const res = await apiFetch('/admin/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password, role, email: email || null })
        });

        if (res.ok) {
            cerrarModalCrearUsuario();
            await cargarDatosAdmin();
            toast(`Usuario '${username}' y su base de datos individual han sido creados con éxito.`, 'ok');
        } else {
            let errText = "Error al crear usuario.";
            try {
                const err = await res.json();
                errText = formatErrorDetail(err.detail, errText);
            } catch (_) {}
            toast(errText, 'error');
        }
    } catch (error) {
        toast("Error de red al intentar crear el usuario.", 'error');
    } finally {
        guardandoUsuario = false;
        if (btnSubmit) {
            btnSubmit.disabled = false;
            btnSubmit.innerHTML = origHTML || '<span>Crear Usuario</span>';
        }
    }
}


/**
 * Abre el modal de edición de usuario desde el panel de administración.
 */
export function abrirModalEditarUsuario(id, username, role, email) {
    const modal = document.getElementById('modal-editar-usuario');
    if (!modal) return;

    document.getElementById('lbl-edit-user-title').textContent = username;
    document.getElementById('edit-user-id').value = id;
    document.getElementById('edit-user-username').value = username;
    const emailInput = document.getElementById('edit-user-email');
    if (emailInput) emailInput.value = email || '';
    document.getElementById('edit-user-password').value = '';
    
    const roleSelect = document.getElementById('edit-user-role');
    const roleGroup = document.getElementById('group-edit-user-role');
    
    if (roleSelect) {
        roleSelect.value = role || 'user';
        // Si es el admin actual editándose a sí mismo, bloquear el cambio de rol para evitar perder privilegios
        if (estado.usuarioActual && estado.usuarioActual.id === id && role === 'admin') {
            roleSelect.disabled = true;
        } else {
            roleSelect.disabled = false;
        }
    }
    if (roleGroup) {
        roleGroup.classList.toggle('hidden', !((estado.usuarioActual && estado.usuarioActual.role === 'admin')));
    }

    // Zona de Peligro (Eliminar Mi Cuenta): solo visible si es el usuario en sesión activa y NO es administrador
    const dangerZone = document.getElementById('danger-zone-eliminar-cuenta');
    if (dangerZone) {
        const esMismoUsuario = estado.usuarioActual && estado.usuarioActual.id === id;
        const esAdmin = estado.usuarioActual && (estado.usuarioActual.role === 'admin' || estado.usuarioActual.id === 1 || (estado.usuarioActual.username && estado.usuarioActual.username.toLowerCase() === 'admin'));
        dangerZone.classList.toggle('hidden', !((esMismoUsuario && !esAdmin)));
    }

    modal.classList.remove('hidden');
    setTimeout(() => {
        const input = document.getElementById('edit-user-username');
        if (input) input.focus();
    }, 50);
}


/**
 * Abre el modal de edición para que el usuario activo edite su propio perfil.
 */
export function abrirModalEditarMiPerfil() {
    if (!estado.usuarioActual) return;
    abrirModalEditarUsuario(estado.usuarioActual.id, estado.usuarioActual.username, estado.usuarioActual.role, estado.usuarioActual.email);
}


/**
 * Cierra el modal de edición de usuario.
 */
export function cerrarModalEditarUsuario() {
    const modal = document.getElementById('modal-editar-usuario');
    if (modal) modal.classList.add('hidden');
    const form = document.getElementById('form-editar-usuario');
    if (form) form.reset();
}


/**
 * Envía la actualización de nombre de usuario, correo y/o contraseña al backend.
 */
export async function guardarEdicionUsuario(e) {
    e.preventDefault();
    const userId = document.getElementById('edit-user-id').value;
    const username = document.getElementById('edit-user-username').value.trim();
    const emailInput = document.getElementById('edit-user-email');
    const email = emailInput ? emailInput.value.trim() : null;
    const password = document.getElementById('edit-user-password').value;
    const roleSelect = document.getElementById('edit-user-role');
    const role = roleSelect ? roleSelect.value : undefined;

    if (!username) {
        toast("El nombre de usuario no puede estar vacío.", 'error');
        return;
    }

    const payload = { username, email: email || null };
    if (password && password.trim().length > 0) {
        if (password.length < 4) {
            toast("La nueva contraseña debe tener al menos 4 caracteres.", 'error');
            return;
        }
        payload.password = password;
    }

    if (roleSelect && !roleSelect.disabled && role) {
        payload.role = role;
    }

    try {
        const endpoint = (estado.usuarioActual && estado.usuarioActual.role === 'admin') 
            ? `/admin/users/${userId}` 
            : `/auth/profile`;

        const res = await apiFetch(endpoint, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            const data = await res.json();
            cerrarModalEditarUsuario();
            
            // Si el usuario editado es el usuario en sesión activa, actualizar token y UI
            if (estado.usuarioActual && estado.usuarioActual.id === parseInt(userId, 10)) {
                if (data.new_token) {
                    localStorage.setItem('jwt_token', data.new_token);
                }
                if (data.user) {
                    estado.usuarioActual = data.user;
                    localStorage.setItem('user_info', JSON.stringify(data.user));
                    const lblUser = document.getElementById('lbl-username');
                    const lblRole = document.getElementById('lbl-user-role');
                    const dropUser = document.getElementById('dropdown-user-name');
                    const dropRole = document.getElementById('dropdown-user-role');
                    const dropEmail = document.getElementById('dropdown-user-email');
                    if (lblUser) lblUser.textContent = data.user.username;
                    if (dropUser) dropUser.textContent = data.user.username;
                    if (dropEmail) {
                        dropEmail.textContent = data.user.email || 'Sin correo vinculado';
                        dropEmail.style.color = data.user.email ? 'var(--gold-color)' : 'var(--text-muted)';
                    }
                    if (lblRole) {
                        lblRole.textContent = data.user.role;
                        lblRole.className = `badge-role ${data.user.role === 'admin' ? 'badge-role-admin' : 'badge-role-user'}`;
                    }
                    if (dropRole) {
                        dropRole.textContent = data.user.role === 'admin' ? 'Administrador' : 'Usuario';
                        dropRole.className = `dropdown-role-pill ${data.user.role === 'admin' ? 'badge-role-admin' : 'badge-role-user'}`;
                    }
                }
            }
            
            if (estado.usuarioActual && estado.usuarioActual.role === 'admin') {
                await cargarDatosAdmin();
            }
            toast(data.mensaje || "Usuario actualizado exitosamente.", 'ok');
        } else {
            const err = await res.json();
            toast(err.detail || "Error al actualizar el usuario.", 'error');
        }
    } catch (error) {
        toast("Error de conexión al guardar cambios.", 'error');
    }
}


/**
 * Permite a un usuario estándar eliminar definitivamente su propia cuenta y todos sus datos.
 * Bloquea la acción para administradores del sistema.
 */
export async function solicitarEliminarMiCuenta() {
    if (!estado.usuarioActual) return;

    if (estado.usuarioActual.role === 'admin' || estado.usuarioActual.id === 1 || (estado.usuarioActual.username && estado.usuarioActual.username.toLowerCase() === 'admin')) {
        toast("Los administradores del sistema no pueden eliminar su propia cuenta.", 'error');
        return;
    }

    const confirm1 = confirm("ATENCIÓN: ¿Estás seguro de que deseas eliminar permanentemente tu cuenta?\n\nEsta acción es irreversible y eliminará de inmediato:\n• Tu usuario y credenciales de acceso.\n• Todos tus movimientos de caja y categorías.\n• Tus apartados y metas de ahorro.\n• Tu portafolio bursátil y cotizaciones.\n\n¿Deseas continuar?");
    if (!confirm1) return;

    const respuesta = prompt("Para confirmar la eliminación definitiva de tu cuenta y todos tus datos, escribe ELIMINAR:");
    if (!respuesta || respuesta.trim().toUpperCase() !== "ELIMINAR") {
        toast("Operación cancelada. Tu cuenta no ha sido modificada.", 'ok');
        return;
    }

    try {
        const res = await apiFetch('/auth/delete-account', {
            method: 'DELETE'
        });

        if (res.ok) {
            toast("Tu cuenta y todos tus datos financieros han sido eliminados permanentemente.", 'ok');
            cerrarModalEditarUsuario();
            localStorage.removeItem('jwt_token');
            localStorage.removeItem('user_info');
            sessionStorage.clear();
            window.location.reload();
        } else {
            const err = await res.json();
            toast(err.detail || "No se pudo eliminar tu cuenta.", 'error');
        }
    } catch (e) {
        toast("Error de red al intentar eliminar la cuenta.", 'error');
    }
}


export function abrirModalEditarUsuarioPorId(id) {
    const u = estado.cache.usuarios[id];
    if (!u) return;
    abrirModalEditarUsuario(u.id, u.username, u.role, u.email);
}


// -----------------------------------------------------------------------------
// Directorio de usuarios: búsqueda, filtro por estado, orden, detalle y menú por fila
// -----------------------------------------------------------------------------

let usuariosAdmin = [];
let filtroAdmin = { texto: '', estado: 'todos', orden: 'acceso' };
let usuarioEnEliminacion = null;
let controlesAdminListos = false;

const DIAS_TRAZA = 30;

const accionDelegada = (...llamadas) => escapeHTML(JSON.stringify(llamadas));

// Traza de acceso: el punto cae a la derecha si entró hoy y se corre a la izquierda con los días, hasta 30
function trazaAcceso(ultimoAcceso) {
    if (!ultimoAcceso) return '<span class="admin-traza admin-traza-vacia" aria-hidden="true"></span>';
    const dias = (Date.now() - new Date(ultimoAcceso.replace(' ', 'T')).getTime()) / 86400000;
    const pos = Math.min(100, Math.max(0, (1 - dias / DIAS_TRAZA) * 100));
    return `<span class="admin-traza" aria-hidden="true"><span class="admin-traza-punto" style="left: ${pos.toFixed(1)}%"></span></span>`;
}

function usuariosVisibles() {
    const texto = filtroAdmin.texto.trim().toLowerCase();
    const comparadores = {
        acceso: (a, b) => (b.last_login || '').localeCompare(a.last_login || ''),
        nombre: (a, b) => (a.username || '').localeCompare(b.username || '', 'es'),
        creado: (a, b) => (b.created_at || '').localeCompare(a.created_at || ''),
    };
    return usuariosAdmin
        .filter(u => filtroAdmin.estado === 'todos' || (filtroAdmin.estado === 'activos') === !!u.is_active)
        .filter(u => !texto || (u.username || '').toLowerCase().includes(texto) || (u.email || '').toLowerCase().includes(texto))
        .sort(comparadores[filtroAdmin.orden]);
}

function filaUsuarioAdmin(u) {
    const esMismoAdmin = !!(estado.usuarioActual && estado.usuarioActual.id === u.id);
    const esPrincipal = u.id === 1 || (u.username || '').toLowerCase() === 'admin';
    const menuId = `menu-usuario-${u.id}`;
    const detalleId = `detalle-usuario-${u.id}`;
    const nombre = escapeHTML(u.username);

    const correo = u.email
        ? `<span class="admin-correo">${escapeHTML(u.email)}</span>`
        : '<span class="admin-correo admin-correo-vacio">Sin correo registrado</span>';
    const estadoHtml = u.is_active
        ? '<span class="admin-estado admin-estado-activo"><span class="admin-punto" aria-hidden="true"></span>Activo</span>'
        : '<span class="admin-estado admin-estado-suspendido"><span class="admin-punto" aria-hidden="true"></span>Suspendido</span>';
    const rol = `<span class="badge-role ${u.role === 'admin' ? 'badge-role-admin' : 'badge-role-user'}">${escapeHTML(u.role)}</span>`;
    const accesoTxt = u.last_login ? formatoTiempoRelativo(u.last_login) : 'Nunca';

    const itemsMenu = [];
    if (!esMismoAdmin) {
        itemsMenu.push(`<button type="button" class="admin-menu-item" role="menuitem" data-on-click='${accionDelegada(["cambiarEstadoUsuario", u.id, !u.is_active])}'>${u.is_active ? 'Suspender acceso' : 'Activar acceso'}</button>`);
    }
    if (esPrincipal || esMismoAdmin) {
        const motivo = esMismoAdmin ? 'No puedes eliminar tu propia cuenta' : 'La cuenta principal no se puede eliminar';
        itemsMenu.push(`<p class="admin-menu-nota">${motivo}</p>`);
    } else {
        itemsMenu.push(`<button type="button" class="admin-menu-item admin-menu-peligro" role="menuitem" data-on-click='${accionDelegada(["eliminarUsuarioTenantPorId", u.id])}'>Eliminar usuario</button>`);
    }

    return `
        <tr data-usuario="${u.id}">
            <td class="admin-celda-usuario" data-label="Usuario">
                <span class="admin-nombre">${nombre}</span>
                ${correo}
            </td>
            <td data-label="Rol">${rol}</td>
            <td data-label="Estado">${estadoHtml}</td>
            <td class="admin-celda-acceso" data-label="Último acceso">
                <span class="admin-acceso-txt">${escapeHTML(accesoTxt)}</span>
                ${trazaAcceso(u.last_login)}
            </td>
            <td class="admin-celda-acciones" data-label="Acciones">
                <button type="button" class="admin-accion" aria-expanded="false" aria-controls="${detalleId}" data-on-click='${accionDelegada(["alternarDetalleUsuario", u.id, "$this"])}'>Detalles</button>
                <button type="button" class="admin-accion" data-on-click='${accionDelegada(["abrirModalEditarUsuarioPorId", u.id])}'>Editar</button>
                <div class="admin-menu-wrap">
                    <button type="button" class="admin-accion admin-accion-icono" aria-haspopup="menu" aria-expanded="false" aria-controls="${menuId}" aria-label="Más acciones para ${nombre}" data-on-click='${accionDelegada(["alternarMenuUsuario", u.id, "$this"])}'>
                        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>
                    </button>
                    <div class="admin-menu" id="${menuId}" role="menu" hidden>${itemsMenu.join('')}</div>
                </div>
            </td>
        </tr>
        <tr class="admin-detalle" id="${detalleId}" hidden>
            <td colspan="5">
                <dl class="admin-detalle-lista">
                    <div><dt>Cuenta</dt><dd>#${u.id}</dd></div>
                    <div><dt>Creada</dt><dd>${escapeHTML((u.created_at || '').split(' ')[0] || '-')}</dd></div>
                    <div><dt>Inicios de sesión</dt><dd>${escapeHTML(String(u.login_count ?? 0))}</dd></div>
                    <div><dt>Base de datos</dt><dd>${escapeHTML(u.db_size_formatted || '0 KB')}</dd></div>
                    <div><dt>Último acceso exacto</dt><dd>${escapeHTML(u.last_login || 'Nunca')}</dd></div>
                </dl>
            </td>
        </tr>`;
}

function renderizarUsuariosAdmin() {
    const tbody = document.getElementById('tabla-usuarios-admin');
    if (!tbody) return;
    const texto = (id, valor) => { const el = document.getElementById(id); if (el) el.textContent = valor; };
    texto('cnt-admin-todos', usuariosAdmin.length);
    texto('cnt-admin-activos', usuariosAdmin.filter(u => u.is_active).length);
    texto('cnt-admin-suspendidos', usuariosAdmin.filter(u => !u.is_active).length);

    if (usuariosAdmin.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="admin-vacio">Todavía no hay usuarios registrados.</td></tr>';
        return;
    }
    const lista = usuariosVisibles();
    if (lista.length === 0) {
        const buscado = filtroAdmin.texto.trim();
        const mensaje = buscado
            ? `Ningún usuario coincide con «${escapeHTML(buscado)}». Borra la búsqueda o cambia el filtro.`
            : 'Ningún usuario tiene este estado.';
        tbody.innerHTML = `<tr><td colspan="5" class="admin-vacio">${mensaje}</td></tr>`;
        return;
    }
    tbody.innerHTML = lista.map(filaUsuarioAdmin).join('');
}

function cerrarMenusAdmin() {
    document.querySelectorAll('.admin-menu:not([hidden])').forEach(m => { m.hidden = true; });
    document.querySelectorAll('[aria-haspopup="menu"][aria-expanded="true"]').forEach(b => b.setAttribute('aria-expanded', 'false'));
}

function prepararControlesAdmin() {
    if (controlesAdminListos) return;
    controlesAdminListos = true;

    document.getElementById('admin-buscar')?.addEventListener('input', e => {
        filtroAdmin.texto = e.target.value;
        renderizarUsuariosAdmin();
    });

    const campoConfirmar = document.getElementById('eliminar-usuario-confirmar');
    campoConfirmar?.addEventListener('input', e => {
        const coincide = !!usuarioEnEliminacion && e.target.value === usuarioEnEliminacion.username;
        document.getElementById('btn-confirmar-eliminar').disabled = !coincide;
    });

    document.addEventListener('keydown', e => { if (e.key === 'Escape') cerrarMenusAdmin(); });
    document.addEventListener('click', e => { if (!e.target.closest('.admin-menu-wrap')) cerrarMenusAdmin(); });
}

export function alternarDetalleUsuario(id, boton) {
    const fila = document.getElementById(`detalle-usuario-${id}`);
    if (!fila) return;
    const abrir = fila.hidden;
    fila.hidden = !abrir;
    boton.setAttribute('aria-expanded', String(abrir));
}

export function alternarMenuUsuario(id, boton) {
    const menu = document.getElementById(`menu-usuario-${id}`);
    if (!menu) return;
    const abrir = menu.hidden;
    cerrarMenusAdmin();
    menu.hidden = !abrir;
    boton.setAttribute('aria-expanded', String(abrir));
}

export function filtrarUsuariosAdmin(estadoFiltro, boton) {
    filtroAdmin.estado = estadoFiltro;
    document.querySelectorAll('.admin-filtro').forEach(b => {
        const on = b === boton;
        b.classList.toggle('is-on', on);
        b.setAttribute('aria-pressed', String(on));
    });
    renderizarUsuariosAdmin();
}

export function ordenarUsuariosAdmin(orden) {
    filtroAdmin.orden = orden;
    renderizarUsuariosAdmin();
}

// Eliminar borra la base del usuario: se confirma escribiendo su nombre, no con un diálogo del navegador
export function eliminarUsuarioTenantPorId(id) {
    const u = estado.cache.usuarios[id];
    if (!u) return;
    cerrarMenusAdmin();
    usuarioEnEliminacion = u;
    document.getElementById('eliminar-usuario-nombre').textContent = u.username;
    document.getElementById('eliminar-usuario-confirmar').value = '';
    document.getElementById('btn-confirmar-eliminar').disabled = true;
    document.getElementById('modal-eliminar-usuario').classList.remove('hidden');
    document.getElementById('btn-cancelar-eliminar').focus();
}

export function cerrarModalEliminarUsuario() {
    document.getElementById('modal-eliminar-usuario')?.classList.add('hidden');
    usuarioEnEliminacion = null;
}

export async function confirmarEliminarUsuario() {
    const u = usuarioEnEliminacion;
    if (!u || document.getElementById('eliminar-usuario-confirmar').value !== u.username) return;
    await eliminarUsuarioTenant(u.id);
    cerrarModalEliminarUsuario();
}

export async function eliminarUsuarioTenant(userId) {
    try {
        const res = await apiFetch(`/admin/users/${userId}`, {
            method: 'DELETE'
        });

        if (res.ok) {
            cargarDatosAdmin();
        } else {
            const err = await res.json();
            toast(err.detail || "No se pudo eliminar el usuario.", 'error');
        }
    } catch (e) {
        toast("Error de red al eliminar usuario.", 'error');
    }
}

Object.assign(window, {
    abrirModalCrearUsuario,
    abrirModalEditarMiPerfil,
    abrirModalEditarUsuario,
    abrirModalEditarUsuarioPorId,
    cambiarEstadoUsuario,
    cerrarModalCrearUsuario,
    cerrarModalEditarUsuario,
    eliminarUsuarioTenant,
    eliminarUsuarioTenantPorId,
    alternarDetalleUsuario,
    alternarMenuUsuario,
    cerrarModalEliminarUsuario,
    confirmarEliminarUsuario,
    filtrarUsuariosAdmin,
    ordenarUsuariosAdmin,
    guardarEdicionUsuario,
    guardarNuevoUsuario,
    solicitarEliminarMiCuenta
});
