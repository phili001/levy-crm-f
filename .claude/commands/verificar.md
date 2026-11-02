---
description: Comprueba que la instalación funciona de verdad, no solo que arranca
---

Corre `python3 scripts/omni.py verify --instance <dominio>` y explícame el
resultado en español.

Si algo falla, no te quedes en el mensaje: mira los registros del contenedor
que corresponda con la herramienta `logs` del MCP y dime la causa probable y
qué hacer. Si el agente contestó algo que no es el horario sembrado, revisa la
base de conocimiento (que esté indexada) y el prompt del agente de texto.
