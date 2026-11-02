"""Simuladores de los proveedores externos, para probar sin cuentas reales.

Cada simulador imita la parte de la API de su proveedor que el CRM usa DE
VERDAD (ver `app/providers/`), con la misma forma de petición, respuesta y
firma. No son un proveedor de respaldo: solo se usan si una URL base apunta a
ellos a propósito (perfil `dev` del compose, `smoke.sh` en modo simuladores y
las pruebas).

  - `wa_mock`: YCloud (WhatsApp). Registra lo que se envía y emite webhooks
    entrantes firmados como los firma YCloud.
  - `llm_mock`: pasarela compatible con OpenAI con respuestas deterministas.
  - `retell_mock`: la API REST de Retell y su webhook `call_analyzed` firmado.
  - `imap_mock`: un buzón IMAP mínimo y un SMTP que captura lo enviado.
  - `zernio_mock`: Zernio (Instagram y Messenger); emite `message.received` y
    `message.sent` firmados como Zernio y respeta `Idempotency-Key`.

Todos exponen una ruta de control bajo `/_mock/` para que una prueba provoque
eventos ("llega un mensaje") y lea lo capturado ("¿qué se envió?"). Esa ruta no
existe en ningún proveedor real, así que no se confunde con su API.

Para levantarlos todos: `python -m tests.mocks` desde `backend/` (lo hace el
servicio `mocks` del perfil `dev` en `docker-compose.yml`).
"""
