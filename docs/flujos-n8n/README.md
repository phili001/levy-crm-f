# Flujos de ejemplo de n8n

Tres flujos listos para importar en tu n8n (menú **⋯** del flujo → **Import from
file...**). Cada uno trae notas con lo que hay que cambiar. La
guía completa está en [`../n8n.md`](../n8n.md).

| Archivo | Qué hace | En el CRM |
|---|---|---|
| `mandar-foto-tratamiento.json` | Herramienta que le manda al paciente una foto del tratamiento que le interesa | Conexiones → n8n, herramienta `mandar_foto_tratamiento` |
| `buscar-en-hoja.json` | Herramienta que consulta un precio en tu hoja de Google | Conexiones → n8n, herramienta `consultar_precio` |
| `sofia-en-n8n.json` | Sofía completa: un AI Agent con su prompt y tres herramientas (buscar en la base de conocimiento del CRM, mandar una foto, pasar a una persona) | Asistentes → el agente → Motor → «Un flujo de n8n» |

En los tres:

1. En «Webhook del CRM», crea la credencial **Header Auth** con **Name**
   `X-Omnicanal-Token` y **Value** el secreto que te da el CRM.
2. Cambia lo que es de tu clínica: las fotos, la hoja o el prompt de Sofía. En
   `sofia-en-n8n.json`, `buscar_en_conocimiento` lleva **otra** credencial Header
   Auth: el token **«Para n8n»** del CRM (Conexiones → API / MCP), con **Name**
   `Authorization` y **Value** `Bearer <token>`.
3. **Publish** y copia la **Production URL** del Webhook (no la Test URL).
4. Pégala en el CRM y dale **«Probar»**.

Probados con n8n 2.23 y el CRM 1.1.3 (en vivo con el n8n del curso, el 2026-10-16).
