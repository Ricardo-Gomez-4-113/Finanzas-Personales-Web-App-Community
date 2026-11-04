"""tests/auditoria/bitacora.py - Escribe lo que hace la simulación y guarda una carpeta por cada fallo.

bitacora.jsonl solo lleva lo que se repite con la misma semilla, así que dos corridas con la misma semilla dan
el mismo archivo byte a byte. Los tiempos y los id de petición cambian en cada corrida y van aparte, en tiempos.jsonl.
"""

import json
import os
import shutil
import sqlite3

MAXIMO_FALLOS_CON_BASE = 50  # cada base copiada pesa lo de un usuario entero; más de esto llena el disco


def _linea(datos):
    return json.dumps(datos, ensure_ascii=False, sort_keys=True) + "\n"


def copiar_base(origen, destino):
    """Copia la base de SQLite completa (con lo que aún está en el WAL), no solo el archivo principal."""
    con_origen = sqlite3.connect(origen)
    con_destino = sqlite3.connect(destino)
    try:
        con_origen.backup(con_destino)
    finally:
        con_destino.close()
        con_origen.close()


class Bitacora:
    def __init__(self, carpeta):
        self.carpeta = carpeta
        os.makedirs(carpeta, exist_ok=True)
        self._pasos = open(os.path.join(carpeta, "bitacora.jsonl"), "w", encoding="utf-8")
        self._tiempos = open(os.path.join(carpeta, "tiempos.jsonl"), "w", encoding="utf-8")
        self._fallos_con_base = 0

    def paso(self, datos, tiempos):
        self._pasos.write(_linea(datos))
        self._tiempos.write(_linea(tiempos))

    def fallo(self, nombre, datos, ruta_base=None):
        """Carpeta del fallo: fallo.json con el paso y las violaciones, y base.db con la base de la persona."""
        carpeta = os.path.join(self.carpeta, "fallos", nombre)
        os.makedirs(carpeta, exist_ok=True)
        with open(os.path.join(carpeta, "fallo.json"), "w", encoding="utf-8") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=2, sort_keys=True)
        if ruta_base and self._fallos_con_base < MAXIMO_FALLOS_CON_BASE:
            copiar_base(ruta_base, os.path.join(carpeta, "base.db"))
            self._fallos_con_base += 1
        return carpeta

    def cerrar(self):
        self._pasos.close()
        self._tiempos.close()
