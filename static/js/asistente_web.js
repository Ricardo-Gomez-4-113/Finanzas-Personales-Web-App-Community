/**
 * static/js/asistente_web.js - La burbuja del asistente: abajo a la derecha, abre un chat con el mismo asistente de Telegram.
 *
 * Solo existe con la sesión iniciada y si el servidor dice que está disponible (`/api/asistente/web/estado`: el servicio del asistente
 * está arriba y el administrador no lo apagó para esta cuenta). Todo lo que viene del asistente (texto, resúmenes de propuestas) es texto no
 * confiable: se escribe SIEMPRE con textContent, nunca con innerHTML. Las operaciones que el asistente prepara solo se ejecutan con el botón
 * «Confirmar» de la tarjeta; el navegador nunca ve tokens del asistente.
 */

import { apiFetch, estado, toast } from './core.js';

const API = '/api/asistente/web';
const MAX_ARCHIVO_BYTES = 5 * 1024 * 1024;
const MAX_FOTO_ORIGINAL_BYTES = 25 * 1024 * 1024;   // antes de decodificar: un archivo enorme no llega al decodificador
const MAX_FOTO_PIXELES = 100e6;                      // una imagen pequeña en bytes pero con dimensiones absurdas tampoco
const MAX_FOTO_LADO = 1600;
const MAX_AUDIO_SEG = 85;          // el servidor acepta hasta 90 s
const MAX_AUDIO_BYTES = 3 * 1024 * 1024;
const TIEMPO_MAXIMO_MS = 80000;
const MAX_TEXTO = 2000;
const TASA_AUDIO = 16000;          // WAV mono de 16 kHz: lo que Gemini acepta y pesa ~32 KB por segundo

const SVG_NS = 'http://www.w3.org/2000/svg';
const ICONOS = {
    burbuja: 'M21 12a8 8 0 0 1-11.5 7.2L4 20l1-4.3A8 8 0 1 1 21 12z',
    cerrar: 'M6 6l12 12M18 6L6 18',
    nuevo: 'M12 5v14M5 12h14',
    foto: 'M4 8h3l1.5-2h7L17 8h3v11H4zM12 17a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z',
    mic: 'M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM6 11a6 6 0 0 0 12 0M12 17v4',
    parar: 'M7 7h10v10H7z',
    enviar: 'M4 12l16-8-6 16-3-7z',
};

const s = {
    montada: false,
    abierto: false,
    ocupado: false,
    adjunto: null,          // { tipo: 'foto' | 'audio', blob, etiqueta, vista }
    grabacion: null,        // { contexto, flujo, procesador, fuente, trozos, tasa, inicio, reloj }
    pidiendoMic: false,     // esperando el permiso del micrófono: otro clic no abre un segundo flujo
    cancelar: null,         // AbortController de la petición en curso
    generacion: 0,          // cambia al cerrar la sesión: una respuesta que llega tarde no se pinta en otra sesión
};

const el = {};

function icono(nombre, ancho = 20) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', String(ancho));
    svg.setAttribute('height', String(ancho));
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.8');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    const ruta = document.createElementNS(SVG_NS, 'path');
    ruta.setAttribute('d', ICONOS[nombre]);
    svg.appendChild(ruta);
    return svg;
}

function nodo(etiqueta, clase, atributos = {}, texto = '') {
    const n = document.createElement(etiqueta);
    if (clase) n.className = clase;
    Object.entries(atributos).forEach(([k, v]) => n.setAttribute(k, v));
    if (texto) n.textContent = texto;
    return n;
}

function boton(clase, etiqueta, nombreIcono, tipo = 'button') {
    const b = nodo('button', clase, { type: tipo, 'aria-label': etiqueta, title: etiqueta });
    b.appendChild(icono(nombreIcono));
    return b;
}

// --------------------------------------------------------------------------------------------------------------------
// Montaje
// --------------------------------------------------------------------------------------------------------------------

