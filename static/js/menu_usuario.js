/**
 * static/js/menu_usuario.js - Pinta los datos de la persona en sesión en el botón y el menú de usuario.
 * Lo usan el inicio de sesión (auth.js) y el guardado del perfil (admin.js), para que un solo lugar decida qué se ve.
 */

function texto(id, valor) {
    const el = document.getElementById(id);
    if (el) el.textContent = valor;
}

function iniciales(nombre) {
    const limpio = String(nombre || '').trim();
    return (limpio.slice(0, 2) || '?').toUpperCase();
}

export function renderMenuUsuario(user) {
    if (!user) return;
    const esAdmin = user.role === 'admin';

    texto('lbl-username', user.username);
    texto('dropdown-user-name', user.username);
    texto('dropdown-avatar-iniciales', iniciales(user.username));

    const correo = document.getElementById('dropdown-user-email');
    if (correo) {
        correo.textContent = user.email || 'Sin correo vinculado';
        correo.classList.toggle('dropdown-user-email-vacio', !user.email);
    }

    // El rol solo aporta información cuando es administrador
    const rolBoton = document.getElementById('lbl-user-role');
    if (rolBoton) {
        rolBoton.textContent = 'admin';
        rolBoton.classList.toggle('hidden', !esAdmin);
    }
    const rolMenu = document.getElementById('dropdown-user-role');
    if (rolMenu) {
        rolMenu.textContent = 'Administrador';
        rolMenu.classList.toggle('hidden', !esAdmin);
    }
}
