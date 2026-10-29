/**
 * static/js/ui.js - Interfaz: pestañas, sidebar, tema, privacidad y modal legal.
 */

import { cargarDatosAdmin } from './admin.js';
import { estado } from './core.js';
import { cargarRecurrentes } from './recurrentes.js';
import { renderTablaMovimientos } from './movimientos.js';
import { cargarInversiones } from './inversiones.js';
import { repintarGraficas } from './dashboard.js';


// =============================================================================
// 4. GESTIÓN DEL TEMA VISUAL (MODO OSCURO / CLARO)
// =============================================================================

export function actualizarIconosModoOscuro(esOscuro) {
    const iconModo = document.getElementById('icon-modo-oscuro');
    const lblModo = document.getElementById('lbl-modo-oscuro');
    const btn = document.getElementById('btn-modo');
    if (iconModo) {
        iconModo.innerHTML = esOscuro
            ? `<svg class="ui-svg-icon" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`
            : `<svg class="ui-svg-icon" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;
    }
    if (lblModo) lblModo.textContent = esOscuro ? 'Modo Claro' : 'Modo Oscuro';
    if (btn) btn.setAttribute('title', esOscuro ? 'Cambiar a Modo Claro' : 'Cambiar a Modo Oscuro');
}


export function toggleModoOscuro() {
    const esOscuro = document.documentElement.getAttribute('data-theme') === 'dark';
    const nuevoTemaOscuro = !esOscuro;
    if (nuevoTemaOscuro) {
        document.documentElement.setAttribute('data-theme', 'dark');
        localStorage.setItem('theme', 'dark');
        actualizarIconosModoOscuro(true);
    } else {
        document.documentElement.setAttribute('data-theme', 'light');
        localStorage.setItem('theme', 'light');
        actualizarIconosModoOscuro(false);
    }
    // Las gráficas leen sus colores de tokens.css: basta con volver a dibujarlas.
    repintarGraficas();
    if (estado.graficas.dona) cargarInversiones();
}


// Inicialización de tema guardado (por defecto oscuro)
export const temaGuardado = localStorage.getItem('theme');

const prefiereOscuro = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)').matches : true;

export const inicialOscuro = temaGuardado ? temaGuardado === 'dark' : true;

if (inicialOscuro) {
    document.documentElement.setAttribute('data-theme', 'dark');
    actualizarIconosModoOscuro(true);
} else {
    document.documentElement.setAttribute('data-theme', 'light');
    actualizarIconosModoOscuro(false);
}

if (window.Chart) {
    // Los colores de cada gráfica salen de tokens.css (ver dashboard.js); aquí solo la fuente base.
    Chart.defaults.font.family = getComputedStyle(document.documentElement).getPropertyValue('--font-ui').trim() || 'sans-serif';
}


// =============================================================================
// 4.1 MODO PRIVACIDAD / DISCRECIÓN (ESTILO COPILOT & MONARCH MONEY)
// =============================================================================

export function toggleModoPrivacidad() {
    const activo = document.body.classList.toggle('privacy-mode');
    localStorage.setItem('finanzas_cloud_privacy_mode', activo ? '1' : '0');
    localStorage.removeItem('finora_privacy_mode');
    actualizarIconoPrivacidad(activo);
}


export function actualizarIconoPrivacidad(activo) {
    const icon = document.getElementById('icon-privacy');
    const btn = document.getElementById('btn-toggle-privacidad');
    if (icon) {
        if (activo) {
            icon.innerHTML = `<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>`;
            if (btn) {
                btn.classList.add('active');
                btn.setAttribute('title', 'Modo Discreción ACTIVO: Cifras ocultas (pasa el cursor para ver). Haz clic para revelar todo.');
            }
        } else {
            icon.innerHTML = `<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>`;
            if (btn) {
                btn.classList.remove('active');
                btn.setAttribute('title', 'Modo Discreción: Ocultar cifras sensibles en pantalla');
            }
        }
    }
}


export const savedPrivacyMode = localStorage.getItem('finanzas_cloud_privacy_mode') ?? localStorage.getItem('finora_privacy_mode');

if (savedPrivacyMode === '1') {
    document.body.classList.add('privacy-mode');
    setTimeout(() => actualizarIconoPrivacidad(true), 60);
}


// =============================================================================
// 10. SISTEMA DE PESTAÑAS (TABS)
// =============================================================================

export function inicializarTabs() {
    const tabBtns = document.querySelectorAll('.tab-btn');
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const targetTab = btn.getAttribute('data-tab');
            cambiarPestana(targetTab);
        });
    });

    const pestanaGuardada = localStorage.getItem('active_tab') || 'tab-dashboard';
    cambiarPestana(pestanaGuardada);
}


export function cambiarPestana(tabId) {
    if (tabId === 'tab-admin' && (!estado.usuarioActual || estado.usuarioActual.role !== 'admin')) {
        tabId = 'tab-dashboard';
    }

    const targetContent = document.getElementById(tabId);
    if (!targetContent) return;

    // Redirigir suavemente de tab-recurrentes a tab-movimientos con subtab de fijos
    if (tabId === 'tab-recurrentes') {
        tabId = 'tab-movimientos';
        setTimeout(() => cambiarSubtabMovimientos('fijos'), 10);
    }

    document.querySelectorAll('.tab-btn').forEach(b => {
        b.classList.toggle('active', b.getAttribute('data-tab') === tabId);
    });

    document.querySelectorAll('.tab-content').forEach(c => {
        c.classList.toggle('active', c.id === tabId);
    });

    // Actualizar título dinámico en la barra superior (topbar)
    const titulosMap = {
        'tab-dashboard': 'Resumen',
        'tab-movimientos': 'Movimientos y fijos',
        'tab-apartados': 'Metas de ahorro',
        'tab-presupuesto': 'Presupuesto',
        'tab-inversiones': 'Inversiones',
        'tab-calculadora-rf': 'Calculadora de renta fija',
        'tab-admin': 'Administración'
    };
    const lblTitulo = document.getElementById('topbar-section-title');
    if (lblTitulo && titulosMap[tabId]) {
        lblTitulo.textContent = titulosMap[tabId];
    }

    localStorage.setItem('active_tab', tabId);

    // En pantallas móviles, cerrar la barra lateral al seleccionar pestaña
    if (window.innerWidth <= 768) {
        cerrarSidebarMobile();
    }

    // Cargar datos correspondientes al cambiar de pestaña
    if (tabId === 'tab-admin') {
        cargarDatosAdmin();
    }
    if (tabId === 'tab-movimientos') {
        cargarRecurrentes();
        renderTablaMovimientos();
    }
    if (tabId === 'tab-presupuesto') {
        // El módulo de presupuesto lo carga main.js: así existe una sola instancia del módulo
        document.dispatchEvent(new Event('activar-presupuesto'));
    }
    if (tabId === 'tab-inversiones') {
        cargarInversiones();
    }

    setTimeout(() => {
        if (estado.graficas.barras) estado.graficas.barras.resize();
        if (estado.graficas.dona) estado.graficas.dona.resize();
    }, 60);
}


export function cambiarSubtabMovimientos(subtab) {
    const tabMov = document.getElementById('tab-movimientos');
    if (tabMov && !tabMov.classList.contains('active')) {
        cambiarPestana('tab-movimientos');
    }
    const btnHistorial = document.getElementById('btn-subtab-historial');
    const btnFijos = document.getElementById('btn-subtab-fijos');
    const contentHistorial = document.getElementById('subtab-content-historial');
    const contentFijos = document.getElementById('subtab-content-fijos');

    if (!btnHistorial || !btnFijos || !contentHistorial || !contentFijos) return;

    if (subtab === 'fijos') {
        btnHistorial.classList.remove('active');
        btnFijos.classList.add('active');
        contentHistorial.classList.remove('active');
        contentFijos.classList.add('active');
        cargarRecurrentes();
    } else {
        btnFijos.classList.remove('active');
        btnHistorial.classList.add('active');
        contentFijos.classList.remove('active');
        contentHistorial.classList.add('active');
        renderTablaMovimientos();
    }
}
window.cambiarSubtabMovimientos = cambiarSubtabMovimientos;



// =============================================================================
// 12. GESTIÓN DEL PANEL LATERAL (SIDEBAR) Y MENÚ DE USUARIO
// =============================================================================

export function toggleSidebar() {
    const sidebar = document.getElementById('app-sidebar');
    const backdrop = document.getElementById('sidebar-backdrop');
    const appLayout = document.getElementById('vista-app');
    const btnCollapse = document.getElementById('btn-sidebar-collapse');
    const collapseIcon = btnCollapse ? btnCollapse.querySelector('.collapse-icon') : null;
    
    if (window.innerWidth <= 768) {
        // En móviles se comporta como Drawer Off-Canvas
        if (!sidebar) return;
        const isOpen = sidebar.classList.toggle('sidebar-open');
        if (backdrop) backdrop.classList.toggle('active', isOpen);
    } else {
        // En escritorio se colapsa / expande (ancho completo vs solo iconos)
        if (!sidebar || !appLayout) return;
        const isCollapsed = sidebar.classList.toggle('sidebar-collapsed');
        appLayout.classList.toggle('sidebar-collapsed', isCollapsed);
        if (collapseIcon) {
            collapseIcon.innerHTML = isCollapsed
                ? `<svg class="ui-svg-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>`
                : `<svg class="ui-svg-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>`;
        }
        localStorage.setItem('sidebar_collapsed', isCollapsed ? '1' : '0');
        // Redimensionar gráficas tras la transición del layout
        setTimeout(() => {
            if (estado.graficas.barras) estado.graficas.barras.resize();
            if (estado.graficas.dona) estado.graficas.dona.resize();
        }, 280);
    }
}