function montar() {
    if (s.montada) return;
    const raiz = nodo('div', 'asistente-web', { id: 'asistente-web' });

    el.burbuja = boton('asistente-burbuja', 'Abrir el asistente', 'burbuja');
    el.burbuja.id = 'asistente-burbuja';
    el.burbuja.setAttribute('aria-expanded', 'false');
    el.burbuja.setAttribute('aria-controls', 'asistente-panel');

    el.panel = nodo('section', 'asistente-panel hidden', { id: 'asistente-panel', role: 'dialog', 'aria-labelledby': 'asistente-titulo' });
    const cabecera = nodo('header', 'asistente-cabecera');
    cabecera.appendChild(nodo('h2', 'asistente-titulo', { id: 'asistente-titulo' }, 'Asistente'));
    el.nuevo = boton('asistente-icono-btn', 'Nueva conversación', 'nuevo');
    el.cerrar = boton('asistente-icono-btn', 'Cerrar el asistente', 'cerrar');
    cabecera.append(el.nuevo, el.cerrar);

    el.mensajes = nodo('div', 'asistente-mensajes', { id: 'asistente-mensajes', role: 'log', 'aria-live': 'polite', 'aria-label': 'Conversación con el asistente', tabindex: '0' });

    el.chip = nodo('div', 'asistente-adjunto hidden', { id: 'asistente-adjunto' });
    el.chipVista = nodo('img', 'asistente-adjunto-vista hidden', { alt: '', width: '36', height: '36' });
    el.chipTexto = nodo('span', 'asistente-adjunto-texto');
    el.chipQuitar = boton('asistente-icono-btn', 'Quitar el archivo adjunto', 'cerrar');
    el.chip.append(el.chipVista, el.chipTexto, el.chipQuitar);

    el.form = nodo('form', 'asistente-form', { id: 'asistente-form', autocomplete: 'off' });
    el.foto = boton('asistente-icono-btn', 'Adjuntar una foto', 'foto');
    el.archivo = nodo('input', 'hidden', { type: 'file', accept: 'image/jpeg,image/png,image/webp', id: 'asistente-archivo', 'aria-hidden': 'true', tabindex: '-1' });
    el.mic = boton('asistente-icono-btn', 'Grabar una nota de voz', 'mic');
    el.mic.id = 'asistente-mic';
    el.texto = nodo('textarea', 'asistente-texto', { id: 'asistente-texto', rows: '1', maxlength: String(MAX_TEXTO), placeholder: 'Escribe tu pregunta', 'aria-label': 'Mensaje para el asistente' });
    el.enviar = boton('asistente-enviar', 'Enviar', 'enviar', 'submit');
    el.enviar.id = 'asistente-enviar';
    const hayMicrofono = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && (window.AudioContext || window.webkitAudioContext));
    el.mic.classList.toggle('hidden', !hayMicrofono);
    el.form.append(el.foto, el.mic, el.texto, el.enviar, el.archivo);

    el.panel.append(cabecera, el.mensajes, el.chip, el.form);
    raiz.append(el.burbuja, el.panel);
    document.body.appendChild(raiz);
    document.body.classList.add('con-asistente');
    s.montada = true;

    el.burbuja.addEventListener('click', () => alternar(true));
    el.cerrar.addEventListener('click', () => alternar(false));
    el.nuevo.addEventListener('click', nuevaConversacion);
    el.form.addEventListener('submit', (e) => { e.preventDefault(); enviar(); });
    el.texto.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); enviar(); }
    });
    el.texto.addEventListener('input', ajustarAltura);
    el.panel.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); alternar(false); } });
    el.foto.addEventListener('click', () => el.archivo.click());
    el.archivo.addEventListener('change', alElegirFoto);
    el.mic.addEventListener('click', alternarGrabacion);
    el.chipQuitar.addEventListener('click', quitarAdjunto);
}

function desmontar() {
    s.generacion++;
    if (s.cancelar) s.cancelar.abort();
    detenerGrabacion(false);
    document.getElementById('asistente-web')?.remove();
    document.body.classList.remove('con-asistente', 'asistente-abierto');
    Object.keys(el).forEach((k) => delete el[k]);
    Object.assign(s, { montada: false, abierto: false, ocupado: false, adjunto: null, cancelar: null, pidiendoMic: false });
}

