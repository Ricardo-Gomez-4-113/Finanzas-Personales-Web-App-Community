"""tests/test_fijos_metas.py - Un gasto fijo ligado a una meta: qué hace al cumplirse la fecha, al cancelarla, saltarla o borrar la meta.

Sin red ni navegador: bases SQLite en memoria con las migraciones reales. El oráculo es el SQL de los movimientos, no el saldo guardado de la meta.

Tres reglas de producto están decididas (plan-auditoria-asistente-y-core.md, sección 6). Dos ya se cumplen (sin saldo no se aporta, y al cumplirse
la meta se quita el fijo); la tercera (borrar una meta con fijo ligado) aún no y queda escrita como
`brecha_*` (sin prefijo test_, no rompen la corrida) y `test_las_brechas_siguen_abiertas` exige que cada una siga fallando: el día que
se arregle una, esa prueba lo avisa y se le quita el prefijo `brecha_` para que pase a ser una prueba normal.
"""

import os
import sqlite3
import sys
from datetime import date

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("JWT_SECRET_KEY", "k" * 48)

from fastapi import HTTPException  # noqa: E402

from database import apply_lazy_migrations, configure_sqlite_pragmas  # noqa: E402
from dinero import a_centavos, fabrica_filas  # noqa: E402
from finanzas_core import (  # noqa: E402
    archivar_movimiento_cancelado,
    balance_disponible_a_fecha,
    deshacer_efectos_de_movimiento,
    exportar_presupuesto_a_fijos_core,
    materializar_recurrentes,
    obtener_total_apartados,
    saltar_recurrente_core,
)
from routers.apartados import eliminar_apartado  # noqa: E402
from routers.recurrentes import RecurrenteUpdate, editar_recurrente, eliminar_recurrente  # noqa: E402


def _bd():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = fabrica_filas
    configure_sqlite_pragmas(conn)
    apply_lazy_migrations(conn)
    return conn


def _filas(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def _ingreso(conn, monto, fecha="2026-01-01"):
    conn.execute("INSERT INTO transacciones (fecha, monto, tipo, categoria_id) VALUES (?, ?, 'ingreso', 1)", (fecha, a_centavos(monto)))
    conn.commit()


def _meta(conn, objetivo=1000.0, nombre="Emergencia"):
    conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual, meta_monto) VALUES (?, 'meta', 0, ?)", (nombre, a_centavos(objetivo)))
    conn.commit()
    return _filas(conn, "SELECT id FROM apartados WHERE nombre = ?", nombre)[0]["id"]


def _fijo(conn, apartado, monto, proxima="2026-02-01", frecuencia="mensual", dia=1):
    conn.execute(
        "INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, categoria_id, apartado_id, activo) "
        "VALUES ('Ahorro', 'gasto', ?, ?, ?, ?, 1, ?, 1)", (a_centavos(monto), frecuencia, dia, proxima, apartado))
    conn.commit()
    return _filas(conn, "SELECT id FROM recurrentes WHERE descripcion = 'Ahorro'")[0]["id"]


def _guardado(conn, apartado):
    """Saldo guardado de la meta, en pesos."""
    return _filas(conn, "SELECT monto_actual / 100.0 AS m FROM apartados WHERE id = ?", apartado)[0]["m"]


def _aportaciones(conn):
    return _filas(conn, "SELECT * FROM transacciones WHERE recurrente_id IS NOT NULL ORDER BY fecha")


# ---------------------------------------------------------------- lo que ya se cumple

def test_la_aportacion_automatica_es_un_gasto_de_la_meta_y_cuadra_con_su_saldo():
    conn = _bd()
    _ingreso(conn, 10000)
    meta = _meta(conn, 5000)
    _fijo(conn, meta, 400)
    assert materializar_recurrentes(conn, date(2026, 4, 15)) == 3   # feb, mar y abr
    for t in _aportaciones(conn):
        assert t["tipo"] == "gasto" and t["apartado_id"] == meta and t["rubro"] == "metas", dict(t)
    assert _guardado(conn, meta) == 1200.0
    assert obtener_total_apartados(conn.cursor()) == 1200.0   # lo que salió de la cuenta es lo mismo que lo guardado
    assert balance_disponible_a_fecha(conn.cursor(), "2026-04-15") == 8800.0


