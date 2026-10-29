/**
 * static/js/auth.js - Autenticación, login, sesión y recuperación de contraseña.
 */

import { cargarDatosAdmin } from './admin.js';
import { apiFetch, cargarTiposCambio, estado, recargar, toast } from './core.js';
import { cambiarPestana } from './ui.js';


// =============================================================================
// 3. AUTENTICACIÓN, LOGIN Y PURGA DE SESIÓN
// =============================================================================

/**
 * En la edición Comunitaria no hay landing page: redirige directamente al login.
 */
export function mostrarVistaLanding() {
    mostrarVistaLogin();
}


/**
 * Muestra la vista de login y oculta la app principal.
 */
export function mostrarVistaLogin(errorMsg = '') {
    const vistaLogin = document.getElementById('vista-login');
    const vistaApp = document.getElementById('vista-app');
    const loginError = document.getElementById('login-error');

    if (vistaLogin) vistaLogin.classList.remove('hidden');
    if (vistaApp) vistaApp.classList.add('hidden');

    if (loginError) {
        if (errorMsg) {
            loginError.textContent = errorMsg;
            loginError.classList.remove('hidden');
        } else {
            loginError.classList.add('hidden');
        }
    }
    const inputUser = document.getElementById('login-username');
    if (inputUser) setTimeout(() => inputUser.focus(), 80);
}


/**
 * Acción principal al pulsar "Ingresar a la Plataforma" en la Landing Page.
 * Si el usuario ya cuenta con una sesión activa y validada, ingresa directamente
 * al dashboard; si no, muestra el formulario de inicio de sesión.
 */
export function ingresarOAcceder() {
    const token = localStorage.getItem('jwt_token');
    const userJson = localStorage.getItem('user_info');
    if (token && (estado.usuarioActual || userJson)) {
        try {
            const user = estado.usuarioActual || JSON.parse(userJson);
            establecerSesionActiva(user, token);
            return;
        } catch (e) {}
    }
    mostrarVistaLogin();
}


/**
 * Verifica el estado inicial de navegación:
 * En la edición Comunitaria, si existe token válido entra al dashboard;
 * en caso contrario, muestra directamente la pantalla de inicio de sesión.
 */
export async function verificarSesionInicial() {
    const token = localStorage.getItem('jwt_token');

    if (!token) {
        mostrarVistaLogin();
        return;
    }

    try {
        const res = await apiFetch('/auth/me');
        if (res.ok) {
            const data = await res.json();
            establecerSesionActiva(data.user, token);
            return;
        } else {
            cerrarSesionLimpia();
            mostrarVistaLogin();
            return;
        }
    } catch (e) {
        mostrarVistaLogin("Error al verificar la sesión con el servidor.");
        return;
    }
}


/**
 * Configura la UI para el usuario autenticado y carga sus datos.
 */