function alternar(abrir) {
    s.abierto = abrir;
    el.panel.classList.toggle('hidden', !abrir);
    el.burbuja.setAttribute('aria-expanded', String(abrir));
    document.body.classList.toggle('asistente-abierto', abrir);
    if (abrir) {
        if (!el.mensajes.children.length) saludo();
        el.texto.focus();
        bajar();
    } else {
        detenerGrabacion(false);   // cerrar el panel corta la grabación: el chip que la mostraba vive dentro de él
        el.burbuja.focus();
    }
}

function saludo() {
    agregar('asistente', 'Hola, soy tu asistente. Pregúntame por tus saldos, gastos, metas o inversiones. También puedo preparar un gasto o un ingreso, o leer la foto de un ticket o una nota de voz; siempre te pido confirmar antes de registrar algo.');
}

function ajustarAltura() {
    el.texto.style.height = 'auto';
    el.texto.style.height = `${Math.min(el.texto.scrollHeight, 110)}px`;
}

function bajar() {
    el.mensajes.scrollTop = el.mensajes.scrollHeight;
}

// --------------------------------------------------------------------------------------------------------------------
// Mensajes
// --------------------------------------------------------------------------------------------------------------------

function agregar(rol, texto, clase = '') {
    const m = nodo('div', `asistente-msg asistente-msg-${rol} ${clase}`.trim());
    m.textContent = texto;   // texto no confiable: nunca HTML
    el.mensajes.appendChild(m);
    bajar();
    return m;
}

function tarjetaDePropuesta(propuesta) {
    const tarjeta = nodo('div', 'asistente-propuesta', { role: 'group', 'aria-label': 'Operación por confirmar' });
    tarjeta.appendChild(nodo('p', 'asistente-propuesta-resumen', {}, propuesta.resumen));
    const acciones = nodo('div', 'asistente-propuesta-acciones');
    const confirmar = nodo('button', 'asistente-btn asistente-btn-principal', { type: 'button' }, 'Confirmar');
    const cancelar = nodo('button', 'asistente-btn', { type: 'button' }, 'Cancelar');
    const resultado = nodo('p', 'asistente-propuesta-resultado hidden', { role: 'status' });
    acciones.append(confirmar, cancelar);
    tarjeta.append(acciones, resultado);

    const resolver = async (ruta, esConfirmar) => {
        confirmar.disabled = cancelar.disabled = true;   // un doble toque no confirma dos veces
        const generacion = s.generacion;
        try {
            const res = await apiFetch(`${API}/${ruta}/${encodeURIComponent(propuesta.id)}`, { method: 'POST' });
            if (generacion !== s.generacion) return;
            const datos = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(typeof datos.detail === 'string' ? datos.detail : 'No se pudo completar la operación.');
            acciones.classList.add('hidden');
            resultado.textContent = typeof datos.texto === 'string' ? datos.texto : '';
            resultado.classList.remove('hidden');
            if (esConfirmar && datos.resultado === 'confirmada') document.dispatchEvent(new CustomEvent('recargar'));   // el cambio aparece sin recargar la página
        } catch (error) {
            if (generacion !== s.generacion) return;
            confirmar.disabled = cancelar.disabled = false;
            toast(error.message === 'No autorizado' ? 'Tu sesión terminó.' : (error.message || 'No se pudo completar la operación.'), 'error');
        }
    };
    confirmar.addEventListener('click', () => resolver('confirmar', true));
    cancelar.addEventListener('click', () => resolver('cancelar', false));
    return tarjeta;
}

function mostrarRespuesta(datos) {
    const aviso = datos.estado !== 'ok';
    if (datos.texto) agregar('asistente', datos.texto, aviso ? 'asistente-msg-aviso' : '');
    (datos.propuestas || []).forEach((p) => {
        el.mensajes.appendChild(tarjetaDePropuesta(p));
    });
    bajar();
}

function escribiendo(visible) {
    el.mensajes.querySelector('.asistente-escribiendo')?.remove();
    if (!visible) return;
    const e = nodo('div', 'asistente-msg asistente-msg-asistente asistente-escribiendo', { role: 'status', 'aria-label': 'El asistente está escribiendo' });
    for (let i = 0; i < 3; i++) e.appendChild(nodo('span', 'asistente-punto'));
    el.mensajes.appendChild(e);
    bajar();
}

