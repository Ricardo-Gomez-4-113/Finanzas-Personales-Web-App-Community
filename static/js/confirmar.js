/**
 * static/js/confirmar.js - Modal de confirmación propio, en lugar de confirm() nativo.
 * `confirmar({...})` devuelve una promesa con true (aceptó) o false (canceló, Escape o clic afuera).
 */

let resolver = null;
let enlazado = false;

function elemento(id) {
    return document.getElementById(id);
}

function cerrar(aceptado) {
    const raiz = elemento('modal-confirmar');
    if (!resolver || !raiz) return;
    const listo = resolver;
    resolver = null;
    raiz.classList.add('hidden');
    elemento('confirmar-detalle').replaceChildren();
    listo(aceptado);
}

function enlazar() {
    if (enlazado) return;
    enlazado = true;
    const raiz = elemento('modal-confirmar');
    elemento('btn-confirmar-cancelar').addEventListener('click', () => cerrar(false));
    elemento('btn-confirmar-aceptar').addEventListener('click', () => cerrar(true));
    raiz.addEventListener('click', e => { if (e.target === raiz) cerrar(false); });
    // En captura y antes que los demás: Escape cierra solo esta confirmación, no el modal que quedó debajo
    window.addEventListener('keydown', e => {
        if (e.key !== 'Escape' || !resolver) return;
        e.stopPropagation();
        cerrar(false);
    }, true);
}

/**
 * @param {object} opciones
 * @param {string} opciones.titulo
 * @param {string} [opciones.mensaje] Texto plano; los saltos de línea se respetan.
 * @param {Node|Node[]} [opciones.detalle] Contenido adicional ya construido (sin HTML en bruto).
 * @param {string} [opciones.aceptar] Texto del botón de confirmar.
 * @param {string} [opciones.cancelar] Texto del botón de cancelar.
 * @param {boolean} [opciones.peligro] Resalta la acción como destructiva.
 * @returns {Promise<boolean>}
 */
export function confirmar({ titulo, mensaje = '', detalle = null, aceptar = 'Continuar', cancelar = 'Cancelar', peligro = false }) {
    const raiz = elemento('modal-confirmar');
    if (!raiz) return Promise.resolve(false);
    enlazar();
    if (resolver) cerrar(false);

    elemento('confirmar-titulo').textContent = titulo;
    const texto = elemento('confirmar-mensaje');
    texto.textContent = mensaje;
    texto.classList.toggle('hidden', !mensaje);
    elemento('confirmar-detalle').replaceChildren(...(detalle ? [].concat(detalle) : []));
    elemento('btn-confirmar-cancelar').textContent = cancelar;
    const botonAceptar = elemento('btn-confirmar-aceptar');
    botonAceptar.textContent = aceptar;
    botonAceptar.classList.toggle('btn-danger-solid', peligro);
    botonAceptar.classList.toggle('btn-primary', !peligro);

    return new Promise(resolve => {
        resolver = resolve;
        raiz.classList.remove('hidden');
    });
}