export function establecerSesionActiva(user, token) {
    estado.usuarioActual = user;
    localStorage.setItem('jwt_token', token);
    localStorage.setItem('user_info', JSON.stringify(user));

    const vistaLogin = document.getElementById('vista-login');
    const vistaApp = document.getElementById('vista-app');
    if (vistaLogin) vistaLogin.classList.add('hidden');
    if (vistaApp) vistaApp.classList.remove('hidden');

    if (window.location.hash !== '#app') {
        history.pushState(null, '', '#app');
    }

    // Actualizar badges de usuario en el topbar y dropdown popover
    const lblUser = document.getElementById('lbl-username');
    const lblRole = document.getElementById('lbl-user-role');
    const dropUser = document.getElementById('dropdown-user-name');
    const dropRole = document.getElementById('dropdown-user-role');
    const dropEmail = document.getElementById('dropdown-user-email');
    if (lblUser) lblUser.textContent = user.username;
    if (dropUser) dropUser.textContent = user.username;
    if (dropEmail) {
        dropEmail.textContent = user.email || 'Sin correo vinculado';
        dropEmail.style.color = user.email ? 'var(--gold-color)' : 'var(--text-muted)';
    }
    if (lblRole) {
        lblRole.textContent = user.role;
        lblRole.className = `badge-role ${user.role === 'admin' ? 'badge-role-admin' : 'badge-role-user'}`;
    }
    if (dropRole) {
        dropRole.textContent = user.role === 'admin' ? 'Administrador' : 'Usuario';
        dropRole.className = `dropdown-role-pill ${user.role === 'admin' ? 'badge-role-admin' : 'badge-role-user'}`;
    }

    // Pestaña de administración: visible estrictamente para rol admin
    const btnTabAdmin = document.getElementById('btn-tab-admin');
    if (btnTabAdmin) {
        btnTabAdmin.classList.toggle('hidden', !((user.role === 'admin')));
    }

    // Botón de eliminar mi cuenta en el dropdown: solo para usuarios que NO sean admin
    const btnDropEliminar = document.getElementById('btn-dropdown-eliminar-cuenta');
    if (btnDropEliminar) {
        btnDropEliminar.classList.toggle('hidden', !((user.role !== 'admin')));
    }

    // Si un usuario no admin tiene guardada la pestaña admin, redirigir al resumen
    if (user.role !== 'admin') {
        const pestanaGuardada = localStorage.getItem('active_tab');
        if (pestanaGuardada === 'tab-admin') {
            cambiarPestana('tab-dashboard');
        }
    }

    // Cargar datos financieros del tenant
    cargarTiposCambio();
    recargar();

    // Si es administrador, precargar datos de gestión
    if (user.role === 'admin') {
        cargarDatosAdmin();
    }
}


/**
 * Maneja el envío del formulario de login.
 */
