/**
 * static/js/inversiones.js - Posiciones bursátiles, dona de asignación, buscador y sincronización.
 */

import { actualizarIndicadorTasa, apiFetch, colorVar, escapeHTML, estado, fmt, formatErrorDetail, recargar, toast } from './core.js';


export async function cargarInversiones() {
    try {
        const res = await apiFetch(`/api/inversiones?moneda_base=${encodeURIComponent(estado.monedaGlobal)}`);
        if (!res.ok) return;
        const data = await res.json();
        const { resumen, posiciones, tasa_usd_mxn } = data;

        if (tasa_usd_mxn) {
            estado.tasasCambio.MXN = tasa_usd_mxn;
            actualizarIndicadorTasa();
        }


        // Tarjetas de Resumen
        document.getElementById('lbl-inv-invertido').textContent = `${fmt(resumen.total_invertido)} ${estado.monedaGlobal}`;
        document.getElementById('lbl-inv-valor').textContent = `${fmt(resumen.total_valor_actual)} ${estado.monedaGlobal}`;
        
        const lblPnl = document.getElementById('lbl-inv-pnl');
        const signo = resumen.total_pnl >= 0 ? '+' : '';
        const signoMonto = resumen.total_pnl < 0 ? '-' : (resumen.total_pnl > 0 ? '+' : '');

        if (lblPnl) {
            lblPnl.innerHTML = `${signoMonto}${fmt(Math.abs(resumen.total_pnl))} ${estado.monedaGlobal} <span id="lbl-inv-pnl-pct" class="pnl-badge">(${signo}${resumen.total_pnl_porcentaje.toFixed(2)}%)</span>`;
            lblPnl.className = resumen.total_pnl > 0 ? 'pnl-positivo' : (resumen.total_pnl < 0 ? 'pnl-negativo' : 'pnl-neutro');
        }

        const lblDashPnl = document.getElementById('lbl-dash-pnl');
        if (lblDashPnl) {
            lblDashPnl.innerHTML = `${signoMonto}${fmt(Math.abs(resumen.total_pnl))} ${estado.monedaGlobal} <small style="font-size: 0.85rem;">(${signo}${resumen.total_pnl_porcentaje.toFixed(2)}%)</small>`;
            lblDashPnl.className = resumen.total_pnl > 0 ? 'pnl-positivo' : (resumen.total_pnl < 0 ? 'pnl-negativo' : 'pnl-neutro');
        }

        // Tabla de Inversiones
        const tbody = document.getElementById('tabla-inversiones');
        if (tbody) {
            tbody.innerHTML = '';
            if (posiciones.length === 0) {
                tbody.innerHTML = '<tr><td colspan="9" class="empty-state">Sin posiciones registradas. ¡Añade tu primer ETF o acción! <br><button type="button" class="btn-primary btn-sm" data-on-click="[[&quot;enfocarTickerInversion&quot;]]">Registrar posición</button></td></tr>';
            } else {
                posiciones.forEach(pos => {
                    const tr = document.createElement('tr');
                    const signoPos = pos.pnl >= 0 ? '+' : '';
                    const clasePnl = pos.pnl > 0 ? 'pnl-positivo' : (pos.pnl < 0 ? 'pnl-negativo' : 'pnl-neutro');
                    const signoPosMonto = pos.pnl < 0 ? '-' : (pos.pnl > 0 ? '+' : '');

                    const badgeMonedaColor = pos.moneda === 'USD' 
                        ? 'background: rgba(46, 204, 113, 0.15); color: var(--success-color);' 
                        : 'background: rgba(52, 152, 219, 0.15); color: var(--accent-color);';

                    const subOrig = (pos.moneda !== estado.monedaGlobal)
                        ? `<br><small style="color: var(--text-muted); font-size: 0.75rem;">(${fmt(pos.valor_actual_orig)} ${pos.moneda})</small>`
                        : '';

                    estado.cache.inversiones[pos.id] = pos;
                    tr.innerHTML = `
                        <td><strong>${escapeHTML(pos.ticker)}</strong></td>
                        <td><span class="badge-peso" style="${badgeMonedaColor}">${escapeHTML(pos.moneda)}</span></td>
                        <td>${escapeHTML(String(pos.cantidad))}</td>
                        <td>${fmt(pos.precio_promedio)} <small style="color: var(--text-muted);">${escapeHTML(pos.moneda)}</small></td>
                        <td>${fmt(pos.precio_actual)} <small style="color: var(--text-muted);">${escapeHTML(pos.moneda)}</small></td>
                        <td><strong>${fmt(pos.valor_actual)}</strong> ${subOrig}</td>
                        <td class="${clasePnl}">${signoPosMonto}${fmt(Math.abs(pos.pnl))} (${signoPos}${pos.pnl_porcentaje.toFixed(2)}%)</td>
                        <td><span class="badge-peso">${pos.peso_porcentaje.toFixed(1)}%</span></td>
                        <td class="col-acciones">
                            <button type="button" class="btn-accion btn-editar" data-on-click="${escapeHTML(JSON.stringify([["abrirModalEditarInversionPorId", pos.id]]))}" title="Editar posición"><svg class="action-svg-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg></button>
                            <button type="button" class="btn-accion btn-eliminar" data-on-click="${escapeHTML(JSON.stringify([["eliminarInversion", pos.id]]))}" title="Eliminar posición"><svg class="action-svg-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
                        </td>
                    `;
                    tbody.appendChild(tr);
                });
            }
        }

        actualizarGraficaDona(posiciones);

    } catch (error) {
        console.error("Error al cargar inversiones:", error);
    }
}


