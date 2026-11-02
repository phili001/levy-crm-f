---
description: Conecta la IA, el chat web, WhatsApp, Instagram y Messenger, el correo o la voz
---

Argumento: qué conectar (`ai`, `widget`, `whatsapp`, `social`, `email` o `voice`).

- Para `ai`: si falta la clave, pídele a la persona que la guarde en su segunda
  terminal con `python3 scripts/omni.py secret set OPENAI_API_KEY --instance
  <dominio>`. Nunca se la pidas por el chat. Luego corre
  `python3 scripts/omni.py connect ai --instance <dominio>`.
- Para `widget`: pregunta el dominio de la web donde se va a poner el chat y
  corre `connect widget --dominios <dominio>`. Si la persona no lo tiene claro,
  explícale que dejarlo vacío permite que cualquier web use su agente a su
  costa.
- Para `whatsapp`, `social`, `email` y `voice`: sigue el prompt de ese canal
  en `docs/prompts/` (el mismo que da «Copiar prompt» en su tarjeta del panel).
  Las llaves (`YCLOUD_API_KEY`, `ZERNIO_API_KEY`, `EMAIL_APP_PASSWORD`,
  `RETELL_API_KEY`) se guardan igual, en la segunda terminal.

Tras el alta, `connect` espera hasta 5 minutos a que entre el mensaje (o la
llamada) de prueba: córrelo con un tiempo de espera de 10 minutos y dile a la
persona cuándo mandarlo. Si no llega, explica lo que imprimió.
