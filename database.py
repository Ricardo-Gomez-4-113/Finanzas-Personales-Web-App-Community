"""
database.py - Módulo de Gestión de Esquemas Tenant, Conexiones LRU y Migraciones Automáticas

Este módulo implementa:
1. Aislamiento físico multi-tenant en SQLite ('data/tenants/user_{id}.db').
2. Configuración obligatoria de PRAGMAs en cada conexión abierta:
   - journal_mode = WAL
   - synchronous = NORMAL
   - foreign_keys = ON
   - busy_timeout = 5000
3. Conexión por petición: cada request abre su propia conexión y la cierra al terminar.
4. Migraciones automáticas bajo demanda (lazy migrations) verificando PRAGMA user_version al conectar.
5. Respaldos y exportaciones en caliente exclusivamente mediante VACUUM INTO o la API nativa de backup de SQLite.
"""

import calendar
import os
import re
import sqlite3
import threading
from datetime import datetime
from typing import Tuple, Optional

from dinero import fabrica_filas

# =============================================================================
# 1. CONSTANTES Y RUTAS DE ALMACENAMIENTO MULTI-TENANT
# =============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TENANTS_DIR = os.path.join(DATA_DIR, "tenants")

# Versión actual del esquema de base de datos de cada tenant
CURRENT_TENANT_SCHEMA_VERSION = 24


# =============================================================================
# 2. GESTIÓN DE DIRECTORIOS Y RUTAS
# =============================================================================

def ensure_tenant_directories() -> None:
    """Garantiza la existencia física de los directorios 'data' y 'data/tenants'."""
    os.makedirs(TENANTS_DIR, exist_ok=True)

def get_tenant_path(db_name_or_path: str) -> str:
    """
    Retorna la ruta absoluta al archivo SQLite del tenant.
    Sanitiza nombres de archivo para evitar Directory Traversal si se pasa un db_name relativo.
    """
    ensure_tenant_directories()
    if os.path.isabs(db_name_or_path):
        return db_name_or_path
    nombre_limpio = os.path.basename(db_name_or_path.strip())
    return os.path.join(TENANTS_DIR, nombre_limpio)

def get_tenant_path_by_id(user_id: int) -> str:
    """Retorna la ruta absoluta canónica '/data/tenants/user_{id}.db'."""
    ensure_tenant_directories()
    return os.path.join(TENANTS_DIR, f"user_{user_id}.db")


# =============================================================================
# 3. CONFIGURACIÓN OBLIGATORIA DE PRAGMAS SQLITE
# =============================================================================

def configure_sqlite_pragmas(conn: sqlite3.Connection) -> None:
    """
    Aplica obligatoriamente los PRAGMAs requeridos por la arquitectura multi-tenant:
    - journal_mode = WAL: Concurrencia real (lectores no bloquean escritores).
    - synchronous = NORMAL: Durabilidad sin I/O innecesario en cada transacción.
    - foreign_keys = ON: Integridad referencial forzada.
    - busy_timeout = 15000: Espera de hasta 15 segundos ante contención antes de emitir error.
    """
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 15000;")


# =============================================================================
# 4. MIGRACIONES AUTOMÁTICAS BAJO DEMANDA (LAZY MIGRATIONS)
# =============================================================================