export const pluginDonaPorcentajes = {
    id: 'doughnutPercentageLabels',
    afterDraw(chart) {
        const { ctx, data } = chart;
        const meta = chart.getDatasetMeta(0);
        if (!meta || !meta.data || meta.data.length === 0) return;

        const total = data.datasets[0].data.reduce((acc, val, idx) => {
            const isVisible = chart.getDataVisibility ? chart.getDataVisibility(idx) : true;
            return acc + (isVisible ? Number(val || 0) : 0);
        }, 0);
        if (total <= 0) return;

        ctx.save();
        meta.data.forEach((element, index) => {
            if (chart.getDataVisibility && !chart.getDataVisibility(index)) return;

            const val = Number(data.datasets[0].data[index] || 0);
            const pct = (val / total) * 100;
            // Solo dibujar el porcentaje en rebanadas >= 10%; las pequeñas se leen en la leyenda
            if (pct < 10) return;

            let posX, posY;
            if (typeof element.tooltipPosition === 'function') {
                const pos = element.tooltipPosition();
                posX = pos.x;
                posY = pos.y;
            } else {
                const angle = (element.startAngle + element.endAngle) / 2;
                const r = (element.innerRadius + element.outerRadius) / 2;
                posX = element.x + Math.cos(angle) * r;
                posY = element.y + Math.sin(angle) * r;
            }

            const pctText = `${pct.toFixed(1)}%`;
            ctx.font = `600 12px ${getComputedStyle(document.body).fontFamily}`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';

            // Borde oscuro grueso para alto contraste sobre cualquier color
            ctx.lineWidth = 2.5;
            ctx.strokeStyle = 'rgba(15, 19, 24, 0.8)';
            ctx.strokeText(pctText, posX, posY);

            // Relleno blanco brillante
            ctx.fillStyle = colorVar('--chart-label');
            ctx.fillText(pctText, posX, posY);
        });
        ctx.restore();
    }
};


