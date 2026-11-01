/**
 * static/js/comentarios.js - Envío de comentarios desde el menú de usuario.
 * El comentario se guarda en la base maestra (tabla comentarios). La app no lo muestra: se consulta en la base.
 */

import { apiFetch, toast } from './core.js';

const LIMITE = 1000;
let enviando = false;

const campo = (id) => document.getElementById(id);

function pestanaActual() {
    return document.querySelector('.tab-content.active')?.id || null;
}

export function actualizarContadorComentario() {
    const texto = campo('comentario-mensaje');
    const contador = campo('comentario-contador');
    if (texto && contador) contador.textContent = `${texto.value.length}/${LIMITE}`;
}

export function abrirModalComentarios() {
    const modal = campo('modal-comentarios');
    if (!modal) return;
    campo('form-comentarios')?.reset();
    actualizarContadorComentario();
    modal.classList.remove('hidden');
    setTimeout(() => campo('comentario-mensaje')?.focus(), 10);
}

export function cerrarModalComentarios() {
    campo('modal-comentarios')?.classList.add('hidden');
}

export async function enviarComentario(event) {
    event?.preventDefault();
    if (enviando) return;  // doble clic: un solo envío
    const mensaje = campo('comentario-mensaje')?.value.trim() || '';
    if (!mensaje) {
        toast('Escribe tu comentario antes de enviarlo.', 'error');
        return;
    }

    enviando = true;
    const boton = campo('btn-enviar-comentario');
    if (boton) boton.disabled = true;
    try {
        const res = await apiFetch('/api/comentarios', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tipo: campo('comentario-tipo')?.value || 'otro',
                mensaje,
                contexto: pestanaActual(),
            }),
        });
        const datos = await res.json().catch(() => ({}));
        if (res.ok) {
            cerrarModalComentarios();
            toast(datos.mensaje || 'Gracias. Recibimos tu comentario.', 'ok');
        } else {
            toast(datos.detail || 'No se pudo enviar el comentario.', 'error');
        }
    } catch (err) {
        toast('Error al conectar con el servidor.', 'error');
    } finally {
        enviando = false;
        if (boton) boton.disabled = false;
    }
}

Object.assign(window, {
    abrirModalComentarios,
    actualizarContadorComentario,
    cerrarModalComentarios,
    enviarComentario,
});
