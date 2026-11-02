---
titulo: Conectar Instagram y Messenger (Zernio)
canal: social
grupo: principal
resumen: Los DMs de Instagram y los mensajes de la página de Facebook, sin crear una app de Meta.
---
Conecta el Instagram y la página de Facebook de la clínica a mi CRM (dominio: <dominio>) por Zernio.

1. Antes, recuérdame dos cosas: conectar el Instagram (Business o Creator) y la página en zernio.com (Connections), y desconectar ManyChat u otro bot de esa cuenta de Instagram.
2. Pídeme que guarde la API key de Zernio (API keys → Create API key) en mi segunda terminal: `python3 scripts/omni.py secret set ZERNIO_API_KEY --instance <dominio>`. No me la pidas por el chat.
3. Corre `python3 scripts/omni.py connect social --instance <dominio>` con un tiempo de espera de 10 minutos. Si hay varias cuentas de una plataforma, pregúntame cuál y repite con `--instagram <id>` o `--facebook <id>`.
4. Cuando te lo pida, mando un DM de prueba desde otra cuenta de Instagram (o escribo a la página desde un Facebook que no la administre).
5. Si no entra, dime qué revisar. La guía completa está en `docs/avanzado/zernio.md`.
