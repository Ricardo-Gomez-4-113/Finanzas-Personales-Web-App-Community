/**
 * core.js - Estado compartido, cliente HTTP y helpers base del frontend.
 * Solo depende de diagnostico.js, que no importa nada (evita imports circulares).
 */

import { reportarFallo } from './diagnostico.js';

// =============================================================================
// 1. ESTADO COMPARTIDO DE LA APLICACIÓN
// =============================================================================

export const estado = {
    usuarioActual: null,
    monedaGlobal: localStorage.getItem('moneda_global') || 'MXN',
    tasasCambio: { USD: 1.0 },   // las cotiza el servidor (/api/tipo-cambio); sin ellas no se inventa un valor
    tasasEnRespaldo: [],         // monedas cuya tasa no es una cotización real
    factoresMXN: null,   // lo que vale 1 MXN en cada moneda; lo calcula el servidor (/api/tipo-cambio)
    graficas: { barras: null, dona: null },
    debounceTimerBuscar: null,
    cache: { transacciones: {}, listaTransacciones: [], categorias: [], inversiones: {}, usuarios: {}, ventana: null }
};
if (!['MXN', 'USD', 'EUR'].includes(estado.monedaGlobal)) estado.monedaGlobal = 'MXN';



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
 * 2. Si el servidor responde 401, o 403 por cuenta desactivada o suspendida, purga la sesión
 *    y redirige de inmediato a la pantalla de login.
 * 3. Si no hay conexión, avisa con un toast y propaga el error.
 */
export async function apiFetch(url, options = {}) {
    const token = localStorage.getItem('jwt_token');
    const headers = options.headers ? { ...options.headers } : {};

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    let response;
    try {
        response = await fetch(url, { ...options, headers });
    } catch (error) {
        toast('Sin conexión con el servidor. Revisa tu red e inténtalo de nuevo.', 'error');
        throw error;
    }

    // Un 5xx es un fallo del servidor: se reporta con el id de la petición para cruzarlo con el registro
    if (response.status >= 500) {
        const ruta = url.split('?')[0];
        reportarFallo('api', `${response.status} ${(options.method || 'GET').toUpperCase()} ${ruta}`, { id_peticion: response.headers.get('X-Request-Id') || undefined });
    }

    // Un 401 es sesión vencida. Un 403 solo cierra sesión si la cuenta está desactivada o suspendida;
    // en otros casos (p. ej. una ruta solo de administradores) el llamador muestra el motivo.
    if (response.status === 401 || (response.status === 403 && await cuentaInactiva(response))) {
        if (!url.includes('/auth/login')) {
            document.dispatchEvent(new CustomEvent('sesion-expirada', { detail: "Tu sesión ha expirado o tu cuenta ha sido suspendida." }));
            throw new Error("No autorizado");
        }
    }

    return response;
}

/**
 * Devuelve el JSON de una respuesta correcta. Si la respuesta es de error, lanza un Error con el
 * motivo que dio el servidor (o `mensaje`) y marca `esHttp` para que el llamador pueda avisar.
 */
export async function leerJson(res, mensaje) {
    if (!res.ok) {
        const cuerpo = await res.json().catch(() => ({}));
        const error = new Error(formatErrorDetail(cuerpo.detail, mensaje));
        error.esHttp = true;
        throw error;
    }
    return res.json();
}

async function cuentaInactiva(response) {
    const cuerpo = await response.clone().json().catch(() => ({}));
    return /desactivada|suspendida|cancelada/i.test(cuerpo.detail || '');
}

// =============================================================================
// 4. HELPERS BASE
// =============================================================================

export function fmt(valor, moneda = estado.monedaGlobal) {
    return new Intl.NumberFormat('es-MX', { style: 'currency', currency: moneda, currencyDisplay: 'narrowSymbol' }).format(Number(valor) || 0);
}

// Cifra sensible: el modo privacidad la desenfoca (regla .dato-sensible). Solo para innerHTML con datos numéricos.
export function montoHTML(valor, moneda = estado.monedaGlobal) {
    return `<span class="dato-sensible">${fmt(valor, moneda)}</span>`;
}

// Los movimientos, apartados, fijos y presupuesto se guardan en MXN. Para mostrarlos en la moneda base se
// multiplican por el factor que calcula el servidor. Sin factor (aún no llegó o falló), se muestran como MXN
// con su símbolo, nunca con el de otra moneda.
function factorMXN() {
    if (estado.monedaGlobal === 'MXN') return 1;
    return (estado.factoresMXN && estado.factoresMXN[estado.monedaGlobal]) || null;
}

export const monedaDeMonto = () => (factorMXN() ? estado.monedaGlobal : 'MXN');

export function desdeMXN(valor) {
    return (Number(valor) || 0) * (factorMXN() || 1);
}

export const fmtMXN = (valor) => fmt(desdeMXN(valor), monedaDeMonto());

export const montoMxnHTML = (valor) => montoHTML(desdeMXN(valor), monedaDeMonto());

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
            estado.tasasEnRespaldo = data.en_respaldo || [];
            const primeraVez = !estado.factoresMXN;
            estado.factoresMXN = data.factores_desde_mxn || estado.factoresMXN;
            actualizarIndicadorTasa();
            // Lo que se pintó antes de tener el factor salió en MXN: una vez, y solo si la base es otra moneda
            if (primeraVez && estado.factoresMXN && estado.monedaGlobal !== 'MXN') recargar();
        }
    } catch (e) {
        console.warn("No se pudo obtener tipo de cambio:", e);
    }
}


export function actualizarIndicadorTasa() {
    const selectGlobal = document.getElementById('select-moneda-global');
    if (selectGlobal) selectGlobal.value = estado.monedaGlobal;

    // Se avisa si la tasa mostrada o la de la moneda base no es una cotización real
    const aprox = estado.tasasEnRespaldo.includes('MXN') || estado.tasasEnRespaldo.includes(estado.monedaGlobal);
    const texto = estado.tasasCambio.MXN
        ? `1 USD ≈ ${fmt(estado.tasasCambio.MXN, 'MXN')} MXN${aprox ? ' (aprox.)' : ''}`
        : '1 USD ≈ — MXN';
    const aviso = aprox ? 'No se pudo cotizar el tipo de cambio: se usa un valor aproximado de respaldo' : '';
    for (const id of ['badge-tasa-global', 'badge-tasa-inversiones']) {
        const badge = document.getElementById(id);
        if (!badge) continue;
        badge.textContent = texto;
        badge.title = aviso;
    }

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
