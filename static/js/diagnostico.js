/**
 * static/js/diagnostico.js - Envía al servidor los fallos que ve el navegador, para revisarlos junto al registro.
 *
 * Un mismo fallo se envía una sola vez por carga de página, y hay un máximo por sesión: un bucle de errores
 * no llena el registro. El envío nunca interrumpe la aplicación.
 */

const MAXIMO_ENVIOS = 20;
const MAXIMO_TEXTO = 500;
const SIN_REPORTAR = 'No autorizado'; // lo lanza apiFetch al cerrar sesión: no es un fallo

let enviados = 0;
const vistos = new Set();

function rutaDe(url) {
    try {
        return new URL(url, window.location.origin).pathname.slice(0, 300);
    } catch {
        return undefined;
    }
}

export function reportarFallo(tipo, mensaje, extra = {}) {
    const texto = String(mensaje || 'Error sin mensaje').slice(0, MAXIMO_TEXTO);
    const clave = `${tipo}|${texto}`;
    if (enviados >= MAXIMO_ENVIOS || vistos.has(clave)) return;
    vistos.add(clave);
    enviados += 1;

    const cuerpo = { tipo, mensaje: texto, ruta: window.location.pathname.slice(0, 200), ...extra };
    if (cuerpo.id_peticion !== undefined && !/^[0-9a-f]{16}$/.test(cuerpo.id_peticion)) delete cuerpo.id_peticion;
    try {
        fetch('/api/diagnostico/cliente', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cuerpo),
            keepalive: true,
        }).catch(() => {});
    } catch {
        // Sin soporte para el envío: el fallo ya se mostró en la consola
    }
}

export function inicializarDiagnostico() {
    window.addEventListener('error', (e) => {
        reportarFallo('js', e.message, { origen: rutaDe(e.filename), linea: e.lineno || undefined });
    });
    window.addEventListener('unhandledrejection', (e) => {
        const motivo = e.reason?.message || e.reason;
        if (motivo === SIN_REPORTAR) return;
        reportarFallo('promesa', motivo);
    });
}