def test_tras_una_ausencia_larga_se_crea_una_aportacion_por_fecha_y_repetir_no_duplica():
    conn = _bd()
    _ingreso(conn, 100000)
    meta = _meta(conn, 100000)
    _fijo(conn, meta, 400, proxima="2026-01-05", frecuencia="semanal", dia=None)
    assert materializar_recurrentes(conn, date(2026, 3, 6)) == 9
    assert materializar_recurrentes(conn, date(2026, 3, 6)) == 0
    fechas = [t["fecha"] for t in _aportaciones(conn)]
    assert len(fechas) == len(set(fechas)) == 9 and fechas[0] == "2026-01-05" and fechas[-1] == "2026-03-02", fechas
    assert _guardado(conn, meta) == 3600.0


def test_cancelar_una_aportacion_la_saca_de_la_meta_y_no_se_vuelve_a_crear():
    conn = _bd()
    _ingreso(conn, 10000)
    meta = _meta(conn, 5000)
    _fijo(conn, meta, 400)
    materializar_recurrentes(conn, date(2026, 3, 15))
    ultima = _aportaciones(conn)[-1]
    cursor = conn.cursor()
    deshacer_efectos_de_movimiento(cursor, ultima)
    archivar_movimiento_cancelado(cursor, ultima)
    cursor.execute("DELETE FROM transacciones WHERE id = ?", (ultima["id"],))
    conn.commit()
    assert _guardado(conn, meta) == 400.0 and obtener_total_apartados(conn.cursor()) == 400.0
    assert materializar_recurrentes(conn, date(2026, 3, 15)) == 0   # esa fecha ya pasó: no resurge sola


def test_no_se_cancela_una_aportacion_si_ese_dinero_ya_salio_de_la_meta():
    conn = _bd()
    _ingreso(conn, 10000)
    meta = _meta(conn, 5000)
    _fijo(conn, meta, 400)
    materializar_recurrentes(conn, date(2026, 2, 15))
    conn.execute("UPDATE apartados SET monto_actual = 100 WHERE id = ?", (meta,))   # solo quedan 1.00 de los 4.00 aportados
    conn.commit()
    try:
        deshacer_efectos_de_movimiento(conn.cursor(), _aportaciones(conn)[0])
    except ValueError as error:
        assert "ya se retiró" in str(error)
    else:
        raise AssertionError("debe rechazar revertir más de lo que la meta tiene")
    assert _guardado(conn, meta) == 1.0   # y no recortó nada en silencio


def test_saltar_un_ciclo_no_aporta_ni_duplica_al_ponerse_al_dia():
    conn = _bd()
    _ingreso(conn, 10000)
    meta = _meta(conn, 5000)
    fijo = _fijo(conn, meta, 400)
    info = saltar_recurrente_core(conn.cursor(), fijo, hoy=date(2026, 2, 1))
    conn.commit()
    assert info["fecha_anterior"] == "2026-02-01" and info["nueva_proxima_fecha"] == "2026-03-01", info
    assert _aportaciones(conn) == [] and _guardado(conn, meta) == 0.0
    assert materializar_recurrentes(conn, date(2026, 3, 15)) == 1
    assert [t["fecha"] for t in _aportaciones(conn)] == ["2026-03-01"] and _guardado(conn, meta) == 400.0


# ---------------------------------------------------------------- cruces con presupuesto, ingreso fijo y respaldo

def _ingreso_fijo(conn, monto, frecuencia="quincenal", dia=15, proxima="2026-02-15"):
    conn.execute(
        "INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, categoria_id, activo) "
        "VALUES ('Nómina', 'ingreso', ?, ?, ?, ?, 1, 1)", (a_centavos(monto), frecuencia, dia, proxima))
    conn.commit()
    return _filas(conn, "SELECT id FROM recurrentes WHERE descripcion = 'Nómina'")[0]["id"]


def _presupuesto_con_tarjeta_a_meta(conn, meta):
    conn.execute("UPDATE presupuesto_config SET ingreso_base = 3000000, frecuencia = 'mensual', dia_pago = 1 WHERE id = 1")
    conn.execute(
        "INSERT INTO presupuesto_tarjetas (nombre, icono, color, porcentaje, monto, dia_pago, apartado_id) "
        "VALUES ('Ahorro casa', 'credit-card', 'purple', 10.0, 400000, 5, ?)", (meta,))
    conn.commit()
    return _filas(conn, "SELECT id FROM presupuesto_tarjetas WHERE nombre = 'Ahorro casa'")[0]["id"]


