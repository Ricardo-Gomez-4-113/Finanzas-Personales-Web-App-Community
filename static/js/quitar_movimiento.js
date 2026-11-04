/**
 * static/js/quitar_movimiento.js - Cuadro de tres opciones para quitar un movimiento.
 * `elegirQuitar({...})` devuelve una promesa con 'cancelar' (el dinero vuelve y queda constancia como cancelado),
 * 'eliminar' (el dinero vuelve y se borra sin rastro) o null (Volver, Escape o clic afuera).
 * Es aparte de confirmar(): ese solo sabe de dos botones.
 */

let resolver = null;
let enlazado = false;

function elemento(id) {
    return document.getElementById(id);
}

function cerrar(opcion) {
    const raiz = elemento('modal-quitar-movimiento');
    if (!resolver || !raiz) return;
    const listo = resolver;
    resolver = null;
    raiz.classList.add('hidden');
    listo(opcion);
}

function enlazar() {
    if (enlazado) return;
    enlazado = true;
    const raiz = elemento('modal-quitar-movimiento');
    elemento('btn-quitar-cancelar').addEventListener('click', () => cerrar('cancelar'));
    elemento('btn-quitar-eliminar').addEventListener('click', () => cerrar('eliminar'));
    elemento('btn-quitar-volver').addEventListener('click', () => cerrar(null));
    raiz.addEventListener('click', e => { if (e.target === raiz) cerrar(null); });
    // En captura y antes que los demás: Escape cierra solo este cuadro
    window.addEventListener('keydown', e => {
        if (e.key !== 'Escape' || !resolver) return;
        e.stopPropagation();
        cerrar(null);
    }, true);
}

/**
 * @param {object} opciones
 * @param {string} opciones.titulo
 * @param {string} [opciones.mensaje] Advertencias del movimiento (texto plano; los saltos de línea se respetan).
 * @param {string} opciones.ayudaCancelar Qué hace «Cancelar operación» en este caso.
 * @param {string} opciones.ayudaEliminar Qué hace «Eliminar» en este caso.
 * @returns {Promise<'cancelar'|'eliminar'|null>}
 */
export function elegirQuitar({ titulo, mensaje = '', ayudaCancelar, ayudaEliminar }) {
    const raiz = elemento('modal-quitar-movimiento');
    if (!raiz) return Promise.resolve(null);
    enlazar();
    if (resolver) cerrar(null);

    elemento('quitar-titulo').textContent = titulo;
    const texto = elemento('quitar-mensaje');
    texto.textContent = mensaje;
    texto.classList.toggle('hidden', !mensaje);
    elemento('quitar-ayuda-cancelar').textContent = ayudaCancelar;
    elemento('quitar-ayuda-eliminar').textContent = ayudaEliminar;

    return new Promise(resolve => {
        resolver = resolve;
        raiz.classList.remove('hidden');
    });
}
