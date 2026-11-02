# Llamadas de voz por Retell

Retell pone el oído y la voz: contesta el teléfono, pasa a texto lo que dice
quien llama y lee en voz alta la respuesta. El cerebro es el CRM: cada turno
llega por un WebSocket y contesta Sofía con el mismo agente, la misma base de
conocimiento y la misma agenda que en los chats. Cada llamada queda en **Voz**
con su transcripción.

## Lo que cuesta

- **Retell** cobra por minuto de llamada (voz y telefonía), en tu cuenta de
  Retell.
- **El modelo** lo pone el CRM con tu clave de IA. Con un modelo mini de OpenAI
  anda alrededor de US$0.03 por minuto.
- **El número**: uno de EE. UU. comprado en Retell se cobra cada mes en tu
  cuenta de Retell; el precio sale al comprarlo. Uno de México se compra y se
  paga en Twilio.

## Conectar

1. Crea la cuenta en retellai.com y una API key: Settings → API Keys.
2. En el panel: Conexiones → Servicios → **Llamadas de voz (Retell)** →
   «Conectar la voz». Deja marcado **Que el CRM cree el agente**, pega la llave
   y dale a **Crear y conectar**.

Al conectar, el CRM:

- crea el agente en tu cuenta de Retell ya apuntando a esta instalación
  (WebSocket y avisos de cada llamada), en español de Latinoamérica (`es-419`),
  con las grabaciones protegidas por enlace firmado, y cuelga si nadie habla;
- lo **publica** (un agente sin publicar no contesta) y lo comprueba leyendo de
  vuelta de Retell: lo que falle sale en rojo con el motivo;
- si ya lo había creado, lo pone al día en vez de crear otro. Un agente que
  hiciste tú a mano en Retell no se toca.

Desde la terminal, lo mismo:

```bash
# En tu terminal de llaves:
python3 scripts/omni.py secret set RETELL_API_KEY --instance <dominio>
# Desde el chat:
python3 scripts/omni.py connect voice --instance <dominio>
```

Opciones de `connect voice`:

| Opción | Qué hace |
|---|---|
| `--comprar-numero [--lada 305]` | Compra un número de EE. UU. en Retell, ya atado al agente. Se cobra cada mes. |
| `--numero +13055551234` | Ata al agente un número que ya está en tu cuenta de Retell. Deja de contestar en el agente donde estuviera. |
| `--twilio --numero +529981234567` | Conecta un número de México de Twilio (abajo). |
| `--voz retell-Gaby` | Otra voz de Retell. Por defecto, `retell-Andrea` (mexicana). |
| `--transferir-a +529981234567` | El teléfono de recepción, para pasar la llamada en horario (abajo). |

## Un número de México (Twilio)

Retell solo vende números de EE. UU. y Canadá. Uno mexicano se compra en
Twilio y llega a Retell por una troncal SIP. El CRM la monta sola:

```bash
# En tu terminal de llaves (están en la portada de la consola de Twilio):
python3 scripts/omni.py secret set TWILIO_ACCOUNT_SID --instance <dominio>
python3 scripts/omni.py secret set TWILIO_AUTH_TOKEN --instance <dominio>
# Desde el chat:
python3 scripts/omni.py connect voice --twilio --numero +529981234567 --instance <dominio>
```

- Crea la troncal `<tu-dominio>.pstn.twilio.com` (o usa la que ya tenga el
  número si solo manda llamadas a Retell), le apunta la entrada a
  `sip:sip.retellai.com`, autoriza las IPs de Retell, cuelga el número de la
  troncal y lo da de alta en Retell con tu agente.
- Si el número estaba en una troncal que manda a **otro** sitio, esa troncal no
  se toca: el número se pasa a la nueva, porque solo puede ir a un lugar.
- El Account SID y el Auth Token se usan para esto y **no se guardan** en el
  CRM. Se puede repetir las veces que haga falta: no duplica nada.
- Si Twilio dice que el dominio ya lo usa otra cuenta (tiene que ser único en
  todo Twilio), elige otro con `--troncal clinica-dental-cancun`.

## Pasar la llamada a recepción

Si quien llama pide hablar con una persona (o con el doctor, facturación, una
queja), Sofía le pregunta su nombre y el motivo y usa la herramienta **Derivar
a humano**:

- **En horario** y con **teléfono de recepción** en la conexión de voz, dice
  «Te paso con recepción, un momento» y Retell pasa la llamada a ese número.
  Recepción ve el número de quien llama. La conversación se archiva al colgar:
  ya la atendió una persona.
- **Fuera de horario**, o sin teléfono de recepción, toma el recado, dice que
  le devolverán la llamada y cuelga. La conversación queda en **Para hacer**
  para que alguien la devuelva.

