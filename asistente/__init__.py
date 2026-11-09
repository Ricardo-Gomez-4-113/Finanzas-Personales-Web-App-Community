"""asistente - Asistente con LLM que usa la plataforma a través de su propia API.

Canal y motor van separados: `motor.Motor` recibe texto y devuelve texto, y no sabe nada de Telegram. Las herramientas
llaman a la API de la app con un token de corta vida (`tokens.emitir_token`) que solo sirve en las rutas de
`permisos.RUTAS_PERMITIDAS`; ninguna abre una base de datos.
"""