export async function iniciarSesion(e) {
    e.preventDefault();
    const usernameInput = document.getElementById('login-username');
    const passwordInput = document.getElementById('login-password');
    const loginError = document.getElementById('login-error');
    const btnSubmit = document.getElementById('btn-login-submit');

    const username = usernameInput.value.trim();
    const password = passwordInput.value;

    if (!username || !password) return;

    btnSubmit.disabled = true;
    btnSubmit.textContent = 'Verificando credenciales...';
    if (loginError) loginError.classList.add('hidden');

    try {
        const res = await fetch('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });

        const data = await res.json();

        if (res.ok && data.access_token) {
            usernameInput.value = '';
            passwordInput.value = '';
            establecerSesionActiva(data.user, data.access_token);
        } else {
            loginError.textContent = data.detail || 'Usuario o contraseña incorrectos.';
            loginError.classList.remove('hidden');
        }
    } catch (err) {
        loginError.textContent = 'Error de conexión con el servidor.';
        loginError.classList.remove('hidden');
    } finally {
        btnSubmit.disabled = false;
        btnSubmit.textContent = 'Ingresar a la Plataforma';
    }
}


/**
 * Cierre de sesión voluntario por parte del usuario.
 */
export function logoutUsuario() {
    if (confirm("¿Deseas cerrar tu sesión actual?")) {
        cerrarSesionLimpia();
    }
}


/**
 * Purga completa de sesión y aislamiento total de memoria:
 * 1. Elimina tokens de localStorage.
 * 2. Destruye instancias activas de Chart.js.
 * 3. Vacía variables de memoria y tablas del DOM.
 * 4. Muestra la pantalla de login para evitar contaminación visual entre cuentas.
 */
export function cerrarSesionLimpia(errorMsg = '') {
    // 1. Eliminar persistencia local
    localStorage.removeItem('jwt_token');
    localStorage.removeItem('user_info');
    estado.usuarioActual = null;

    // 2. Destruir instancias de Chart.js
    if (estado.graficas.barras) {
        estado.graficas.barras.destroy();
        estado.graficas.barras = null;
    }
    if (estado.graficas.dona) {
        estado.graficas.dona.destroy();
        estado.graficas.dona = null;
    }
    if (estado.graficas.categorias) {
        estado.graficas.categorias.destroy();
        estado.graficas.categorias = null;
    }

    // 3. Limpiar elementos del DOM
    const tMov = document.getElementById('tabla-movimientos');
    if (tMov) tMov.innerHTML = '';

    const tInv = document.getElementById('tabla-inversiones');
    if (tInv) tInv.innerHTML = '';

    const tApt = document.getElementById('contenedor-apartados');
    if (tApt) tApt.innerHTML = '';

    const lMes = document.getElementById('lista-resumen-mensual');
    if (lMes) lMes.innerHTML = '';

    const tAdm = document.getElementById('tabla-usuarios-admin');
    if (tAdm) tAdm.innerHTML = '';

    // Restablecer etiquetas numéricas a cero
    document.querySelectorAll('[id^="lbl-dash-"], [id^="lbl-inv-"], #lbl-balance, #lbl-ingresos, #lbl-gastos, #lbl-apartados-total-acum').forEach(el => {
        el.textContent = '$0.00';
    });

    // Ocultar botón de admin y restablecer pestaña por defecto
    const btnTabAdmin = document.getElementById('btn-tab-admin');
    if (btnTabAdmin) btnTabAdmin.classList.add('hidden');
    cambiarPestana('tab-dashboard');

    // 4. Mostrar pantalla de login
    mostrarVistaLogin(errorMsg);
}


// =============================================================================
// 12.1 RECUPERACIÓN DE CONTRASEÑA POR EMAIL
// =============================================================================

export let identificadorRecuperacionEnProceso = '';


export function abrirModalRecuperarPassword() {
    const modal = document.getElementById('modal-recuperar-password');
    if (!modal) return;
    
    const formPaso1 = document.getElementById('form-solicitar-codigo');
    const formPaso2 = document.getElementById('form-restablecer-password');
    if (formPaso1) {
        formPaso1.reset();
        formPaso1.classList.remove('hidden');
    }
    if (formPaso2) {
        formPaso2.reset();
        formPaso2.classList.add('hidden');
    }
    
    const inputLoginUser = document.getElementById('login-username');
    const inputIdentifier = document.getElementById('recuperar-identifier');
    if (inputLoginUser && inputIdentifier && inputLoginUser.value.trim()) {
        inputIdentifier.value = inputLoginUser.value.trim();
    }
    
    identificadorRecuperacionEnProceso = '';
    modal.classList.remove('hidden');
    if (inputIdentifier) inputIdentifier.focus();
}


export function aceptarAvisoCorreoAdmin() {
    sessionStorage.setItem('admin_email_warning_dismissed', '1');
    const banner = document.getElementById('admin-banner-email-warning');
    if (banner) banner.classList.add('hidden');
}


export function cerrarModalRecuperarPassword() {
    const modal = document.getElementById('modal-recuperar-password');
    if (modal) modal.classList.add('hidden');
}


export function volverAPaso1Recuperacion() {
    const formPaso1 = document.getElementById('form-solicitar-codigo');
    const formPaso2 = document.getElementById('form-restablecer-password');
    if (formPaso1) formPaso1.classList.remove('hidden');
    if (formPaso2) formPaso2.classList.add('hidden');
    const inputIdentifier = document.getElementById('recuperar-identifier');
    if (inputIdentifier) inputIdentifier.focus();
}


export async function enviarSolicitudCodigo(e) {
    e.preventDefault();
    const inputIdentifier = document.getElementById('recuperar-identifier');
    const btnSubmit = document.getElementById('btn-enviar-codigo');
    const identifier = inputIdentifier ? inputIdentifier.value.trim() : '';

    if (!identifier) {
        toast('Por favor, ingresa tu usuario o correo electrónico.', 'error');
        return;
    }

    identificadorRecuperacionEnProceso = identifier;

    if (btnSubmit) {
        btnSubmit.disabled = true;
        btnSubmit.innerHTML = '<span>Enviando código...</span>';
    }

    try {
        const res = await fetch('/auth/forgot-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ identifier })
        });
        
        const data = await res.json();
        
        if (res.ok) {
            const formPaso1 = document.getElementById('form-solicitar-codigo');
            const formPaso2 = document.getElementById('form-restablecer-password');
            if (formPaso1) formPaso1.classList.add('hidden');
            if (formPaso2) {
                formPaso2.reset();
                formPaso2.classList.remove('hidden');
                const inputCodigo = document.getElementById('recuperar-codigo');
                if (inputCodigo) inputCodigo.focus();
            }
        } else if (res.status === 503) {
            const formPaso1 = document.getElementById('form-solicitar-codigo');
            const formPaso2 = document.getElementById('form-restablecer-password');
            const alertaNoConfigurado = document.getElementById('alerta-email-no-configurado');
            if (formPaso1) formPaso1.classList.add('hidden');
            if (formPaso2) formPaso2.classList.add('hidden');
            if (alertaNoConfigurado) alertaNoConfigurado.classList.remove('hidden');
        } else {
            toast(data.detail || 'Hubo un error al procesar la solicitud.', 'error');
        }
    } catch (err) {
        console.error('Error al solicitar código:', err);
        toast('Error de conexión con el servidor.', 'error');
    } finally {
        if (btnSubmit) {
            btnSubmit.disabled = false;
            btnSubmit.innerHTML = '<span>Enviar Código</span>';
        }
    }
}