def _fecha_valida_mas_cercana(texto: str) -> Optional[str]:
    """AAAA-MM-DD imposible -> la fecha válida más cercana (mes entre 1 y 12, día entre 1 y el último del mes).
    None si ya es una fecha real o no tiene la forma AAAA-MM-DD. Solo para la migración 19."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", texto or "")
    if not m:
        return None
    anio, mes, dia = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if anio < 1:
        anio = 2000
    mes = min(max(mes, 1), 12)
    dia = min(max(dia, 1), calendar.monthrange(anio, mes)[1])
    return f"{anio:04d}-{mes:02d}-{dia:02d}"


# Columnas de dinero que la migración 21 pasa de pesos decimales (REAL) a centavos enteros: (tabla, columna, obligatoria)
_COLUMNAS_A_CENTAVOS = (
    ("cuentas", "saldo_inicial", True),
    ("transacciones", "monto", True),
    ("apartados", "monto_actual", True),
    ("apartados", "meta_monto", False),
    ("recurrentes", "monto", True),
    ("recurrentes", "monto_total", False),
    ("cuentas_rendimiento", "capital", True),
    ("presupuesto_config", "ingreso_base", True),
    ("presupuesto_tarjetas", "monto", True),
)


def _pasar_dinero_a_centavos(cursor: sqlite3.Cursor) -> None:
    """Cada columna de dinero se reconstruye como INTEGER en centavos: columna nueva, copia redondeada, se quita la
    vieja y la nueva toma su nombre. Si la columna ya es INTEGER no se toca, así que repetirla es inofensivo."""
    for tabla, columna, obligatoria in _COLUMNAS_A_CENTAVOS:
        tipos = {f[1]: (f[2] or "").upper() for f in cursor.execute(f'PRAGMA table_info("{tabla}")')}
        if columna not in tipos or tipos[columna] == "INTEGER":
            continue
        nueva = f"{columna}__centavos"
        cursor.execute(f'ALTER TABLE "{tabla}" ADD COLUMN "{nueva}" INTEGER' + (" NOT NULL DEFAULT 0" if obligatoria else ""))
        cursor.execute(
            f'UPDATE "{tabla}" SET "{nueva}" = CAST(ROUND(COALESCE(CAST("{columna}" AS REAL), 0) * 100.0) AS INTEGER)'
            + ("" if obligatoria else f' WHERE "{columna}" IS NOT NULL')
        )
        cursor.execute(f'ALTER TABLE "{tabla}" DROP COLUMN "{columna}"')
        cursor.execute(f'ALTER TABLE "{tabla}" RENAME COLUMN "{nueva}" TO "{columna}"')


def _tolerar_columna_existente(error: sqlite3.OperationalError) -> None:
    """Deja pasar solo «duplicate column name» (el ALTER TABLE ADD COLUMN ya se aplicó); cualquier otro error de SQL sale."""
    if "duplicate column name" not in str(error).lower():
        raise error


def apply_lazy_migrations(conn: sqlite3.Connection) -> None:
    """
    Verifica PRAGMA user_version en la base de datos del usuario y aplica
    automáticamente los cambios de esquema pendientes al vuelo (lazy migrations).
    Se ejecuta de forma transparente en la primera conexión al tenant.
    """
    cursor = conn.cursor()
    res = cursor.execute("PRAGMA user_version;").fetchone()
    current_version = res[0] if res else 0

    if current_version >= CURRENT_TENANT_SCHEMA_VERSION:
        return

    # -------------------------------------------------------------
    # Versión 1: Esquema base financiero (cuentas, categorias, transacciones, apartados, inversiones)
    # -------------------------------------------------------------
    if current_version < 1:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cuentas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                tipo TEXT NOT NULL DEFAULT 'efectivo',
                saldo_inicial REAL NOT NULL DEFAULT 0.0,
                moneda TEXT NOT NULL DEFAULT 'MXN',
                creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO cuentas (id, nombre, tipo, saldo_inicial, moneda)
            VALUES (1, 'Cuenta Principal', 'efectivo', 0.0, 'MXN');
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categorias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                tipo TEXT NOT NULL
            );
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO categorias (id, nombre, tipo)
            VALUES (1, 'General', 'gasto');
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transacciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                monto REAL NOT NULL,
                tipo TEXT NOT NULL,
                categoria_id INTEGER,
                cuenta_id INTEGER DEFAULT 1,
                descripcion TEXT,
                FOREIGN KEY (categoria_id) REFERENCES categorias (id),
                FOREIGN KEY (cuenta_id) REFERENCES cuentas (id)
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS apartados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL UNIQUE,
                tipo TEXT CHECK(tipo IN ('libre', 'meta')) NOT NULL,
                monto_actual REAL NOT NULL DEFAULT 0.0,
                meta_monto REAL DEFAULT NULL,
                creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inversiones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL UNIQUE,
                cantidad REAL NOT NULL,
                precio_promedio REAL NOT NULL,
                precio_actual REAL NOT NULL,
                moneda TEXT NOT NULL DEFAULT 'USD',
                actualizado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("PRAGMA user_version = 1;")
        current_version = 1

    # -------------------------------------------------------------
    # Versión 2: Ajuste de monedas, tickers mexicanos y cuenta_id
    # -------------------------------------------------------------
    if current_version < 2:
        try:
            cursor.execute("ALTER TABLE inversiones ADD COLUMN moneda TEXT NOT NULL DEFAULT 'USD';")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)

        try:
            cursor.execute("UPDATE inversiones SET moneda = 'MXN' WHERE (ticker LIKE '%.MX' OR ticker LIKE '%.mx') AND moneda = 'USD';")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)

        try:
            cursor.execute("ALTER TABLE transacciones ADD COLUMN cuenta_id INTEGER DEFAULT 1 REFERENCES cuentas(id);")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)

        cursor.execute("PRAGMA user_version = 2;")
        current_version = 2

    # -------------------------------------------------------------
    # Versión 3: Integridad de cuentas e índices de alto rendimiento
    # -------------------------------------------------------------
    if current_version < 3:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cuentas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                tipo TEXT NOT NULL DEFAULT 'efectivo',
                saldo_inicial REAL NOT NULL DEFAULT 0.0,
                moneda TEXT NOT NULL DEFAULT 'MXN',
                creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO cuentas (id, nombre, tipo, saldo_inicial, moneda)
            VALUES (1, 'Cuenta Principal', 'efectivo', 0.0, 'MXN');
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_transacciones_fecha ON transacciones(fecha);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_transacciones_tipo ON transacciones(tipo);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_transacciones_cat ON transacciones(categoria_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_inversiones_ticker ON inversiones(ticker);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_apartados_nombre ON apartados(nombre);")
        cursor.execute("PRAGMA user_version = 3;")
        current_version = 3

    # -------------------------------------------------------------
    # Versión 4: Fijos y MSI (recurrentes), cuentas con rendimiento,
    # enlace real de apartados y categorías sembradas
    # -------------------------------------------------------------
    if current_version < 4 and CURRENT_TENANT_SCHEMA_VERSION >= 4:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recurrentes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                descripcion TEXT NOT NULL,
                tipo TEXT NOT NULL CHECK(tipo IN ('ingreso','gasto')),
                monto REAL NOT NULL,
                frecuencia TEXT NOT NULL CHECK(frecuencia IN ('semanal','quincenal','mensual')),
                dia INTEGER,
                proxima_fecha TEXT NOT NULL,
                total_cuotas INTEGER,
                cuotas_generadas INTEGER NOT NULL DEFAULT 0,
                monto_total REAL,
                categoria_id INTEGER DEFAULT 1 REFERENCES categorias(id),
                apartado_id INTEGER REFERENCES apartados(id) ON DELETE SET NULL,
                presupuesto_tarjeta_id INTEGER REFERENCES presupuesto_tarjetas(id) ON DELETE SET NULL,
                activo INTEGER NOT NULL DEFAULT 1,
                creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        try:
            cursor.execute("ALTER TABLE transacciones ADD COLUMN recurrente_id INTEGER REFERENCES recurrentes(id) ON DELETE SET NULL;")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        try:
            cursor.execute("ALTER TABLE transacciones ADD COLUMN apartado_id INTEGER REFERENCES apartados(id) ON DELETE SET NULL;")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_transacciones_rec ON transacciones(recurrente_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_transacciones_apa ON transacciones(apartado_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recurrentes_tarjeta ON recurrentes(presupuesto_tarjeta_id);")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cuentas_rendimiento (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL UNIQUE,
                capital REAL NOT NULL,
                tasa_anual REAL NOT NULL,
                fecha_base TEXT NOT NULL,
                creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Backfill: enlace por texto -> FK real
        cursor.execute("""
            UPDATE transacciones SET apartado_id = (
                SELECT a.id FROM apartados a
                WHERE transacciones.descripcion IN ('Ahorro destinado a: ' || a.nombre, 'Retiro de ahorro: ' || a.nombre)
            ) WHERE descripcion LIKE 'Ahorro destinado a:%' OR descripcion LIKE 'Retiro de ahorro:%';
        """)

        # Categorías sembradas (categorias no tiene UNIQUE: se evita duplicar)
        categorias_seed = [
            ('Comida', 'gasto'), ('Transporte', 'gasto'), ('Vivienda', 'gasto'), ('Servicios', 'gasto'),
            ('Salud', 'gasto'), ('Entretenimiento', 'gasto'), ('Compras', 'gasto'), ('Educación', 'gasto'),
            ('Compras a meses', 'gasto'), ('Otros', 'gasto'),
            ('Nómina', 'ingreso'), ('Freelance', 'ingreso'), ('Rendimientos', 'ingreso'), ('Otros ingresos', 'ingreso'),
        ]
        for nombre, tipo in categorias_seed:
            cursor.execute(
                "INSERT INTO categorias (nombre, tipo) SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM categorias WHERE nombre=? AND tipo=?)",
                (nombre, tipo, nombre, tipo)
            )
        cursor.execute("PRAGMA user_version = 4;")
        current_version = 4

    # -------------------------------------------------------------
    # Versión 5: Organizador de Presupuesto (presupuesto_config y presupuesto_tarjetas)
    # -------------------------------------------------------------
    if current_version < 5 and CURRENT_TENANT_SCHEMA_VERSION >= 5:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS presupuesto_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                ingreso_base REAL NOT NULL DEFAULT 0.0,
                frecuencia TEXT NOT NULL CHECK(frecuencia IN ('semanal','quincenal','mensual')) DEFAULT 'mensual',
                dia_pago INTEGER DEFAULT NULL,
                subdivision_tipo TEXT NOT NULL DEFAULT 'ninguna',
                subdivision_config TEXT NOT NULL DEFAULT '[]',
                actualizado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO presupuesto_config (id, ingreso_base, frecuencia, dia_pago, subdivision_tipo, subdivision_config)
            VALUES (1, 0.0, 'mensual', NULL, 'ninguna', '[]');
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS presupuesto_tarjetas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                icono TEXT NOT NULL DEFAULT 'wallet',
                color TEXT NOT NULL DEFAULT 'gold',
                porcentaje REAL NOT NULL DEFAULT 0.0,
                monto REAL NOT NULL DEFAULT 0.0,
                notas TEXT DEFAULT '',
                orden INTEGER NOT NULL DEFAULT 0,
                dia_pago INTEGER DEFAULT NULL,
                distribucion_subperiodos TEXT DEFAULT NULL,
                creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_presupuesto_orden ON presupuesto_tarjetas(orden);")
        cursor.execute("PRAGMA user_version = 5;")
        current_version = 5

    # -------------------------------------------------------------
    # Versión 6: Sub-división de Presupuesto (quincenas, semanas, personalizada)
    # -------------------------------------------------------------
    if current_version < 6 and CURRENT_TENANT_SCHEMA_VERSION >= 6:
        try:
            cursor.execute("ALTER TABLE presupuesto_config ADD COLUMN subdivision_tipo TEXT NOT NULL DEFAULT 'ninguna';")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        try:
            cursor.execute("ALTER TABLE presupuesto_config ADD COLUMN subdivision_config TEXT NOT NULL DEFAULT '[]';")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        cursor.execute("PRAGMA user_version = 6;")
        current_version = 6

    # -------------------------------------------------------------
    # Versión 7: Enlace de Fijos/MSI con Apartados / Metas de ahorro
    # -------------------------------------------------------------
    if current_version < 7 and CURRENT_TENANT_SCHEMA_VERSION >= 7:
        try:
            cursor.execute("ALTER TABLE recurrentes ADD COLUMN apartado_id INTEGER REFERENCES apartados(id) ON DELETE SET NULL;")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recurrentes_apartado ON recurrentes(apartado_id);")
        cursor.execute("PRAGMA user_version = 7;")
        current_version = 7

    # -------------------------------------------------------------
    # Versión 8: Índice único estricto para evitar duplicados en cuotas de recurrentes
    # -------------------------------------------------------------
    if current_version < 8 and CURRENT_TENANT_SCHEMA_VERSION >= 8:
        cursor.execute("""
            DELETE FROM transacciones 
            WHERE recurrente_id IS NOT NULL 
              AND id NOT IN (
                  SELECT MIN(id) 
                  FROM transacciones 
                  WHERE recurrente_id IS NOT NULL 
                  GROUP BY recurrente_id, fecha
              );
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_transacciones_rec_fecha 
            ON transacciones(recurrente_id, fecha) 
            WHERE recurrente_id IS NOT NULL;
        """)
        cursor.execute("PRAGMA user_version = 8;")
        current_version = 8

    # -------------------------------------------------------------
    # Versión 9: Presupuesto asimétrico por sub-período
    # -------------------------------------------------------------
    if current_version < 9 and CURRENT_TENANT_SCHEMA_VERSION >= 9:
        try:
            cursor.execute("ALTER TABLE presupuesto_tarjetas ADD COLUMN distribucion_subperiodos TEXT DEFAULT NULL;")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        cursor.execute("PRAGMA user_version = 9;")
        current_version = 9

    # -------------------------------------------------------------
    # Versión 10: Día de cargo en tarjetas de presupuesto (general y por sub-período)
    # -------------------------------------------------------------
    if current_version < 10 and CURRENT_TENANT_SCHEMA_VERSION >= 10:
        try:
            cursor.execute("ALTER TABLE presupuesto_tarjetas ADD COLUMN dia_pago INTEGER DEFAULT NULL;")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        cursor.execute("PRAGMA user_version = 10;")
        current_version = 10

    # -------------------------------------------------------------
    # Versión 11: Día de cobro en configuración de ingreso fijo base
    # -------------------------------------------------------------
    if current_version < 11 and CURRENT_TENANT_SCHEMA_VERSION >= 11:
        try:
            cursor.execute("ALTER TABLE presupuesto_config ADD COLUMN dia_pago INTEGER DEFAULT NULL;")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        cursor.execute("PRAGMA user_version = 11;")
        current_version = 11

    # -------------------------------------------------------------
    # Versión 12: Enlace de Fijos/MSI con Tarjetas de Presupuesto
    # -------------------------------------------------------------
    if current_version < 12 and CURRENT_TENANT_SCHEMA_VERSION >= 12:
        try:
            cursor.execute("ALTER TABLE recurrentes ADD COLUMN presupuesto_tarjeta_id INTEGER REFERENCES presupuesto_tarjetas(id) ON DELETE SET NULL;")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recurrentes_tarjeta ON recurrentes(presupuesto_tarjeta_id);")
        cursor.execute("PRAGMA user_version = 12;")
        current_version = 12

    # -------------------------------------------------------------
    # Versión 13: Rubro del movimiento guardado en la propia transacción.
    # Antes el rubro (fijo, MSI, normal, meta) se deducía del enlace a recurrentes, que al borrar el
    # fijo pasa a NULL y movía el cargo a "normales" en el historial de periodos.
    # -------------------------------------------------------------
    if current_version < 13 and CURRENT_TENANT_SCHEMA_VERSION >= 13:
        try:
            cursor.execute("ALTER TABLE transacciones ADD COLUMN rubro TEXT;")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        cursor.execute("UPDATE transacciones SET rubro = 'metas' WHERE rubro IS NULL AND apartado_id IS NOT NULL;")
        cursor.execute("UPDATE transacciones SET rubro = 'ingreso' WHERE rubro IS NULL AND tipo = 'ingreso';")
        cursor.execute("""
            UPDATE transacciones SET rubro = 'msi'
            WHERE rubro IS NULL AND recurrente_id IN (SELECT id FROM recurrentes WHERE total_cuotas IS NOT NULL);
        """)
        cursor.execute("UPDATE transacciones SET rubro = 'fijos' WHERE rubro IS NULL AND recurrente_id IS NOT NULL;")
        cursor.execute("UPDATE transacciones SET rubro = 'normales' WHERE rubro IS NULL;")
        cursor.execute("PRAGMA user_version = 13;")
        current_version = 13

    # -------------------------------------------------------------
    # Versión 14: Unidades de pago del presupuesto. Hay cuatro filas fijas (semana 1 a 4, o quincena 1 y 2,
    # o el mes); la frecuencia del ingreso decide cuántas están activas. Cada tarjeta puede pertenecer a
    # una unidad (unidad_orden); las tarjetas sin unidad forman el tablero general, como antes.
    # -------------------------------------------------------------
    if current_version < 14 and CURRENT_TENANT_SCHEMA_VERSION >= 14:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS presupuesto_unidades (
                orden INTEGER PRIMARY KEY CHECK (orden BETWEEN 1 AND 4),
                dia_pago INTEGER CHECK (dia_pago BETWEEN 1 AND 31)
            );
        """)
        cursor.execute("INSERT OR IGNORE INTO presupuesto_unidades (orden, dia_pago) VALUES (1, NULL), (2, NULL), (3, NULL), (4, NULL);")
        try:
            cursor.execute("ALTER TABLE presupuesto_tarjetas ADD COLUMN unidad_orden INTEGER REFERENCES presupuesto_unidades(orden) ON DELETE SET NULL;")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_presupuesto_unidad ON presupuesto_tarjetas(unidad_orden);")
        cursor.execute("PRAGMA user_version = 14;")
        current_version = 14

    # -------------------------------------------------------------
    # Versión 15: sin tablero general. Cada tarjeta pertenece a un pago; las que quedaban fuera pasan
    # al pago 1 (con el día del ingreso fijo si el pago aún no tiene fecha). La subdivisión del
    # presupuesto desaparece: cada pago reparte su propio ingreso.
    # -------------------------------------------------------------
    if current_version < 15 and CURRENT_TENANT_SCHEMA_VERSION >= 15:
        cursor.execute("""
            UPDATE presupuesto_unidades SET dia_pago = (SELECT dia_pago FROM presupuesto_config WHERE id = 1)
            WHERE orden = 1 AND dia_pago IS NULL
              AND EXISTS (SELECT 1 FROM presupuesto_tarjetas WHERE unidad_orden IS NULL)
              AND (SELECT dia_pago FROM presupuesto_config WHERE id = 1) IS NOT NULL;
        """)
        cursor.execute("UPDATE presupuesto_tarjetas SET unidad_orden = 1, distribucion_subperiodos = NULL WHERE unidad_orden IS NULL;")
        cursor.execute("UPDATE presupuesto_config SET subdivision_tipo = 'ninguna', subdivision_config = '[]' WHERE id = 1;")
        cursor.execute("PRAGMA user_version = 15;")
        current_version = 15

    # -------------------------------------------------------------
    # Versión 16: Compras a meses sin intereses dentro del presupuesto.
    # tipo 'msi' marca una tarjeta cuyo monto es la cuota mensual; msi_cuotas es su plazo.
    # Al exportar se convierte en una compra a cuotas (recurrente con total_cuotas).
    # -------------------------------------------------------------
    if current_version < 16 and CURRENT_TENANT_SCHEMA_VERSION >= 16:
        try:
            cursor.execute("ALTER TABLE presupuesto_tarjetas ADD COLUMN tipo TEXT NOT NULL DEFAULT 'normal' CHECK (tipo IN ('normal', 'msi'));")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        try:
            cursor.execute("ALTER TABLE presupuesto_tarjetas ADD COLUMN msi_cuotas INTEGER CHECK (msi_cuotas IS NULL OR msi_cuotas BETWEEN 2 AND 60);")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        cursor.execute("PRAGMA user_version = 16;")
        current_version = 16

    if current_version < 17 and CURRENT_TENANT_SCHEMA_VERSION >= 17:
        # Fijos quincenales con una fecha que no es día 15 ni fin de mes (p. ej. día 30 en octubre) generaban
        # dos cargos en la misma quincena. Se llevan al primer día de cobro de su quincena.
        import calendar
        from datetime import date as _date
        for fila in cursor.execute("SELECT id, proxima_fecha FROM recurrentes WHERE frecuencia = 'quincenal'").fetchall():
            if not fila["proxima_fecha"]:
                continue
            actual = _date.fromisoformat(fila["proxima_fecha"])
            ultimo = calendar.monthrange(actual.year, actual.month)[1]
            if actual.day in (15, ultimo):
                continue
            nueva = _date(actual.year, actual.month, 15) if actual.day < 15 else _date(actual.year, actual.month, ultimo)
            cursor.execute(
                "UPDATE recurrentes SET proxima_fecha = ?, dia = ? WHERE id = ?",
                (nueva.isoformat(), nueva.day, fila["id"]),
            )
        cursor.execute("PRAGMA user_version = 17;")
        current_version = 17

    # Versión 18: versión por movimiento. Cada edición sube el contador y una edición con una versión vieja
    # (otra pestaña la cambió antes) se rechaza en vez de pisar el cambio. Se verifica con PRAGMA table_info,
    # sin try/except, para que un error real de SQL no quede oculto.
    if current_version < 18 and CURRENT_TENANT_SCHEMA_VERSION >= 18:
        columnas = {fila[1] for fila in cursor.execute("PRAGMA table_info(transacciones);").fetchall()}
        if "version" not in columnas:
            cursor.execute("ALTER TABLE transacciones ADD COLUMN version INTEGER NOT NULL DEFAULT 1;")
        cursor.execute("PRAGMA user_version = 18;")
        current_version = 18

    # Versión 19: repara fechas imposibles de los movimientos. Antes de validar la fecha real, la API aceptaba
    # cualquier AAAA-MM-DD (2026-11-31, mes 13, día 00) y esa fila hacía fallar /api/resumen en cada carga.
    # Se recorta a la fecha válida más cercana y se deja la original en la descripción. Las fechas reales
    # (incluidas las muy lejanas) no se tocan. Idempotente: una fila reparada ya no se vuelve a seleccionar.
    if current_version < 19 and CURRENT_TENANT_SCHEMA_VERSION >= 19:
        sospechosas = cursor.execute(
            "SELECT id, fecha, descripcion FROM transacciones "
            "WHERE fecha GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND strftime('%Y-%m-%d', fecha) IS NOT fecha"
        ).fetchall()
        for fila in sospechosas:
            reparada = _fecha_valida_mas_cercana(fila[1])
            if reparada is None or reparada == fila[1]:
                continue
            nota = f"(fecha original {fila[1]})"
            descripcion = f"{fila[2]} {nota}" if fila[2] else nota
            cursor.execute("UPDATE transacciones SET fecha = ?, descripcion = ? WHERE id = ?", (reparada, descripcion, fila[0]))
        cursor.execute("PRAGMA user_version = 19;")
        current_version = 19

    # Versión 20: la tarjeta del presupuesto se vincula a su meta por id y no por nombre. Al exportar, el
    # nombre exacto unía una tarjeta con una meta sin que nadie lo decidiera, y renombrar cualquiera de las
    # dos rompía el aporte. Las tarjetas que hoy coinciden por nombre conservan su meta.
    if current_version < 20 and CURRENT_TENANT_SCHEMA_VERSION >= 20:
        try:
            cursor.execute("ALTER TABLE presupuesto_tarjetas ADD COLUMN apartado_id INTEGER REFERENCES apartados(id) ON DELETE SET NULL;")
        except sqlite3.OperationalError as error:
            _tolerar_columna_existente(error)
        cursor.execute("""
            UPDATE presupuesto_tarjetas
            SET apartado_id = (
                SELECT a.id FROM apartados a
                WHERE LOWER(TRIM(a.nombre)) = LOWER(TRIM(presupuesto_tarjetas.nombre))
                ORDER BY a.id LIMIT 1
            )
            WHERE apartado_id IS NULL
        """)
        cursor.execute("PRAGMA user_version = 20;")
        current_version = 20

    # -------------------------------------------------------------
    # Versión 21: el dinero se guarda en centavos enteros (INTEGER) en vez de pesos decimales (REAL)
    # -------------------------------------------------------------
    if current_version < 21 and CURRENT_TENANT_SCHEMA_VERSION >= 21:
        _pasar_dinero_a_centavos(cursor)
        cursor.execute("PRAGMA user_version = 21;")
        current_version = 21

    # -------------------------------------------------------------
    # Versión 22: movimientos cancelados. «Cancelar operación» saca el movimiento de `transacciones` (el dinero
    # vuelve al saldo, igual que al eliminarlo) pero deja una copia aquí para mostrarlo como cancelado.
    # Sin llaves foráneas: la copia debe sobrevivir si luego se borra la categoría, el fijo o la meta.
    # -------------------------------------------------------------
    if current_version < 22 and CURRENT_TENANT_SCHEMA_VERSION >= 22:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transacciones_canceladas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_original INTEGER,
                fecha TEXT NOT NULL,
                monto INTEGER NOT NULL,
                tipo TEXT NOT NULL,
                categoria_id INTEGER,
                descripcion TEXT,
                recurrente_id INTEGER,
                apartado_id INTEGER,
                rubro TEXT,
                cancelado_en TEXT NOT NULL
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trans_canceladas_fecha ON transacciones_canceladas(fecha);")
        cursor.execute("PRAGMA user_version = 22;")
        current_version = 22

    if current_version < 23 and CURRENT_TENANT_SCHEMA_VERSION >= 23:
        # Avisos de la app (aportación omitida, meta cumplida...). `clave` evita repetir el mismo aviso por el mismo hecho.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notificaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo TEXT NOT NULL,
                clave TEXT NOT NULL UNIQUE,
                titulo TEXT NOT NULL,
                texto TEXT NOT NULL,
                creada_en TEXT NOT NULL,
                leida INTEGER NOT NULL DEFAULT 0
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_notificaciones_leida ON notificaciones(leida, id);")
        cursor.execute("PRAGMA user_version = 23;")
        current_version = 23

    if current_version < 24 and CURRENT_TENANT_SCHEMA_VERSION >= 24:
        # Entrega de cada aviso por Telegram: 0 pendiente, 1 reclamado por el bot (con su hora), 2 entregado. Los fallos se cuentan para no reintentar sin fin.
        for columna in ("entrega_telegram INTEGER NOT NULL DEFAULT 0", "intentos_telegram INTEGER NOT NULL DEFAULT 0", "reclamado_en TEXT"):
            try:
                cursor.execute(f"ALTER TABLE notificaciones ADD COLUMN {columna};")
            except sqlite3.OperationalError as error:
                _tolerar_columna_existente(error)
        cursor.execute("PRAGMA user_version = 24;")
        current_version = 24

    conn.commit()


