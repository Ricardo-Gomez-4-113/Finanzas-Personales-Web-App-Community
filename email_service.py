"""
email_service.py - Servicio de Notificaciones y Envío de Correos Transaccionales (SMTP)

Este módulo gestiona:
1. Conexión segura TLS/SSL con servidores SMTP (por defecto Gmail: smtp.gmail.com:587).
2. Construcción de correos en formato multiparte (HTML enriquecido + texto plano).
3. Plantilla con la identidad visual de Finanzas Personales (Obsidian & Satin Gold).
"""

import os
import html
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def enviar_correo_codigo_recuperacion(destinatario: str, nombre_usuario: str, codigo: str) -> bool:
    """
    Envía un correo electrónico con el código de 6 dígitos para restablecer contraseña.
    
    Parámetros:
    -----------
    destinatario : str
        Dirección de correo destino.
    nombre_usuario : str
        Nombre del usuario para personalización del mensaje.
    codigo : str
        Código de 6 dígitos generado por el sistema.
        
    Retorna:
    --------
    bool: True si el correo fue enviado exitosamente, False si hubo error o falta configuración.
    """
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_password = os.environ.get("SMTP_PASSWORD", "").strip()
    smtp_from = os.environ.get("SMTP_FROM", f"Finanzas Personales <{smtp_user}>")

    asunto = f"{codigo} es tu código de recuperación en Finanzas Personales"
    nombre_seguro = html.escape(nombre_usuario or "Usuario")

    # Si está configurada la API Key de Resend, enviamos directamente por Resend API
    resend_api_key = os.environ.get("RESEND_API_KEY", "").strip()

    # Cuerpo en texto plano (fallback para clientes de correo sin soporte HTML)
    texto_plano = f"""Hola {nombre_usuario},

Recibimos una solicitud para restablecer la contraseña de tu cuenta en Finanzas Personales.

Tu código de recuperación es: {codigo}

Este código es de un solo uso y expirará en 15 minutos.

Si no solicitaste este cambio, puedes ignorar este mensaje de forma segura. Tu cuenta permanece protegida.

--
Finanzas Personales
"""

    # Cuerpo HTML enriquecido con diseño institucional Obsidian & Satin Gold
    html_contenido = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Recuperación de Contraseña</title>
