"""
master_db.py - Gestión de la Base de Datos Maestra (auth_master.db) y Usuarios Multi-Tenant

Este módulo centraliza:
1. El esquema de la base de datos central maestra ('data/auth_master.db').
2. La gestión de usuarios, credenciales hasheadas con bcrypt y estado de suscripción ('active', 'suspended', 'trial').
3. El registro de tokens OTP efímeros con tiempo de expiración y límites de reintentos.
4. El mapeo y almacenamiento de rutas físicas de archivos (/data/tenants/user_{id}.db).
5. Configuración obligatoria de PRAGMAs (WAL, NORMAL, foreign_keys=ON, busy_timeout=5000) en cada conexión.
6. Aprovisionamiento seguro del primer Administrador y migración transparente de bases de datos heredadas.
"""

import os
import shutil
import hmac
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from database import (
    DATA_DIR, 
    TENANTS_DIR, 
    get_tenant_path, 
    get_tenant_path_by_id, 
    provision_tenant_db, 
    init_tenant_db,
    configure_sqlite_pragmas,
    olvidar_tenant
)

# =============================================================================
# 1. CONSTANTES Y RUTAS DE LA BASE MAESTRA
# =============================================================================

AUTH_MASTER_DB_NAME = "auth_master.db"
AUTH_MASTER_DB_PATH = os.path.join(DATA_DIR, AUTH_MASTER_DB_NAME)

# Alias de compatibilidad hacia atrás
MASTER_DB_PATH = AUTH_MASTER_DB_PATH
LEGACY_MASTER_DB_PATH = os.path.join(DATA_DIR, "master.db")


# =============================================================================
# 2. CONEXIÓN A LA BASE CENTRAL MAESTRA CON PRAGMAS OBLIGATORIOS
# =============================================================================

def get_master_conn() -> sqlite3.Connection:
    """
    Crea y retorna una conexión a la base de datos central maestra (auth_master.db)
    configurada obligatoriamente con los PRAGMAs del estándar institucional:
    - journal_mode = WAL
    - synchronous = NORMAL
    - foreign_keys = ON
    - busy_timeout = 15000
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(AUTH_MASTER_DB_PATH, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    configure_sqlite_pragmas(conn)
    return conn


# =============================================================================
# 3. INICIALIZACIÓN Y MIGRACIONES DE LA BASE MAESTRA (auth_master.db)
# =============================================================================

def init_master_db() -> None:
    """
    Inicializa el esquema de la base de datos central maestra 'auth_master.db':
    - Migra datos transparentemente desde el archivo legacy 'master.db' si existe.
    - Crea la tabla 'users' con control de suscripción y rutas de archivos de tenant.
    - Crea la tabla 'password_resets' para tokens de seguridad OTP.
    - Crea índices de alto rendimiento sobre username, email y estado de suscripción.
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    # Migración transparente: si existe master.db anterior pero no auth_master.db, copiarlo
    if os.path.exists(LEGACY_MASTER_DB_PATH) and not os.path.exists(AUTH_MASTER_DB_PATH):
        try:
            shutil.copy2(LEGACY_MASTER_DB_PATH, AUTH_MASTER_DB_PATH)
            # Copiar también archivos WAL y SHM si existen
            for ext in ("-wal", "-shm"):
                leg_ext = f"{LEGACY_MASTER_DB_PATH}{ext}"
                if os.path.exists(leg_ext):
                    shutil.copy2(leg_ext, f"{AUTH_MASTER_DB_PATH}{ext}")
        except Exception:
            pass

    conn = get_master_conn()
    try:
        cursor = conn.cursor()

        # -------------------------------------------------------------
        # Tabla: users (usuarios, credenciales, suscripción y rutas de BD)
        # -------------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT CHECK(role IN ('admin', 'user')) NOT NULL DEFAULT 'user',
                is_active INTEGER NOT NULL DEFAULT 1,
                subscription_status TEXT NOT NULL DEFAULT 'active',
                db_name TEXT NOT NULL UNIQUE,
                db_path TEXT NOT NULL DEFAULT '',
                email TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP DEFAULT NULL,
                login_count INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Migraciones defensivas para bases de datos existentes
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'active';")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN db_path TEXT NOT NULL DEFAULT '';")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT NULL;")
        except sqlite3.OperationalError:
            pass

        # Poblar db_path para cualquier fila existente donde esté vacío
        rows_without_path = cursor.execute("SELECT id, db_name FROM users WHERE db_path IS NULL OR db_path = ''").fetchall()
        for r in rows_without_path:
            p = get_tenant_path(r["db_name"])
            cursor.execute("UPDATE users SET db_path = ? WHERE id = ?", (p, r["id"]))

        # -------------------------------------------------------------
        # Tabla: password_resets (tokens OTP de recuperación)
        # -------------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS password_resets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)

        try:
            cursor.execute("ALTER TABLE password_resets ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;")
        except sqlite3.OperationalError:
            pass

        # Índices optimizados
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_subscription ON users(subscription_status);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_resets_user_code ON password_resets(user_id, code);")
    
        conn.commit()
    finally:
        conn.close()