export function actualizarGraficaDona(posiciones) {
    const canvas = document.getElementById('graficaDonaAllocation');
    if (!canvas) return;

    const noDataMsg = document.getElementById('dona-sin-datos');
    const esOscuro = document.documentElement.getAttribute('data-theme') === 'dark';
    const textColor = colorVar('--chart-text');

    if (!posiciones || posiciones.length === 0) {
        if (noDataMsg) noDataMsg.classList.remove('hidden');
        canvas.classList.add('hidden');
        if (estado.graficas.dona) {
            estado.graficas.dona.destroy();
            estado.graficas.dona = null;
        }
        return;
    }

    if (noDataMsg) noDataMsg.classList.add('hidden');
    canvas.classList.remove('hidden');

    const paleta = [1, 2, 3, 4, 5, 6, 7, 8].map(i => colorVar(`--cat-${i}`));

    const labels = posiciones.map(p => p.ticker);
    const dataValores = posiciones.map(p => p.valor_actual);
    const colores = labels.map((_, i) => paleta[i % paleta.length]);

    // En pantallas de escritorio, si hay hasta 8 posiciones, ubicar leyenda a la derecha
    const isDesktop = window.innerWidth > 900;
    const legendPos = isDesktop && labels.length <= 8 ? 'right' : 'bottom';

    const funcionGenerarEtiquetas = function(chart) {
        const data = chart.data;
        if (!data.labels || !data.labels.length || !data.datasets.length) return [];
        const dataset = data.datasets[0];
        const total = dataset.data.reduce((acc, val, idx) => {
            const isVisible = chart.getDataVisibility ? chart.getDataVisibility(idx) : true;
            return acc + (isVisible ? Number(val || 0) : 0);
        }, 0);

        const modoOscuroActivo = document.documentElement.getAttribute('data-theme') === 'dark';
        const colorEtiqueta = colorVar('--text-color');

        return data.labels.map((label, i) => {
            const val = Number(dataset.data[i] || 0);
            const pct = total > 0 ? ((val / total) * 100).toFixed(1) : '0.0';
            const bg = dataset.backgroundColor[i % dataset.backgroundColor.length];
            const isHidden = chart.getDataVisibility ? !chart.getDataVisibility(i) : false;
            return {
                text: `${label} (${pct}%)`,
                fillStyle: bg,
                strokeStyle: dataset.borderColor || colorVar('--panel-bg'),
                lineWidth: 1,
                hidden: isHidden,
                index: i,
                fontColor: colorEtiqueta
            };
        });
    };

    if (estado.graficas.dona) {
        estado.graficas.dona.data.labels = labels;
        estado.graficas.dona.data.datasets[0].data = dataValores;
        estado.graficas.dona.data.datasets[0].backgroundColor = colores;
        estado.graficas.dona.data.datasets[0].borderColor = colorVar('--panel-bg');
        estado.graficas.dona.options.plugins.legend.position = legendPos;
        estado.graficas.dona.options.plugins.legend.labels.color = textColor;
        estado.graficas.dona.options.plugins.legend.labels.generateLabels = funcionGenerarEtiquetas;
        estado.graficas.dona.update();
    } else {
        estado.graficas.dona = new Chart(canvas.getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: dataValores,
                    backgroundColor: colores,
                    borderWidth: 2,
                    borderColor: colorVar('--panel-bg')
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '62%',
                plugins: {
                    legend: {
                        position: legendPos,
                        labels: {
                            color: textColor,
                            boxWidth: 12,
                            padding: 10,
                            font: { size: 12, weight: '600' },
                            generateLabels: funcionGenerarEtiquetas
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const valor = context.raw || 0;
                                const pct = total > 0 ? ((valor / total) * 100).toFixed(1) : 0;
                                const valorFmt = Number(valor).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                                return ` ${context.label}: $${valorFmt} ${estado.monedaGlobal} (${pct}%)`;
                            }
                        }
                    }
                }
            },
            plugins: [pluginDonaPorcentajes]
        });
    }
}


// =============================================================================
// 8. BUSCADOR Y AUTOCOMPLETADO DE TICKERS
// =============================================================================

export function inicializarBuscadorTickers() {
    const input = document.getElementById('inv-ticker');
    const dropdown = document.getElementById('ticker-sugerencias');
    if (!input || !dropdown) return;

    input.addEventListener('input', (e) => {
        const val = e.target.value.trim();
        clearTimeout(estado.debounceTimerBuscar);

        if (val.length < 1) {
            dropdown.classList.add('hidden');
            dropdown.innerHTML = '';
            return;
        }

        estado.debounceTimerBuscar = setTimeout(async () => {
            try {
                const res = await apiFetch(`/api/inversiones/buscar?q=${encodeURIComponent(val)}`);
                if (!res.ok) return;
                const sugerencias = await res.json();
                if (input.value.trim().toUpperCase() === val.toUpperCase()) {
                    renderizarSugerenciasTickers(sugerencias, dropdown, input);
                }
            } catch (err) {
                console.error("Error al buscar tickers:", err);
            }
        }, 220);
    });

    document.addEventListener('click', (e) => {
        if (!input.contains(e.target) && !dropdown.contains(e.target)) {
            dropdown.classList.add('hidden');
        }
    });
}