function fijarOcupado(valor) {
    s.ocupado = valor;
    el.enviar.disabled = valor;
    el.texto.disabled = valor;
    el.foto.disabled = valor;
    el.mic.disabled = valor && !s.grabacion;
    el.nuevo.disabled = valor;
    el.panel.setAttribute('aria-busy', String(valor));
}

// --------------------------------------------------------------------------------------------------------------------
// Enviar
// --------------------------------------------------------------------------------------------------------------------

async function enviar() {
    if (s.ocupado || s.grabacion) return;
    const texto = el.texto.value.trim();
    const adjunto = s.adjunto;
    if (!texto && !adjunto) return;
    const generacion = s.generacion;
    agregar('persona', [adjunto ? `[${adjunto.etiqueta}]` : '', texto].filter(Boolean).join(' '));
    el.texto.value = '';
    ajustarAltura();
    quitarAdjunto();
    fijarOcupado(true);
    escribiendo(true);
    s.cancelar = new AbortController();
    const reloj = setTimeout(() => s.cancelar?.abort(), TIEMPO_MAXIMO_MS);
    try {
        let opciones;
        if (adjunto) {
            const forma = new FormData();
            forma.append('texto', texto);
            forma.append('archivo', adjunto.blob, adjunto.tipo === 'audio' ? 'nota.wav' : 'foto.jpg');
            opciones = { method: 'POST', body: forma };
        } else {
            opciones = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ texto }) };
        }
        const res = await apiFetch(adjunto ? `${API}/mensaje-archivo` : `${API}/mensaje`, { ...opciones, signal: s.cancelar.signal });
        if (generacion !== s.generacion) return;
        const datos = await res.json().catch(() => ({}));
        escribiendo(false);
        if (res.ok) {
            mostrarRespuesta(datos);
        } else if (res.status === 403) {
            toast('El administrador desactivó el asistente para tu cuenta.', 'error');
            desmontar();
            return;
        } else if (res.status === 429) {
            agregar('asistente', 'Espera a que termine de contestar y vuelve a intentarlo.', 'asistente-msg-aviso');
        } else if (res.status === 503) {
            agregar('asistente', 'El asistente no está disponible ahora. Intenta en un momento.', 'asistente-msg-aviso');
        } else {
            agregar('asistente', typeof datos.detail === 'string' ? datos.detail : 'No pude procesar ese mensaje.', 'asistente-msg-aviso');
        }
    } catch (error) {
        if (generacion !== s.generacion) return;
        escribiendo(false);
        agregar('asistente', error.name === 'AbortError' ? 'Tardó demasiado en contestar. Intenta de nuevo.' : 'No pude conectar con el asistente. Revisa tu red e inténtalo de nuevo.', 'asistente-msg-aviso');
    } finally {
        clearTimeout(reloj);
        if (generacion === s.generacion && s.montada) {
            s.cancelar = null;
            fijarOcupado(false);
            if (s.abierto) el.texto.focus();
        }
    }
}

async function nuevaConversacion() {
    if (s.ocupado) return;
    el.mensajes.replaceChildren();
    quitarAdjunto();
    saludo();
    try { await apiFetch(`${API}/olvidar`, { method: 'POST' }); } catch { /* sin conexión: la conversación local ya se limpió */ }
}

// --------------------------------------------------------------------------------------------------------------------
// Adjuntos: foto y nota de voz
// --------------------------------------------------------------------------------------------------------------------

function ponerAdjunto(adjunto) {
    quitarAdjunto();
    s.adjunto = adjunto;
    el.chipTexto.textContent = adjunto.etiqueta;
    if (adjunto.vista) {
        el.chipVista.src = adjunto.vista;
        el.chipVista.classList.remove('hidden');
    }
    el.chip.classList.remove('hidden');
}

function quitarAdjunto() {
    s.adjunto = null;
    if (!s.montada) return;
    el.chip.classList.add('hidden');
    el.chipVista.classList.add('hidden');
    el.chipVista.removeAttribute('src');
    el.chipTexto.textContent = '';
    el.archivo.value = '';
}

function comoDataUrl(blob) {
    return new Promise((resolver) => {
        const lector = new FileReader();   // data: (no blob:) porque la política de contenido solo deja imágenes propias, data: y https
        lector.onload = () => resolver(String(lector.result));
        lector.onerror = () => resolver('');
        lector.readAsDataURL(blob);
    });
}