# =============================================================================
# 4. CONSULTAS DE USUARIOS Y AUTENTICACIÓN
# =============================================================================

def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Busca un usuario por su nombre de usuario (insensible a mayúsculas)."""
    conn = get_master_conn()
    try:
        cursor = conn.cursor()
        row = cursor.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_user_by_identifier(identifier: str) -> Optional[Dict[str, Any]]:
    """Busca un usuario por su nombre de usuario o por su dirección de correo electrónico."""
    ident = identifier.strip()
    conn = get_master_conn()
    try:
        cursor = conn.cursor()
        row = cursor.execute(
            """
            SELECT * FROM users 
            WHERE LOWER(username) = LOWER(?) OR (email IS NOT NULL AND LOWER(email) = LOWER(?))
            LIMIT 1
            """,
            (ident, ident)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Busca un usuario por su ID único."""
    conn = get_master_conn()
    try:
        cursor = conn.cursor()
        row = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# =============================================================================
# 5. GESTIÓN DE TOKENS OTP (ONE-TIME PASSWORDS)
# =============================================================================

def count_recent_reset_requests(user_id: int, hours: int = 24) -> int:
    """Cuenta cuántas solicitudes de código OTP de restablecimiento ha generado el usuario en las últimas N horas."""
    limite = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_master_conn()
    try:
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT COUNT(*) as total FROM password_resets WHERE user_id = ? AND created_at >= ?",
            (user_id, limite)
        ).fetchone()
        return row["total"] if row else 0
    finally:
        conn.close()

