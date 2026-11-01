"""scripts/comentarios.py - Lista los comentarios de los usuarios desde la base maestra.

Uso (dentro del contenedor):  python3 scripts/comentarios.py [--nuevos] [--marcar-leidos] [--limite 50]
  --nuevos         solo los que no se han marcado como leídos
  --marcar-leidos  marca como leídos los que se muestren
"""

import argparse
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)


def listar(conn, solo_nuevos=False, limite=50):
    """Filas de comentarios, de más reciente a más antiguo. El autor queda vacío si la cuenta se eliminó."""
    condicion = "WHERE c.leido = 0" if solo_nuevos else ""
    return conn.execute(
        f"""
        SELECT c.id, c.creado_en, COALESCE(u.username, '(cuenta eliminada)') AS usuario,
               c.tipo, c.contexto, c.mensaje, c.leido
        FROM comentarios c LEFT JOIN users u ON u.id = c.user_id
        {condicion}
        ORDER BY c.creado_en DESC, c.id DESC
        LIMIT ?
        """,
        (limite,),
    ).fetchall()


def marcar_leidos(conn, ids):
    conn.executemany("UPDATE comentarios SET leido = 1 WHERE id = ?", [(i,) for i in ids])
    conn.commit()


if __name__ == "__main__":
    import master_db

    parser = argparse.ArgumentParser(description="Comentarios de los usuarios")
    parser.add_argument("--nuevos", action="store_true")
    parser.add_argument("--marcar-leidos", action="store_true")
    parser.add_argument("--limite", type=int, default=50)
    args = parser.parse_args()

    conn = master_db.get_master_conn()
    try:
        filas = listar(conn, solo_nuevos=args.nuevos, limite=args.limite)
        for f in filas:
            marca = " " if f["leido"] else "*"
            print(f"{marca} #{f['id']} {f['creado_en']} [{f['tipo']}] {f['usuario']} ({f['contexto'] or 's/d'})")
            print(f"    {f['mensaje']}")
        if args.marcar_leidos:
            marcar_leidos(conn, [f["id"] for f in filas])
        print(f"\n{len(filas)} comentario(s). '*' = nuevo.")
    finally:
        conn.close()
