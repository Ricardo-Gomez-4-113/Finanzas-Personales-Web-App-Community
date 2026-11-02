/**
 * static/js/respaldo.js - Exportación a Excel, respaldo y restauración.
 */

import { cargarDatosAdmin } from './admin.js';
import { apiFetch, estado, formatErrorDetail, recargar, toast } from './core.js';
import { confirmar } from './confirmar.js';


// =============================================================================
// 12. EXPORTACIÓN A EXCEL
// =============================================================================

export function exportarExcel() {
    const btn = document.querySelector('.btn-export');
    const origHTML = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span>Exportando...</span>';
    }

    apiFetch(`/api/exportar-excel?moneda_base=${encodeURIComponent(estado.monedaGlobal)}`)
        .then(res => {
            if (!res.ok) throw new Error("Error en la exportación");
            return res.blob();
        })
        .then(blob => {
            const downloadUrl = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = downloadUrl;
            a.download = `reporte_financiero_${estado.usuarioActual ? estado.usuarioActual.username : 'tenant'}_${estado.monedaGlobal}.xlsx`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(downloadUrl);
        })
        .catch(err => {
            toast("No se pudo generar la exportación a Excel.", 'error');
        })
        .finally(() => {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = origHTML || '<span>Exportar Excel</span>';
            }
        });
}


export async function descargarBackup() {
    const btn = document.querySelector('.btn-backup');
    const origHTML = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span>Generando respaldo...</span>';
    }

    try {
        const res = await apiFetch('/api/backup');
        if (!res.ok) {
            let errorMsg = "Error al descargar el respaldo.";
            try {
                const err = await res.json();
                errorMsg = formatErrorDetail(err.detail, errorMsg);
            } catch (_) {}
            toast(errorMsg, 'error');
            return;
        }

        const disposition = res.headers.get('Content-Disposition');
        let filename = `finanzas_${estado.usuarioActual ? estado.usuarioActual.username : 'tenant'}_respaldo.db`;
        if (disposition && disposition.includes('filename=')) {
            const match = disposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
            if (match && match[1]) {
                filename = match[1].replace(/['"]/g, '').trim();
            }
        }

        const blob = await res.blob();
        const downloadUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(downloadUrl);
    } catch (err) {
        console.error("Error al descargar respaldo:", err);
        toast("Error de conexión al descargar la base de datos.", 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origHTML || '<span>Descargar Respaldo</span>';
        }
    }
}


export async function solicitarImportarBackup() {
    const input = document.getElementById('input-restore-db');
    if (!input) return;
    const aceptado = await confirmar({
        titulo: 'Restaurar copia de seguridad',
        mensaje: 'Restaurar una copia de seguridad reemplazará los datos actuales de tu cuenta con los del archivo seleccionado.\n\n' +
            'Se creará un respaldo de seguridad automático (.bak) antes de aplicar la restauración.\n\n' +
            '¿Deseas seleccionar el archivo de respaldo (.db) ahora?',
        aceptar: 'Elegir archivo',
        peligro: true,
    });
    if (aceptado) {
        input.value = '';
        input.click();
    }
}


export async function procesarArchivoBackup(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;

    if (!file.name.toLowerCase().endsWith('.db') && !file.name.toLowerCase().endsWith('.sqlite') && !file.name.toLowerCase().endsWith('.sqlite3')) {
        toast("El archivo seleccionado debe ser una base de datos SQLite (.db).", 'error');
        return;
    }

    if (file.size > 50 * 1024 * 1024) {
        toast("El archivo excede el tamaño máximo permitido (50 MB).", 'error');
        return;
    }

    const btnRestore = document.querySelector('.btn-restore');
    const origHTML = btnRestore ? btnRestore.innerHTML : '';
    if (btnRestore) {
        btnRestore.disabled = true;
        btnRestore.innerHTML = '<span>Restaurando base de datos...</span>';
    }

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await apiFetch('/api/restore', {
            method: 'POST',
            body: formData
        });

        if (res.ok) {
            const data = await res.json();
            toast("¡Base de datos restaurada con éxito!\n\nTus datos han sido actualizados.", 'ok');
            await recargar();
            if (estado.usuarioActual && estado.usuarioActual.role === 'admin') {
                await cargarDatosAdmin();
            }
        } else {
            let errorMsg = "No se pudo restaurar la base de datos.";
            try {
                const err = await res.json();
                errorMsg = formatErrorDetail(err.detail, errorMsg);
            } catch (_) {}
            toast(errorMsg, 'error');
        }
    } catch (err) {
        console.error("Error al restaurar respaldo:", err);
        toast("Error al procesar o subir el archivo de respaldo.", 'error');
    } finally {
        if (btnRestore) {
            btnRestore.disabled = false;
            btnRestore.innerHTML = origHTML || '<span>Restaurar Base de Datos</span>';
        }
        e.target.value = '';
    }
}

Object.assign(window, {
    descargarBackup,
    exportarExcel,
    procesarArchivoBackup,
    solicitarImportarBackup
});