def test_una_tarjeta_ligada_a_una_meta_exporta_un_fijo_que_aporta_y_exportar_de_nuevo_no_duplica():
    conn = _bd()
    meta = _meta(conn, 20000)   # con un objetivo mayor que dos aportaciones: la meta no se cumple a mitad de la prueba
    _presupuesto_con_tarjeta_a_meta(conn, meta)
    exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 1))
    conn.commit()
    fijo = _filas(conn, "SELECT * FROM recurrentes WHERE descripcion = 'Ahorro casa'")
    assert len(fijo) == 1 and fijo[0]["apartado_id"] == meta and fijo[0]["monto"] == 4000.0, dict(fijo[0])
    materializar_recurrentes(conn, date(2026, 11, 10))
    assert [t["fecha"] for t in _aportaciones(conn) if t["apartado_id"] == meta] == ["2026-10-05", "2026-11-05"]
    assert _guardado(conn, meta) == 8000.0
    exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 11, 10))
    conn.commit()
    assert [r["id"] for r in _filas(conn, "SELECT id FROM recurrentes WHERE descripcion = 'Ahorro casa'")] == [fijo[0]["id"]]
    assert materializar_recurrentes(conn, date(2026, 11, 10)) == 0   # nada se cobra dos veces tras reexportar
    assert _guardado(conn, meta) == 8000.0 and obtener_total_apartados(conn.cursor()) == 8000.0


def test_desligar_la_meta_de_la_tarjeta_y_reexportar_actualiza_el_mismo_fijo_sin_aportar_mas():
    conn = _bd()
    meta = _meta(conn, 5000)
    tarjeta = _presupuesto_con_tarjeta_a_meta(conn, meta)
    exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 1))
    materializar_recurrentes(conn, date(2026, 10, 10))
    conn.execute("UPDATE presupuesto_tarjetas SET apartado_id = NULL WHERE id = ?", (tarjeta,))
    conn.commit()
    exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 10, 10))
    conn.commit()
    fijos = _filas(conn, "SELECT id, apartado_id FROM recurrentes WHERE descripcion = 'Ahorro casa'")
    assert len(fijos) == 1 and fijos[0]["apartado_id"] is None, [dict(f) for f in fijos]
    materializar_recurrentes(conn, date(2026, 11, 10))
    assert _guardado(conn, meta) == 4000.0   # lo aportado en octubre se queda; noviembre ya es un gasto normal
    nov = _filas(conn, "SELECT * FROM transacciones WHERE fecha = '2026-11-05' AND descripcion = 'Ahorro casa'")
    assert len(nov) == 1 and nov[0]["apartado_id"] is None and nov[0]["rubro"] == "fijos"


def test_exportar_el_presupuesto_avisa_que_borra_un_aporte_a_meta_hecho_a_mano_y_conserva_lo_aportado():
    conn = _bd()
    _ingreso(conn, 10000)
    meta = _meta(conn, 5000)
    _fijo(conn, meta, 400)
    materializar_recurrentes(conn, date(2026, 3, 15))
    conn.execute("UPDATE presupuesto_config SET ingreso_base = 3000000, frecuencia = 'mensual', dia_pago = 1 WHERE id = 1")
    conn.commit()
    resultado = exportar_presupuesto_a_fijos_core(conn.cursor(), fecha_base=date(2026, 3, 15))
    conn.commit()
    borrado = [d for d in resultado["detalles"] if d["accion"] == "eliminado" and d["descripcion"] == "Ahorro"]
    assert len(borrado) == 1 and borrado[0]["origen"] == "meta", resultado["detalles"]   # la vista previa lo distingue de un fijo cualquiera
    assert _filas(conn, "SELECT COUNT(*) AS n FROM recurrentes WHERE descripcion = 'Ahorro'")[0]["n"] == 0
    assert len(_aportaciones(conn)) == 0 and _guardado(conn, meta) == 800.0   # los movimientos se conservan, ya sin fijo
    assert len(_filas(conn, "SELECT id FROM transacciones WHERE apartado_id = ?", meta)) == 2


def test_no_se_elimina_el_unico_ingreso_fijo_si_hay_un_aporte_a_una_meta_que_depende_de_el():
    conn = _bd()
    meta = _meta(conn, 5000)
    nomina = _ingreso_fijo(conn, 1000)
    _fijo(conn, meta, 400)
    try:
        eliminar_recurrente(nomina, conn)
    except HTTPException as error:
        assert error.status_code == 422
    else:
        raise AssertionError("dejó un aporte a meta sin ingreso fijo que lo respalde")
    assert len(_filas(conn, "SELECT id FROM recurrentes WHERE tipo = 'ingreso'")) == 1