def create_password_reset_code(user_id: int) -> str:
    """Genera y registra un código numérico aleatorio de 6 dígitos con vigencia de 15 minutos."""
    import secrets
    codigo = f"{secrets.randbelow(900000) + 100000}"
    ahora_dt = datetime.now()
    ahora_str = ahora_dt.strftime("%Y-%m-%d %H:%M:%S")
    expira = (ahora_dt + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")

    conn = get_master_conn()
    try:
        cursor = conn.cursor()
    
        # Limpieza automática: purgar códigos caducados o usados de más de 24 horas
        limite_limpieza = (ahora_dt - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("DELETE FROM password_resets WHERE expires_at < ? OR (used = 1 AND created_at < ?)", (limite_limpieza, limite_limpieza))

        # Invalidar cualquier código anterior pendiente del usuario
        cursor.execute("UPDATE password_resets SET used = 1 WHERE user_id = ? AND used = 0", (user_id,))
        cursor.execute(
            "INSERT INTO password_resets (user_id, code, expires_at, used, attempts, created_at) VALUES (?, ?, ?, 0, 0, ?)",
            (user_id, codigo, expira, ahora_str)
        )
        conn.commit()
        return codigo
    finally:
        conn.close()

MENSAJE_CODIGO_INVALIDO = "El código es incorrecto, ya fue usado o expiró. Solicita uno nuevo."

def verify_reset_code(identifier: str, code: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Valida el código de recuperación sin tocar la contraseña. Devuelve (usuario, None) si es válido
    o (None, mensaje_neutro) en cualquier otro caso: mismo mensaje para usuario inexistente y para
    código erróneo, para no revelar cuentas. El conteo de intentos ocurre en una transacción
    inmediata, así que ráfagas concurrentes no superan el límite.
    """
    user = get_user_by_identifier(identifier)
    ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_master_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        row = None
        if user:
            row = cursor.execute(
                "SELECT * FROM password_resets WHERE user_id = ? AND used = 0 AND expires_at >= ? ORDER BY id DESC LIMIT 1",
                (user["id"], ahora_str),
            ).fetchone()
        if not user or not row:
            conn.rollback()
            return None, MENSAJE_CODIGO_INVALIDO
        if hmac.compare_digest(code.strip().encode("utf-8"), row["code"].encode("utf-8")):
            cursor.execute("UPDATE password_resets SET used = 1 WHERE id = ?", (row["id"],))
            conn.commit()
            return dict(user), None
        cursor.execute("UPDATE password_resets SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
        cursor.execute("UPDATE password_resets SET used = 1 WHERE id = ? AND attempts >= 2", (row["id"],))
        conn.commit()
        return None, MENSAJE_CODIGO_INVALIDO
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def update_user_password(user_id: int, password_hash: str) -> None:
    """Actualiza solo el hash de contraseña de un usuario."""
    conn = get_master_conn()
    try:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
        conn.commit()
    finally:
        conn.close()

def create_user_entry(
    username: str, 
    password_hash: str, 
    role: str = 'user', 
    is_active: int = 1, 
    email: Optional[str] = None,
    subscription_status: str = 'active'
) -> Dict[str, Any]:
    """
    Crea un nuevo usuario en la base central maestra 'auth_master.db' y dispara el
    aprovisionamiento automático de su archivo físico independiente '/data/tenants/user_{id}.db'.
    """
    username_clean = username.strip()
    email_clean = email.strip() if (email and email.strip()) else None
    conn = get_master_conn()
    try:
        cursor = conn.cursor()

        if email_clean:
            existing_email = cursor.execute(
                "SELECT id FROM users WHERE LOWER(email) = LOWER(?)",
                (email_clean,)
            ).fetchone()
            if existing_email:
                raise ValueError(f"El correo electrónico '{email_clean}' ya está registrado en otra cuenta.")
    
        # 1. Insertar inicialmente para obtener el ID único
        cursor.execute(
            """
            INSERT INTO users (username, password_hash, role, is_active, subscription_status, db_name, db_path, email)
            VALUES (?, ?, ?, ?, ?, ?, '', ?)
            """,
            (username_clean, password_hash, role, is_active, subscription_status, f"temp_{os.urandom(4).hex()}", email_clean)
        )
        new_id = cursor.lastrowid
    
        # 2. Aprovisionar físicamente el tenant 'user_{id}.db' con PRAGMAs y migraciones
        db_name, db_path = provision_tenant_db(new_id)
    
        # 3. Guardar el nombre de archivo y la ruta física canónica en auth_master.db
        cursor.execute(
            "UPDATE users SET db_name = ?, db_path = ? WHERE id = ?",
            (db_name, db_path, new_id)
        )
        conn.commit()
    
        user_row = cursor.execute("SELECT * FROM users WHERE id = ?", (new_id,)).fetchone()
        return dict(user_row)
    finally:
        conn.close()

def update_login_activity(user_id: int, timestamp_str: str) -> None:
    """Registra la fecha/hora del último inicio de sesión e incrementa el contador de accesos."""
    conn = get_master_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE users 
            SET last_login = ?, login_count = login_count + 1 
            WHERE id = ?
            """,
            (timestamp_str, user_id)
        )
        conn.commit()
    finally:
        conn.close()

def set_user_status(user_id: int, is_active: int) -> None:
    """Activa o suspende el acceso de un usuario al sistema."""
    conn = get_master_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if is_active else 0, user_id))
        conn.commit()
    finally:
        conn.close()

def update_subscription_status(user_id: int, status: str) -> None:
    """Actualiza el estado de suscripción de un usuario ('active', 'suspended', 'trial', 'cancelled')."""
    conn = get_master_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET subscription_status = ? WHERE id = ?", (status.strip().lower(), user_id))
        conn.commit()
    finally:
        conn.close()

