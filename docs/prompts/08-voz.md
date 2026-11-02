---
titulo: Conectar la voz (Retell)
canal: voz
grupo: principal
resumen: Sofía contesta el teléfono, agenda, pasa llamadas a recepción y deja el resumen en la ficha.
---
Conecta la voz de Sofía a mi CRM (dominio: <dominio>) con Retell.

1. Pídeme que guarde la API key de Retell en mi segunda terminal: `python3 scripts/omni.py secret set RETELL_API_KEY --instance <dominio>`. No me la pidas por el chat.
2. Pregúntame el número: uno nuevo de EE. UU. en Retell (`--comprar-numero`; se cobra cada mes: confírmalo conmigo antes) o uno de México por Twilio (`--twilio --numero +52…`, con `TWILIO_ACCOUNT_SID` y `TWILIO_AUTH_TOKEN` guardados igual en mi segunda terminal).
3. Pregúntame si Sofía puede pasar llamadas a recepción y a qué número (`--transferir-a +52…`).
4. Corre `python3 scripts/omni.py connect voice --instance <dominio>` con esas opciones y un tiempo de espera de 10 minutos.
5. Cuando te lo pida, llamo al número desde mi celular. Si algo sale en rojo, explícame qué comprobar; la guía es `docs/avanzado/voz.md`.