export function cerrarSidebarMobile() {
    const sidebar = document.getElementById('app-sidebar');
    const backdrop = document.getElementById('sidebar-backdrop');
    if (sidebar) sidebar.classList.remove('sidebar-open');
    if (backdrop) backdrop.classList.remove('active');
}


export function inicializarEstadoSidebar() {
    if (window.innerWidth > 768) {
        const collapsedSaved = localStorage.getItem('sidebar_collapsed') === '1';
        if (collapsedSaved) {
            const sidebar = document.getElementById('app-sidebar');
            const appLayout = document.getElementById('vista-app');
            const btnCollapse = document.getElementById('btn-sidebar-collapse');
            const collapseIcon = btnCollapse ? btnCollapse.querySelector('.collapse-icon') : null;
            if (sidebar) sidebar.classList.add('sidebar-collapsed');
            if (appLayout) appLayout.classList.add('sidebar-collapsed');
            if (collapseIcon) {
                collapseIcon.innerHTML = `<svg class="ui-svg-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>`;
            }
        }
    }
}


export function toggleUserDropdown(event) {
    if (event) event.stopPropagation();
    const menu = document.getElementById('user-dropdown-menu');
    const trigger = document.getElementById('btn-user-menu-trigger');
    if (!menu) return;
    const isHidden = menu.classList.contains('hidden') || !menu.classList.contains('show');
    if (isHidden) {
        menu.classList.remove('hidden');
        setTimeout(() => menu.classList.add('show'), 10);
        if (trigger) trigger.setAttribute('aria-expanded', 'true');
    } else {
        cerrarUserDropdown();
    }
}