def test_con_dos_ingresos_fijos_solo_se_elimina_uno_si_el_otro_cubre_el_aporte_a_la_meta():
    conn = _bd()
    meta = _meta(conn, 5000)
    chico = _ingreso_fijo(conn, 100, frecuencia="mensual", dia=1, proxima="2026-03-01")
    conn.execute("INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, categoria_id, activo) "
                 "VALUES ('Extra', 'ingreso', ?, 'mensual', 1, '2026-03-01', 1, 1)", (a_centavos(1000),))
    conn.commit()
    _fijo(conn, meta, 400)
    eliminar_recurrente(chico, conn)   # el que queda (1000/mes) cubre los 400
    extra = _filas(conn, "SELECT id FROM recurrentes WHERE descripcion = 'Extra'")[0]["id"]
    try:
        eliminar_recurrente(extra, conn)
    except HTTPException as error:
        assert error.status_code == 422
    else:
        raise AssertionError("eliminó el último ingreso con un aporte a meta activo")


def test_pasar_el_ingreso_de_quincenal_a_mensual_se_rechaza_si_ya_no_cubre_el_aporte_a_la_meta():
    conn = _bd()
    meta = _meta(conn, 5000)
    nomina = _ingreso_fijo(conn, 1000)   # quincenal: 2000 al mes
    conn.execute("INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, categoria_id, activo) "
                 "VALUES ('Renta', 'gasto', ?, 'mensual', 1, '2026-03-01', 1, 1)", (a_centavos(1500),))
    conn.commit()
    _fijo(conn, meta, 400)   # 1500 + 400 = 1900 de 2000
    try:
        editar_recurrente(nomina, RecurrenteUpdate(frecuencia="mensual"), conn)   # 1000 al mes ya no alcanza
    except HTTPException as error:
        assert error.status_code == 422
    else:
        raise AssertionError("aceptó un ingreso que ya no cubre los gastos fijos con su aporte a meta")
    assert _filas(conn, "SELECT frecuencia FROM recurrentes WHERE id = ?", nomina)[0]["frecuencia"] == "quincenal"


def test_restaurar_una_copia_vieja_y_ponerse_al_dia_da_el_mismo_estado_que_no_haber_salido():
    original = _bd()
    _ingreso(original, 100000)
    meta = _meta(original, 100000)
    _fijo(original, meta, 400, proxima="2026-01-05", frecuencia="semanal", dia=None)
    materializar_recurrentes(original, date(2026, 2, 1))
    copia = sqlite3.connect(":memory:")
    original.backup(copia)   # la copia de seguridad, a mitad de ciclo
    copia.row_factory = fabrica_filas
    materializar_recurrentes(original, date(2026, 4, 1))
    materializar_recurrentes(copia, date(2026, 4, 1))   # restaurada después, al abrir la app
    consulta = "SELECT fecha, monto, apartado_id, rubro FROM transacciones ORDER BY fecha, id"
    assert [tuple(f) for f in _filas(copia, consulta)] == [tuple(f) for f in _filas(original, consulta)]
    semanas = (date(2026, 4, 1) - date(2026, 1, 5)).days // 7 + 1   # lunes 5 de enero hasta el 1 de abril
    assert _guardado(copia, meta) == _guardado(original, meta) == 400.0 * semanas
    assert obtener_total_apartados(copia.cursor()) == 400.0 * semanas


# ---------------------------------------------------------------- decididas, aún sin cumplirse (ver la cabecera)

def test_sin_saldo_la_aportacion_se_omite_con_aviso_y_el_ciclo_avanza():
    """Decisión 1: igual que el depósito manual («No puedes guardar dinero que no tienes»)."""
    conn = _bd()
    _ingreso(conn, 100)
    meta = _meta(conn)
    fijo = _fijo(conn, meta, 500)
    assert materializar_recurrentes(conn, date(2026, 3, 15)) == 0
    assert balance_disponible_a_fecha(conn.cursor(), "2026-03-15") == 100.0 and _guardado(conn, meta) == 0.0
    avisos = _filas(conn, "SELECT tipo, clave, titulo, texto FROM notificaciones ORDER BY id")
    assert [a["clave"] for a in avisos] == [f"aporte_omitido:{fijo}:2026-02-01", f"aporte_omitido:{fijo}:2026-03-01"], [dict(a) for a in avisos]
    assert "Emergencia" in avisos[0]["texto"] and "no había saldo" in avisos[0]["texto"]
    assert _filas(conn, "SELECT proxima_fecha FROM recurrentes")[0]["proxima_fecha"] == "2026-04-01"   # no se queda trabado en un ciclo viejo
    assert materializar_recurrentes(conn, date(2026, 3, 15)) == 0
    assert _filas(conn, "SELECT COUNT(*) AS n FROM notificaciones")[0]["n"] == 2   # repetir no vuelve a avisar


