/**
 * static/js/asistente_telegram.js - Vincular el asistente de Telegram desde el menú de usuario.
 * La persona escribe su contraseña actual, recibe un código QR de un solo uso (10 minutos) y lo escanea con el teléfono.
 * El código y el QR solo viven en el DOM mientras el modal está abierto.
 */

import { apiFetch, toast } from './core.js';
import { confirmar } from './confirmar.js';

const SECCIONES = ['telegram-cargando', 'telegram-no-disponible', 'telegram-vinculado', 'telegram-formulario', 'telegram-qr-caja'];
const SONDEO_MS = 3000;

let reloj = null;
let sondeo = null;
let venceEn = 0;
let generando = false;

const campo = (id) => document.getElementById(id);

function mostrar(seccion) {
    SECCIONES.forEach((id) => campo(id)?.classList.toggle('hidden', id !== seccion));
}

function detener() {
    clearInterval(reloj);
    clearInterval(sondeo);
    reloj = sondeo = null;
}

function limpiarCodigo() {
    detener();
    const imagen = campo('telegram-qr');
    if (imagen) imagen.removeAttribute('src');
    campo('telegram-enlace')?.removeAttribute('href');
    const clave = campo('telegram-contrasena');
    if (clave) clave.value = '';
}

async function leerEstado() {
    const res = await apiFetch('/api/asistente/telegram');
    if (!res.ok) throw new Error('estado');
    return res.json();
}

function pintarEstado(estado) {
    if (!estado.disponible) {
        mostrar('telegram-no-disponible');
    } else if (estado.vinculado) {
        campo('telegram-usuario').textContent = estado.username ? `@${estado.username}` : 'tu chat';
        mostrar('telegram-vinculado');
    } else {
        mostrar('telegram-formulario');
    }
}

function pintarCuentaRegresiva() {
    const restante = Math.max(0, Math.round((venceEn - Date.now()) / 1000));
    const minutos = String(Math.floor(restante / 60));
    const segundos = String(restante % 60).padStart(2, '0');
    campo('telegram-vence').textContent = restante > 0 ? `Vence en ${minutos}:${segundos}.` : 'El código venció: genera uno nuevo.';
    if (restante === 0) {
        detener();
        campo('telegram-qr')?.removeAttribute('src');
        campo('telegram-enlace')?.removeAttribute('href');
    }
}

export async function abrirModalTelegram() {
    const modal = campo('modal-telegram');
    if (!modal) return;
    limpiarCodigo();
    mostrar('telegram-cargando');
    modal.classList.remove('hidden');
    try {
        pintarEstado(await leerEstado());
    } catch (err) {
        mostrar('telegram-no-disponible');
        toast('No se pudo consultar el estado de Telegram.', 'error');
    }
    setTimeout(() => campo('telegram-contrasena')?.focus(), 10);
}

export function cerrarModalTelegram() {
    limpiarCodigo();
    campo('modal-telegram')?.classList.add('hidden');
}

export function mostrarFormularioTelegram() {
    limpiarCodigo();
    mostrar('telegram-formulario');
    campo('telegram-contrasena')?.focus();
}

export async function generarCodigoTelegram(event) {
    event?.preventDefault();
    if (generando) return;  // doble clic: un solo código
    const clave = campo('telegram-contrasena')?.value || '';
    if (!clave) {
        toast('Escribe tu contraseña actual.', 'error');
        return;
    }
    generando = true;
    const boton = campo('btn-generar-telegram');
    if (boton) boton.disabled = true;
    try {
        const res = await apiFetch('/api/asistente/telegram/codigo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ contrasena_actual: clave }),
        });
        const datos = await res.json().catch(() => ({}));
        if (!res.ok) {
            toast(typeof datos.detail === 'string' ? datos.detail : 'No se pudo generar el código.', 'error');
            return;
        }
        if (typeof datos.enlace !== 'string' || !datos.enlace.startsWith('https://t.me/') || typeof datos.qr !== 'string' || !datos.qr.startsWith('data:image/svg+xml')) {
            toast('La respuesta del servidor no es válida.', 'error');
            return;
        }
        campo('telegram-contrasena').value = '';
        campo('telegram-qr').src = datos.qr;
        campo('telegram-enlace').href = datos.enlace;
        venceEn = Date.now() + (Number(datos.vence_en_segundos) || 600) * 1000;
        mostrar('telegram-qr-caja');
        pintarCuentaRegresiva();
        detener();
        reloj = setInterval(pintarCuentaRegresiva, 1000);
        sondeo = setInterval(async () => {
            try {
                const estado = await leerEstado();
                if (estado.vinculado) {
                    limpiarCodigo();
                    pintarEstado(estado);
                    toast('Telegram quedó vinculado.', 'ok');
                }
            } catch (err) { /* el siguiente sondeo lo reintenta */ }
        }, SONDEO_MS);
    } catch (err) {
        toast('Error al conectar con el servidor.', 'error');
    } finally {
        generando = false;
        if (boton) boton.disabled = false;
    }
}

export async function desvincularTelegram() {
    if (!await confirmar({ titulo: 'Desconectar Telegram', mensaje: 'El asistente dejará de responder en ese chat. Puedes volver a vincularlo cuando quieras.', aceptar: 'Desconectar', peligro: true })) return;
    try {
        const res = await apiFetch('/api/asistente/telegram', { method: 'DELETE' });
        if (!res.ok) {
            toast('No se pudo desconectar Telegram.', 'error');
            return;
        }
        toast('Telegram desconectado.', 'ok');
        pintarEstado(await leerEstado());
    } catch (err) {
        toast('Error al conectar con el servidor.', 'error');
    }
}

Object.assign(window, {
    abrirModalTelegram,
    cerrarModalTelegram,
    desvincularTelegram,
    generarCodigoTelegram,
    mostrarFormularioTelegram,
});