# =============================================================================
# 5. CONEXIONES POR PETICIÓN
# =============================================================================

_tenants_migrados = set()
_lock_migraciones = threading.Lock()

PREMIGRACION_DIR = os.path.join(DATA_DIR, "backups_premigracion")
PREMIGRACION_CONSERVAR = 3


def respaldar_antes_de_migrar(conn: sqlite3.Connection, db_path: str) -> Optional[str]:
    """Copia consistente del tenant antes de subir su esquema. Solo si ya tiene datos (user_version > 0)
    y está desactualizado. Conserva los últimos PREMIGRACION_CONSERVAR respaldos por tenant."""
    version = conn.execute("PRAGMA user_version;").fetchone()[0]
    if version <= 0 or version >= CURRENT_TENANT_SCHEMA_VERSION:
        return None
    os.makedirs(PREMIGRACION_DIR, exist_ok=True)
    base = os.path.splitext(os.path.basename(db_path))[0]
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = os.path.join(PREMIGRACION_DIR, f"{base}.v{version}.{sello}.bak")
    respaldo = sqlite3.connect(destino)
    try:
        conn.backup(respaldo)
    finally:
        respaldo.close()
    previos = sorted(
        (os.path.join(PREMIGRACION_DIR, f) for f in os.listdir(PREMIGRACION_DIR) if f.startswith(f"{base}.v")),
        key=os.path.getmtime,
    )
    for viejo in previos[:-PREMIGRACION_CONSERVAR]:
        os.remove(viejo)
    return destino


