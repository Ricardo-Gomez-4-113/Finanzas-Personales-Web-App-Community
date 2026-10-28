/**
 * static/js/dashboard.js - Gráfica mensual del dashboard.
 */

import { colorVar, escapeHTML, estado, fmt } from './core.js';


// Colores y tipografía de las gráficas salen de tokens.css para seguir el tema activo.
function estiloGraficas() {
    return {
        texto: colorVar('--chart-text'),
        rejilla: colorVar('--chart-grid'),
        ingreso: colorVar('--chart-ingreso'),
        gasto: colorVar('--chart-gasto'),
        fondo: colorVar('--panel-bg'),
        categorias: [1, 2, 3, 4, 5, 6, 7, 8].map(i => colorVar(`--cat-${i}`)),
        fuente: { family: getComputedStyle(document.body).fontFamily, size: 12 },
    };
}

// Últimos datos dibujados: permiten repintar al cambiar de tema sin volver a pedirlos.
let ultimoMensual = null;
let ultimasCategorias = null;

export function repintarGraficas() {
    ['barras', 'categorias'].forEach(k => {
        if (estado.graficas[k]) { estado.graficas[k].destroy(); estado.graficas[k] = null; }
    });
    if (ultimoMensual) actualizarGrafica(ultimoMensual);
    if (ultimasCategorias) actualizarGraficaCategorias(ultimasCategorias);
}

