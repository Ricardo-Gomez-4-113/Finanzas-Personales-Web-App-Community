/**
 * static/js/main.js - Punto de entrada: compone los módulos, escucha eventos globales y arranca la app.
 */

import { cerrarModalCrearUsuario, cerrarModalEditarUsuario } from './admin.js';
import { cargarApartados, cerrarModalCrearApartado } from './apartados.js';
import { cerrarModalRecuperarPassword, cerrarSesionLimpia, establecerSesionActiva, mostrarVistaLanding, mostrarVistaLogin, verificarSesionInicial } from './auth.js';
import { apiFetch, conTituloPrivado, desdeMXN, escapeHTML, estado, etiquetaPeriodo, fmt, leerJson, montoHTML, montoMxnHTML, toast } from './core.js';
import { actualizarGrafica, actualizarGraficaCategorias, renderPeriodo } from './dashboard.js';
import { renderCintaPeriodos } from './cinta_periodos.js';
import { cargarInversiones, cerrarModalEditarInversion, inicializarBuscadorTickers } from './inversiones.js';
import { cargarRecurrentes } from './recurrentes.js?v=a7a7eaf797';
import { cargarCategorias, cerrarModalEditarTransaccion, renderTablaMovimientos } from './movimientos.js';
import { cerrarModalLegal, cerrarSidebarMobile, cerrarUserDropdown, inicializarAccesibilidadModales, inicializarEstadoSidebar, inicializarFab, inicializarMenuUsuario, inicializarTabs } from './ui.js';
import { inicializarCalculadoraRF } from './calculadora_rf.js';
import { cargarPresupuesto, cerrarModalTarjetaPresupuesto, inicializarPresupuesto } from './presupuesto.js?v=a7a7eaf797';
import './respaldo.js'; // registra handlers de Excel y respaldo en window
import './comentarios.js'; // registra el envío de comentarios en window
import { activarAccionesDelegadas } from './acciones.js';
import { inicializarDiagnostico } from './diagnostico.js';

activarAccionesDelegadas();
document.getElementById('btn-inicio-vacio')?.addEventListener('click', () => document.querySelector('[data-tab="tab-movimientos"]')?.click());


async function cargarMovimientos() {
    try {
        const resTrans = await apiFetch('/api/transacciones');
        const dataTrans = await leerJson(resTrans, 'No se pudieron cargar los movimientos.');
        estado.cache.transacciones = {};
        dataTrans.forEach(t => { estado.cache.transacciones[t.id] = t; });
        estado.cache.listaTransacciones = dataTrans;
        document.getElementById('inicio-vacio')?.classList.toggle('hidden', dataTrans.length > 0);
        renderTablaMovimientos();
    } catch (error) {
        if (error.esHttp) toast(error.message, 'error');
        console.error("Error al cargar movimientos:", error);
    }
}

async function cargarGraficaCategorias() {
    const res = await apiFetch(`/api/gastos-por-categoria?moneda_base=${encodeURIComponent(estado.monedaGlobal)}`);
    if (res.ok) actualizarGraficaCategorias(await res.json());
}

