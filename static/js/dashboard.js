/**
 * static/js/dashboard.js - Gráfica mensual del dashboard.
 */

import { colorVar, escapeHTML, estado, fmt } from './core.js';


export function actualizarGrafica(dataMensual) {
    const canvas = document.getElementById('graficaFinanzas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const esOscuro = document.documentElement.getAttribute('data-theme') === 'dark';
    const textColor = esOscuro ? '#FFFFFF' : '#0A0A0A';
    const gridColor = esOscuro ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.08)';
    
    const meses = dataMensual.map(m => m.mes);
    const ingresos = dataMensual.map(m => m.ingresos);
    const gastos = dataMensual.map(m => m.gastos);

    if (estado.graficas.barras) {
        estado.graficas.barras.data.labels = meses;
        estado.graficas.barras.data.datasets[0].data = ingresos;
        estado.graficas.barras.data.datasets[1].data = gastos;
        if (estado.graficas.barras.options && estado.graficas.barras.options.scales) {
            if (estado.graficas.barras.options.scales.x) {
                estado.graficas.barras.options.scales.x.ticks.color = textColor;
                estado.graficas.barras.options.scales.x.grid.color = gridColor;
            }
            if (estado.graficas.barras.options.scales.y) {
                estado.graficas.barras.options.scales.y.ticks.color = textColor;
                estado.graficas.barras.options.scales.y.grid.color = gridColor;
            }
        }
        if (estado.graficas.barras.options && estado.graficas.barras.options.plugins && estado.graficas.barras.options.plugins.legend) {
            estado.graficas.barras.options.plugins.legend.labels.color = textColor;
        }
        estado.graficas.barras.update();
    } else {
        estado.graficas.barras = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: meses,
                datasets: [
                    {
                        label: 'Ingresos',
                        data: ingresos,
                        backgroundColor: 'rgba(34, 197, 94, 0.75)',
                        borderColor: colorVar('--accent-color'),
                        borderWidth: 1,
                        borderRadius: 6
                    },
                    {
                        label: 'Gastos',
                        data: gastos,
                        backgroundColor: 'rgba(239, 68, 68, 0.75)',
                        borderColor: colorVar('--danger-color'),
                        borderWidth: 1,
                        borderRadius: 6
                    }
                ]
            },
            options: {
                responsive: true,
                scales: {
                    x: { ticks: { color: textColor }, grid: { color: gridColor } },
                    y: { beginAtZero: true, ticks: { color: textColor }, grid: { color: gridColor } }
                },
                plugins: {
                    legend: { labels: { color: textColor } }
                }
            }
        });
    }
}


// =============================================================================
// 5.3 GASTO POR CATEGORÍA (dona) Y PRÓXIMOS MOVIMIENTOS
// =============================================================================

const PALETA_CATEGORIAS = ['#22c55e', '#a3e635', '#06b6d4', '#3b82f6', '#8b5cf6', '#ec4899', '#f59e0b', '#64748b', '#10b981', '#14b8a6'];

export function actualizarGraficaCategorias(data) {
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

    const labels = cats.map(c => c.categoria);
    const valores = cats.map(c => c.total);
    const colores = labels.map((_, i) => PALETA_CATEGORIAS[i % PALETA_CATEGORIAS.length]);
    const esOscuro = document.documentElement.getAttribute('data-theme') === 'dark';
    const textColor = esOscuro ? '#FFFFFF' : '#0A0A0A';

    if (estado.graficas.categorias) {
        estado.graficas.categorias.data.labels = labels;
        estado.graficas.categorias.data.datasets[0].data = valores;
        estado.graficas.categorias.data.datasets[0].backgroundColor = colores;
        estado.graficas.categorias.data.datasets[0].borderColor = esOscuro ? '#0D0D0E' : '#ffffff';
        estado.graficas.categorias.options.plugins.legend.labels.color = textColor;
        estado.graficas.categorias.update();
        return;
    }
    estado.graficas.categorias = new Chart(canvas.getContext('2d'), {
        type: 'doughnut',
        data: {
            labels,
            datasets: [{
                data: valores,
                backgroundColor: colores,
                borderWidth: 1,
                borderColor: esOscuro ? '#0D0D0E' : '#ffffff'
            }]
        },
        options: {
            responsive: true,
            cutout: '62%',
            plugins: {
                legend: { position: 'right', labels: { color: textColor } },
                tooltip: { callbacks: { label: ctx => `${ctx.label}: ${fmt(ctx.parsed)}` } }
            }
        }
    });
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

