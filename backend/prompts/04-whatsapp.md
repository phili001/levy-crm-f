---
titulo: Conectar WhatsApp (YCloud)
canal: whatsapp
grupo: principal
resumen: El número de la clínica contesta con Sofía y el equipo sigue usando la app WhatsApp Business.
---
Conecta el WhatsApp de la clínica a mi CRM (dominio: <dominio>) por YCloud, sin dejar de usar la app WhatsApp Business en el celular (coexistencia).

1. Pregúntame el número que tengo dado de alta en YCloud (+52 y 10 dígitos).
2. Pídeme que guarde la API key de YCloud en mi segunda terminal: `python3 scripts/omni.py secret set YCLOUD_API_KEY --instance <dominio>`. No me la pidas por el chat.
3. Corre `python3 scripts/omni.py connect whatsapp --instance <dominio> --numero <número>` con un tiempo de espera de 10 minutos: después del alta se queda hasta 5 minutos esperando mi mensaje de prueba.
4. Guíame con el QR de coexistencia de YCloud y dime cuándo mandar el WhatsApp de prueba desde otro teléfono.
5. Si no entra, explícame lo que imprimió (webhook, secreto de firma) y qué hago.