El horario es el de atención (Asistentes → Horarios). El teléfono de recepción se
pone en el mismo formulario de la voz («Recepción») o con
`omni.py connect voice --transferir-a +529981234567`.

Lo que hay que saber:

- **Activa «Derivar a humano» en el Agente de Voz** (Asistentes) si tu
  instalación es anterior a esta versión: las nuevas ya la traen. Sin ella,
  Sofía no puede pasar la llamada y el recado no llega a Para hacer.
- **Solo en llamadas de teléfono.** Una llamada de prueba desde el navegador
  de Retell no se puede transferir: la segunda vez que se pide, Sofía toma el
  recado en vez de intentarlo otra vez.
- **Con un número de Twilio**, la troncal tiene que permitir transferencias.
  `omni.py connect voice --twilio` lo deja activado; si conectaste el número
  antes de esta versión, vuelve a lanzarlo. Si recepción está en otro país que
  el número, actívalo en Twilio → Voice Geographic Permissions → Elastic SIP
  Trunking.
- **El tramo transferido se paga** como una llamada saliente (a Retell o a
  Twilio, según de quién sea el número).
- **Recepción en otro país que el número.** Retell solo deja llamar a los
  países de la lista del número (*allowed outbound countries*); los números
  que se dieron de alta en el curso 02 traen solo México. Al conectar el
  número o guardar el teléfono de recepción, el CRM añade el país de recepción
  a esa lista (y solo ese) y lo enseña en las comprobaciones. Si el prefijo de
  recepción no lo reconoce, lo dice en rojo: entonces añádelo en Retell →
  Phone Numbers → el número, o Retell rechaza la transferencia y Sofía toma el
  recado.
- **Las llamadas por devolver** salen en **Para hacer** (Chats, pestaña
  Todas) y en **Voz → Por devolver**, marcadas con «Devolver llamada». Cuando
  alguien la devuelva, «Ya la devolví» la saca de las dos listas.
- **Antes de pasar la llamada, Sofía pregunta para qué es** si la persona no
  lo dijo («quiero hablar con recepción» no es un motivo). El motivo queda en
  el registro de la derivación.

## Al colgar: el resumen en la ficha

Cuando Retell termina de procesar la llamada, el CRM le pide al modelo del
Agente de Voz un resumen y lo deja como nota en la ficha de quien llamó
(«📞 Resumen automático de la llamada»): qué quería, el siguiente paso, si
quedó cita en la agenda, su interés y su urgencia, y si Sofía se salió de sus
reglas. El mismo resumen sale en **Voz**. Su gasto cuenta para el tope y en Uso
aparece como «Resumen de llamadas».

- Si el modelo no contesta o devuelve algo que no encaja, no hay nota (nunca
  cifras inventadas) y el motivo sale en **Registros**. La transcripción
  completa sigue en la conversación.
- Si quien llama cuelga durante el saludo, o el tope de gasto está alcanzado,
  no se resume.
- «Cita en la agenda» dice si la herramienta de agenda la creó de verdad, no
  lo que Sofía contó.

## El saludo avisa de la grabación

Retell graba las llamadas y la LFPDPPP pide avisarlo antes de que la persona
hable. Si el canal no tiene saludo propio, Sofía dice el de tu perfil de
instalación (en `dental-mx`, con el nombre de la clínica), y si no hay perfil,
el genérico. Los dos avisan de que la llamada se graba. Si escribes el tuyo en
la tarjeta del canal, avísalo también.

## Probar

- Llama al número desde tu celular: contesta Sofía con el saludo del canal.
- Pide una cita y cuelga: la llamada sale en **Voz** con su transcripción y,
  al terminar, con su duración y su costo. En un minuto, la nota con el
  resumen aparece en la ficha del contacto.
- Si no contesta nadie, mira **Registros** en el panel: el motivo sale ahí
  (llave de Retell, agente sin publicar, WebSocket rechazado…).

## El prompt de voz tiene reglas que no se borran

La sección **SEGURIDAD — NO TOCAR** del prompt del Agente de Voz (no
diagnosticar, no recomendar medicamentos, no confirmar una cita que el sistema
no creó…) se puede ampliar, pero no quitar ni reescribir: el panel, el
historial de versiones y los asistentes externos rechazan el guardado que
pierda una de sus reglas. Una instalación que se sembró antes de esto puede
ganar las reglas médicas pegándolas dentro de esa sección.

## Si ya tenías un agente hecho a mano

Sigue funcionando: «Ya tengo uno en Retell» en el mismo formulario, con su
identificador. En ese caso hay que pegar en Retell las dos URLs que enseña el
panel. Cuando quieras, cámbialo a «Que el CRM cree el agente»: se crea uno
nuevo y el tuyo se queda como estaba.
