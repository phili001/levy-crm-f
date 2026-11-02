# n8n con Sofía

Hay dos formas de usar n8n con el CRM:

- **[Herramientas de n8n](#parte-1--herramientas-de-n8n)**: Sofía sigue pensando
  en el CRM (su prompt, su modelo) y tus flujos son herramientas que ella usa:
  buscar un pedido, mandar una foto, apuntar algo en una hoja.
- **[Sofía en n8n](#parte-2--sofía-en-n8n)**: tu flujo es el cerebro. Cada
  mensaje del paciente le llega a n8n y lo que conteste tu flujo es lo que sale.
  El CRM sigue recibiendo, entregando y mostrando todo en el inbox.

Si dudas, empieza por las herramientas: Sofía ya sabe conversar, agendar y
pasar a una persona, y tú solo le añades lo que le falta.

En [`flujos-n8n/`](flujos-n8n/) hay tres flujos listos para importar en n8n
(menú **⋯** del flujo → **Import from file...**): la foto de un tratamiento,
un precio en tu hoja de Google y «Sofía en n8n». Cada uno trae notas con lo que
hay que cambiar.

## Parte 1 · Herramientas de n8n

Sofía trae sus propias herramientas: buscar en la base de conocimiento, guardar
la ficha, agendar, pasar la conversación a una persona. Con una **herramienta de
n8n** le das una más, hecha con un flujo tuyo: buscar un pedido en tu base de
datos, mandarle al paciente la foto de un tratamiento, apuntar algo en una hoja
de cálculo… lo que puedas armar en n8n.

Sofía la usa como las suyas: decide cuándo, le pide al paciente los datos que
haga falta, y lo que contesta tu flujo le llega como dato para responder. Si tu
flujo devuelve archivos (imagen, audio, video o documento), el CRM se los manda
al paciente.

### Lo que necesitas

- **n8n con una dirección HTTPS pública.** En tu Coolify es un servicio de un
  clic (por ejemplo `https://n8n.miclinica.com`). El CRM no llama a direcciones
  `http://` ni a IPs internas.
- **El CRM en la versión 1.1.3 o más nueva.** Con YCloud, los archivos por
  WhatsApp salen desde la 1.1.2, y «Probar» de un agente en n8n que busca en tu
  base funciona desde la 1.1.3.

### Paso a paso

1. **En n8n**, crea un flujo nuevo con un nodo **Webhook**:
   - HTTP Method: `POST`.
   - Path: algo que lo identifique (`pedidos`, `foto-tratamiento`).
   - Authentication: **Header Auth**. Crea una credencial y deja el valor para
     el paso 3.
   - Respond: **Using 'Respond to Webhook' Node**.
2. **En el CRM**, ve a Conexiones → **n8n** → «Nueva»:
   - **Nombre**: en minúsculas y con guion bajo (`buscar_pedido`). Es como
     Sofía la llama.
   - **Para qué sirve**: lo que lee Sofía para decidir cuándo usarla. Di qué
     hace y en qué momento: «Busca un pedido por su número y dice en qué estado
     está. Úsala cuando el paciente pregunte por su pedido.»
   - **Datos que pide**: lo que Sofía le tiene que preguntar al paciente antes
     de usarla, cada uno con su clave (`numero`), su tipo y qué es.
   - **URL del webhook**: la «Production URL» del nodo Webhook.
   - **Tipo**: «Hace algo» si cambia algo (aparta, registra, manda) o «Solo
     consulta» si solo lee.
   - Guarda.
3. **Copia el secreto** («Copiar el secreto») y pégalo en la credencial de
   n8n: Name `X-Omnicanal-Token`, Value el secreto.
4. **Arma tu flujo**: lo que te llega está en el cuerpo del webhook (abajo).
   Termina con un nodo **Respond to Webhook** que conteste un JSON.
5. **Publica el flujo** en n8n (botón **Publish**, arriba a la derecha) y, en el CRM, dale
   **«Probar»**. Se llama a tu flujo con `prueba: true` y no le escribe a nadie.
6. **Actívala en el agente**: Asistentes → el agente → Tools disponibles →
   marca la herramienta (sale en el grupo «De n8n»). Si el agente está «sin
   configurar», ya puede usar todas.

### Lo que te manda el CRM

```json
{
  "evento": "herramienta",
  "herramienta": "buscar_pedido",
  "args": { "numero": "123" },
  "llamada_id": "9f2c…",
  "prueba": false,
  "conversacion": { "id": "…", "canal": "whatsapp" },
  "contacto": { "id": "…" },
  "agente": { "id": "…" }
}
```

- En `args` solo vienen los datos que declaraste.
- `contacto` trae solo el id. Si marcas «Mandar a n8n el nombre, el teléfono y
  el correo del paciente», también vienen `nombre`, `telefono` y `email`.
- `llamada_id` es igual si Sofía repite la misma herramienta con los mismos
  datos en el mismo turno: úsalo para no hacer dos veces lo mismo.
- `prueba: true` es el botón «Probar» del panel: no apartes ni registres nada.

### Lo que tienes que contestar

```json
{
  "resultado": "El pedido 123 va en camino y llega mañana.",
  "archivos": [
    { "tipo": "imagen", "url": "https://…/foto.jpg", "pie": "Antes y después", "nombre": "caso.jpg" }
  ]
}
```

- **`resultado`**: texto u objeto. Es lo que Sofía usa para contestar. Que sea
  corto: le llegan como mucho 8 KB.
- **`archivos`** (opcional): hasta 3. `tipo` es `imagen`, `audio`, `video` o
  `documento`. El CRM baja cada uno de su `url` (HTTPS pública), comprueba que
  es lo que dice y se lo manda al paciente. Tope: 5 MB las imágenes, 16 MB lo
  demás.

| Canal | ¿Manda archivos? |
|---|---|
| WhatsApp | Sí, los cuatro tipos |
| Messenger (Zernio) | Sí, los cuatro tipos |
| Instagram (Zernio) | Imagen, audio y video; documentos no |
| Chat de la web | Sí, los cuatro tipos |
| Correo, llamadas | No: Sofía ofrece otra forma |

En **Entrenamiento** no sale nada: el archivo queda en la sugerencia y se manda
cuando alguien le da Enviar.

### Dos ejemplos

Los dos están en [`flujos-n8n/`](flujos-n8n/), listos para importar.

**Mandar la foto de un tratamiento** (`mandar-foto-tratamiento.json`).
- Nombre: `mandar_foto_tratamiento`. Tipo: «Solo consulta».
- Dato que pide: `tratamiento` (tipo «Opciones»: blanqueamiento, ortodoncia,
  implantes).
- Flujo: Webhook → un Code con la foto de cada tratamiento → Respond to Webhook
  con `{"resultado": "…", "archivos": [{"tipo": "imagen", "url": "…", "pie": "…"}]}`.
- Cambia las URL por las fotos de tu clínica: públicas, `https://`, JPG o PNG.
  Un `.webp` no sirve como foto.

**Consultar un precio en tu hoja de Google** (`buscar-en-hoja.json`).
- Nombre: `consultar_precio`. Tipo: «Solo consulta».
- Dato que pide: `tratamiento` (texto).
- La hoja: primera fila con `tratamiento`, `precio`, `duracion` y `notas`.
- Flujo: Webhook → Google Sheets (lee la hoja) → un Code que busca el
  tratamiento sin fijarse en acentos ni mayúsculas → Respond to Webhook. Si no
  está, Sofía recibe la lista de los que sí hay.

### Cuando algo no sale

| Lo que ves | Por qué | Qué hacer |
|---|---|---|
| «Probar» dice HTTP 403 | El secreto de n8n no es el del CRM | Copia otra vez el secreto y pégalo en la credencial Header Auth |
| «Probar» dice HTTP 404 | El flujo no está publicado, o pegaste la «Test URL» | Dale **Publish** y usa la «Production URL» |
| «n8n no contestó en 8 s» | El flujo tarda demasiado | Contesta antes con «Respond to Webhook» y haz lo lento después, o sube la espera (máx. 15 s) |
| Al guardar: «URL del webhook…» | La dirección es `http://` o apunta dentro del servidor | Usa la dirección HTTPS pública de tu n8n |
| Sofía no la usa | No está activada en el agente, o «Para qué sirve» no dice cuándo usarla | Márcala en Tools disponibles y describe el momento de usarla |
| Un archivo «no enviado» | El canal no lo admite, pesa demasiado o no es lo que dice ser | Revisa la tabla de canales y el tipo del archivo |
| Una de «Hace algo» falló y la conversación pasó a una persona | n8n no confirmó, y Sofía no puede dar por hecho lo que no sabe | Revisa la ejecución en n8n y contéstale al paciente desde el inbox |

En una llamada de voz las herramientas también funcionan, pero se espera como
mucho 6 segundos y no se mandan archivos.

### Qué sale del CRM

- Solo los datos que declara la herramienta, el canal y los ids. Nombre,
  teléfono y correo del paciente solo si lo marcas.
- El secreto va en la cabecera `X-Omnicanal-Token`, por HTTPS. Si se filtra,
  dale a «Regenerar» y pega el nuevo en n8n: el viejo deja de valer al momento.
- Para los archivos, el CRM no sigue redirecciones y descarta lo que no es un
  archivo de verdad (una página web disfrazada de imagen, por ejemplo).

## Parte 2 · Sofía en n8n

Con «Sofía en n8n», un agente de texto deja de pensar en el CRM: el prompt, el
modelo y las herramientas los pones tú en n8n (por ejemplo, con el nodo **AI
Agent**). El CRM hace todo lo demás:

- recibe por WhatsApp, Instagram, Messenger, correo y el chat de la web;
- aparta el spam y espera a que el paciente termine de escribir;
- respeta la ventana de 24 h de WhatsApp;
- parte la respuesta y le da el formato de cada canal;
- aplica el modo Entrenamiento y las reglas del correo;
- y lo muestra todo en el inbox, donde puedes tomar el control cuando quieras.

Las llamadas de voz no se pueden mover a n8n: necesitan contestar en tiempo
real. En un agente de voz usa herramientas de n8n.

### Lo que necesitas

- **n8n con una dirección HTTPS pública**, como en la parte 1.
- **Un agente de texto.** Puedes crear uno nuevo para no tocar el que ya
  atiende: los canales eligen su agente, así que WhatsApp puede ir a n8n
  mientras el chat de la web sigue con Sofía.

### Paso a paso

1. **En n8n**, crea un flujo con un nodo **Webhook**: HTTP Method `POST`,
   Authentication **Header Auth** (crea la credencial y deja el valor para el
   paso 3) y Respond **Using 'Respond to Webhook' Node**.
2. **En el CRM**, ve a Asistentes → el agente de texto → Editar → **Motor** →
   «Un flujo de n8n». Pega la «Production URL» del Webhook y dale **Guardar
   cambios**. El CRM genera el secreto y el editor se queda abierto.
3. **Copia el secreto** («Copiar») y pégalo en la credencial de n8n: Name
   `X-Omnicanal-Token`, Value el secreto.
4. **Arma el flujo** o importa [`flujos-n8n/sofia-en-n8n.json`](flujos-n8n/sofia-en-n8n.json):
   Webhook → **AI Agent** (tu prompt, tu modelo, tus herramientas) → **Respond
   to Webhook**, con «Respond With: JSON». El de ejemplo es Sofía completa: su
   prompt y tres herramientas (`buscar_en_conocimiento` en la base del CRM con
   el token del paso 5, `mandar_foto` y `pasar_a_persona`); «Arma la respuesta»
   lee lo que hizo el agente y devuelve `respuesta`, `archivos` y
   `acciones.pasar_a_humano`, y si el AI Agent falla, pasa la conversación a
   una persona en ese momento. Lo que te llega está abajo.
5. **El token «Para n8n»:** si tu flujo busca en la base de conocimiento del
   CRM (el de ejemplo lo hace en cada mensaje), contesta más tarde o cambia la
   ficha por la API, crea un token en Conexiones → API / MCP → «Nuevo token» →
   **«Para n8n»** y guárdalo en n8n como credencial (Header Auth, Name
   `Authorization`, Value `Bearer <token>`). Es otra credencial, no la del
   Webhook. Va antes de probar: sin él, la búsqueda falla y n8n corta el flujo.
6. **Publica el flujo** (**Publish**) y, en el CRM, dale **«Probar»**: se manda un mensaje
   de ejemplo con `prueba: true` y ves lo que le llegaría al paciente.
7. **Pon ese agente en el canal**: en Canales, en la tarjeta del canal,
   **«Responde:»** → elígelo.

### Lo que te manda el CRM

Un `POST` por turno, con `X-Omnicanal-Token` y `X-Omnicanal-Evento: mensajes`:

```json
{
  "evento": "mensajes",
  "evento_id": "4b1e…",
  "prueba": false,
  "conversacion": { "id": "…", "canal": "whatsapp", "entrenamiento": false,
                    "ventana_abierta": true, "ventana_cierra": "2026-10-12T18:20:00Z" },
  "contacto": { "id": "…", "nombre": "Ana López", "telefono": "+529981112233",
                "email": null, "etapa": "contacto", "etiquetas": ["limpieza"] },
  "mensajes": [
    { "id": "…", "tipo": "texto", "texto": "¿Tienen cita mañana?", "fecha": "…" },
    { "id": "…", "tipo": "audio", "transcripcion": "…", "archivo_url": "https://…/api/v1/media/p/…" }
  ],
  "historial": [ { "rol": "paciente", "texto": "…", "fecha": "…" },
                 { "rol": "sofia", "texto": "…", "fecha": "…" } ],
  "agente": { "id": "…", "nombre": "Agente de Texto" },
  "api": { "base": "https://crm…/api/v1/agent-api", "conversacion": "/conversations/…" }
}
```

- `mensajes` son los que el paciente mandó desde la última respuesta (si
  escribe tres seguidos, llegan los tres juntos). Las notas de voz ya vienen
  transcritas.
- Los archivos del paciente traen un enlace que caduca en 1 hora.
- `historial` son los últimos mensajes de la conversación (los que marque
  «Context window» en el agente). Las sugerencias sin enviar no vienen.
- `evento_id` es el mismo si el CRM repite el envío porque n8n falló:
  úsalo para no contestar dos veces.
- `prueba: true` es el botón «Probar»: no le escribas a nadie.

### Lo que tienes que contestar

Con el nodo **Respond to Webhook**, antes de la «Espera máxima» del agente (25
s por defecto). Todo es opcional:

```json
{
  "respuesta": "¡Hola Ana! Mañana tengo hueco a las 10:00 y a las 17:30.",
  "archivos": [ { "tipo": "imagen", "url": "https://…/promo.jpg" } ],
  "acciones": {
    "pasar_a_humano": "pide precio especial",
    "etapa": "cliente",
    "etiquetas": ["limpieza"],
    "ficha": { "nombre": "Ana López", "email": "ana@correo.com" }
  }
}
```

- **`respuesta`**: lo que le llega al paciente. El CRM le da el formato del
  canal y, si es larga, la parte en varios mensajes. En Entrenamiento queda
  como sugerencia en el inbox; por correo, como borrador (o sale sola si
  activaste «Sofía envía sola»).
- **`archivos`**: igual que en la parte 1 (mismos tipos, topes y canales).
- **`acciones.pasar_a_humano`**: la conversación pasa a una persona y
  `respuesta` se usa como mensaje para el paciente (hasta 280 caracteres; sin
  `respuesta`, sale el mensaje de derivación del agente).
- **`etapa`, `etiquetas`, `ficha`**: se aplican a la ficha. El teléfono no se
  puede cambiar: es lo que identifica al paciente.
- **Sin `respuesta` ni `archivos`** (`{}`): «lo mando después por la API».

### Contestar más tarde, por la API

Para flujos lentos (más de la espera máxima) o mensajes que quieras mandar
después. Necesitas el token «Para n8n» (paso 5), en la cabecera
`Authorization: Bearer <token>`. La base es `api.base` del evento:

| Para | Llamada |
|---|---|
| Mandar un mensaje | `POST /conversations/{id}/messages` con `{"texto": "…", "archivos": [...], "idempotency_key": "<evento_id>"}` |
| Leer la conversación | `GET /conversations/{id}/messages` |
| Pasarla a una persona | `POST /conversations/{id}/handoff` con `{"motivo": "…", "mensaje_al_cliente": "…"}` |
| Buscar en la base de conocimiento | `POST /kb/search` con `{"query": "…", "top_k": 4}` |
| Cambiar la ficha | `PATCH /contacts/{id}` con `{"nombre", "email", "etapa", "etiquetas_agregar"}` |

- La misma `idempotency_key` no se manda dos veces (durante 24 h). Usa el
  `evento_id`.
- **409** si una persona tomó la conversación (n8n ya no contesta ahí), si la
  ventana de 24 h de WhatsApp está cerrada (solo vale una plantilla desde el
  panel) o si es de correo (el correo se contesta en la respuesta directa).
- Si contestas `{}` y en **10 minutos** no llega nada por la API, la
  conversación pasa a una persona. Si la respuesta directa no llegó a tiempo,
  el plazo es de **2 minutos**: el CRM no repite el envío, por si tu flujo
  sigue trabajando.

### Qué deja de usarse

Con el motor en n8n, del agente **no se usan**: el prompt, el modelo, las
herramientas, el tope de gasto, la moderación del CRM ni las reglas de estilo
aprendidas. Lo que gaste tu flujo no aparece en Tarifas y límites. En el
inbox, a una sugerencia de n8n no se le puede dar «Corregir» (llamaría al
modelo del CRM): se edita a mano.

Lo que **sí** se usa: el nombre, «Context window» (cuánto historial te llega),
el mensaje de derivación a humano (si n8n falla) y que el agente esté activo.

### Dónde se ve

- En el inbox, las respuestas de n8n llevan la marca **n8n** junto a «Bot».
- En **Actividad del agente** de cada conversación sale una línea por turno:
  «Turno enviado a n8n · 1.8 s · respuesta directa» (o «contesta después por
  la API», «pasa a una persona», «sin respuesta»).

### Cuando algo no sale

| Lo que ves | Por qué | Qué hacer |
|---|---|---|
| «Probar» dice HTTP 403 | El secreto de n8n no es el del CRM | Copia otra vez el secreto y pégalo en la credencial Header Auth |
| «Probar» dice HTTP 404 | El flujo no está publicado, o pegaste la «Test URL» | Dale **Publish** y usa la «Production URL» |
| «Probar» dice HTTP 405 | El Webhook no está en `POST` | En el nodo Webhook, HTTP Method = POST |
| «Contestó sin `respuesta`» | El Respond to Webhook no devuelve `respuesta` | Revisa el cuerpo del paso 4 |
| La conversación pasó a una persona sola | n8n falló (después de 2 reintentos) o no contestó a tiempo ni por la API | Revisa la ejecución en n8n; en Actividad del agente ves cuál de las dos |
| La API contesta 409 | Una persona tomó la conversación, la ventana de 24 h está cerrada o es correo | Es a propósito: el inbox es de quien atiende |
| Al guardar: «Un agente de voz no puede pensar en n8n» | El agente es de voz | Usa un agente de texto, o herramientas de n8n en la voz |

