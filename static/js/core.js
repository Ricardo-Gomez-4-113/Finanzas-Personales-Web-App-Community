/**
 * core.js - Estado compartido, cliente HTTP y helpers base del frontend.
 * Sin dependencias de otros módulos (evita imports circulares).
 */

// =============================================================================
// 1. ESTADO COMPARTIDO DE LA APLICACIÓN
// =============================================================================

export const estado = {
    usuarioActual: null,
    monedaGlobal: localStorage.getItem('moneda_global') || 'MXN',
    tasasCambio: { USD: 1.0, MXN: 18.40, EUR: 0.89 },
    graficas: { barras: null, dona: null },
    debounceTimerBuscar: null,
    cache: { transacciones: {}, listaTransacciones: [], categorias: [], inversiones: {}, usuarios: {}, ventana: null }
};
if (!['MXN', 'USD'].includes(estado.monedaGlobal)) estado.monedaGlobal = 'MXN';



export function escapeHTML(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

export function formatErrorDetail(detail, fallback = "Ocurrió un error inesperado.") {
    if (!detail) return fallback;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        return detail.map(d => (d && d.msg) ? `${d.msg}` : JSON.stringify(d)).join('\n');
    }
    if (typeof detail === 'object') {
        return detail.msg || detail.message || JSON.stringify(detail);
    }
    return String(detail);
}

// =============================================================================
// 2. CLIENTE HTTP INTERCEPTOR (apiFetch)
// =============================================================================

/**
 * Cliente HTTP centralizado que intercepta todas las peticiones hacia la API:
 * 1. Inyecta la cabecera 'Authorization: Bearer <token>' automáticamente.
 * 2. Si el servidor responde 401 Unauthorized o 403 Forbidden, purga la sesión
 *    y redirige de inmediato a la pantalla de login.
 */
export async function apiFetch(url, options = {}) {
    const token = localStorage.getItem('jwt_token');
    const headers = options.headers ? { ...options.headers } : {};

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(url, { ...options, headers });

    // Si el token expiró o la cuenta fue suspendida en master_db
    if (response.status === 401 || response.status === 403) {
        if (!url.includes('/auth/login')) {
            document.dispatchEvent(new CustomEvent('sesion-expirada', { detail: "Tu sesión ha expirado o tu cuenta ha sido suspendida." }));
            throw new Error("No autorizado");
        }
    }

    return response;
}

// =============================================================================
// 4. HELPERS BASE
// =============================================================================

export function fmt(valor, moneda = estado.monedaGlobal) {
    return new Intl.NumberFormat('es-MX', { style: 'currency', currency: moneda, currencyDisplay: 'narrowSymbol' }).format(Number(valor) || 0);
}

export function toast(msg, tipo = 'ok') {
    const raiz = document.getElementById('toast-root') || document.body;
    const el = document.createElement('div');
    el.className = `toast toast-${tipo}`;
    el.textContent = msg;
    raiz.appendChild(el);
    setTimeout(() => el.remove(), 3500);
}

export const colorVar = (nombre) => getComputedStyle(document.documentElement).getPropertyValue(nombre).trim();

export const recargar = () => document.dispatchEvent(new Event('recargar'));

// =============================================================================
// 5. MONEDA Y TIPOS DE CAMBIO
// =============================================================================


// =============================================================================
// 7. INVERSIONES & ASSET ALLOCATION
// =============================================================================

export async function cargarTiposCambio() {
    try {
        const res = await apiFetch('/api/tipo-cambio');
        if (res.ok) {
            const data = await res.json();
            estado.tasasCambio = data.tasas || estado.tasasCambio;
            actualizarIndicadorTasa();
        }
    } catch (e) {
        console.warn("No se pudo obtener tipo de cambio:", e);
    }
}


export function actualizarIndicadorTasa() {
    const selectGlobal = document.getElementById('select-moneda-global');
    if (selectGlobal) selectGlobal.value = estado.monedaGlobal;

    const badgeGlobal = document.getElementById('badge-tasa-global');
    if (badgeGlobal) badgeGlobal.textContent = `1 USD ≈ ${fmt(estado.tasasCambio.MXN || 18.40)} MXN`;

    const badgeInv = document.getElementById('badge-tasa-inversiones');
    if (badgeInv) badgeInv.textContent = `1 USD ≈ ${fmt(estado.tasasCambio.MXN || 18.40)} MXN`;

    document.querySelectorAll('.lbl-moneda-base').forEach(el => {
        el.textContent = estado.monedaGlobal;
    });
}


export function cambiarMonedaGlobal(nuevaMoneda) {
    estado.monedaGlobal = nuevaMoneda;
    localStorage.setItem('moneda_global', nuevaMoneda);
    actualizarIndicadorTasa();
    recargar();
}

Object.assign(window, {
    cambiarMonedaGlobal,
    cambiarMonedaInversiones: cambiarMonedaGlobal
});
