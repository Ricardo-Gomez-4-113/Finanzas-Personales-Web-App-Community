/**
 * static/js/calculadora_rf.js - Calculadora interactiva de Renta Fija en tiempo real
 * Operación 100% en el cliente (sin peticiones HTTP ni almacenamiento en base de datos).
 */

const formatoMonedaMXN = new Intl.NumberFormat('es-MX', {
    style: 'currency',
    currency: 'MXN',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
});

/**
 * Realiza el cálculo matemático de rendimientos y totales acumulados
 * y actualiza inmediatamente las tarjetas en el DOM.
 */
export function calcularRentaFija() {
    const elMonto = document.getElementById('calc-rf-monto');
    const elTasa = document.getElementById('calc-rf-tasa');
    const elBase = document.getElementById('calc-rf-base');
    if (!elMonto || !elTasa || !elBase) return;

    const monto = Math.max(0, parseFloat(elMonto.value) || 0);
    const tasa = Math.max(0, parseFloat(elTasa.value) || 0);
    const baseDias = parseInt(elBase.value, 10) || 365;

    const tasaDecimal = tasa / 100;
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

    setTxt('calc-rf-res-dia', rendDia);
    setTxt('calc-rf-res-mes', rendMes);
    setTxt('calc-rf-res-anio', rendAnio);

    setTxt('calc-rf-total-dia', totalDia);
    setTxt('calc-rf-total-mes', totalMes);
    setTxt('calc-rf-total-anio', totalAnio);
}

/**
 * Vincula los event listeners de entrada en tiempo real y ejecuta el primer cálculo.
 */
export function inicializarCalculadoraRF() {
    const elMonto = document.getElementById('calc-rf-monto');
    const elTasa = document.getElementById('calc-rf-tasa');
    const elBase = document.getElementById('calc-rf-base');

    if (elMonto) elMonto.addEventListener('input', calcularRentaFija);
    if (elTasa) elTasa.addEventListener('input', calcularRentaFija);
    if (elBase) elBase.addEventListener('change', calcularRentaFija);

    calcularRentaFija();
}

// Exposición en window por conveniencia
window.calcularRentaFija = calcularRentaFija;
