# Catálogo de prompts

Los prompts que el panel enseña en **Plataforma → Prompts** y en el botón
**Copiar prompt** de cada tarjeta de Canales → Servicios. Se pegan en Claude
Code, abierto en la carpeta de esta copia del CRM.

Cada archivo es un prompt: la cabecera entre `---` dice su título, de qué
tarjeta es (`canal`), si es de los de siempre o de los avanzados (`grupo`) y un
resumen de una línea. `<dominio>` se cambia por el de la instalación al copiar.

Viven aquí (dentro de `backend/`) y no en `docs/` porque tienen que viajar en
la imagen del backend, igual que el CHANGELOG; `docs/prompts` es un enlace a
esta carpeta. Este archivo no sale en el panel.