export async function enviarRestablecerPassword(e) {
    e.preventDefault();
    const codigoInput = document.getElementById('recuperar-codigo');
    const passInput = document.getElementById('recuperar-nuevo-password');
    const passConfirmInput = document.getElementById('recuperar-confirmar-password');
    const btnSubmit = document.getElementById('btn-confirmar-reset');

    const code = codigoInput ? codigoInput.value.trim() : '';
    const new_password = passInput ? passInput.value : '';
    const confirm_password = passConfirmInput ? passConfirmInput.value : '';

    if (!code || code.length !== 6) {
        toast('Ingresa el código numérico de 6 dígitos que te enviamos.', 'error');
        return;
    }
    if (new_password.length < 4) {
        toast('La nueva contraseña debe tener al menos 4 caracteres.', 'error');
        return;
    }
    if (new_password !== confirm_password) {
        toast('Las contraseñas no coinciden. Por favor verifícalas.', 'error');
        return;
    }

    if (btnSubmit) {
        btnSubmit.disabled = true;
        btnSubmit.innerHTML = '<span>Restableciendo...</span>';
    }

    try {
        const res = await fetch('/auth/reset-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                identifier: identificadorRecuperacionEnProceso,
                code,
                new_password
            })
        });

        const data = await res.json();

        if (res.ok) {
            toast(data.mensaje || '¡Contraseña actualizada con éxito! Ya puedes iniciar sesión.', 'ok');
            cerrarModalRecuperarPassword();
            const inputLoginUser = document.getElementById('login-username');
            const inputLoginPass = document.getElementById('login-password');
            if (inputLoginUser) inputLoginUser.value = identificadorRecuperacionEnProceso;
            if (inputLoginPass) {
                inputLoginPass.value = '';
                inputLoginPass.focus();
            }
        } else {
            toast(data.detail || 'No se pudo restablecer la contraseña. Verifica el código.', 'error');
        }
    } catch (err) {
        console.error('Error al restablecer contraseña:', err);
        toast('Error de conexión con el servidor.', 'error');
    } finally {
        if (btnSubmit) {
            btnSubmit.disabled = false;
            btnSubmit.innerHTML = '<span>Restablecer Contraseña</span>';
        }
    }
}

Object.assign(window, {
    abrirModalRecuperarPassword,
    aceptarAvisoCorreoAdmin,
    cerrarModalRecuperarPassword,
    enviarRestablecerPassword,
    enviarSolicitudCodigo,
    ingresarOAcceder,
    iniciarSesion,
    logoutUsuario,
    mostrarVistaLanding,
    mostrarVistaLogin,
    volverAPaso1Recuperacion
});