export async function cargarDatos() {
    try {

        // 1. Resumen Consolidado del Tenant
        const resResumen = await apiFetch(`/api/resumen?moneda_base=${encodeURIComponent(estado.monedaGlobal)}`);
        const dataResumen = await resResumen.json();
        const setHTML = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };

        if (document.getElementById('lbl-dash-patrimonio')) {
            setHTML('lbl-dash-patrimonio', montoHTML(dataResumen.patrimonio_neto));
            setHTML('lbl-dash-apartados', montoHTML(dataResumen.apartados));
            setHTML('lbl-dash-inversiones', montoHTML(dataResumen.inversiones));
            // La moneda va una vez, en la etiqueta del bloque
            const etiquetaPatrimonio = document.querySelector('.networth-header small');
            if (etiquetaPatrimonio) etiquetaPatrimonio.textContent = `Patrimonio en ${estado.monedaGlobal} (metas e inversiones)`;

            // Tira de Distribución de Patrimonio (Metas vs Inversiones)
            const vAp = Math.max(0, Number(dataResumen.apartados) || 0);
            const vInv = Math.max(0, Number(dataResumen.inversiones) || 0);
            const totalPat = vAp + vInv;
            const segmentos = [
                ['alloc-bar-apartados', vAp, 'Metas/Apartados'],
                ['alloc-bar-inversiones', vInv, 'Inversiones'],
            ];
            segmentos.forEach(([id, valor, nombre]) => {
                const bar = document.getElementById(id);
                if (!bar) return;
                const pct = totalPat > 0 ? ((valor / totalPat) * 100).toFixed(1) : '0.0';
                bar.style.width = `${pct}%`;
                conTituloPrivado(bar, `${nombre}: ${pct}%`, `${nombre}: ${pct}% (${fmt(valor)})`);
            });
        }

        renderPeriodo(dataResumen.periodo);

        // Cinta de periodos: el periodo en curso y los siguientes, con su saldo al cierre
        const resCinta = await apiFetch('/api/recurrentes/periodos?n=4');
        const dataCinta = await leerJson(resCinta, 'No se pudo cargar la cinta de periodos.');
        renderCintaPeriodos(document.getElementById('cinta-periodos'), dataCinta.periodos);

        // La lista de movimientos se acota al periodo en curso, el mismo corte que las tarjetas del resumen
        const periodosResumen = dataResumen.periodos || [];
        const periodoActual = periodosResumen.find(p => p.en_curso) || periodosResumen[0];
        estado.cache.ventana = periodoActual ? { inicio: periodoActual.inicio, fin: periodoActual.fin } : null;
        renderTablaMovimientos();

        if (document.getElementById('lbl-apartados-total-acum')) {
            document.getElementById('lbl-apartados-total-acum').innerHTML = `${montoHTML(dataResumen.apartados)}`;
        }

        // 2. Historial por periodo de cobro: mes, quincena o semana, según el ingreso fijo
        const resHistorial = await apiFetch('/api/historial-periodos');
        const historial = await leerJson(resHistorial, 'No se pudo cargar el historial.');
        const por = historial.granularidad;   // «mes», «quincena» o «semana»
        document.getElementById('titulo-grafica-periodos').textContent = `Ingresos y gastos por ${por}`;
        document.getElementById('titulo-historial-periodos').textContent = `Historial por ${por}`;

        const contenedorHistorial = document.getElementById('lista-historial-periodos');
        if (contenedorHistorial) {
            contenedorHistorial.innerHTML = '';
            if (historial.periodos.length === 0) {
                contenedorHistorial.innerHTML = '<p class="monthly-vacio">Sin registros aún.</p>';
            } else {
                historial.periodos.forEach(p => {
                    const div = document.createElement('div');
                    div.className = 'monthly-card';
                    div.innerHTML = `
                        <div><strong>${escapeHTML(etiquetaPeriodo(p))}</strong>${p.en_curso ? ' <span class="texto-tenue texto-xs">en curso</span>' : ''}</div>
                        <div class="monthly-info">
                            <span class="monthly-ingreso${Number(p.ingresos) ? '' : ' es-cero'}">+${montoMxnHTML(p.ingresos)}</span>
                            <span class="monthly-gasto${Number(p.gastos) ? '' : ' es-cero'}">−${montoMxnHTML(p.gastos)}</span>
                        </div>
                    `;
                    contenedorHistorial.appendChild(div);
                });
            }
        }

        actualizarGrafica(historial.periodos.slice(0, 12).reverse().map(p => ({ ...p, ingresos: desdeMXN(p.ingresos), gastos: desdeMXN(p.gastos) })));

        // 3. Resto de datos en paralelo: movimientos, categorías, fijos y MSI, metas e inversiones
        await Promise.all([
            cargarMovimientos(),
            cargarCategorias(),
            cargarRecurrentes(),
            cargarGraficaCategorias(),
            cargarApartados(),
            cargarInversiones(),
            cargarPresupuesto(),
        ]);

    } catch (error) {
        if (error.esHttp) toast(error.message, 'error');
        console.error("Error al actualizar datos:", error);
    }
}


window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        cerrarModalEditarInversion();
        cerrarModalEditarTransaccion();
        cerrarModalCrearUsuario();
        cerrarModalEditarUsuario();
        cerrarModalCrearApartado();
        cerrarModalRecuperarPassword();
        cerrarModalLegal();
        cerrarModalTarjetaPresupuesto();
    }
});


// Cierre al hacer clic afuera de los menús emergentes
document.addEventListener('click', (e) => {
    const menu = document.getElementById('user-dropdown-menu');
    const trigger = document.getElementById('btn-user-menu-trigger');
    if (menu && !menu.classList.contains('hidden')) {
        if (!menu.contains(e.target) && (!trigger || !trigger.contains(e.target))) {
            cerrarUserDropdown();
        }
    }
});


// Cierre al presionar la tecla Escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        cerrarUserDropdown();
        cerrarSidebarMobile();
    }
});


// Navegación en el historial del navegador (Atrás / Adelante)
window.addEventListener('popstate', () => {
    const token = localStorage.getItem('jwt_token');
    const hash = window.location.hash.toLowerCase();
    const pathname = window.location.pathname.toLowerCase();

    if (hash === '#app' || pathname === '/app') {
        if (token && estado.usuarioActual) {
            establecerSesionActiva(estado.usuarioActual, token);
        } else {
            mostrarVistaLogin("Por favor inicia sesión para acceder al panel.");
        }
    } else if (hash === '#login' || pathname === '/login') {
        mostrarVistaLogin();
    } else {
        // Secciones de presentación / Landing Page (#capacidades, #plataforma, #como-funciona, etc.)
        mostrarVistaLanding();
        if (hash && hash !== '#' && hash !== '#inicio') {
            const target = document.querySelector(hash);
            if (target) {
                target.scrollIntoView({ behavior: 'smooth' });
            }
        }
    }
});


// =============================================================================
// 14. ARRANQUE DE LA APLICACIÓN
// =============================================================================
document.addEventListener('sesion-expirada', (e) => cerrarSesionLimpia(e.detail));

inicializarDiagnostico();
inicializarTabs();
inicializarFab();
inicializarMenuUsuario();
inicializarAccesibilidadModales();

inicializarEstadoSidebar();

inicializarBuscadorTickers();
inicializarCalculadoraRF();
inicializarPresupuesto();

verificarSesionInicial();

document.addEventListener('recargar', cargarDatos);
document.addEventListener('activar-presupuesto', cargarPresupuesto);
document.addEventListener('activar-recurrentes', () => cargarRecurrentes());
