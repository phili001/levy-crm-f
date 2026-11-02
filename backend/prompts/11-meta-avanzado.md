---
titulo: Rutas avanzadas de Meta (app propia o WhatsApp por Zernio)
canal: meta
grupo: avanzado
resumen: Instagram o WhatsApp con tu propia app de Meta, o WhatsApp por Zernio en vez de YCloud.
---
Quiero conectar un canal de Meta por una ruta avanzada en mi CRM (dominio: <dominio>). Antes de nada, pregúntame cuál y dime si de verdad la necesito: la ruta recomendada es Zernio para Instagram y Messenger, e YCloud para WhatsApp.

- WhatsApp por Zernio (sin YCloud): pídeme la API key de Zernio en mi segunda terminal (`python3 scripts/omni.py secret set ZERNIO_API_KEY --instance <dominio>`) y corre `python3 scripts/omni.py connect social --instance <dominio> --wa-por-zernio` con un tiempo de espera de 10 minutos.
- Instagram con app de Meta propia: se conecta desde el panel (Canales → Servicios → «Instagram · vía oficial de Meta»). Avísame antes de que pide una app de Meta, la verificación del negocio y la revisión de Meta (de 2 a 5 semanas). Guíame paso a paso con las direcciones que enseña esa tarjeta.
- WhatsApp por la API oficial de Meta: igual, desde la tarjeta de WhatsApp del panel, eligiendo Meta como proveedor.

Las llaves se escriben en el panel (Claves de cada tarjeta) o en mi segunda terminal, nunca en el chat.
