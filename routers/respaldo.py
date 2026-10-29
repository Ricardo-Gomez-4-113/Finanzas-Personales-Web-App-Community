"""routers/respaldo.py - Respaldo, restauración y exportación a Excel por tenant."""

from fastapi import APIRouter
from fastapi import HTTPException, status, Depends, BackgroundTasks, UploadFile, File
from fastapi.responses import FileResponse, Response
from typing import Dict, Any
import sqlite3
import os
import shutil
import tempfile

from database import (
    init_tenant_db, 
    get_tenant_conn, 
    get_tenant_path, 
    backup_tenant_db_hot, 
    olvidar_tenant
)
from finanzas_core import ahora_local
from security import (
    verify_token,
    get_tenant_db
)

router = APIRouter()

# =============================================================================
# RESPALDO AISLADO Y EXPORTACIÓN A EXCEL POR TENANT
# =============================================================================

@router.get("/api/backup", summary="Descargar respaldo directo de base de datos del tenant")
def descargar_backup(background_tasks: BackgroundTasks, current_user: Dict[str, Any] = Depends(verify_token)):
    """Descarga una copia consistente en SQLite propio del usuario autenticado incluyendo cambios en WAL."""
    db_name = current_user.get("db_name", "user_1.db")
    db_path = get_tenant_path(db_name)
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="Base de datos del usuario no encontrada.")
        
    fecha_str = ahora_local().strftime("%Y%m%d_%H%M%S")
    username_safe = "".join(c for c in current_user.get("username", "user") if c.isalnum() or c in ("-", "_"))
    
    # Crear respaldo consistente en caliente usando VACUUM INTO o API nativa de backup de SQLite
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(tmp_fd)
    
    backup_tenant_db_hot(db_name, tmp_path, prefer_vacuum=True)
        
    background_tasks.add_task(os.unlink, tmp_path)
    
    return FileResponse(
        tmp_path,
        filename=f"finanzas_{username_safe}_{fecha_str}.db",
        media_type="application/octet-stream"
    )

@router.post("/api/restore", summary="Restaurar base de datos individual del tenant")
def restaurar_backup(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(verify_token)
):
    """
    Restaura la base de datos SQLite del tenant autenticado (administrador o usuario normal)
    a partir de un archivo de respaldo .db subido.
    Aplica controles estrictos de seguridad y validación:
    1. Límite de tamaño máximo (50 MB).
    2. Cabecera válida SQLite 3 ('SQLite format 3\\x00').
    3. PRAGMA integrity_check.
    4. Verificación y migración del esquema financiero (init_tenant_db).
    5. Copia de seguridad preventiva (.bak) antes del reemplazo.
    """
    filename = file.filename or ""
    if not (filename.lower().endswith(".db") or filename.lower().endswith(".sqlite") or filename.lower().endswith(".sqlite3")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo seleccionado debe ser una base de datos SQLite válida con extensión .db."
        )

    MAX_SIZE = 50 * 1024 * 1024
    content = file.file.read(MAX_SIZE + 1)  # lectura acotada: nunca más de 50 MB + 1 byte en memoria
    if len(content) > MAX_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo excede el tamaño máximo permitido (50 MB)."
        )
        
    if len(content) < 100 or content[:16] != b"SQLite format 3\x00":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo proporcionado no es una base de datos SQLite válida o está dañado."
        )

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(tmp_fd, "wb") as f_tmp:
            f_tmp.write(content)

        # 1. Comprobar integridad con PRAGMA integrity_check
        test_conn = sqlite3.connect(tmp_path, timeout=5.0)
        try:
            cursor_test = test_conn.cursor()
            res_integrity = cursor_test.execute("PRAGMA integrity_check;").fetchone()
            if not res_integrity or res_integrity[0] != "ok":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="La base de datos presenta errores de integridad interna o corrupción."
                )

            # 2. Comprobar que contiene el esquema de la aplicación
            tables_row = cursor_test.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('transacciones', 'apartados', 'inversiones', 'categorias')"
            ).fetchone()
            if not tables_row or tables_row[0] < 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="El archivo no corresponde a un respaldo válido de la aplicación de finanzas."
                )
        except sqlite3.DatabaseError:
            # Cabecera válida pero páginas ilegibles (archivo truncado o basura): es un 400, no un 500.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El archivo proporcionado no es una base de datos SQLite válida o está dañado."
            )
        finally:
            test_conn.close()

        # 3. Aplicar migraciones defensivas a la BD temporal
        try:
            init_tenant_db(tmp_path)
        except sqlite3.DatabaseError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El respaldo no se pudo migrar al esquema actual de la aplicación."
            )

        # 4. Copia de seguridad automática preventiva de la BD actual
        db_name = current_user.get("db_name", "user_1.db")
        target_path = get_tenant_path(db_name)
        if os.path.exists(target_path):
            bak_path = target_path + ".bak"
            try:
                shutil.copy2(target_path, bak_path)
            except Exception:
                pass

        # 5. Reemplazo seguro mediante SQLite backup API
        src = sqlite3.connect(tmp_path)
        dst = get_tenant_conn(db_name)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()

        # 6. Re-aplicar esquema final en la base destino
        init_tenant_db(target_path)
        olvidar_tenant(db_name)

        return {
            "status": "ok",
            "mensaje": f"Base de datos de '{current_user['username']}' restaurada con éxito.",
            "archivo": filename
        }

    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

@router.get("/api/exportar-excel", summary="Descargar reporte en Excel del tenant")
def exportar_excel(
    moneda_base: str = "MXN",
    conn: sqlite3.Connection = Depends(get_tenant_db),
    current_user: Dict[str, Any] = Depends(verify_token)
):
    """Genera el reporte financiero en Excel exclusivamente con los datos del tenant activo."""
    from export_service import generar_reporte_excel
    from finanzas_core import materializar_recurrentes
    materializar_recurrentes(conn, ahora_local().date())
    buf = generar_reporte_excel(
        moneda_base=moneda_base,
        conn=conn,
        db_name=current_user.get("db_name", "user_1.db")
    )
    fecha_str = ahora_local().strftime("%Y%m%d_%H%M%S")
    username_safe = "".join(c for c in current_user.get("username", "user") if c.isalnum() or c in ("-", "_"))
    nombre_archivo = f"reporte_{username_safe}_{moneda_base.upper()}_{fecha_str}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre_archivo}"'
        }
    )
