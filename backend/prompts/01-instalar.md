---
titulo: Instalar el CRM en tu servidor
grupo: principal
resumen: Claude Code lo instala en tu Coolify por el MCP. Sin Claude Code, en un clic, está en INSTALAR.md.
---
Lee INSTALAR.md e instala el CRM en mi servidor de Coolify siguiendo la sección «Para el agente», paso por paso y sin saltarte ninguno.

Antes de empezar, comprueba que el MCP de Coolify responde. Si no, dime que corra `python3 scripts/omni.py mcp-setup` en mi segunda terminal y que reinicie Claude Code.

Reglas: nunca me pidas una llave, un token ni una contraseña por el chat (los guardo yo en mi segunda terminal con `python3 scripts/omni.py secret set <NOMBRE>`), nada de `reveal: true` en el MCP, los secretos los genera Coolify (tú no los escribes ni los subes), el código de arranque lo copio yo de Coolify y confírmame cualquier borrado antes de hacerlo.

Al terminar, resume en cinco líneas: dónde entro, con qué usuario, qué falta según el checklist de inicio y cuál es el siguiente paso.