</head>
<body style="margin: 0; padding: 0; background-color: #080c14; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #f1f5f9;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #080c14; padding: 40px 15px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" style="max-width: 520px; background-color: #0f1624; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 16px; overflow: hidden; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
                    <!-- Cabecera de Marca -->
                    <tr>
                        <td style="padding: 32px 32px 20px 32px; text-align: center; border-bottom: 1px solid rgba(255, 255, 255, 0.06);">
                            <table role="presentation" cellspacing="0" cellpadding="0" style="margin: 0 auto;">
                                <tr>
                                    <td style="vertical-align: middle; padding-right: 12px;">
                                        <div style="width: 36px; height: 36px; border-radius: 9px; background: #080c14; border: 1.5px solid #c5a059; text-align: center; line-height: 36px;">
                                            <span style="color: #dfba73; font-weight: 800; font-size: 18px;">F</span>
                                        </div>
                                    </td>
                                    <td style="vertical-align: middle;">
                                        <span style="font-size: 20px; font-weight: 800; letter-spacing: -0.02em; color: #f8fafc;">FINANZAS <span style="color: #dfba73;">PERSONALES</span></span>
                                    </td>
                                </tr>
                            </table>
                            <p style="margin: 6px 0 0 0; font-size: 11px; text-transform: uppercase; letter-spacing: 0.15em; color: #c5a059; font-weight: 600;">Finanzas personales</p>
                        </td>
                    </tr>
                    
                    <!-- Contenido Principal -->
                    <tr>
                        <td style="padding: 32px;">
                            <h1 style="margin: 0 0 14px 0; font-size: 18px; font-weight: 700; color: #ffffff; text-align: center;">Código de Recuperación</h1>
                            <p style="margin: 0 0 20px 0; font-size: 14px; line-height: 1.6; color: #94a3b8; text-align: center;">
                                Hola <strong style="color: #f1f5f9;">{nombre_seguro}</strong>, recibimos una solicitud para restablecer el acceso a tu cuenta.
                            </p>
                            
                            <!-- Caja de Código 6 Dígitos -->
                            <div style="background-color: #080c14; border: 1.5px dashed rgba(197, 160, 89, 0.5); border-radius: 12px; padding: 20px; text-align: center; margin: 24px 0;">
                                <div style="font-family: 'Courier New', Courier, monospace; font-size: 34px; font-weight: 800; letter-spacing: 10px; color: #dfba73; margin-left: 10px;">
                                    {codigo}
                                </div>
                                <span style="display: inline-block; margin-top: 10px; font-size: 12px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em;">
                                    Válido durante 15 minutos
                                </span>
                            </div>

                            <p style="margin: 0 0 10px 0; font-size: 13px; line-height: 1.5; color: #94a3b8; text-align: center;">
                                Introduce este código de 6 dígitos en la pantalla de inicio de sesión de Finanzas Personales para configurar tu nueva contraseña.
                            </p>
                            
                            <!-- Alerta de Seguridad -->
                            <div style="margin-top: 26px; padding: 14px; background-color: rgba(255, 255, 255, 0.02); border-left: 3px solid #64748b; border-radius: 4px;">
                                <p style="margin: 0; font-size: 12px; line-height: 1.5; color: #64748b;">
                                    <strong>¿No fuiste tú?</strong> Si no realizaste esta solicitud, puedes ignorar este correo de forma segura. Tu contraseña actual no ha sido modificada.
                                </p>
                            </div>
                        </td>
                    </tr>
                    
                    <!-- Pie de Página -->
                    <tr>
                        <td style="padding: 20px 32px 28px 32px; text-align: center; border-top: 1px solid rgba(255, 255, 255, 0.06); background-color: rgba(0, 0, 0, 0.2);">
                            <p style="margin: 0; font-size: 11px; color: #475569;">
                                Este es un mensaje de seguridad automatizado emitido por tu servidor Finanzas Personales.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

    es_resend_valido = bool(resend_api_key and resend_api_key.lower() not in RESEND_PLACEHOLDERS and not resend_api_key.lower().startswith("tu_"))
    es_smtp_valido = bool(
        smtp_user
        and smtp_password
        and smtp_user.lower() not in SMTP_USER_PLACEHOLDERS
        and smtp_password.lower() not in SMTP_PASSWORD_PLACEHOLDERS
        and not smtp_user.lower().startswith("tu_")
    )

    # 1. Enviar vía Resend API si está configurado con una clave real
    if es_resend_valido:
        try:
            import json
            import urllib.request
            
            resend_from = os.environ.get("RESEND_FROM", "Finanzas Personales <no-responder@example.com>").strip()
            payload = {
                "from": resend_from,
                "to": [destinatario],
                "subject": asunto,
                "html": html_contenido,
                "text": texto_plano
            }
            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {resend_api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "FinanzasPersonales/1.0"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=12) as response:
                if response.status in (200, 201):
                    print(f"[RESEND INFO] Correo con código de recuperación enviado con éxito a {destinatario} vía Resend API", flush=True)
                    return True
                else:
                    print(f"[RESEND WARNING] Respuesta no esperada de Resend API: {response.status}", flush=True)
        except Exception as e:
            print(f"[RESEND ERROR] Error al enviar correo vía Resend API: {e}", flush=True)
            if hasattr(e, "read"):
                try:
                    print(f"[RESEND ERROR DETAIL] {e.read().decode('utf-8')}", flush=True)
                except Exception:
                    pass

    # 2. Enviar vía SMTP (Gmail, Brevo, etc.)
    if not es_smtp_valido:
        if not es_resend_valido:
            print("[EMAIL AVISO] No se pudo enviar el correo: Ni RESEND_API_KEY ni SMTP tienen credenciales válidas en .env (modo autónomo)")
        return False

    # Construir el mensaje MIME multiparte
    mensaje = MIMEMultipart("alternative")
    mensaje["Subject"] = asunto
    mensaje["From"] = smtp_from
    mensaje["To"] = destinatario

    parte_texto = MIMEText(texto_plano, "plain", "utf-8")
    parte_html = MIMEText(html_contenido, "html", "utf-8")

    mensaje.attach(parte_texto)
    mensaje.attach(parte_html)

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=12) as servidor:
                servidor.login(smtp_user, smtp_password)
                servidor.sendmail(smtp_user, [destinatario], mensaje.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=12) as servidor:
                servidor.ehlo()
                servidor.starttls()
                servidor.ehlo()
                servidor.login(smtp_user, smtp_password)
                servidor.sendmail(smtp_user, [destinatario], mensaje.as_string())
        print(f"[EMAIL INFO] Correo con código de recuperación enviado a {destinatario} vía SMTP")
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] Error al enviar correo vía SMTP ({smtp_host}:{smtp_port}): {e}")
        return False

# Valores de plantilla que indican que el usuario aún no ha configurado credenciales reales
RESEND_PLACEHOLDERS = {
    "tu_api_key_aqui",
    "tu_api_key",
    "your_api_key_here",
    "re_123456789",
    "",
}

SMTP_USER_PLACEHOLDERS = {
    "tu_correo@gmail.com",
    "tu_usuario@tu-dominio.com",
    "tu_usuario@ejemplo.com",
    "",
}

SMTP_PASSWORD_PLACEHOLDERS = {
    "tu_contrasena_de_aplicacion_16_caracteres",
    "tu_contrasena_o_token",
    "",
}

def esta_configurado_servicio_correo() -> bool:
    """
    Determina si el servidor cuenta con configuración válida y no-placeholder
    para el envío de correos transaccionales (ya sea mediante API Key de Resend o mediante SMTP).
    """
    resend_api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if resend_api_key and resend_api_key.lower() not in RESEND_PLACEHOLDERS and not resend_api_key.lower().startswith("tu_"):
        return True
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_password = os.environ.get("SMTP_PASSWORD", "").strip()
    if (
        smtp_user
        and smtp_password
        and smtp_user.lower() not in SMTP_USER_PLACEHOLDERS
        and smtp_password.lower() not in SMTP_PASSWORD_PLACEHOLDERS
        and not smtp_user.lower().startswith("tu_")
    ):
        return True
    return False


