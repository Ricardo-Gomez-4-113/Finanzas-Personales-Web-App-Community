/**
 * static/js/notificaciones.js - La campana de avisos de la barra superior: contador de no leídos y un panel con la bandeja.
 *
 * Los avisos los genera el servidor (aporte omitido por falta de saldo, meta cumplida, gasto fijo retirado) y se guardan por cuenta; aquí solo
 * se leen y se marcan. El texto lleva nombres que escribió la persona: se escribe SIEMPRE con textContent, nunca con innerHTML.
 * Se consulta al iniciar sesión, al abrir el panel y cada minuto mientras la pestaña está a la vista.
 */

import { apiFetch, leerJson } from './core.js';

const API = '/api/notificaciones';
const CADA_MS = 60000;

const s = { montada: false, abierto: false, no_leidas: 0, lista: [], reloj: null, ocupado: false };

function el(id) {
    return document.getElementById(id);
}

function fechaCorta(texto) {
    const d = new Date(String(texto || '').replace(' ', 'T'));
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleDateString('es-MX', { day: 'numeric', month: 'short' });
}

function nodo(etiqueta, clase, texto) {
    const n = document.createElement(etiqueta);
    if (clase) n.className = clase;
    if (texto !== undefined) n.textContent = texto;
    return n;
}

function boton(texto, etiquetaAria, alHacerClic) {
    const b = nodo('button', 'notif-accion', texto);
    b.type = 'button';
    b.setAttribute('aria-label', etiquetaAria);
    b.addEventListener('click', alHacerClic);
    return b;
}

function pintarContador() {
    const contador = el('notif-contador');
    const campana = el('btn-notificaciones');
    if (!contador || !campana) return;
    contador.textContent = s.no_leidas > 99 ? '99+' : String(s.no_leidas);
    contador.classList.toggle('hidden', s.no_leidas === 0);
    campana.setAttribute('aria-label', s.no_leidas ? `Avisos, ${s.no_leidas} sin leer` : 'Avisos');
}

function pintarPanel() {
    const cuerpo = el('notif-lista');
    if (!cuerpo) return;
    cuerpo.replaceChildren();
    const vacio = el('notif-vacio');
    if (vacio) vacio.classList.toggle('hidden', s.lista.length > 0);
    for (const aviso of s.lista) {
        const item = nodo('li', 'notif-item' + (aviso.leida ? '' : ' no-leida'));
        const cabecera = nodo('div', 'notif-item-cabecera');
        cabecera.append(nodo('strong', 'notif-titulo', aviso.titulo), nodo('small', 'notif-fecha', fechaCorta(aviso.creada_en)));
        item.append(cabecera, nodo('p', 'notif-texto', aviso.texto));
        const acciones = nodo('div', 'notif-acciones');
        if (!aviso.leida) acciones.append(boton('Marcar como leído', `Marcar como leído: ${aviso.titulo}`, () => leer(aviso.id)));
        acciones.append(boton('Borrar', `Borrar aviso: ${aviso.titulo}`, () => borrar(aviso.id)));
        item.append(acciones);
        cuerpo.append(item);
    }
    const hayLeidas = s.lista.some(a => a.leida);
    el('notif-borrar-leidas')?.classList.toggle('hidden', !hayLeidas);
    el('notif-leer-todas')?.classList.toggle('hidden', s.no_leidas === 0);
}

export async function cargarNotificaciones() {
    if (s.ocupado) return;
    s.ocupado = true;
    try {
        const res = await apiFetch(API);
        if (!res.ok) return;
        const datos = await leerJson(res);
        const antes = s.no_leidas;
        s.no_leidas = datos.no_leidas;
        s.lista = datos.notificaciones;
        pintarContador();
        pintarPanel();
        const anuncio = el('notif-anuncio');
        if (anuncio && s.no_leidas > antes) anuncio.textContent = s.no_leidas - antes === 1 ? 'Tienes un aviso nuevo.' : `Tienes ${s.no_leidas - antes} avisos nuevos.`;
    } catch (error) {
        // sin red no pasa nada: el contador se queda como estaba y se vuelve a intentar al minuto
    } finally {
        s.ocupado = false;
    }
}

async function accion(ruta, metodo) {
    try {
        await apiFetch(ruta, { method: metodo });
    } catch (error) {
        // se vuelve a leer abajo y la bandeja muestra lo que de verdad quedó
    }
    await cargarNotificaciones();
}

const leer = (id) => accion(`${API}/${id}/leer`, 'POST');
const borrar = (id) => accion(`${API}/${id}`, 'DELETE');
const leerTodas = () => accion(`${API}/leer-todas`, 'POST');
const borrarLeidas = () => accion(API, 'DELETE');

function abrirPanel(abrir) {
    const panel = el('panel-notificaciones');
    const campana = el('btn-notificaciones');
    if (!panel || !campana) return;
    s.abierto = abrir;
    panel.classList.toggle('hidden', !abrir);
    campana.setAttribute('aria-expanded', abrir ? 'true' : 'false');
    if (abrir) cargarNotificaciones();
}

function alPulsarTecla(evento) {
    if (evento.key === 'Escape' && s.abierto) {
        abrirPanel(false);
        el('btn-notificaciones')?.focus();
    }
}

function alHacerClicFuera(evento) {
    if (s.abierto && !el('notificaciones-wrapper')?.contains(evento.target)) abrirPanel(false);
}

function montar() {
    if (s.montada) return;
    const campana = el('btn-notificaciones');
    if (!campana) return;
    s.montada = true;
    campana.addEventListener('click', () => abrirPanel(!s.abierto));
    el('notif-leer-todas')?.addEventListener('click', leerTodas);
    el('notif-borrar-leidas')?.addEventListener('click', borrarLeidas);
    document.addEventListener('keydown', alPulsarTecla);
    document.addEventListener('click', alHacerClicFuera);
}

function iniciar() {
    montar();
    cargarNotificaciones();
    clearInterval(s.reloj);
    s.reloj = setInterval(() => { if (document.visibilityState === 'visible') cargarNotificaciones(); }, CADA_MS);
}

function terminar() {
    clearInterval(s.reloj);
    s.reloj = null;
    s.no_leidas = 0;
    s.lista = [];
    if (s.montada) {
        abrirPanel(false);
        pintarContador();
        pintarPanel();
    }
}

document.addEventListener('sesion-activa', iniciar);
document.addEventListener('sesion-cerrada', terminar);   // el token ya no existe: no se vuelve a consultar ni queda nada de la cuenta a la vista