def test_con_saldo_solo_para_algunos_ciclos_aporta_los_que_caben_en_su_fecha():
    conn = _bd()
    _ingreso(conn, 700)   # alcanza para uno de 500, no para el segundo
    meta = _meta(conn, 5000)
    _fijo(conn, meta, 500)
    assert materializar_recurrentes(conn, date(2026, 3, 15)) == 1
    assert [t["fecha"] for t in _aportaciones(conn)] == ["2026-02-01"] and _guardado(conn, meta) == 500.0
    assert balance_disponible_a_fecha(conn.cursor(), "2026-03-15") == 200.0
    assert _filas(conn, "SELECT COUNT(*) AS n FROM notificaciones WHERE tipo = 'aporte_omitido'")[0]["n"] == 1


def test_el_ingreso_del_mismo_dia_se_cuenta_antes_que_el_aporte_aunque_el_fijo_se_creara_despues():
    conn = _bd()
    meta = _meta(conn, 5000)
    _fijo(conn, meta, 400, proxima="2026-02-01")   # el aporte tiene el id menor
    _ingreso_fijo(conn, 1000, frecuencia="mensual", dia=1, proxima="2026-02-01")
    assert materializar_recurrentes(conn, date(2026, 2, 1)) == 2   # entra la nómina y luego sale el aporte
    assert _guardado(conn, meta) == 400.0 and _filas(conn, "SELECT COUNT(*) AS n FROM notificaciones")[0]["n"] == 0


def test_los_gastos_normales_se_pagan_antes_que_el_ahorro():
    conn = _bd()
    _ingreso(conn, 1000)
    meta = _meta(conn, 5000)
    _fijo(conn, meta, 800, proxima="2026-02-01")
    conn.execute("INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, categoria_id, activo) "
                 "VALUES ('Renta', 'gasto', ?, 'mensual', 1, '2026-02-01', 1, 1)", (a_centavos(600),))
    conn.commit()
    materializar_recurrentes(conn, date(2026, 2, 1))
    assert [t["descripcion"] for t in _filas(conn, "SELECT descripcion FROM transacciones WHERE recurrente_id IS NOT NULL")] == ["Renta"]
    assert _guardado(conn, meta) == 0.0   # la renta salió y el ahorro de 800 ya no cabía


def test_al_alcanzar_el_objetivo_la_ultima_aportacion_se_recorta_el_fijo_se_quita_y_se_felicita():
    """Decisión 2: 400 + 400 + 200, el fijo desaparece y los movimientos se conservan."""
    conn = _bd()
    _ingreso(conn, 100000)
    meta = _meta(conn, 1000)
    _fijo(conn, meta, 400)
    assert materializar_recurrentes(conn, date(2026, 6, 15)) == 3
    assert _guardado(conn, meta) == 1000.0 and obtener_total_apartados(conn.cursor()) == 1000.0
    assert [t["monto"] for t in _aportaciones(conn)] == []   # sin enlace al fijo borrado...
    assert [t["monto"] for t in _filas(conn, "SELECT monto FROM transacciones WHERE apartado_id = ? ORDER BY fecha", meta)] == [400.0, 400.0, 200.0]
    assert _filas(conn, "SELECT COUNT(*) AS n FROM recurrentes")[0]["n"] == 0
    aviso = _filas(conn, "SELECT tipo, titulo, texto FROM notificaciones")
    assert len(aviso) == 1 and aviso[0]["tipo"] == "meta_cumplida" and "Felicidades" in aviso[0]["texto"] and "Ahorro" in aviso[0]["texto"], [dict(a) for a in aviso]
    assert materializar_recurrentes(conn, date(2026, 7, 15)) == 0 and _guardado(conn, meta) == 1000.0   # y no sigue aportando después


def test_una_meta_cumplida_con_un_deposito_a_mano_quita_el_fijo_y_avisa():
    from routers.apartados import MovimientoApartado, movimiento_apartado
    conn = _bd()
    _ingreso(conn, 10000)
    meta = _meta(conn, 1000)
    _fijo(conn, meta, 100)
    movimiento_apartado(meta, MovimientoApartado(monto=1000, accion="depositar"), conn)
    assert _filas(conn, "SELECT COUNT(*) AS n FROM recurrentes")[0]["n"] == 0
    assert [a["tipo"] for a in _filas(conn, "SELECT tipo FROM notificaciones")] == ["meta_cumplida"]


