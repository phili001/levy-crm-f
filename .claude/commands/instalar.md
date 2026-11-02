---
description: Instala el CRM en tu servidor de Coolify, de principio a fin
---

Instala esta copia del CRM siguiendo `INSTALAR.md`, sección "Para el agente",
paso por paso y sin saltarte ninguno. (Si la persona prefiere hacerlo sin ti,
la sección "Para ti" es la instalación en un clic: pegar el compose en Coolify.)

Antes de empezar, comprueba que el MCP de Coolify responde (`get_version`). Si
no está, dile a la persona que corra `python3 scripts/omni.py mcp-setup` en su
segunda terminal y que reinicie Claude Code.

Recuerda las reglas: ninguna llave por el chat, nada de `reveal: true`, los
secretos los genera Coolify al crear el service (tú no los escribes ni los
subes), el código de arranque lo copia la persona de Coolify, y cualquier
borrado se confirma antes.

Al terminar, resume en cinco líneas: dónde entrar, con qué usuario, qué falta
según el checklist de inicio y cuál es el siguiente paso.
