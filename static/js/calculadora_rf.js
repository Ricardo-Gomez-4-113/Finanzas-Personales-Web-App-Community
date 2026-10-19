/**
 * static/js/calculadora_rf.js - Calculadora interactiva de Renta Fija
 * Operación 100% en el cliente (sin peticiones HTTP ni almacenamiento en base de datos).
 * Soporta cálculo interactivo en vivo, botón explícito de cálculo, presets de mercado y tabla de proyección.
 */

const formatoMonedaMXN = new Intl.NumberFormat('es-MX', {
    style: 'currency',
    currency: 'MXN',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
});

/** Plazos financieros estándar para la matriz de proyección */
const PLAZOS_FINANCIEROS = [
    { label: '1 Día (Diario)', dias: 1, tag: 'A la vista' },
    { label: '7 Días (Semanal)', dias: 7, tag: 'Corto plazo' },
    { label: '14 Días (Quincenal)', dias: 14, tag: 'Nómina' },
    { label: '28 Días (CETES 28d)', dias: 28, tag: 'CETES 28d' },
    { label: '91 Días (3 Meses)', dias: 91, tag: 'CETES 91d' },
    { label: '182 Días (6 Meses)', dias: 182, tag: 'Semestral' },
    { label: '365 Días (1 Año)', dias: 365, tag: 'Anual' }
];

/**
 * Realiza el cálculo matemático de rendimientos y totales acumulados
 * y actualiza inmediatamente las tarjetas y la tabla de proyección.
 * 
 * @param {boolean} [activarPulso=false] Si es true, añade una animación visual sutil de actualización.
 */
export function calcularRentaFija(activarPulso = false) {
    const elMonto = document.getElementById('calc-rf-monto');
    const elTasa = document.getElementById('calc-rf-tasa');
    const elBase = document.getElementById('calc-rf-base');
    if (!elMonto || !elTasa || !elBase) return;

    const monto = Math.max(0, parseFloat(elMonto.value) || 0);
    const tasa = Math.max(0, parseFloat(elTasa.value) || 0);
    const baseDias = parseInt(elBase.value, 10) || 365;

    const tasaDecimal = tasa / 100;

    // Rendimientos base
    const rendDia = (monto * tasaDecimal) / baseDias;
    const rendMes = (monto * tasaDecimal) / 12;
    const rendAnio = monto * tasaDecimal;

    const totalDia = monto + rendDia;
    const totalMes = monto + rendMes;
    const totalAnio = monto + rendAnio;

    const setTxt = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = formatoMonedaMXN.format(val);
    };

    // 1. Actualizar Tarjetas Principales (Rendimientos y Totales)
    setTxt('calc-rf-res-dia', rendDia);
    setTxt('calc-rf-res-mes', rendMes);
    setTxt('calc-rf-res-anio', rendAnio);

    setTxt('calc-rf-cap-dia', monto);
    setTxt('calc-rf-cap-mes', monto);
    setTxt('calc-rf-cap-anio', monto);

    setTxt('calc-rf-total-dia', totalDia);
    setTxt('calc-rf-total-mes', totalMes);
    setTxt('calc-rf-total-anio', totalAnio);

    // 2. Actualizar Tabla de Proyección Temporal por Plazos
    const tbody = document.getElementById('tabla-calc-rf-plazos');
    if (tbody) {
        tbody.innerHTML = PLAZOS_FINANCIEROS.map(p => {
            const rendPlazo = (monto * tasaDecimal * p.dias) / baseDias;
            const totalPlazo = monto + rendPlazo;
            return `
                <tr>
                    <td>
                        <div class="calc-plazo-name">
                            <strong>${p.label}</strong>
                            <span class="badge-tag badge-tag-sm">${p.tag}</span>
                        </div>
                    </td>
                    <td class="s-text-align-center calc-col-dias">${p.dias}</td>
                    <td class="s-text-align-right text-ingreso font-tabular">+${formatoMonedaMXN.format(rendPlazo)}</td>
                    <td class="s-text-align-right font-tabular"><strong>${formatoMonedaMXN.format(totalPlazo)}</strong></td>
                </tr>
            `;
        }).join('');
    }

    // 3. Animación táctil de feedback si fue disparado explícitamente
    if (activarPulso) {
        const container = document.getElementById('calc-cards-container');
        if (container) {
            container.classList.remove('calc-pulse');
            // Forzar reflow para reiniciar la animación
            void container.offsetWidth;
            container.classList.add('calc-pulse');
            setTimeout(() => container.classList.remove('calc-pulse'), 450);
        }
    }
}

/**
 * Restablece los parámetros de la calculadora a sus valores por defecto.
 */
export function restablecerCalculadoraRF() {
    const elMonto = document.getElementById('calc-rf-monto');
    const elTasa = document.getElementById('calc-rf-tasa');
    const elBase = document.getElementById('calc-rf-base');

    if (elMonto) elMonto.value = '10000';
    if (elTasa) elTasa.value = '11.50';
    if (elBase) elBase.value = '365';

    // Desactivar chips activos
    document.querySelectorAll('.calc-chip').forEach(c => c.classList.remove('active'));

    calcularRentaFija(true);
}

/**
 * Vincula todos los event listeners de cálculo, botones y presets.
 */
export function inicializarCalculadoraRF() {
    const elMonto = document.getElementById('calc-rf-monto');
    const elTasa = document.getElementById('calc-rf-tasa');
    const elBase = document.getElementById('calc-rf-base');
    const form = document.getElementById('form-calc-rf');
    const btnCalcular = document.getElementById('btn-calcular-rf');
    const btnReset = document.getElementById('btn-reset-calc-rf');

    // Escuchas interactivas en vivo (sin pulso)
    if (elMonto) elMonto.addEventListener('input', () => calcularRentaFija(false));
    if (elTasa) elTasa.addEventListener('input', () => calcularRentaFija(false));
    if (elBase) elBase.addEventListener('change', () => calcularRentaFija(false));

    // Botón explícito de cálculo (con pulso)
    if (btnCalcular) {
        btnCalcular.addEventListener('click', (e) => {
            e.preventDefault();
            calcularRentaFija(true);
        });
    }

    // Envío del formulario (por ejemplo, presionar Enter en cualquier input)
    if (form) {
        form.addEventListener('submit', (e) => {
            e.preventDefault();
            calcularRentaFija(true);
        });
    }

    // Botón de restablecer
    if (btnReset) {
        btnReset.addEventListener('click', (e) => {
            e.preventDefault();
            restablecerCalculadoraRF();
        });
    }

    // Chips de benchmarks de mercado (CETES, Pagaré, SOFIPO, etc.)
    document.querySelectorAll('.calc-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const tasa = chip.dataset.tasa;
            const base = chip.dataset.base;
            if (tasa && elTasa) elTasa.value = tasa;
            if (base && elBase) elBase.value = base;

            document.querySelectorAll('.calc-chip').forEach(c => c.classList.remove('active'));
            chip.classList.add('active');

            calcularRentaFija(true);
        });
    });

    // Primer renderizado
    calcularRentaFija(false);
}

// Exposición global
Object.assign(window, {
    calcularRentaFija,
    restablecerCalculadoraRF,
    inicializarCalculadoraRF
});
