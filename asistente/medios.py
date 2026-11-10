"""asistente/medios.py - Fotos y audios que llegan por Telegram: qué se acepta, con qué tamaño y qué son de verdad.

El tipo lo dicen los primeros bytes del archivo, no Telegram ni la extensión: un archivo que dice ser foto y es otra cosa se rechaza.
Nada se guarda: el bot descarga los bytes, el modelo los lee en ese turno y se descartan.
"""

MAX_BYTES = 5 * 1024 * 1024   # por foto: tope de memoria y de costo del modelo
MAX_BYTES_AUDIO = 3 * 1024 * 1024   # un audio de 90 s cabe de sobra (WAV mono de 16 kHz: ~3 MB); más es un audio más largo de lo permitido
MAX_AUDIO_SEG = 90            # duración máxima de una nota de voz o un audio

NO_ENTIENDE = "Por ahora solo entiendo texto, fotos y notas de voz."


class Rechazo(Exception):
    """El archivo no se acepta. El mensaje es para la persona: sin datos del archivo ni del error."""


def _entero(valor):
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0 else None


def elegir(mensaje):
    """Qué archivo trae el mensaje de Telegram: {"tipo": "foto"|"audio", "file_id": str} o None si no trae. Lanza `Rechazo`."""
    if mensaje.get("photo"):
        fotos = [f for f in mensaje["photo"] if isinstance(f, dict) and isinstance(f.get("file_id"), str)]
        # Telegram da la misma foto en varios tamaños, de menor a mayor: se usa la más grande que cabe
        cabe = [f for f in fotos if (_entero(f.get("file_size")) or 0) <= MAX_BYTES]
        if not cabe:
            raise Rechazo("Esa foto es demasiado grande. Mándala más pequeña (máximo 5 MB).")
        return {"tipo": "foto", "file_id": cabe[-1]["file_id"]}
    for clave in ("voice", "audio"):
        audio = mensaje.get(clave)
        if isinstance(audio, dict):
            if not isinstance(audio.get("file_id"), str):
                raise Rechazo(NO_ENTIENDE)
            if (_entero(audio.get("duration")) or 0) > MAX_AUDIO_SEG:
                raise Rechazo(f"Ese audio es muy largo. Mándalo de máximo {MAX_AUDIO_SEG} segundos.")
            if (_entero(audio.get("file_size")) or 0) > MAX_BYTES_AUDIO:
                raise Rechazo("Ese audio pesa demasiado (máximo 3 MB).")
            return {"tipo": "audio", "file_id": audio["file_id"]}
    documento = mensaje.get("document")
    if isinstance(documento, dict):
        if str(documento.get("mime_type") or "").lower() not in ("image/jpeg", "image/png", "image/webp") or not isinstance(documento.get("file_id"), str):
            raise Rechazo("Solo puedo leer fotos (JPG, PNG o WEBP) y audios. Ese archivo no.")
        if (_entero(documento.get("file_size")) or 0) > MAX_BYTES:
            raise Rechazo("Esa imagen es demasiado grande. Mándala más pequeña (máximo 5 MB).")
        return {"tipo": "foto", "file_id": documento["file_id"]}
    if any(mensaje.get(k) for k in ("video", "video_note", "animation", "sticker", "contact", "location", "poll")):
        raise Rechazo(NO_ENTIENDE)
    return None


FORMATO_NO_RECONOCIDO = "No reconozco ese formato. Acepto fotos (JPG, PNG, WEBP) y audios (OGG, MP3, WAV, FLAC)."


def clasificar(datos):
    """`("foto"|"audio", mime)` de unos bytes ya descargados, comprobando formato y tamaño. Lanza `Rechazo` con un texto para la persona."""
    real = tipo_real(datos) if isinstance(datos, (bytes, bytearray)) else None
    if real is None:
        raise Rechazo(FORMATO_NO_RECONOCIDO)
    if real[0] == "audio" and len(datos) > MAX_BYTES_AUDIO:
        raise Rechazo("Ese audio pesa demasiado (máximo 3 MB).")
    if real[1] == "audio/wav":
        _revisar_wav(datos)
    if real[0] == "foto" and len(datos) > MAX_BYTES:
        raise Rechazo("Esa foto es demasiado grande. Mándala más pequeña (máximo 5 MB).")
    return real


def _revisar_wav(datos):
    """Un WAV de 90 s o menos, sin comprimir (PCM). Los otros formatos se acotan por peso; aquí la cabecera dice cuánto dura de verdad,
    así que no se acepta una que mienta (ni tasas o profundidades que metan horas de audio en 3 MB)."""
    import struct
    pos, tasa, canales, bits, formato = 12, 0, 0, 0, 0
    while pos + 8 <= len(datos):
        trozo, largo = datos[pos:pos + 4], struct.unpack("<I", datos[pos + 4:pos + 8])[0]
        if trozo == b"fmt " and largo >= 16 and pos + 24 <= len(datos):
            formato, canales, tasa, _, _, bits = struct.unpack("<HHIIHH", datos[pos + 8:pos + 24])
        elif trozo == b"data":
            if formato != 1 or not canales or not tasa or bits not in (8, 16, 24, 32):
                break
            muestras = min(largo, len(datos) - pos - 8) // (canales * bits // 8)
            if muestras / tasa > MAX_AUDIO_SEG + 1:
                raise Rechazo(f"Ese audio es muy largo. Mándalo de máximo {MAX_AUDIO_SEG} segundos.")
            return
        pos += 8 + largo + (largo & 1)
    raise Rechazo(FORMATO_NO_RECONOCIDO)


def tipo_real(datos):
    """(`"foto"` o `"audio"`, mime) según los primeros bytes, o None si no es un formato que se acepte."""
    if datos[:3] == b"\xff\xd8\xff":
        return "foto", "image/jpeg"
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        return "foto", "image/png"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "foto", "image/webp"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WAVE":
        return "audio", "audio/wav"
    if datos[:4] == b"OggS":
        return "audio", "audio/ogg"
    if datos[:4] == b"fLaC":
        return "audio", "audio/flac"
    if datos[:3] == b"ID3" or (len(datos) > 1 and datos[0] == 0xFF and datos[1] & 0xE0 == 0xE0):
        return "audio", "audio/mp3"
    return None