/** Reduce la foto a un JPEG de hasta 1600 px: pesa menos, se sube más rápido y el asistente gasta menos. */
async function reducirFoto(archivo) {
    const mapa = await createImageBitmap(archivo, { imageOrientation: 'from-image' });
    if (mapa.width * mapa.height > MAX_FOTO_PIXELES) {
        mapa.close?.();
        throw new Error('imagen demasiado grande');
    }
    const escala = Math.min(1, MAX_FOTO_LADO / Math.max(mapa.width, mapa.height));
    const lienzo = document.createElement('canvas');
    lienzo.width = Math.max(1, Math.round(mapa.width * escala));
    lienzo.height = Math.max(1, Math.round(mapa.height * escala));
    const ctx = lienzo.getContext('2d');
    ctx.fillStyle = 'white';   // dato de imagen, no de interfaz: las transparencias de un PNG pasan a blanco en vez de negro
    ctx.fillRect(0, 0, lienzo.width, lienzo.height);
    ctx.drawImage(mapa, 0, 0, lienzo.width, lienzo.height);
    mapa.close?.();
    return new Promise((resolver, rechazar) => lienzo.toBlob((b) => (b ? resolver(b) : rechazar(new Error('sin imagen'))), 'image/jpeg', 0.85));
}

async function alElegirFoto() {
    const archivo = el.archivo.files && el.archivo.files[0];
    if (!archivo) return;
    if (archivo.size > MAX_FOTO_ORIGINAL_BYTES) {
        toast('Esa imagen es demasiado grande.', 'error');
        el.archivo.value = '';
        return;
    }
    let blob;
    try {
        blob = await reducirFoto(archivo);   // pasar por el lienzo también quita los metadatos (ubicación, cámara) de la foto
    } catch {
        toast('No pude leer esa imagen. Prueba con una foto JPG o PNG.', 'error');
        el.archivo.value = '';
        return;
    }
    if (blob.size > MAX_ARCHIVO_BYTES) {
        toast('Esa foto es demasiado grande (máximo 5 MB).', 'error');
        el.archivo.value = '';
        return;
    }
    ponerAdjunto({ tipo: 'foto', blob, etiqueta: 'Foto', vista: await comoDataUrl(blob) });
    el.texto.focus();
}

// --- Notas de voz: se graban como WAV mono de 16 kHz (Gemini no lee el webm/mp4 que graba MediaRecorder) ---

function aWav(trozos, tasaOrigen) {
    let total = 0;
    trozos.forEach((t) => { total += t.length; });
    const crudo = new Float32Array(total);
    let pos = 0;
    trozos.forEach((t) => { crudo.set(t, pos); pos += t.length; });
    const razon = tasaOrigen / TASA_AUDIO;
    const largo = Math.floor(crudo.length / razon);
    const salida = new DataView(new ArrayBuffer(44 + largo * 2));
    const texto = (desde, cadena) => { for (let i = 0; i < cadena.length; i++) salida.setUint8(desde + i, cadena.charCodeAt(i)); };
    texto(0, 'RIFF'); salida.setUint32(4, 36 + largo * 2, true); texto(8, 'WAVE'); texto(12, 'fmt ');
    salida.setUint32(16, 16, true); salida.setUint16(20, 1, true); salida.setUint16(22, 1, true);
    salida.setUint32(24, TASA_AUDIO, true); salida.setUint32(28, TASA_AUDIO * 2, true); salida.setUint16(32, 2, true); salida.setUint16(34, 16, true);
    texto(36, 'data'); salida.setUint32(40, largo * 2, true);
    for (let i = 0; i < largo; i++) {
        const origen = i * razon;
        const a = Math.floor(origen);
        const b = Math.min(a + 1, crudo.length - 1);
        const muestra = crudo[a] + (crudo[b] - crudo[a]) * (origen - a);   // interpolación lineal
        salida.setInt16(44 + i * 2, Math.max(-1, Math.min(1, muestra)) * 0x7fff, true);
    }
    return new Blob([salida.buffer], { type: 'audio/wav' });
}

const mmss = (seg) => `${Math.floor(seg / 60)}:${String(Math.floor(seg % 60)).padStart(2, '0')}`;

