/**
 * static/js/main.js - Punto de entrada: compone los módulos, escucha eventos globales y arranca la app.
 */

import { cerrarModalCrearUsuario, cerrarModalEditarUsuario } from './admin.js';
import { cargarApartados, cerrarModalCrearApartado } from './apartados.js';
import { cerrarModalRecuperarPassword, cerrarSesionLimpia, establecerSesionActiva, mostrarVistaLanding, mostrarVistaLogin, verificarSesionInicial } from './auth.js';
import { apiFetch, escapeHTML, estado, fmt } from './core.js';
import { actualizarGrafica, actualizarGraficaCategorias, renderPeriodo, renderProximos } from './dashboard.js';
import { cargarInversiones, cerrarModalEditarInversion, inicializarBuscadorTickers } from './inversiones.js';
import { cargarRecurrentes } from './recurrentes.js?v=4e1a08f14d';
import { cargarCategorias, cerrarModalEditarTransaccion, renderTablaMovimientos } from './movimientos.js';
import { cerrarModalLegal, cerrarSidebarMobile, cerrarUserDropdown, inicializarAccesibilidadModales, inicializarEstadoSidebar, inicializarFab, inicializarTabs } from './ui.js';
import { inicializarCalculadoraRF } from './calculadora_rf.js';
import { cargarPresupuesto, cerrarModalTarjetaPresupuesto, cerrarModalSubdivisionPersonalizada, inicializarPresupuesto } from './presupuesto.js?v=4e1a08f14d';
import './respaldo.js'; // registra handlers de Excel y respaldo en window
import { activarAccionesDelegadas } from './acciones.js';

activarAccionesDelegadas();


async function cargarMovimientos() {
    const resTrans = await apiFetch('/api/transacciones');
    const dataTrans = await resTrans.json();
    estado.cache.transacciones = {};
    dataTrans.forEach(t => { estado.cache.transacciones[t.id] = t; });
    estado.cache.listaTransacciones = dataTrans;
    renderTablaMovimientos();
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
        const setTxt = (id, texto) => { const el = document.getElementById(id); if (el) el.textContent = texto; };

        if (document.getElementById('lbl-dash-patrimonio')) {
            setTxt('lbl-dash-patrimonio', `${fmt(dataResumen.patrimonio_neto)} ${estado.monedaGlobal}`);
            setTxt('lbl-dash-balance', `${fmt(dataResumen.balance)}`);
            setTxt('lbl-dash-apartados', `${fmt(dataResumen.apartados)}`);
            setTxt('lbl-dash-inversiones', `${fmt(dataResumen.inversiones)} ${estado.monedaGlobal}`);
            setTxt('lbl-dash-deuda', `${fmt(dataResumen.deuda_msi)}`);

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
                bar.title = `${nombre}: ${pct}% (${fmt(valor)})`;
            });
        }

        renderPeriodo(dataResumen.periodo);

        // Tarjetas del mes
        const mes = dataResumen.mes_actual || { ingresos: 0, gastos: 0, ahorro: 0 };
        setTxt('lbl-dash-mes-ingresos', fmt(mes.ingresos));
        setTxt('lbl-dash-mes-gastos', fmt(mes.gastos));
        setTxt('lbl-dash-mes-ahorro', fmt(mes.ahorro));
        const tasaAhorro = mes.ingresos > 0 ? Math.round((mes.ahorro / mes.ingresos) * 1000) / 10 : 0;
        setTxt('lbl-dash-tasa-ahorro', `(${tasaAhorro}% de ahorro)`);

        if (document.getElementById('lbl-apartados-total-acum')) {
            document.getElementById('lbl-apartados-total-acum').textContent = `${fmt(dataResumen.apartados)}`;
        }

        if (document.getElementById('lbl-balance')) {
            document.getElementById('lbl-balance').textContent = `${fmt(dataResumen.balance)}`;
            document.getElementById('lbl-ingresos').textContent = `${fmt(dataResumen.ingresos)}`;
            document.getElementById('lbl-gastos').textContent = `${fmt(dataResumen.gastos)}`;
        }

        // 2. Resumen Mensual Histórico
        const resMensual = await apiFetch('/api/resumen-mensual');
        const dataMensual = await resMensual.json();
        
        const contenedorMensual = document.getElementById('lista-resumen-mensual');
        if (contenedorMensual) {
            contenedorMensual.innerHTML = '';
            if (dataMensual.length === 0) {
                contenedorMensual.innerHTML = '<p style="text-align: center; color: var(--text-muted);">Sin registros mensuales aún.</p>';
            } else {
                dataMensual.forEach(m => {
                    const div = document.createElement('div');
                    div.className = 'monthly-card';
                    const [anio, numMes] = String(m.mes).split('-').map(Number);
                    const nombreMes = anio && numMes
                        ? new Intl.DateTimeFormat('es-MX', { month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(anio, numMes - 1, 1))).replace('.', '')
                        : m.mes;
                    div.innerHTML = `
                        <div><strong>${escapeHTML(nombreMes)}</strong></div>
                        <div class="monthly-info">
                            <span style="color: var(--success-text);">+${fmt(m.ingresos)}</span>
                            <span style="color: var(--danger-text);">-${fmt(m.gastos)}</span>
                        </div>
                    `;
                    contenedorMensual.appendChild(div);
                });
            }
        }

        actualizarGrafica(dataMensual.slice(0, 12).reverse());

        // 3. Resto de datos en paralelo: movimientos, categorías, fijos y MSI, metas e inversiones
        await Promise.all([
            cargarMovimientos(),
            cargarCategorias(),
            cargarRecurrentes().then(() => renderProximos()),
            cargarGraficaCategorias(),
            cargarApartados(),
            cargarInversiones(),
            cargarPresupuesto(),
        ]);

    } catch (error) {
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
        cerrarModalSubdivisionPersonalizada();
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

inicializarTabs();
inicializarFab();
inicializarAccesibilidadModales();

inicializarEstadoSidebar();

inicializarBuscadorTickers();
inicializarCalculadoraRF();
inicializarPresupuesto();

verificarSesionInicial();

document.addEventListener('recargar', cargarDatos);