def update_user(
    user_id: int, 
    username: Optional[str] = None, 
    password_hash: Optional[str] = None, 
    role: Optional[str] = None,
    email: Optional[str] = None,
    subscription_status: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Actualiza datos de un usuario (nombre de usuario, credencial, rol, email y/o suscripción).
    """
    user = get_user_by_id(user_id)
    if not user:
        return None

    conn = get_master_conn()
    try:
        cursor = conn.cursor()

        fields = []
        params = []

        if username is not None:
            clean_user = username.strip()
            if len(clean_user) < 3:
                raise ValueError("El nombre de usuario debe tener al menos 3 caracteres.")
            existing = cursor.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?) AND id != ?", (clean_user, user_id)).fetchone()
            if existing:
                raise ValueError(f"El nombre de usuario '{clean_user}' ya está en uso por otra cuenta.")
            fields.append("username = ?")
            params.append(clean_user)

        if email is not None:
            clean_email = email.strip()
            if clean_email:
                existing_email = cursor.execute("SELECT id FROM users WHERE LOWER(email) = LOWER(?) AND id != ?", (clean_email, user_id)).fetchone()
                if existing_email:
                    raise ValueError(f"El correo electrónico '{clean_email}' ya está registrado en otra cuenta.")
                fields.append("email = ?")
                params.append(clean_email)
            else:
                fields.append("email = NULL")

        if password_hash is not None and password_hash.strip():
            fields.append("password_hash = ?")
            params.append(password_hash.strip())

        if role is not None and role.strip():
            fields.append("role = ?")
            params.append(role.strip())

        if subscription_status is not None and subscription_status.strip():
            fields.append("subscription_status = ?")
            params.append(subscription_status.strip().lower())

        if not fields:
            return user

        params.append(user_id)
        sql = f"UPDATE users SET {', '.join(fields)} WHERE id = ?"
        cursor.execute(sql, tuple(params))
        conn.commit()

        updated = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(updated) if updated else None
    finally:
        conn.close()

def delete_user_and_tenant(user_id: int) -> bool:
    """
    Elimina permanentemente a un usuario de 'auth_master.db' y borra
    su archivo físico de base de datos tenant (.db, -wal, -shm).
    Olvida el estado de migraciones del tenant antes de borrar su archivo.
    Protege las cuentas con rol admin e ID 1 contra eliminación accidental.
    """
    user = get_user_by_id(user_id)
    if not user:
        return False
        
    if user["id"] == 1 or user["username"].lower() == "admin":
        return False
        
    db_name = user["db_name"]
    db_path = user.get("db_path") or get_tenant_path(db_name)

    # 1. Olvidar el estado de migraciones del tenant antes de borrarlo
    olvidar_tenant(db_name)

    # 2. Eliminar registros de la base maestra
    conn = get_master_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    
        # 3. Eliminar archivos físicos del tenant
        for path in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        return True
    finally:
        conn.close()


# =============================================================================
# 7. MÉTRICAS Y AUDITORÍA DE ALMACENAMIENTO FÍSICO
# =============================================================================

def get_tenant_file_size(db_name_or_path: str) -> int:
    """Calcula el tamaño total en bytes del tenant (archivo principal + WAL + SHM)."""
    db_path = get_tenant_path(db_name_or_path)
    total_bytes = 0
    for path in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
        if os.path.exists(path):
            try:
                total_bytes += os.path.getsize(path)
            except Exception:
                pass
    return total_bytes

def format_bytes(size_bytes: int) -> str:
    """Convierte bytes a formato legible (KB o MB)."""
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.2f} MB"

def list_users_with_metrics() -> List[Dict[str, Any]]:
    """Retorna la lista de todos los usuarios con métricas de actividad, estado de suscripción y peso de su BD."""
    conn = get_master_conn()
    try:
        cursor = conn.cursor()
        rows = cursor.execute(
            """
            SELECT id, username, email, role, is_active, subscription_status, db_name, db_path, created_at, last_login, login_count 
            FROM users 
            ORDER BY id ASC
            """
        ).fetchall()
    
        usuarios = []
        for r in rows:
            u = dict(r)
            bytes_ocupados = get_tenant_file_size(u["db_name"])
            u["db_size_bytes"] = bytes_ocupados
            u["db_size_formatted"] = format_bytes(bytes_ocupados)
            usuarios.append(u)
        return usuarios
    finally:
        conn.close()

def get_admin_stats(ahora: datetime) -> Dict[str, Any]:
    """Calcula las métricas globales para las tarjetas KPI del Panel de Administración."""
    conn = get_master_conn()
    try:
        cursor = conn.cursor()
    
        total_users = cursor.execute("SELECT COUNT(*) as total FROM users").fetchone()["total"]
        inactive_users = cursor.execute("SELECT COUNT(*) as total FROM users WHERE is_active = 0").fetchone()["total"]
        total_logins = cursor.execute("SELECT SUM(login_count) as total FROM users").fetchone()["total"] or 0
    
        hoy_str = ahora.strftime("%Y-%m-%d")
        activos_hoy = cursor.execute(
            "SELECT COUNT(*) as total FROM users WHERE last_login LIKE ? AND is_active = 1",
            (f"{hoy_str}%",)
        ).fetchone()["total"]
    
        siete_dias_atras = (ahora - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        activos_7d = cursor.execute(
            "SELECT COUNT(*) as total FROM users WHERE last_login >= ? AND is_active = 1",
            (siete_dias_atras,)
        ).fetchone()["total"]
    
        total_disk_bytes = 0
        if os.path.exists(TENANTS_DIR):
            for f in os.listdir(TENANTS_DIR):
                f_path = os.path.join(TENANTS_DIR, f)
                if os.path.isfile(f_path):
                    try:
                        total_disk_bytes += os.path.getsize(f_path)
                    except OSError:
                        # Los archivos -wal/-shm de SQLite pueden desaparecer entre listdir y getsize (checkpoint)
                        pass
                
    
        return {
            "total_users": total_users,
            "active_users_today": activos_hoy,
            "active_users_7d": activos_7d,
            "inactive_users": inactive_users,
            "total_logins": total_logins,
            "total_disk_bytes": total_disk_bytes,
            "total_disk_formatted": format_bytes(total_disk_bytes)
        }
    finally:
        conn.close()


# =============================================================================
# 8. SEED INICIAL Y MIGRACIÓN TRANSPARENTE
# =============================================================================

def seed_default_admin_and_migrate(default_password_hash: str) -> Optional[Dict[str, Any]]:
    """
    Rutina de inicio:
    - Inicializa 'auth_master.db'.
    - Si la tabla 'users' está vacía:
      1. Crea el usuario Administrador por defecto 'admin'.
      2. Migra el archivo 'finanzas.db' existente de la raíz a 'data/tenants/user_1.db'
         o inicializa uno nuevo para conservar al 100% los datos históricos.
    """
    init_master_db()
    conn = get_master_conn()
    try:
        cursor = conn.cursor()
        cant = cursor.execute("SELECT COUNT(*) as total FROM users").fetchone()["total"]
    
        if cant > 0:
            return None
        
        base_dir = os.path.dirname(DATA_DIR)
        root_finanzas_db = os.path.join(base_dir, "finanzas.db")
        target_tenant_1 = get_tenant_path_by_id(1)
    
        os.makedirs(TENANTS_DIR, exist_ok=True)
    
        # Migrar archivo existente o inicializar uno nuevo
        if os.path.exists(root_finanzas_db) and not os.path.exists(target_tenant_1):
            shutil.copy2(root_finanzas_db, target_tenant_1)
            init_tenant_db(target_tenant_1)
        else:
            init_tenant_db(target_tenant_1)
        
        cursor.execute(
            """
            INSERT INTO users (username, password_hash, role, is_active, subscription_status, db_name, db_path)
            VALUES (?, ?, 'admin', 1, 'active', 'user_1.db', ?)
            """,
            ("admin", default_password_hash, target_tenant_1)
        )
        conn.commit()
        user_row = cursor.execute("SELECT * FROM users WHERE id = 1").fetchone()
        return dict(user_row)
    finally:
        conn.close()