export function actualizarGrafica(dataMensual) {
    ultimoMensual = dataMensual;
    const canvas = document.getElementById('graficaFinanzas');
    if (!canvas) return;
    const e = estiloGraficas();
    const meses = dataMensual.map(m => m.mes);
    const ingresos = dataMensual.map(m => m.ingresos);
    const gastos = dataMensual.map(m => m.gastos);

    if (estado.graficas.barras) {
        estado.graficas.barras.data.labels = meses;
        estado.graficas.barras.data.datasets[0].data = ingresos;
        estado.graficas.barras.data.datasets[1].data = gastos;
        estado.graficas.barras.update();
        return;
    }
    const serie = (label, data, color) => ({
        label, data,
        backgroundColor: color + 'D9',
        hoverBackgroundColor: color,
        borderWidth: 0,
        borderRadius: 4,
        borderSkipped: 'bottom',
        maxBarThickness: 22,
        categoryPercentage: 0.62,
        barPercentage: 0.82,
    });
    estado.graficas.barras = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: { labels: meses, datasets: [serie('Ingresos', ingresos, e.ingreso), serie('Gastos', gastos, e.gasto)] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { ticks: { color: e.texto, font: e.fuente }, grid: { display: false }, border: { color: e.rejilla } },
                y: {
                    beginAtZero: true,
                    ticks: { color: e.texto, font: e.fuente, maxTicksLimit: 5, callback: v => fmt(v).replace(/\.00$/, '') },
                    grid: { color: e.rejilla }, border: { display: false },
                },
            },
            plugins: {
                legend: { align: 'end', labels: { color: e.texto, font: e.fuente, boxWidth: 10, boxHeight: 10, useBorderRadius: true, borderRadius: 2 } },
                tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${fmt(ctx.parsed.y)}` } },
            },
        },
    });
}


// =============================================================================
// 5.3 GASTO POR CATEGORÍA (dona) Y PRÓXIMOS MOVIMIENTOS
// =============================================================================

export function actualizarGraficaCategorias(data) {
    ultimasCategorias = data;
    const canvas = document.getElementById('graficaCategorias');
    const vacio = document.getElementById('categorias-sin-datos');
    if (!canvas) return;
    const cats = (data && data.categorias) || [];
    if (cats.length === 0) {
        if (vacio) vacio.classList.remove('hidden');
        canvas.classList.add('hidden');
        if (estado.graficas.categorias) {
            estado.graficas.categorias.destroy();
            estado.graficas.categorias = null;
        }
        return;
    }
    if (vacio) vacio.classList.add('hidden');
    canvas.classList.remove('hidden');

    const e = estiloGraficas();
    const labels = cats.map(c => c.categoria);
    const valores = cats.map(c => c.total);
    const colores = labels.map((_, i) => e.categorias[i % e.categorias.length]);

    if (estado.graficas.categorias) {
        estado.graficas.categorias.data.labels = labels;
        estado.graficas.categorias.data.datasets[0].data = valores;
        estado.graficas.categorias.data.datasets[0].backgroundColor = colores;
        estado.graficas.categorias.update();
        return;
    }
    estado.graficas.categorias = new Chart(canvas.getContext('2d'), {
        type: 'doughnut',
        data: { labels, datasets: [{ data: valores, backgroundColor: colores, borderWidth: 2, borderColor: e.fondo, hoverOffset: 4 }] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '68%',
            plugins: {
                legend: { position: 'right', labels: { color: e.texto, font: e.fuente, boxWidth: 10, boxHeight: 10, padding: 12 } },
                tooltip: { callbacks: { label: ctx => `${ctx.label}: ${fmt(ctx.parsed)}` } },
            },
        },
    });
}


// =============================================================================
// 5.4 PERIODO DE COBRO EN CURSO (línea de tiempo del Resumen)
// =============================================================================

const fechaCorta = iso => new Intl.DateTimeFormat('es-MX', { day: 'numeric', month: 'short', timeZone: 'UTC' })
    .format(new Date(`${iso}T00:00:00Z`)).replace('.', '');
const fechaLarga = iso => new Intl.DateTimeFormat('es-MX', { day: 'numeric', month: 'long', timeZone: 'UTC' })
    .format(new Date(`${iso}T00:00:00Z`));
const diasEntre = (a, b) => Math.round((new Date(`${b}T00:00:00Z`) - new Date(`${a}T00:00:00Z`)) / 86400000);

export function renderPeriodo(p) {
    const raiz = document.getElementById('periodo-hero');
    if (!raiz || !p) return;
    const set = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };

    set('periodo-titulo', p.cobro ? `Libre hasta tu cobro del ${fechaLarga(p.fin)}` : `Libre hasta el ${fechaLarga(p.fin)}`);
    set('periodo-libre', fmt(p.libre));
    set('periodo-disponible', fmt(p.disponible));
    set('periodo-comprometido', fmt(p.comprometido));
    set('periodo-dias', p.dias_restantes === 0 ? 'Hoy' : p.dias_restantes === 1 ? '1 día' : `${p.dias_restantes} días`);
    set('periodo-inicio', fechaCorta(p.inicio));
    set('periodo-fin', fechaCorta(p.fin));
    set('periodo-cobro', p.cobro ? p.cobro.descripcion : 'Fin de mes');
    raiz.classList.toggle('periodo-en-riesgo', p.libre < 0);

    const pos = iso => Math.min(100, Math.max(0, (diasEntre(p.inicio, iso) / p.dias_totales) * 100));
    const pista = document.getElementById('periodo-pista');
    if (pista) {
        pista.style.setProperty('--avance', `${pos(p.hoy)}%`);
        pista.querySelectorAll('.periodo-marca').forEach(n => n.remove());
        // Agrupa cargos del mismo día en una sola marca.
        const porDia = {};
        p.eventos.forEach(ev => { (porDia[ev.fecha] = porDia[ev.fecha] || []).push(ev); });
        Object.entries(porDia).forEach(([fecha, evs]) => {
            const m = document.createElement('span');
            m.className = 'periodo-marca';
            m.style.left = `${pos(fecha)}%`;
            m.title = `${fechaCorta(fecha)}: ` + evs.map(ev => `${ev.descripcion} ${fmt(ev.monto)}`).join(', ');
            pista.appendChild(m);
        });
    }

    const lista = document.getElementById('periodo-eventos');
    if (lista) {
        if (p.eventos.length === 0) {
            lista.innerHTML = `<li class="periodo-vacio">No hay cargos fijos antes de ${p.cobro ? 'tu próximo cobro' : 'fin de mes'}.</li>`;
        } else {
            lista.innerHTML = p.eventos.map(ev => `<li>
                <span class="periodo-ev-fecha">${escapeHTML(fechaCorta(ev.fecha))}</span>
                <span class="periodo-ev-desc">${escapeHTML(ev.descripcion)}${ev.es_msi ? ' <span class="badge-tag badge-msi">MSI</span>' : ''}</span>
                <span class="periodo-ev-monto">−${fmt(ev.monto)}</span>
            </li>`).join('');
        }
    }
}


export function renderProximos() {
    const cont = document.getElementById('lista-proximos');
    if (!cont) return;
    const activos = (estado.cache.recurrentes || [])
        .filter(r => r.activo && !(r.es_msi && r.cuotas_generadas >= r.total_cuotas))
        .sort((a, b) => String(a.proxima_fecha).localeCompare(String(b.proxima_fecha)))
        .slice(0, 5);
    if (activos.length === 0) {
        cont.innerHTML = '<p class="empty-state">Sin pagos programados. Agrega tu nómina o tus gastos fijos en Fijos y MSI.</p>';
        return;
    }
    cont.innerHTML = activos.map(r => {
        const signo = r.tipo === 'ingreso' ? '+' : '−';
        const clase = r.tipo === 'ingreso' ? 'text-ingreso' : 'text-gasto';
        const etiqueta = r.es_msi ? `MSI ${r.cuotas_generadas + 1}/${r.total_cuotas}` : (r.frecuencia.charAt(0).toUpperCase() + r.frecuencia.slice(1));
        return `<div class="rec-item proximo-item">
            <div class="rec-item-head">
                <span><strong>${escapeHTML(r.descripcion)}</strong> <span class="badge-tag ${r.es_msi ? 'badge-msi' : 'badge-fijo'}">${escapeHTML(etiqueta)}</span></span>
                <span class="${clase}">${signo}${fmt(r.monto)}</span>
            </div>
            <div class="rec-item-meta"><span>Próximo: ${escapeHTML(r.proxima_fecha)}</span></div>
        </div>`;
    }).join('');
}