export function cerrarUserDropdown() {
    const menu = document.getElementById('user-dropdown-menu');
    const trigger = document.getElementById('btn-user-menu-trigger');
    if (menu) {
        menu.classList.remove('show');
        setTimeout(() => {
            menu.classList.add('hidden');
        }, 150);
    }
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
}


// Funciones del Marco Legal, Términos y Privacidad
export function abrirModalLegal(seccion = 'terminos') {
    const modal = document.getElementById('modal-legal');
    if (!modal) return;
    modal.classList.remove('hidden');
    cambiarTabLegal(seccion);
}


export function cerrarModalLegal() {
    const modal = document.getElementById('modal-legal');
    if (modal) modal.classList.add('hidden');
}


export function cambiarTabLegal(tabId) {
    document.querySelectorAll('.btn-legal-tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.legal-content-panel').forEach(p => p.classList.add('hidden'));

    const btn = document.getElementById(`btn-tab-legal-${tabId}`);
    const panel = document.getElementById(`panel-legal-${tabId}`);
    if (btn) btn.classList.add('active');
    if (panel) panel.classList.remove('hidden');

    const scrollContainer = document.getElementById('legal-content-scroll');
    if (scrollContainer) scrollContainer.scrollTo({ top: 0, behavior: 'smooth' });
}

