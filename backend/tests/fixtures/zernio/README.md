# Eventos reales de Zernio (anonimizados)

Capturados el 2026-09-18 con un webhook de prueba (T-1.1.4) contra una cuenta de
Instagram y una Página de Facebook conectadas en Zernio. Se guardan para que el
adaptador (`providers/zernio/`) se pruebe contra lo que Zernio manda de verdad y
no contra lo que dice su documentación.

Nombres, usuarios, fotos, ids y textos se sustituyeron por valores de prueba
manteniendo la forma (un mismo id real → el mismo id falso en todos los
ficheros). Por eso los cuerpos se volvieron a firmar: `headers.json` trae, por
fichero, las cabeceras que mandaría Zernio con la firma HMAC-SHA256 (hex) del
cuerpo **tal cual está en disco**, con el secreto `fixture-zernio-desechable`.
No reformatees los `.json`: si cambia un byte, la firma deja de coincidir.

| Fichero | Qué es |
|---|---|
| `ig_message_received.json` | DM de texto entrante por Instagram |
| `ig_message_sent.json` | Eco: la clínica contesta desde la app de Instagram (`sentVia: null`) |
| `fb_message_received.json` | Mensaje entrante por Messenger (`platform: facebook`) |
| `fb_message_sent.json` | Eco: la clínica contesta desde Business Suite |
| `ig_postback_received.json` | Clic en un botón: llega como `message.received` con el título del botón en `text`, `metadata.postbackPayload` y un `platformMessageId` `postback_…` que no es un `mid` |
| `ig_message_sent_otro_bot.json` | Eco de **otro bot** conectado a la cuenta (ManyChat): plantilla genérica con `text: null`, indistinguible de una respuesta humana (`sentVia: null`) |

Lo que confirman:

- Llegan a la vez las cabeceras `X-Zernio-*` y las heredadas `X-Late-*`, con la misma firma.
- `X-Zernio-Event-Id` es igual al `id` del cuerpo: es la llave para deduplicar.
- En los ecos, `message.sender` es la propia cuenta (`sender.id == account.id`); el cliente está en `conversation.participantId`.
- `conversation.participantName` puede variar entre eventos de la misma persona (IG mandó el usuario en el entrante y el nombre real en el eco).