function soltarMicrofono(flujo, contexto) {
    flujo?.getTracks().forEach((t) => t.stop());
    try { contexto?.close?.(); } catch { /* ya estaba cerrado */ }
}

async function alternarGrabacion() {
    if (s.grabacion) { detenerGrabacion(true); return; }
    if (s.ocupado || s.pidiendoMic) return;
    s.pidiendoMic = true;
    const generacion = s.generacion;
    let flujo = null;
    let contexto = null;
    try {
        flujo = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (generacion !== s.generacion || !s.montada) {   // la sesión terminó mientras el navegador pedía el permiso
            soltarMicrofono(flujo, null);
            return;
        }
        const Contexto = window.AudioContext || window.webkitAudioContext;
        contexto = new Contexto();
        const fuente = contexto.createMediaStreamSource(flujo);
        const procesador = contexto.createScriptProcessor(4096, 1, 1);
        const g = { contexto, flujo, fuente, procesador, trozos: [], tasa: contexto.sampleRate, inicio: Date.now(), reloj: null };
        procesador.onaudioprocess = (e) => { g.trozos.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
        fuente.connect(procesador);
        procesador.connect(contexto.destination);
        s.grabacion = g;
        quitarAdjunto();
        el.mic.classList.add('grabando');
        el.mic.setAttribute('aria-label', 'Detener la grabación');
        el.mic.replaceChildren(icono('parar'));
        el.chipTexto.textContent = 'Grabando 0:00';
        el.chip.classList.remove('hidden');
        g.reloj = setInterval(() => {
            const seg = (Date.now() - g.inicio) / 1000;
            el.chipTexto.textContent = `Grabando ${mmss(seg)}`;
            if (seg >= MAX_AUDIO_SEG) detenerGrabacion(true);
        }, 250);
        fijarOcupado(false);
    } catch {
        soltarMicrofono(flujo, contexto);   // cualquier fallo después de abrir el micrófono lo suelta
        if (s.grabacion && s.grabacion.flujo === flujo) { clearInterval(s.grabacion.reloj); s.grabacion = null; }
        if (s.montada) toast('No pude usar el micrófono. Revisa el permiso del navegador.', 'error');
    } finally {
        s.pidiendoMic = false;
    }
}

function detenerGrabacion(conservar) {
    const g = s.grabacion;
    if (!g) return;
    s.grabacion = null;
    clearInterval(g.reloj);
    try { g.procesador.disconnect(); g.fuente.disconnect(); } catch { /* ya estaba suelto */ }
    g.flujo.getTracks().forEach((t) => t.stop());
    g.contexto.close?.();
    if (!s.montada) return;
    el.mic.classList.remove('grabando');
    el.mic.setAttribute('aria-label', 'Grabar una nota de voz');
    el.mic.replaceChildren(icono('mic'));
    el.chip.classList.add('hidden');
    el.chipTexto.textContent = '';
    const segundos = (Date.now() - g.inicio) / 1000;
    if (!conservar) return;
    if (segundos < 0.7 || !g.trozos.length) { toast('La nota de voz quedó muy corta.', 'error'); return; }
    const blob = aWav(g.trozos, g.tasa);
    if (blob.size > MAX_AUDIO_BYTES) { toast('La nota de voz es demasiado larga.', 'error'); return; }
    ponerAdjunto({ tipo: 'audio', blob, etiqueta: `Nota de voz ${mmss(segundos)}`, vista: '' });
    el.texto.focus();
}

// --------------------------------------------------------------------------------------------------------------------
// Ciclo de vida con la sesión
// --------------------------------------------------------------------------------------------------------------------

async function iniciar() {
    if (s.montada) return;
    const generacion = ++s.generacion;
    try {
        const res = await apiFetch(`${API}/estado`);
        if (!res.ok || generacion !== s.generacion || !estado.usuarioActual) return;
        const datos = await res.json();
        if (datos.disponible === true) montar();
    } catch { /* sin asistente: la app funciona igual */ }
}

document.addEventListener('sesion-activa', iniciar);
document.addEventListener('sesion-cerrada', desmontar);   // el token ya no existe: el servidor olvida la conversación sola a los 30 minutos
