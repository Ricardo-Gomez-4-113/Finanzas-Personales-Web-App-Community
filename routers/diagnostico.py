"""
routers/diagnostico.py - Recibe los fallos que detecta el navegador y los deja en el registro del servidor.

Sin autenticación a propósito: también interesa ver fallos en la pantalla de inicio de sesión.
Los límites de tamaño acotan cada línea, y el archivo rotativo acota el disco.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

import observabilidad
from security import obtener_ip_cliente, reservar_intento_login

router = APIRouter()
LIMITE_REPORTES_POR_IP = 60   # por ventana de 5 minutos


class ReporteCliente(BaseModel):
    tipo: Literal["js", "promesa", "api"]
    mensaje: str = Field(..., min_length=1, max_length=500)
    origen: Optional[str] = Field(None, max_length=300)
    linea: Optional[int] = Field(None, ge=0, le=10_000_000)
    ruta: Optional[str] = Field(None, max_length=200)
    id_peticion: Optional[str] = Field(None, pattern=r"^[0-9a-f]{16}$")


@router.post("/api/diagnostico/cliente", status_code=status.HTTP_204_NO_CONTENT, summary="Registrar un fallo del navegador")
def registrar_fallo_cliente(reporte: ReporteCliente, request: Request):
    reservar_intento_login(f"diagnostico:{obtener_ip_cliente(request)}", limite=LIMITE_REPORTES_POR_IP)   # público: sin tope, cualquiera inunda el registro y desplaza los eventos reales
    observabilidad.registrar_reporte_cliente(
        reporte.tipo,
        reporte.mensaje,
        origen=reporte.origen,
        linea=reporte.linea,
        ruta=reporte.ruta,
        id_peticion=reporte.id_peticion,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