export function renderizarSugerenciasTickers(sugerencias, dropdown, input) {
    if (!sugerencias || sugerencias.length === 0) {
        dropdown.classList.add('hidden');
        dropdown.innerHTML = '';
        return;
    }

    dropdown.innerHTML = '';
    sugerencias.forEach(item => {
        const div = document.createElement('div');
        div.className = 'sugerencia-item';
        div.innerHTML = `
            <div class="sugerencia-info">
                <span class="sugerencia-simbolo">${escapeHTML(item.symbol)}</span>
                <span class="sugerencia-nombre" title="${escapeHTML(item.nombre)}">${escapeHTML(item.nombre)}</span>
            </div>
            <span class="sugerencia-badge">${escapeHTML(item.tipo)}</span>
        `;

        div.addEventListener('click', () => {
            input.value = item.symbol;
            dropdown.classList.add('hidden');
            dropdown.innerHTML = '';
            buscarCotizacionTicker();
        });

        dropdown.appendChild(div);
    });

    dropdown.classList.remove('hidden');
}


export async function buscarCotizacionTicker() {
    const inputTicker = document.getElementById('inv-ticker');
    const ticker = inputTicker.value.trim().toUpperCase();
    if (!ticker) {
        toast("Por favor ingresa un ticker (ej. VOO, QQQ).", 'error');
        return;
    }

    const inputActual = document.getElementById('inv-precio-actual');
    const btn = document.querySelector('.btn-search-ticker');
    const textoOriginal = btn.textContent;
    btn.textContent = 'Buscando...';
    btn.disabled = true;

    try {
        const res = await apiFetch(`/api/inversiones/cotizar/${encodeURIComponent(ticker)}`);
        if (res.ok) {
            const data = await res.json();
            inputActual.value = data.precio;
            
            const selectMoneda = document.getElementById('inv-moneda');
            if (selectMoneda && data.moneda) {
                selectMoneda.value = data.moneda;
            }
        } else {
            const error = await res.json();
            toast(error.detail || "No se encontró cotización en vivo. Puedes ingresarla manualmente.", 'error');
        }
    } catch (err) {
        toast("Error de conexión al consultar cotización.", 'error');
    } finally {
        btn.textContent = textoOriginal;
        btn.disabled = false;
    }
}


export let guardandoInversion = false;

export const formInversion = document.getElementById('form-inversion');

if (formInversion) {
    formInversion.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (guardandoInversion) return;

        const ticker = document.getElementById('inv-ticker').value.trim().toUpperCase();
        const cantidad = parseFloat(document.getElementById('inv-cantidad').value);
        const precio_promedio = parseFloat(document.getElementById('inv-precio-promedio').value);
        const actualVal = document.getElementById('inv-precio-actual').value;
        const precio_actual = actualVal ? parseFloat(actualVal) : null;
        const moneda = document.getElementById('inv-moneda')?.value || 'USD';

        if (!ticker || isNaN(cantidad) || cantidad <= 0 || isNaN(precio_promedio) || precio_promedio <= 0) {
            toast("Completa los campos obligatorios con valores numéricos válidos mayores a cero.", 'error');
            return;
        }

        guardandoInversion = true;
        const btnSubmit = formInversion.querySelector('button[type="submit"]');
        const origHTML = btnSubmit ? btnSubmit.innerHTML : '';
        if (btnSubmit) {
            btnSubmit.disabled = true;
            btnSubmit.innerHTML = '<span>Registrando...</span>';
        }

        try {
            const res = await apiFetch('/api/inversiones', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticker, cantidad, precio_promedio, precio_actual, moneda })
            });

            if (res.ok) {
                formInversion.reset();
                await recargar();
            } else {
                let errText = "Error al registrar la inversión.";
                try {
                    const err = await res.json();
                    errText = formatErrorDetail(err.detail, errText);
                } catch (_) {}
                toast(errText, 'error');
            }
        } catch (err) {
            toast("Error de red al guardar la inversión.", 'error');
        } finally {
            guardandoInversion = false;
            if (btnSubmit) {
                btnSubmit.disabled = false;
                btnSubmit.innerHTML = origHTML || '<span>Guardar Activo</span>';
            }
        }
    });
}