Object.assign(window, {
    abrirModalLegal,
    cambiarPestana,
    cambiarTabLegal,
    cerrarModalLegal,
    cerrarSidebarMobile,
    cerrarUserDropdown,
    toggleModoOscuro,
    toggleModoPrivacidad,
    toggleSidebar,
    toggleUserDropdown
});


// =============================================================================
// Botón flotante "+" (móvil): abre Movimientos y enfoca el monto
// =============================================================================

export function inicializarFab() {
    const fab = document.getElementById('btn-fab');
    if (!fab) return;
    fab.addEventListener('click', () => {
        cambiarPestana('tab-movimientos');
        const monto = document.getElementById('monto');
        if (monto) setTimeout(() => monto.focus(), 60);
    });
}



// =============================================================================
// ACCESIBILIDAD DE MODALES: foco inicial, Tab atrapado dentro y foco de regreso al cerrar
// =============================================================================

const SELECTOR_ENFOCABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function enfocablesVisibles(contenedor) {
    return [...contenedor.querySelectorAll(SELECTOR_ENFOCABLE)].filter(el => el.offsetParent !== null);
}

function modalAbiertoSuperior() {
    const abiertos = [...document.querySelectorAll('.modal-overlay[role="dialog"]:not(.hidden)')];
    return abiertos[abiertos.length - 1] || null;
}

export function inicializarAccesibilidadModales() {
    const origenFoco = new WeakMap();
    const observador = new MutationObserver(cambios => {
        for (const { target } of cambios) {
            const abierto = !target.classList.contains('hidden');
            if (abierto && !origenFoco.has(target)) {
                origenFoco.set(target, document.activeElement);
                // Tras el render del modal: si su propio código no enfocó nada dentro, enfocar el primer control.
                setTimeout(() => {
                    if (!target.contains(document.activeElement)) {
                        const primero = enfocablesVisibles(target).find(el => !el.classList.contains('modal-close')) || enfocablesVisibles(target)[0];
                        if (primero) primero.focus();
                    }
                }, 100);
            } else if (!abierto && origenFoco.has(target)) {
                const previo = origenFoco.get(target);
                origenFoco.delete(target);
                if (previo && document.contains(previo) && typeof previo.focus === 'function') previo.focus();
            }
        }
    });
    document.querySelectorAll('.modal-overlay[role="dialog"]').forEach(m => observador.observe(m, { attributes: true, attributeFilter: ['class'] }));

    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Tab') return;
        const modal = modalAbiertoSuperior();
        if (!modal) return;
        const enfocables = enfocablesVisibles(modal);
        if (enfocables.length === 0) return;
        const primero = enfocables[0];
        const ultimo = enfocables[enfocables.length - 1];
        if (!modal.contains(document.activeElement)) {
            e.preventDefault();
            primero.focus();
        } else if (e.shiftKey && document.activeElement === primero) {
            e.preventDefault();
            ultimo.focus();
        } else if (!e.shiftKey && document.activeElement === ultimo) {
            e.preventDefault();
            primero.focus();
        }
    });
}
