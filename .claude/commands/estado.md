---
description: Cómo va la instalación ahora mismo
---

Corre `python3 scripts/omni.py status --instance <dominio>` y resume en
lenguaje llano: si la API, la base de datos, Redis y el worker están bien.

Si el worker aparece caído, dilo claramente: el panel sigue funcionando pero
**nadie contesta los mensajes**, porque el agente corre ahí. Mira sus registros
con la herramienta `logs` del MCP y dime qué encuentras.

Termina con el checklist de inicio: qué pasos faltan por hacer.