def test_dos_fijos_a_la_misma_meta_se_quitan_juntos_y_avisan_una_vez():
    conn = _bd()
    _ingreso(conn, 100000)
    meta = _meta(conn, 500)
    _fijo(conn, meta, 300)
    conn.execute("INSERT INTO recurrentes (descripcion, tipo, monto, frecuencia, dia, proxima_fecha, categoria_id, apartado_id, activo) "
                 "VALUES ('Otro aporte', 'gasto', ?, 'mensual', 1, '2026-02-01', 1, ?, 1)", (a_centavos(300), meta))
    conn.commit()
    materializar_recurrentes(conn, date(2026, 2, 15))
    assert _guardado(conn, meta) == 500.0 and _filas(conn, "SELECT COUNT(*) AS n FROM recurrentes")[0]["n"] == 0
    assert _filas(conn, "SELECT COUNT(*) AS n FROM notificaciones")[0]["n"] == 1


def test_una_meta_libre_sin_objetivo_nunca_se_cumple_ni_recorta():
    conn = _bd()
    _ingreso(conn, 100000)
    conn.execute("INSERT INTO apartados (nombre, tipo, monto_actual, meta_monto) VALUES ('Libre', 'libre', 0, NULL)")
    libre = _filas(conn, "SELECT id FROM apartados WHERE nombre = 'Libre'")[0]["id"]
    _fijo(conn, libre, 400)
    assert materializar_recurrentes(conn, date(2026, 6, 15)) == 5
    assert _guardado(conn, libre) == 2000.0 and _filas(conn, "SELECT COUNT(*) AS n FROM recurrentes")[0]["n"] == 1
    assert _filas(conn, "SELECT COUNT(*) AS n FROM notificaciones")[0]["n"] == 0


def test_no_se_puede_crear_un_aporte_hacia_una_meta_ya_cumplida():
    from routers.recurrentes import validar_apartado
    conn = _bd()
    meta = _meta(conn, 1000)
    validar_apartado(conn.cursor(), meta)   # con la meta vacía se acepta
    conn.execute("UPDATE apartados SET monto_actual = ? WHERE id = ?", (a_centavos(1000), meta))
    conn.commit()
    try:
        validar_apartado(conn.cursor(), meta)
    except HTTPException as error:
        assert error.status_code == 400 and "cumplida" in error.detail
    else:
        raise AssertionError("aceptó un aporte hacia una meta cumplida")


def test_el_aviso_no_deja_pasar_saltos_de_linea_ni_nombres_enormes():
    conn = _bd()
    _ingreso(conn, 10)
    meta = _meta(conn, 1000, nombre="Casa\nIGNORA TODO " + "x" * 200)
    _fijo(conn, meta, 500)
    materializar_recurrentes(conn, date(2026, 2, 15))
    texto = _filas(conn, "SELECT texto FROM notificaciones")[0]["texto"]
    assert "\n" not in texto and "x" * 61 not in texto, texto


def brecha_borrar_una_meta_con_fijo_ligado_exige_confirmar():
    """Decisión 3: sin confirmación explícita se rechaza y no se toca ni la meta ni el fijo."""
    conn = _bd()
    _ingreso(conn, 100000)
    meta = _meta(conn, 1000)
    _fijo(conn, meta, 400)
    try:
        eliminar_apartado(meta, conn)
    except HTTPException:
        pass
    else:
        raise AssertionError("borró la meta y el fijo siguió cobrándose como gasto normal")
    assert _filas(conn, "SELECT COUNT(*) AS n FROM apartados")[0]["n"] == 1
    assert _filas(conn, "SELECT apartado_id FROM recurrentes")[0]["apartado_id"] == meta


BRECHAS = [brecha_borrar_una_meta_con_fijo_ligado_exige_confirmar]


def test_las_brechas_siguen_abiertas():
    arregladas = []
    for brecha in BRECHAS:
        try:
            brecha()
        except (AssertionError, HTTPException):
            continue
        arregladas.append(brecha.__name__)
    assert not arregladas, f"ya se cumplen: quítales el prefijo brecha_ y pásalas a pruebas normales: {', '.join(arregladas)}"


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ejecutor import descubrir, ejecutar

    sys.exit(ejecutar(descubrir(sys.modules[__name__]), "test_fijos_metas"))