export async function eliminarInversion(id) {
    if (!confirm("¿Deseas eliminar esta posición de tu portafolio?")) return;
    try {
        const res = await apiFetch(`/api/inversiones/${id}`, { method: 'DELETE' });
        if (res.ok) {
            await cargarInversiones();
            await recargar();
        } else {
            toast("No se pudo eliminar la inversión.", 'error');
        }
    } catch (err) {
        console.error("Error al eliminar inversión:", err);
    }
}


export async function sincronizarPrecios() {
    const btn = document.getElementById('btn-sync-precios');
    if (!btn || btn.disabled) return;

    const span = btn.querySelector('span');
    const textoOriginal = span ? span.textContent : 'Actualizar Cotizaciones';
    const svgIcon = btn.querySelector('.btn-svg-icon');

    if (span) span.textContent = 'Sincronizando...';
    if (svgIcon) svgIcon.classList.add('spin-icon');
    btn.disabled = true;

    try {
        const res = await apiFetch('/api/inversiones/sincronizar', { method: 'POST' });
        if (res.ok) {
            const data = await res.json();
            await cargarInversiones();
            await recargar();
            toast(data.mensaje || 'Cotizaciones actualizadas con éxito.', 'ok');
        } else {
            toast("Error al sincronizar cotizaciones.", 'error');
        }
    } catch (err) {
        toast("Error de conexión al sincronizar.", 'error');
    } finally {
        if (span) span.textContent = textoOriginal;
        if (svgIcon) svgIcon.classList.remove('spin-icon');
        btn.disabled = false;
    }
}


// =============================================================================
// 11. MODALES DE EDICIÓN: INVERSIONES Y MOVIMIENTOS
// =============================================================================

export function abrirModalEditarInversion(id, ticker, cantidad, precio_promedio, moneda) {
    const inputId = document.getElementById('modal-edit-id');
    const lblTicker = document.getElementById('modal-edit-ticker');
    const inputCant = document.getElementById('modal-edit-cantidad');
    const inputProm = document.getElementById('modal-edit-precio-promedio');
    const lblMoneda = document.getElementById('modal-edit-moneda');

    if (inputId) inputId.value = id;
    if (lblTicker) lblTicker.textContent = ticker;
    if (inputCant) inputCant.value = cantidad;
    if (inputProm) inputProm.value = precio_promedio;
    if (lblMoneda) lblMoneda.textContent = moneda || 'USD';

    const modal = document.getElementById('modal-editar-inversion');
    if (modal) {
        modal.classList.remove('hidden');
        setTimeout(() => {
            if (inputCant) {
                inputCant.focus();
                inputCant.select();
            }
        }, 50);
    }
}


export function cerrarModalEditarInversion() {
    const modal = document.getElementById('modal-editar-inversion');
    if (modal) modal.classList.add('hidden');
}


export async function guardarEdicionInversion(e) {
    if (e) {
        e.preventDefault();
        e.stopPropagation();
    }

    const id = document.getElementById('modal-edit-id').value;
    const cantidad = parseFloat(document.getElementById('modal-edit-cantidad').value);
    const precio_promedio = parseFloat(document.getElementById('modal-edit-precio-promedio').value);

    if (isNaN(cantidad) || cantidad <= 0 || isNaN(precio_promedio) || precio_promedio <= 0) {
        toast("Por favor ingresa valores numéricos válidos mayores a cero.", 'error');
        return;
    }

    try {
        const res = await apiFetch(`/api/inversiones/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cantidad, precio_promedio })
        });

        if (res.ok) {
            cerrarModalEditarInversion();
            await cargarInversiones();
            await recargar();
        } else {
            const err = await res.json();
            toast(err.detail || "Error al actualizar la posición.", 'error');
        }
    } catch (err) {
        toast("Error de red al intentar guardar los cambios.", 'error');
    }
}


export function abrirModalEditarInversionPorId(id) {
    const pos = estado.cache.inversiones[id];
    if (!pos) return;
    abrirModalEditarInversion(pos.id, pos.ticker, pos.cantidad, pos.precio_promedio, pos.moneda);
}

Object.assign(window, {
    abrirModalEditarInversion,
    abrirModalEditarInversionPorId,
    buscarCotizacionTicker,
    cerrarModalEditarInversion,
    eliminarInversion,
    guardarEdicionInversion,
    sincronizarPrecios
});
