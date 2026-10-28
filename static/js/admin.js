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
            const usuarios = await resUsers.json();
            const tbody = document.getElementById('tabla-usuarios-admin');
            tbody.innerHTML = '';

            if (usuarios.length === 0) {
                tbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-muted);">Sin usuarios registrados.</td></tr>';
            } else {
                usuarios.forEach(u => {
                    const tr = document.createElement('tr');
                    const esMismoAdmin = (estado.usuarioActual && estado.usuarioActual.id === u.id);
                    
                    const badgeRol = `<span class="badge-role ${u.role === 'admin' ? 'badge-role-admin' : 'badge-role-user'}">${u.role}</span>`;
                    const badgeEstado = u.is_active 
                        ? `<span class="badge-status badge-status-active">Activo</span>`
                        : `<span class="badge-status badge-status-inactive">Inactivo</span>`;

                    const fechaCreacion = (u.created_at || '').split(' ')[0] || '-';
                    const ultimoAccesoRelativo = formatoTiempoRelativo(u.last_login);
                    const detalleTitle = u.last_login ? `title="Fecha exacta: ${escapeHTML(u.last_login)}"` : '';

                    estado.cache.usuarios[u.id] = u;
                    const esAdminPrincipal = (u.id === 1 || (u.username && u.username.toLowerCase() === 'admin'));
                    
                    let accionesHtml = `
                        <button type="button" class="btn-user-action btn-edit-user" onclick="abrirModalEditarUsuarioPorId(${u.id})" title="Editar usuario">
                            <svg class="action-svg-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
                            <span class="btn-action-label">Editar</span>
                        </button>
                    `;

                    if (!esMismoAdmin) {
                        const textoToggle = u.is_active ? 'Suspender' : 'Activar';
                        const nuevoEstado = !u.is_active;
                        const iconoToggle = u.is_active
                            ? `<svg class="action-svg-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="10" y1="15" x2="10" y2="9"/><line x1="14" y1="15" x2="14" y2="9"/></svg>`
                            : `<svg class="action-svg-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>`;
                        const claseToggle = u.is_active ? 'btn-status-suspend' : 'btn-status-activate';
                        accionesHtml += `
                            <button type="button" class="btn-user-action btn-status-toggle ${claseToggle}" onclick="cambiarEstadoUsuario(${u.id}, ${nuevoEstado})" title="${textoToggle} usuario">
                                ${iconoToggle}
                                <span class="btn-action-label">${textoToggle}</span>
                            </button>
                        `;
                    }

                    if (esAdminPrincipal || esMismoAdmin) {
                        accionesHtml += `
                            <button type="button" class="btn-user-action btn-delete-user btn-disabled" disabled title="No se puede eliminar la cuenta principal de administración">
                                <svg class="action-svg-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>
                                <span class="btn-action-label">Eliminar</span>
                            </button>
                        `;
                    } else {
                        accionesHtml += `
                            <button type="button" class="btn-user-action btn-delete-user" onclick="eliminarUsuarioTenantPorId(${u.id})" title="Eliminar usuario y su base de datos">
                                <svg class="action-svg-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                                <span class="btn-action-label">Eliminar</span>
                            </button>
                        `;
                    }

                    const correoVal = u.email ? escapeHTML(u.email) : '';
                    const correoHtml = u.email 
                        ? `<span class="user-email-chip" title="${correoVal}">${correoVal}</span>`
                        : `<span style="color: var(--text-muted); font-size: 0.78rem; font-style: italic;">Sin registrar</span>`;

                    const primeraLetra = (u.username || 'U').charAt(0).toUpperCase();

                    tr.innerHTML = `
                        <td class="col-user-id"><span class="user-id-chip">#${u.id}</span></td>
                        <td class="col-user-name">
                            <div class="user-name-cell">
                                <span class="user-avatar-mini">${escapeHTML(primeraLetra)}</span>
                                <strong>${escapeHTML(u.username)}</strong>
                            </div>
                        </td>
                        <td class="col-user-email">${correoHtml}</td>
                        <td class="col-user-role">${badgeRol}</td>
                        <td class="col-user-status">${badgeEstado}</td>
                        <td class="col-user-created">${fechaCreacion}</td>
                        <td class="col-user-access" ${detalleTitle}>${ultimoAccesoRelativo}</td>
                        <td class="col-user-logins"><span class="login-count-chip">${u.login_count}</span></td>
                        <td class="col-user-size"><span class="db-size-chip">${escapeHTML(u.db_size_formatted || '0 KB')}</span></td>
                        <td class="col-user-actions col-acciones">${accionesHtml}</td>
                    `;
                    tbody.appendChild(tr);
                });
            }
        }
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
export async function eliminarUsuarioTenant(userId, username) {
    const confirmacion = confirm(`¿Estás seguro de eliminar al usuario '${username}'?\n\nADVERTENCIA: Se borrará permanentemente su base de datos y todos sus movimientos financieros.`);
    if (!confirmacion) return;

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


export function eliminarUsuarioTenantPorId(id) {
    const u = estado.cache.usuarios[id];
    if (!u) return;
    eliminarUsuarioTenant(u.id, u.username);
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
    guardarEdicionUsuario,
    guardarNuevoUsuario,
    solicitarEliminarMiCuenta
});