def get_tenant_conn(db_name_or_path: str) -> sqlite3.Connection:
    """Abre una conexión nueva por petición. Las migraciones corren una sola vez por proceso y tenant."""
    db_path = get_tenant_path(db_name_or_path)
    ensure_tenant_directories()
    conn = sqlite3.connect(db_path, timeout=15.0, check_same_thread=False)
    conn.row_factory = fabrica_filas
    configure_sqlite_pragmas(conn)
    if db_path not in _tenants_migrados:
        with _lock_migraciones:
            if db_path not in _tenants_migrados:
                respaldar_antes_de_migrar(conn, db_path)
                apply_lazy_migrations(conn)
                _tenants_migrados.add(db_path)
    return conn


def olvidar_tenant(db_name_or_path: str) -> None:
    """Fuerza re-verificar migraciones (tras restaurar o borrar un tenant)."""
    _tenants_migrados.discard(get_tenant_path(db_name_or_path))


# =============================================================================
# 6. APROVISIONAMIENTO Y MIGRACIÓN INICIAL DE TENANTS
# =============================================================================

def init_tenant_db(db_path: str) -> None:
    """
    Inicializa una base de datos tenant aplicando los PRAGMAs obligatorios
    y las migraciones del esquema financiero completo.
    """
    ensure_tenant_directories()
    conn = sqlite3.connect(db_path, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        configure_sqlite_pragmas(conn)
        apply_lazy_migrations(conn)
    finally:
        conn.close()

def provision_tenant_db(user_id: int) -> Tuple[str, str]:
    """
    Aprovisiona de forma automática la base de datos SQLite independiente de un usuario:
    1. Define el archivo 'user_{user_id}.db' en 'data/tenants/'.
    2. Aplica PRAGMAs y migraciones iniciales.
    3. Retorna (db_name, db_path).
    """
    db_name = f"user_{user_id}.db"
    db_path = get_tenant_path_by_id(user_id)
    init_tenant_db(db_path)
    return db_name, db_path


# =============================================================================
# 7. RESPALDOS EN CALIENTE CON VACUUM INTO / BACKUP API NATIVA
# =============================================================================

def backup_tenant_db_hot(db_name_or_path: str, dest_path: str, prefer_vacuum: bool = True) -> str:
    """
    Ejecuta un respaldo consistente en caliente de la base de datos SQLite del tenant.
    Utiliza exclusivamente VACUUM INTO o la API nativa de backup de SQLite (conn.backup),
    lo que consolida las páginas activas del archivo Write-Ahead Log (WAL) y garantiza
    la integridad de los datos sin corromper lecturas ni escrituras concurrentes.
    """
    db_path = get_tenant_path(db_name_or_path)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Base de datos tenant no encontrada: {db_path}")

    # Si el destino previo existe, eliminarlo antes de VACUUM INTO
    if os.path.exists(dest_path):
        try:
            os.unlink(dest_path)
        except Exception:
            pass

    # Usamos una conexión transaccional con PRAGMAs estándar
    conn = sqlite3.connect(db_path, timeout=15.0, check_same_thread=False)
    configure_sqlite_pragmas(conn)
    try:
        if prefer_vacuum:
            try:
                # VACUUM INTO genera un archivo nuevo, compacto y consistente en caliente
                conn.execute("VACUUM INTO ?", (dest_path,))
                return dest_path
            except Exception:
                # Fallback seguro a la API nativa si VACUUM INTO no está disponible en la versión
                if os.path.exists(dest_path):
                    try:
                        os.unlink(dest_path)
                    except Exception:
                        pass

        # API nativa de respaldo de SQLite (sqlite3.Connection.backup)
        dst = sqlite3.connect(dest_path, timeout=5.0)
        try:
            conn.backup(dst)
        finally:
            dst.close()
    finally:
        conn.close()
        
    return dest_path
