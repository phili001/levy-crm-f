---
description: Deja el CRM con los datos y la voz del negocio
---

Ayuda a dejar la instalación lista para el negocio real. Todo esto se hace
desde el panel, no tocando código:

1. **Preferencias → Sobre el negocio**: completa los `{{PENDIENTE: …}}` (teléfono y
   dirección). Mientras estén ahí, el agente no puede darlos.
2. **Asistentes**: el prompt de Sofía, su nombre y su forma de hablar.
3. **Base de conocimiento**: sube los documentos reales del negocio (precios,
   servicios, políticas) y espera a que queden indexados.
4. **Agenda**: horario, días y duración de cada servicio.
5. **Etiquetas** y campos de la ficha de contacto, si el negocio los usa.

Al terminar, corre `python3 scripts/omni.py verify` para comprobar que el
agente responde con la información nueva, y avisa de lo que siga pendiente.
