# Instagram y Messenger por Zernio

Zernio es un intermediario que habla con Meta como una sola app ya aprobada. La
clínica entra con su usuario de Meta en Zernio y el CRM recibe y contesta los
mensajes por su API: **sin crear una app de Meta y sin pasar la revisión de
Meta**. Es la forma recomendada de conectar Instagram y Messenger.

La ruta antigua (tu propia app de Meta, en Conexiones → Servicios → «Instagram
con app de Meta propia») sigue funcionando para quien ya la tenga.

## Lo que cuesta

- Cuentas: las dos primeras son gratis. Una página de Facebook y su Instagram
  cuentan como **dos** cuentas.
- Mensajes: 10,000 salientes al mes gratis por cuenta de Zernio; recibir no
  cuesta. Desde el 1 de octubre de 2026, sin tarjeta registrada, el envío que
  pase de 10,000 falla (`403 message_allowance_exceeded`) y la conversación pasa
  a una persona.

## Conectar

1. En zernio.com, crea la cuenta y conecta el Instagram (Business o Creator) y
   la página de Facebook en Connections. Es entrar con tu usuario de Meta.
2. **Desconecta ManyChat u otro bot** de esa cuenta de Instagram. Sus mensajes
   llegan igual que los de una persona y apartarían a Sofía en cada
   conversación.
3. Crea la API key: API keys → Create API key, y cópiala (solo se ve una vez).
4. En el panel: Conexiones → Servicios → **Instagram y Messenger (Zernio)** →
   «Conectar con Zernio». Pega la llave, dale a **Probar**, elige las cuentas y
   **Conectar**.

Al conectar, el CRM:

- crea (o reutiliza) los canales de Instagram y Messenger, con el agente que ya
  responde en tus otros canales;
- registra el aviso de mensajes (webhook) en Zernio con un secreto que elige él
  mismo: no hay que copiar ninguna URL ni ningún secreto;
- trae el historial que ya había (hasta 500 conversaciones), como leído y
  archivado. Sofía no contesta a nadie del historial.

Desde la terminal, lo mismo:

```bash
python3 scripts/omni.py secret set ZERNIO_API_KEY --instance <dominio>
python3 scripts/omni.py connect social --instance <dominio>
# Con varias cuentas de una plataforma, elige: --instagram <id> --facebook <id>
```

## Probar

- Escribe un DM a la cuenta de Instagram desde **otra** cuenta: sale en Chats,
  pestaña Instagram, y Sofía contesta.
- Escribe a la página desde un Facebook personal que **no** administre la
  página. Messenger no entrega mensajes de cuentas del mismo portafolio de
  Meta: si escribes desde tu propio perfil de administrador, no llega nada.
- Contesta desde la app de Instagram o desde Business Suite: el mensaje aparece
  como operador y la conversación pasa a humano.

## Ventana de 24 horas

Meta solo deja contestar durante las 24 h siguientes al último mensaje del
cliente. Entre 24 h y 7 días se puede contestar a mano: el CRM lo manda con la
etiqueta de agente humano (`HUMAN_AGENT`). Pasados 7 días el panel no deja
enviar y explica por qué.

## Cuando algo falla

| Síntoma | Qué pasa y qué hacer |
|---|---|
| La tarjeta dice «Una cuenta se desconectó» | Los permisos de Meta caducan (en Facebook, a menudo). Reconéctala en Zernio y vuelve a dar a «Conectar» en el panel. |
| «Falta registrar el aviso de mensajes» | Zernio no aceptó el registro. El panel enseña la URL y el secreto: pégalos en Zernio → Settings → Webhooks, con los eventos `message.received`, `message.sent`, `account.connected` y `account.disconnected`. |
| Sofía no contesta a nadie | Mira que el canal esté encendido y en Activo (no en Entrenamiento) en Conexiones → Canales. |
| Sofía se aparta sola | Hay otro bot conectado a la cuenta (ManyChat…): sus mensajes cuentan como una persona. Desconéctalo. |
| Zernio está caído | La respuesta no se da por enviada: la conversación pasa a humano y queda el aviso en Registros. Cuando Zernio vuelve, lo nuevo se atiende solo. |

## WhatsApp por Zernio (opcional)

En el mismo formulario hay un interruptor «WhatsApp por Zernio (en vez de
YCloud)». Con él, los mensajes de WhatsApp entran por Zernio y las respuestas
libres (dentro de 24 h) salen por ahí. **Las plantillas y los envíos masivos
siguen saliendo por YCloud.** Solo tiene sentido si el número ya está conectado
en Zernio.

## Privacidad

Los mensajes de Instagram y Messenger pasan por Zernio (Zernio Software SL,
España). El aviso de privacidad público (`/privacidad`) ya lo menciona; rellena
los datos de la clínica en Ajustes → Aviso de privacidad. Meta pide esa URL.

## Probar sin cuentas

El simulador de Zernio (`backend/tests/mocks/zernio_mock.py`, puerto 9105)
imita la API y firma sus avisos como Zernio. Con el perfil `dev` del compose y
`ZERNIO_BASE_URL=http://mocks:9105/api/v1` en el `.env`, «Conectar con Zernio»
funciona con cualquier llave que no acabe en `-invalid`.
