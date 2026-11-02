# API — dónde está el contrato de verdad

> **Este documento ya no lista endpoints.** La versión anterior era el contrato
> del diseño inicial y llevaba errores que producían código roto: rutas que no
> existen, formatos de error que no son, cuerpos de petición que en realidad
> van por la ruta y parámetros que FastAPI ignora en silencio. Se ha
> sustituido por el mapa de dónde mirar, más los cuatro detalles que más veces
> hacen fallar el primer intento.

## El contrato es el OpenAPI

FastAPI lo genera solo a partir del código, así que no puede desfasarse.

- **`GET /openapi.json`** — el esquema completo.
- **`GET /docs`** — Swagger, para probar a mano. También hay `/redoc`.

**Ojo:** los tres están **desactivados cuando `APP_ENV` es producción**
(`backend/app/main.py`, `docs_url=None if settings.is_production`). Para verlos,
levanta el entorno de desarrollo.

## Los routers, y por tanto los prefijos

Todo cuelga de `/api/v1` salvo `/health`, que va en la raíz para el healthcheck
de Docker. El montaje está en `backend/app/main.py`; los prefijos, en cada
router:

| Prefijo | Fichero |
|---|---|
| `/auth` | `api/auth.py` |
| `/webhooks` | `api/webhooks.py` |
| `/contacts` | `api/contacts.py` |
| `/contacts/{id}/merge-suggestions`, `/contacts/merge-suggestions/{id}/dismiss` | `api/fusiones.py` |
| `/tags` | `api/tags.py` |
| `/conversations` (REST + WebSocket) | `api/conversations.py` |
| `/kb` | `api/knowledge_base.py` |
| `/admin/learning` | `api/learning.py` |
| `/oauth` | `api/oauth.py` |
| `/admin` | `api/admin.py` |
| `/admin/backups` | `api/backups.py` |
| `/agent-api` | `api/agent_api.py` |
| `/agent-api/conversations/{id}/…`, `/agent-api/contacts/{id}` (n8n) | `api/agent_api_conversaciones.py` |
| `/uploads` | `api/uploads.py` |
| `/voice` | `api/voice.py` |
| `/webchat` | `api/webchat.py` |
| `/push` | `api/push.py` |
| `/apariencia` | `api/apariencia.py` |
| `/admin/channels/email/imap` | `api/correo_imap.py` |
| `/admin/zernio` | `api/zernio_admin.py` |
| `/legal` | `api/legal.py` |
| `/whatsapp/plantillas`, `/conversations/{id}/template` | `api/plantillas_chat.py` |
| `/agenda`, `/appointments`, `/schedule-blocks` | `api/agenda.py` |
| `/primer-arranque` | `api/primer_arranque.py` |
| (raíz, sin prefijo) | `api/health.py` |

`api/admin.py` es un fichero grande y suelto: **no existe** un directorio
`api/admin/`. Todo lo de administración está ahí dentro.

## Los cuatro detalles que rompen el primer intento

### 1. El formato de error NO es uniforme

Solo las excepciones **no controladas (500)** salen como
`{"error": {"code": "INTERNAL_ERROR", "message": …}}`. Lo pone el manejador de
`Exception` en `backend/app/main.py`.

Todo lo demás —401, 403, 404, 409, 422, 429— es una `HTTPException` de FastAPI y
sale como **`{"detail": "…"}`**. No escribas un cliente que espere `error.code`
en los 4xx.

### 2. El WebSocket del inbox va con ticket, no con el JWT

`/api/v1/ws/inbox` acepta **un ticket efímero de un solo uso**, que se pide con
`POST /auth/ws-ticket`. El JWT por query string se retiró a propósito: acababa
en los logs de los proxies. El parámetro es `?ticket=`, no `?token=`
(`api/conversations.py`, `ws_inbox`).

Cada mensaje es `{"type": …, "payload": {…}}`. Desde la 1.3.3:

- **`message.new` de un mensaje entrante lleva `canal`** (el valor de
  `conversation_canal`: `whatsapp`, `instagram_dm`, `web`…) **y ya no
  `from_phone`**. Ningún aviso de la bandeja lleva teléfonos ni correos: si los
  necesitas, pide la conversación por HTTP.
- **El revisor solo recibe los avisos de conversaciones de Instagram**, con el
  mismo criterio que el HTTP (`services/bandeja_acceso.py`). Los avisos sin
  `conversation_id` (`agenda.changed`, `fusion.changed`) no le llegan. Admin y
  cliente reciben todo.

### 3. Las etiquetas se asignan por la RUTA, no en el cuerpo

`POST /contacts/{contact_id}/tags/{tag_id}` y
`DELETE /contacts/{contact_id}/tags/{tag_id}`. No hay cuerpo `{tag_id}`.

### 4. No todo devuelve un objeto con envoltorio

Hay de los dos tipos y hay que mirarlo endpoint por endpoint:

- `GET /conversations` → paginado, `Page[ConversationOut]`.
- `GET /conversations/{id}/messages` → **lista pelada** de mensajes.
- `POST /kb/search` → **lista pelada** de resultados.

Y un aviso general: **FastAPI ignora en silencio los parámetros de query que no
están declarados en la firma**. Si le pasas un filtro que el endpoint no
declara, no da error: devuelve los datos sin filtrar. Antes de fiarte de un
parámetro, míralo en la firma de la función.

## Bloque común del prompt

`GET /api/v1/admin/settings/prompt-comun` → `{bloque_comun}`; `PUT` con el mismo
cuerpo (máx. 20,000 caracteres) lo guarda en `app_settings` (`prompt.bloque_comun`).
`assemble_system_prompt` lo antepone al prompt de **todos** los agentes, entre la
capa de seguridad y el prompt propio, así "Ver prompt efectivo" lo muestra tal cual.

## Checklist de Inicio y demo pública

`GET /api/v1/admin/onboarding` devuelve **siete** pasos calculados del estado real
(contraseña, clave de IA, datos pendientes en agentes y bloque común, KB con
algo indexado, canal real conectado, mensaje respondido, canal en Activo). El
widget solo cuenta como canal si `allowed_domains` trae un dominio distinto al
del panel, y nunca cierra el paso 7.

`GET /api/v1/webchat/demo` (público, 60/min) → `{api_key, api_base, title, greeting}`
del canal de chat web activo, solo si `app_settings["ui.demo_publica"] == "1"`;
si no, 404. Lo usa la página pública `/demo` del panel.

`GET|PUT /api/v1/admin/channels/webchat/demo` (admin) → `{encendida: bool}`: el
interruptor de la demo, en la ventana «Chat en tu web» de Conexiones. El `PUT`
deja auditoría (`demo_publica.encendida` / `demo_publica.apagada`). Antes solo lo
ponía el perfil de instalación.

`GET /api/v1/admin/channels/webchat/snippet` (y el `provision`) devuelven el
código para pegar con `data-title` = título del canal → nombre de Apariencia →
`APP_NAME`, `data-greeting` = saludo del canal → el genérico y `data-brand` = color
de Apariencia, con los valores escapados para HTML.

## Plantillas de WhatsApp desde el chat

Con la ventana de 24 h cerrada, el texto libre a WhatsApp devuelve 400 y el
operador contesta con una plantilla. Cualquier sesión de operador (no revisor)
y sin depender del módulo de envío masivo:

- `GET /api/v1/whatsapp/plantillas`: solo las que salen con texto (aprobadas,
  sin fichero en la cabecera ni variables en cabecera o botones).
- `POST /api/v1/conversations/{id}/template` con `{template_name, language,
  variables}` (en el orden de los huecos). Envía, guarda el mensaje en el hilo
  como `operator` con el texto rellenado y `metadata.template`, pasa la
  conversación a `humano` y audita `conversation.template_sent`. No mira las
  bajas de difusiones; sí los números bloqueados.

## Crear plantillas desde el panel (SPEC-18)

Canales → Plantillas. Solo admin. El listado sigue siendo
`GET /api/v1/admin/whatsapp/templates`, en vivo contra el proveedor, y ahora
trae también `botones` (`[{tipo, texto, valor}]`, el tipo en mayúsculas como
Meta) y `reason`, el motivo del rechazo (es un código de Meta, como
`INVALID_FORMAT`; el panel lo dice en palabras).

- `GET /api/v1/admin/whatsapp/plantillas` → `{proveedor, puede_crear, idiomas,
  campos, sugeridas}`. Solo se crean con YCloud (`puede_crear`); con la API de
  Meta la pestaña solo lista. `sugeridas` son las de
  `services/plantillas_biblioteca.py` (recordatorio, cita perdida, cita
  agendada y seguimiento), con `_v1` en el nombre.
- `POST /api/v1/admin/whatsapp/plantillas/generar` con `{descripcion,
  categoria?, idioma}` (10 por minuto) → `{plantilla, avisos}`. Una llamada al
  modelo del Agente de Texto con el bloque «Sobre el negocio»; el gasto sale en
  Consumo como `plantillas`. Es un borrador: `avisos` dice lo que falta. 502 si
  el modelo no contesta o no devuelve una plantilla.
- `POST /api/v1/admin/whatsapp/plantillas` con la plantilla (`nombre`,
  `categoria` UTILITY|MARKETING, `idioma`, `cabecera`, `cuerpo`, `variables`
  `[{n, etiqueta, ejemplo, campo}]`, `pie`, `botones` `[{tipo:
  respuesta|enlace|telefono, texto, valor}]`) → 201 `{nombre, idioma,
  estado}`. Antes de llamar a YCloud devuelve 400 con lo que Meta rechazaría
  (variables saltadas, al principio o al final, sin ejemplo; respuestas
  rápidas separadas por un enlace…). 409 con la API de Meta. 422 con el motivo
  de YCloud si no la acepta. Crea con el `wabaId` del número (se busca en
  `/whatsapp/phoneNumbers`; no se guarda), guarda las etiquetas en
  `whatsapp_template_var` y audita `whatsapp_template.created`.

## Correo por IMAP/SMTP (buzón con contraseña de aplicación)

Solo admin. Un canal de correo por instalación: conectar el buzón lo deja en
`config.transport = "imap"`; `POST /admin/channels/email/provision` (Gmail
OAuth) lo devuelve a `"gmail"`.

- `GET /api/v1/admin/channels/email/imap/presets` → `[{id, nombre, imap, smtp,
  ayuda}]` (Gmail, Hostinger, cPanel, Zoho, Zoho con dominio; «otro» = manual).
- `POST …/imap/probar` con `{preset, usuario, password?, imap?, smtp?, carpeta}`
  → `{imap_ok, smtp_ok, errores, carpeta_enviados}`. Outlook/Microsoft 365
  (dominio o MX) se rechaza sin intentar. `password` vacía reutiliza la guardada
  SOLO con el mismo usuario y servidores (422 si no).
- `POST …/imap` con lo mismo + `{auto_send, no_responder}`: prueba y, solo si
  entra y envía, guarda. La contraseña va cifrada (`imap_password`,
  `smtp_password`) y `GET /admin/channels` la enmascara.

La ingesta (`poll-imap`, cada 2 min) y el envío por SMTP en el mismo hilo están
en `services/imap_ingest.py` y `services/correo_smtp.py`. Los adjuntos se
guardan en disco al llegar (`attachments[].local = true`) y los sirve el mismo
`GET …/attachments/{attachment_id}`.

## Instagram y Messenger por Zernio

Guía de uso: `docs/avanzado/zernio.md`. Solo admin salvo el webhook.

- `GET /api/v1/admin/zernio` → `{tiene_llave, webhook_id, webhook_url,
  canales: [{channel_id, tipo, nombre, account_id, enabled, estado}],
  wa_por_zernio}`. Nunca devuelve la llave ni el secreto.
- `POST …/zernio/probar` con `{api_key?}` (vacía = la guardada) → las cuentas
  de Zernio: `[{id, platform, username, display_name, profile_id,
  needs_reconnection}]`. `400` si Zernio no acepta la llave; `502` si no
  contesta.
- `POST …/zernio` con `{api_key?, instagram_account_id?, facebook_account_id?,
  wa_por_zernio, whatsapp_account_id?}`: crea o reutiliza los canales
  `instagram_dm` (`transport = "zernio"`) y `messenger`, borra el aviso
  anterior y registra uno nuevo con un secreto nuevo, y encola
  `zernio_backfill` para cada cuenta nueva. Sin cuenta elegida y con varias de
  una plataforma, esa plataforma no se conecta. Devuelve el estado más
  `{webhook_registrado, webhook_secret_manual, aviso, historial_encolado}`;
  `webhook_secret_manual` solo viene cuando Zernio no aceptó el registro, para
  pegarlo a mano.
- `POST …/zernio/probar-webhook` → Zernio manda un evento de prueba.

Secretos cifrados en el canal (`channel_secrets.SECRET_KEYS`) y enmascarados
en `GET /admin/channels`: `zernio_api_key`, `zernio_webhook_secret`. En claro
en `config`: `transport`, `zernio_account_id`, `zernio_profile_id`,
`zernio_cuenta`, `zernio_webhook_id`, `zernio_estado`.

### El webhook

`POST /api/v1/webhooks/zernio` (público, 120/min). Un solo endpoint para todas
las plataformas; el reparto es por `account.platform`: `instagram` →
`instagram_dm`, `facebook` → `messenger`, `whatsapp` → `whatsapp` solo con
`wa_por_zernio`. Una cuenta que no es la guardada en el canal se ignora.

- Firma: `X-Zernio-Signature` (o `X-Late-Signature`) = HMAC-SHA256 hex del
  cuerpo crudo con el secreto registrado. Sin firma o mala → `401`.
- Reentregas: se reconoce el `id` del evento (o `X-Zernio-Event-Id`) durante
  52 h y se contesta `{ok, repetido: true}`. La marca se pone después de
  procesar; `store_incoming` es además idempotente por `platformMessageId`.
- `message.received` → mensaje del cliente (`ig:<igsid>` / `fb:<psid>`) y se
  encola al agente. Los ids de Zernio van en `messages.extra.zernio`
  (`conversation_id`, `account_id`, `platform_message_id`, índices de
  adjuntos): con ellos se contesta y se renuevan las URL de los adjuntos.
- `message.sent` → eco. `sentVia: null` (app de IG/FB) o `human` (inbox de
  Zernio) = una persona: se guarda como operador y la conversación pasa a
  humano. `api` = lo mandó el CRM: se ignora.
- `account.connected|disconnected` → `zernio_estado` en el canal; la tarjeta
  de Conexiones se pone en rojo.
- Todo lo demás (`reaction.received`, `webhook.test`…) → `200` sin hacer nada.

## Voz por Retell: alta del agente y número

Guía de uso: `docs/avanzado/voz.md`. Solo admin.

- `POST /api/v1/admin/channels/retell/provision` con `{api_key, agent_id_retell,
  voice_id, phone_number, greeting?, create_agent, transfer_number, buy_number,
  area_code}`. Al editar, un campo vacío conserva lo guardado (la llave incluida).
  - Sin `create_agent` (a mano): exige `api_key` y `agent_id_retell`, activa las
    grabaciones firmadas en ese agente y devuelve las dos URLs que hay que pegar
    en Retell (`llm_webhook_url`, `call_webhook_url`).
  - Con `create_agent: true`: el CRM crea su agente (Custom LLM hacia
    `…/voice/retell/llm-ws`, webhook, `es-419`, grabaciones firmadas, tope de
    silencio y de duración) y lo publica, o pone al día el que ya creó
    (`config.agente_gestionado`). Un agente pegado a mano no se reutiliza ni se
    repunta. `buy_number: "us"` compra un número de EE. UU. (con `area_code`
    opcional); `phone_number` ata uno que ya esté en la cuenta de Retell (`400`
    si no está). El número se ata con `agent_version: "latest_published"`.
  - `transfer_number` (recepción, para la 8.2) va con `+` y lada de país; `400`
    si no.
  - Devuelve además `{agent_id_retell, agent_created, agent_managed, phone_number,
    checks: [{name, ok, detail}]}`: lo comprobado leyendo de Retell
    (`agente_publicado`, `contesta_el_crm`, `idioma_es_419`, `webhook_del_crm`,
    `grabaciones_firmadas`, `numero_en_retell`, `numero_atado_al_agente` y, con
    recepción y número, `recepcion_permitida`: si la lista de países de salida
    del número en Retell no incluye el de recepción, el CRM lo añade — solo
    ese — y lo dice en `detail`). `POST …/retell/twilio` la añade también.
  - Un error de Retell con respuesta 4xx → `400` con su motivo; sin respuesta o
    5xx → `502`. La llave se prueba contra Retell antes de guardar nada: una
    que Retell rechaza no pisa la guardada ni deja un canal a medias. El agente
    se guarda antes de comprar el número: el reintento reutiliza el mismo.
- `POST …/channels/retell/twilio` con `{account_sid, auth_token, phone_number,
  trunk_prefix?}`: número de Twilio (México) → troncal SIP (se reutiliza la del
  número si ya solo manda a Retell; si no, `<prefijo>.pstn.twilio.com`, por
  defecto el dominio de la instalación) → originación a `sip:sip.retellai.com`
  → IPs de Retell autorizadas → importado en Retell y atado al agente del canal.
  `400` si todavía no hay agente. Devuelve `{phone_number, trunk_sid,
  trunk_domain, trunk_created, number_moved, checks}`. La credencial de Twilio
  **no se guarda** ni va a la auditoría.

En claro en `config` del canal `retell_voice`: `agent_id_retell`, `voice_id`,
`phone_number`, `greeting`, `agente_gestionado`, `transfer_number`,
`twilio_trunk_sid`, `twilio_trunk_domain`. Cifrada: `api_key`.

Pasar la llamada (8.2): en voz, `derivar_humano` decide. En horario
(`calendar.*`) y con `transfer_number` en el canal, deja el número en el
contexto de la llamada y el turno contesta a Retell con `transfer_number`
(transferencia en frío tras decir la frase; nunca junto con `end_call`, que
ganaría) y la conversación sigue en `bot`. Fuera de horario, sin número o si ya
se intentó en esa llamada, deriva como siempre (`humano`) y la herramienta
devuelve `{"accion": "recado", "por_que"}`: al colgar la conversación no se
archiva y queda en «Para hacer» (ni al cerrarse el WebSocket ni con el webhook
`call_ended`). La respuesta del turno transferido guarda
`extra.transferida_a`.

Al colgar (8.3): `POST /api/v1/voice/retell/webhook` con `call_analyzed` encola
`analyze_call` una vez por `call_id` (marca `voice:resumen:{call_id}` en Redis,
24 h). La tarea deja una `contact_note` sin autor («📞 Resumen automático de la
llamada»), la actividad `note_added` con `meta={origen: "llamada",
conversation_id}` y sustituye `conversations.resumen`; el `call_summary` de
Retell solo se guarda si no había resumen. En voz, `consultar_disponibilidad`
devuelve `label` hablado ("mañana a las diez de la mañana"); `start`/`end`
siguen en ISO con el desfase de la zona. Saludo al descolgar: `greeting` del
canal → `app_settings["voice.greeting"]` (perfil) → genérico, los dos últimos
con aviso de grabación.

El prompt del agente de voz tiene una sección "NO TOCAR": `PATCH
/admin/agents/{id}`, restaurar del historial y `PUT
/agent-api/agents/{id}/prompt` responden `400` si el prompt nuevo quita o
cambia una de sus reglas (añadir sí se puede). `services/prompt_guard.py`.

## Aviso de privacidad y términos

- `GET /api/v1/legal` (público) → `{nombre, responsable, domicilio, correo,
  telefono, actualizado, completo}`. Lo leen las páginas `/privacidad` y
  `/terminos` del panel.
- `PUT /api/v1/legal` (solo admin, queda en auditoría) con `{responsable,
  domicilio, correo, telefono}`. `422` si el correo no es válido. Se guarda en
  `app_settings` (`legal.*`).

## Anuncios de Meta (Click to WhatsApp)

No hay endpoint propio: el anuncio viaja en `metadata.anuncio` del mensaje
entrante (`GET /conversations/{id}/messages`) con `{tipo, id, url, titulo, texto,
medio, imagen, video, ctwa_clid}` (solo las claves que vinieron; `url`, `imagen`
y `video` solo si son http/https). La ficha registra la actividad
`llegada_desde_anuncio` con `{titulo, conversation_id}`. La ventana gratuita de
72 h se calcula con los mensajes (`services/anuncios.py` y
`frontend/src/lib/anuncios.ts`, misma regla) y no cambia el bloqueo de 24 h
del texto libre.

## Apariencia (marca blanca)

`GET /api/v1/apariencia` es **público**: el login lo necesita antes de que haya
sesión. Devuelve `{nombre, paleta, color_marca, tipografia, trazo_iconos,
logo_url, tipografias, trazos}`. `nombre: null` significa "el de la instalación"
(`APP_NAME`); `logo_url: null`, "el de fábrica".

Solo admin, y cada uno deja entrada en la auditoría (`apariencia.*`):

- `PUT /api/v1/apariencia` con `{nombre, paleta, color_marca, tipografia,
  trazo_iconos}`. 422 si el color no es `#RRGGBB` o la tipografía o el trazo no
  están en la lista cerrada (el nombre de la tipografía acaba en una URL de
  Google Fonts).
- `DELETE /api/v1/apariencia` vuelve a la de fábrica, logotipo incluido.
- `PUT /api/v1/apariencia/logo` (multipart, campo `fichero`, máx. 256 KB, 30/h).
  El tipo se decide por los bytes: PNG, WebP, JPG o SVG sin código dentro.
- `DELETE /api/v1/apariencia/logo`.

`GET /api/v1/apariencia/logo?v=<huella>` sirve la imagen sin sesión, con
`Content-Security-Policy: sandbox`. La huella cambia con el fichero, así que la
URL puede cachearse. Todo se guarda en `app_settings` (`apariencia` y
`apariencia_logo`), dentro de las copias de seguridad.

## Agenda propia (Fase 9)

La lógica vive en `services/agenda.py` y `services/calendar_sync.py`; el router
del panel es `api/agenda.py`. Sesión de operador o admin (`get_current_user`);
el revisor de Meta no entra. Las horas salen ya en la hora del negocio
(`inicio_local` / `fin_local`, "AAAA-MM-DDTHH:MM") junto a las absolutas
(`starts_at`, `ends_at`), y una hora que llega sin desfase se entiende en la
zona del negocio: la recepción puede estar en otro país.

- `GET /agenda?desde=AAAA-MM-DD&hasta=AAAA-MM-DD` (máx. 42 días): lo que pinta la
  pestaña Agenda. `{zona, hoy, ahora_local, work_days, work_hours, holidays,
  slot_step_min, servicios[], citas[], bloqueos[]}`. Las citas van TODAS, también
  canceladas; el panel decide qué enseñar.
- `GET /appointments/today` → `{hoy, zona, citas[]}`: las activas de hoy, para
  «Citas de hoy» en Para hacer.
- `GET /appointments?contact_id=…[&proximas=true]`: las de una persona (la más
  nueva primero, máx. 100) o solo sus próximas activas (máx. 3).
- `GET /appointments/services` → `[{nombre, duracion_min}]` (catálogo
  `agenda.servicios`).
- `GET /appointments/slots?fecha=…&servicio=…[&duracion_min=…][&excluir_cita=…]`
  → `{fecha, zona, servicio, duracion_min, cerrado, horarios: ["HH:MM"]}`. Todos
  los huecos del día (sin el tope de ocho del agente). `excluir_cita` = la que
  se está moviendo, cuyo hueco cuenta como libre.
- `POST /appointments` `{contact_id, servicio, starts_at, duracion_min?, notas?}`
  → 201 con la cita (`creada_por: "operador"`). 409 si pisa otra cita, 422 si es
  en el pasado, 404 si el contacto no existe. Fuera de horario o encima de un
  bloqueo, sí se puede: decide una persona.
- `PATCH /appointments/{id}`, una cosa por petición: `{starts_at}` mueve y
  devuelve la cita NUEVA (la vieja queda `cancelada`); `{estado: "completada" |
  "no_asistio"}` la marca; `{servicio?, duracion_min?, notas?}` la cambia en el
  sitio (409 si con la nueva duración pisa la siguiente).
- `DELETE /appointments/{id}` cancela (no borra) y devuelve la cita.
- `GET /schedule-blocks[?desde&hasta]` (por defecto, hoy + 180 días),
  `POST /schedule-blocks` `{starts_at, ends_at, motivo?}` → 201 con
  `citas_afectadas` (las activas que quedan dentro: no se tocan) y
  `DELETE /schedule-blocks/{id}`. Crear y borrar bloqueos deja entrada en la
  auditoría (`agenda.bloqueo_*`).

Cada cambio de la agenda (del panel o del asistente) publica `agenda.changed`
en el WebSocket del inbox; la Agenda y «Citas de hoy» se recargan con él.

El horario sigue en `GET/PUT /admin/settings/calendar` (solo admin), que desde
la Fase 9 devuelve también `google_sync` (el modo que se aplica: `off` | `push` |
`push_and_busy`) y `google_cuenta` (`sin_cuenta` | `activa` | `sin_permiso`), y
acepta `google_sync` en el `PUT` (sin él, no se toca).

## Recordatorios, «no llegó» y retomar (SPEC-18)

Asistente → Seguimientos. Solo admin. La configuración vive en
`app_settings["seguimientos"]` y la vuelta que manda los avisos es la tarea
`app.tasks.seguimientos.tick` (beat, cada 10 min; `services/seguimientos.py`).

- `GET /api/v1/admin/seguimientos` → `{config, datos, plantillas, error_plantillas,
  estado, zona, negocio_sugerido}`. `config` = `{recordatorio, inasistencia,
  negocio, hora_desde, hora_hasta}`, cada aviso `{activo, horas, plantilla, idioma,
  variables}` donde `variables` es `{"1": "paciente"}`. `datos`: de dónde puede
  salir una variable (`paciente`, `negocio`, `cita_fecha`, `cita_hora`,
  `cita_servicio`). Cada plantilla trae `problema` (por qué no sirve para un aviso
  automático) y `sugerencia` (el dato que se deduce de sus etiquetas). `estado`
  dice, por aviso encendido, por qué no va a salir ahora, o `null`.
- `PUT /api/v1/admin/seguimientos` con el `config` → lo mismo que el GET. 400 con
  el motivo si un aviso encendido no tiene plantilla, una variable sale de un
  dato que no existe o falta el nombre del negocio. Audita `seguimientos.updated`.
- `GET /api/v1/admin/seguimientos/registro` → los últimos 50 avisos
  (`recordatorio_enviado|omitido`, `inasistencia_enviada|omitida`) con contacto,
  canal o motivo. Salen de la actividad de la ficha.

Reglas del envío: solo entre `hora_desde` y `hora_hasta` (hora del negocio);
recordatorio `horas` antes de citas agendadas antes de ese momento; «no llegó»
`horas` después de marcar «No asistió», de los últimos 7 días. Por WhatsApp con
plantilla si la ficha tiene `+52…` o BSUID; si no, por el canal de la cita
(Instagram/Messenger con la ventana de 24 h abierta, sin HUMAN_AGENT; correo en su
hilo). Respeta bajas, bloqueados, pausa del canal o del asistente, Entrenamiento
y tope de gasto. El mensaje queda en el hilo como `assistant` con
`metadata.kind` = `recordatorio` | `inasistencia`, y la conversación sigue con
Sofía.

**Retomar a quien preguntó y no agendó** (`config.retomar = {activo, horas}`,
`services/retomar.py`, en la misma vuelta): conversaciones de WhatsApp, Instagram
o Messenger con Sofía, cuyo último mensaje es de Sofía y cuyo paciente lleva
entre `horas` (1–22, 20 por defecto) y 23 h sin escribir, sin cita futura, con la
ficha fuera de `cliente`/`perdido`/`no_cualifica` y sin
`conversations.seguimiento_enviado_at`. Una llamada al modelo del agente del canal
(gasto `seguimiento`) decide: `NO_ESCRIBIR` o un mensaje corto. Sale como texto
normal (dentro de la ventana, sin plantilla), `metadata.kind = "seguimiento"`.
Una vez por conversación; si el paciente escribe mientras el modelo piensa, no
sale. El registro del panel solo enseña los enviados; los que Sofía decidió no
mandar quedan en la actividad de la ficha (`seguimiento_omitido`, con motivo).

`AppointmentOut` (agenda) trae además `confirmada_at`, `recordatorio_enviado_at`,
`inasistencia_enviada_at` y `avisos`. Herramienta nueva del agente:
`confirmar_cita(cita_id?)`.

## ¿Es el mismo paciente? (Fase 10.1)

Las propuestas de unir fichas las deja `services/identity_linking.py` cuando
alguien dice por Instagram, Messenger, el chat web o el correo un teléfono (o
escribe desde un correo) que ya es de otra ficha, o cuando nace una ficha de
WhatsApp o de voz con un teléfono que alguien ya había dicho. Nunca se fusiona
sola.

- `GET /contacts/{id}/merge-suggestions` (operador o admin) → las pendientes en
  las que está esa ficha, por cualquiera de los dos lados:
  `[{id, motivo: "telefono" | "email", dato, absorbe: {id, nombre, telefono,
  social_handle, canal}, queda: {…}, telefono_final, citas_activas, bloqueo,
  created_at}]`. `absorbe` desaparece al unirlas y `queda` se queda con todo;
  `canal` ya viene en texto ("WhatsApp", "Instagram"…). `bloqueo` dice por qué
  no se puede unir ahora (dos cuentas de canal que guardar); con él, el panel
  no ofrece el botón.
- `POST /contacts/merge-suggestions/{id}/dismiss` (solo admin) → `{ok: true}`.
  «No es el mismo paciente»: queda `descartada` y ese par no se vuelve a
  proponer. 404 si ya no está pendiente.
- `POST /contacts/{id}/merge` `{source_id, suggestion_id?}` (solo admin): la
  fusión de siempre. Con `suggestion_id`, la propuesta tiene que ser de esas dos
  fichas (422 si no) y queda en la auditoría. La llave de la ficha que queda es
  el teléfono de verdad, venga de la ficha que venga, y el identificador del
  canal pasa a `identidad_canal`. **409** si se perdería una cuenta de canal:
  no se toca nada.
- `ContactOut` lleva `telefono_declarado` e `identidad_canal` (solo lectura).
- En la bandeja, `ConversationOut.fusion_pendiente` marca la conversación de la
  ficha que desaparecería; esas conversaciones entran en «Para hacer»
  (`action_only`) y en `GET /conversations/counts` → `fusiones`. No cuentan
  como `needs_action` ni disparan el aviso push: nadie está esperando respuesta.

Crear, descartar o aceptar una propuesta publica `fusion.changed` en el
WebSocket del inbox; el aviso del chat y «Para hacer» se recargan con él.

## Canales: estado y catálogo de prompts (Fase 10.2)

- `GET /admin/channels/estado` (solo admin) → `{global_paused, channels: [{canal,
  paused, configured, enabled, agent_assigned, last_inbound_at}], recent_errors:
  [{ts, level, event, canal}], ahora, ahora_ms}`. Es lo que mira `omni.py
  connect` para esperar el mensaje de prueba: compara `last_inbound_at` con
  `ahora` (hora del servidor). Sale de la misma función que
  `/agent-api/monitor/channels` (`services/estado_canales.py`).
- `GET /admin/prompts-catalogo` (solo admin) → `[{id, titulo, grupo:
  "principal" | "avanzado", canal, resumen, texto}]`, en el orden de los
  archivos de `backend/prompts/`. `canal` es la tarjeta de Canales → Servicios
  que lleva el botón «Copiar prompt»; `texto` trae `<dominio>` sin rellenar (lo
  pone el panel al copiar).
- El `buffer_seconds` de los agentes (`/admin/agents`) sigue en la API, pero ya
  no manda en los canales de texto: cada canal tiene el suyo
  (`core/channel_capabilities.py`), y el panel ya no lo enseña.

## Primer arranque (Fase 10.3b)

La instalación en un clic arranca sin administrador (`SETUP_CODE` puesto y sin
`INITIAL_ADMIN_*` propios) y el dueño lo crea desde el panel. Los dos son
públicos: no hay sesión todavía.

- `GET /primer-arranque` → `{necesita_admin}`: `true` solo con `SETUP_CODE` y sin
  ningún administrador activo. Es lo que mira el login para mandar a
  `/primer-arranque`. No devuelve nada más.
- `POST /primer-arranque` `{codigo, email, password, nombre?}` → 201 `{access_token,
  token_type, user, llave_cifrado}`. `llave_cifrado` es el `ENCRYPTION_KEY` de la
  instalación y solo sale aquí, una vez. El administrador nace con
  `debe_cambiar_password = false` y el paso «Cambia tu contraseña» del Inicio ya
  hecho. Errores: 404 sin `SETUP_CODE`; 403 con otro código (el `detail` dice
  dónde está en Coolify); 409 si ya hay un administrador activo o el correo ya
  tiene cuenta; 422 si la contraseña no pasa la política o el correo no vale;
  429 a partir de 5/min o 20/h por IP.

## Módulos apagables

`GET /api/v1/admin/feature-flags` devuelve `{flags: [{clave, descripcion, activa}]}`
con las cuatro banderas conocidas; `PUT` con `{flags: {clave: bool}}` las cambia
(422 si la clave no existe). Con una bandera apagada, las rutas de su módulo
responden **404** (no 403): `/admin/outbound/*` con `envio_masivo`,
`/admin/internal-agent/*` con `agente_interno`. `flow_dashboard` y
`tarifas_openrouter` solo afectan al panel y a la tarea diaria de precios.

## Herramientas de n8n (Fase 11, SPEC-06)

Router `api/n8n.py`, prefijo `/api/v1/admin/n8n`, solo administradores:

| Método y ruta | Qué hace |
|---|---|
| `GET /tools` | Lista, con `secreto_enmascarado` (8 puntos + los 4 últimos). |
| `POST /tools` | Alta. 422 con el motivo si algo no vale (nombre, URL, parámetros); 409 si el nombre ya existe o hay 20. El secreto lo genera el CRM. |
| `PUT /tools/{id}` | Edición, con las mismas reglas. |
| `DELETE /tools/{id}` | Baja. |
| `GET /tools/{id}/secreto` | `{secreto, cabecera}` para «Copiar»; deja `n8n_tool.secreto_visto` en la auditoría. |
| `POST /tools/{id}/regenerar-secreto` | Secreto nuevo; el viejo deja de valer. |
| `POST /tools/{id}/probar` | `{args}` → `{ok, estado, ms, respuesta, error}`. Llama al flujo con `prueba: true`; 20/min. |

`GET /api/v1/admin/tools` (la lista de Agentes) suma las de n8n activas con
`origen: "n8n"`; las del código llevan `origen: "crm"`.

**Lo que recibe n8n** (POST, `X-Omnicanal-Token: <secreto>`,
`X-Omnicanal-Evento: herramienta`):
`{evento: "herramienta", herramienta, args, llamada_id, prueba, conversacion: {id, canal}, contacto: {id, nombre?, telefono?, email?}, agente: {id}}`.
Solo van los `args` que declara la herramienta. `llamada_id` es igual para la
misma herramienta con los mismos datos en el mismo turno. Sin redirecciones.

**Lo que contesta n8n:** JSON `{resultado, archivos?}` o texto. A Sofía le
llega `resultado` (≤ 8 KB) envuelto como dato, nunca los enlaces de `archivos`.

**Archivos** (`services/archivos_salientes.py`): `archivos: [{tipo: imagen|audio|video|documento, url, pie?, nombre?}]`, hasta 3 por llamada.
- El CRM baja cada uno: HTTPS pública, sin redirecciones, ≤ 16 MB (5 MB las imágenes).
- Comprueba que el contenido es lo que dice: nada de HTML, SVG ni ejecutables, y el tipo tiene que coincidir.
- Lo guarda en `UPLOADS_PATH` y lo manda por el canal, con la ventana de 24 h:

| Canal | Cómo sale |
|---|---|
| WhatsApp (YCloud/Meta) | `upload_media` + `send_media` |
| WhatsApp, Instagram y Messenger por Zernio | `attachmentUrl` = enlace firmado |
| Chat web | `message.out` con `media: {tipo, url, nombre, archivo}` |
| Correo, voz e Instagram por la API oficial | no sale |

- Queda en el hilo como `Message` de Sofía con `media_*` y `extra.origen = "n8n"`.
- En Entrenamiento queda como sugerencia (`is_draft`) con el archivo, y sale con `POST /conversations/{id}/drafts/{msg}/send`, que ahora acepta texto vacío si la sugerencia lleva archivo.
- A Sofía le vuelve `archivos: [{tipo, nombre, estado: enviado|en la sugerencia|no enviado, motivo?}]`.
- Qué tipos admite cada canal: `Capacidades.archivos_salientes`.

**Enlace firmado:** `GET /api/v1/media/p/{token}` (público, 120/min).
- Sirve un archivo de `UPLOADS_PATH` si el token es bueno: HMAC con una llave derivada de `JWT_SECRET`, nombre del archivo y caducidad (1 h por defecto).
- Si el token es malo o caducó, devuelve 404.
- Responde con `X-Content-Type-Options: nosniff`.
- Lo usan Zernio y el widget.
- `GET /webchat/history` firma un enlace nuevo en cada lectura para los archivos que mandó Sofía o la operadora (campo `media`).

## «Sofía en n8n» (Fase 11, SPEC-07)

Un agente de texto con `motor: "n8n"` no llama a ningún modelo del CRM: cada
turno sale a `n8n_url` (`services/n8n_motor.py`) y lo que n8n conteste sigue el
camino de una respuesta de Sofía (formato del canal, partir, Entrenamiento,
borrador de correo, envío). La moderación, el límite por contacto y el resumen
rodante no corren.

**Panel:**
- `POST/PATCH /admin/agents` aceptan `motor`, `n8n_url` y `n8n_espera_s`; `AgentOut` devuelve además `n8n_secreto_enmascarado`.
- Con `motor: "n8n"`: 422 si el agente es de voz o si la URL no es HTTPS pública. El secreto lo genera el CRM la primera vez.
- `GET /admin/agents/{id}/n8n/secreto`: con auditoría.
- `POST …/n8n/regenerar-secreto`.
- `POST …/n8n/probar` `{texto}`: un turno de ejemplo con `prueba: true`.

**Lo que recibe n8n** (`X-Omnicanal-Token`, `X-Omnicanal-Evento: mensajes`):

```json
{"evento": "mensajes", "evento_id": "…", "prueba": false,
 "conversacion": {"id", "canal", "entrenamiento", "ventana_abierta", "ventana_cierra"},
 "contacto": {"id", "nombre", "telefono", "email", "etapa", "etiquetas"},
 "mensajes": [{"id", "tipo", "texto", "transcripcion?", "archivo_url?", "fecha"}],
 "historial": [{"rol": "paciente|sofia|operador", "texto", "fecha"}],
 "agente": {"id", "nombre"}, "api": {"base", "conversacion"}}
```

**Respuesta directa:** todo es opcional.

```json
{"respuesta": "texto", "archivos": [...],
 "acciones": {"pasar_a_humano": "motivo", "etiquetas": [], "etapa": "", "ficha": {"nombre", "email"}}}
```

- `pasar_a_humano` usa `respuesta` como mensaje puente.
- 200 sin `respuesta` ni `archivos` = «contesto después por la API». Si en 10 minutos no llegó nada, la conversación pasa a una persona; si la directa no llegó en `n8n_espera_s`, el plazo es de 2 minutos (`tasks/n8n_motor_check.py`).
- Red o 5xx: 2 reintentos (a los 5 y a los 20 s) con el mismo `evento_id`. Después, `n8n.sin_respuesta` y la conversación pasa a humano con mensaje puente.
- Los mensajes que salen llevan `extra.motor = "n8n"`. El inbox lo enseña
  como una marca «n8n» (`MessageOut.metadata.motor`).

**Contestar después** (`api/agent_api_conversaciones.py`, token de la API de
agentes con los ámbitos de n8n; detalle en `docs/agent_api.md`):

| Endpoint | Ámbito | Notas |
|---|---|---|
| `GET /agent-api/conversations/{id}/messages?limit=50` | `conversations:read` | Del más viejo al más nuevo; archivos con enlace firmado de 1 h; `sugerencia_sin_enviar` en las de Entrenamiento |
| `POST /agent-api/conversations/{id}/messages` `{texto?, archivos?, idempotency_key?}` | `conversations:reply` | 409 si no está en `bot`, si es correo o con la ventana cerrada; en Entrenamiento queda como sugerencia; la misma clave no sale dos veces en 24 h |
| `POST /agent-api/conversations/{id}/handoff` `{motivo, mensaje_al_cliente?}` | `conversations:reply` | El camino de `derivar_humano` |
| `PATCH /agent-api/contacts/{id}` `{nombre?, email?, etapa?, etiquetas_agregar?}` | `contacts:write` | Nunca el teléfono |

**Lo demás que cambia con el motor en n8n:**
- `POST /conversations/{id}/drafts/{mid}/refine` da **409** si el borrador es
  de n8n o el agente del canal tiene el motor en n8n (re-redactar llamaría al
  modelo del CRM). Se mira antes del cupo diario: no gasta re-redacciones.
- `GET /admin/conversations/{id}/trace` trae una línea `router` por turno:
  «Turno enviado a n8n · 1.8 s · respuesta directa» (`latency_ms`; nivel
  `warn` si la directa no llegó a tiempo y `error` si n8n falló).

## Permisos

`backend/app/api/deps.py` lleva el mapa de roles comentado y es la fuente de
verdad. En resumen:

- `Depends(get_current_user)` = "hay sesión válida", que hoy equivale a
  operador.
- `Depends(require_admin)` = solo `admin`. Cubre todo `/admin/*` y también
  algo que no lo parece: **todo `/kb/*`**, incluidos los `GET` de listado y
  `POST /kb/search`. Comprobado endpoint por endpoint en
  `api/knowledge_base.py`. (El comentario de `deps.py` dice que leer la base de
  conocimiento sigue abierto a cualquier sesión; eso ya no es cierto.)
- El rol `revisor` se corta arriba, en `get_current_user`, contra una lista
  blanca de rutas. No lleva dependencia propia.

El token va **siempre por la cabecera `Authorization: Bearer`**. Por query
string no se acepta.

---

## WhatsApp por YCloud: qué entra por el webhook

`POST /api/v1/webhooks/ycloud` recibe **cinco** tipos de evento, no uno. Todos
se firman igual (`YCloud-Signature: t=<epoch>,s=<hmac>`, HMAC-SHA256 sobre
`<t>.<cuerpo crudo>`) y **todos caducan a los 5 minutos**: una petición
legítima capturada no se puede reenviar mañana.

| Evento | Qué es | Qué hace el CRM |
|---|---|---|
| `whatsapp.inbound_message.received` | El paciente escribe (o toca un botón de respuesta rápida: `button`/`interactive`, que llega como el texto del botón) | Guarda el mensaje y despierta al agente |
| `whatsapp.message.updated` | ✓, ✓✓, leído o fallo | Lo anota en el globo del mensaje |
| `whatsapp.smb.message.echoes` | La clínica contestó **desde su celular** | Lo guarda como operador y **pasa la conversación a humano**: el agente se aparta |
| `whatsapp.smb.history` | Historial que sincroniza la app al conectar por QR | Lo importa como contexto, **sin despertar al agente** |
| `whatsapp.smb.app.state.sync` | Agenda del celular | Rellena el nombre de fichas que no lo tienen; nunca lo pisa |

Antes de procesar nada:

1. **Dedupe por `id` de evento** (Redis, 24 h). YCloud reintenta hasta 7 veces
   con el mismo evento: sin esto, un pico de latencia se traduce en el paciente
   recibiendo la respuesta tres veces.
2. **Los grupos se descartan** (`groupId` o `whatsapp.group.*`), con traza.
3. **Filtro por número de destino.** Una misma clave de YCloud puede tener
   varios números; los que no son el del canal se descartan con un aviso. Se
   apaga con `channels.config.filtrar_por_numero = false`.

Dos identificadores por mensaje, y los dos se guardan en `messages.metadata`:
el **`wamid`** de Meta (el que viaja en estados, ecos e historial; indexado en
`ix_messages_wamid`) y el **`ycloud_id`**, que es el que devuelven sus
endpoints de envío. El dedupe de entrada mira los dos.

### Alta del canal

`POST /api/v1/admin/channels/whatsapp/provision` con `provider: "ycloud"`
**registra el webhook en YCloud por su API** y guarda el secreto de firma
cifrado. Devuelve `webhook_estado`:

- `registrado` — creado y secreto guardado.
- `reutilizado` — ya existía uno con esa URL; se actualizaron sus eventos y se
  conserva el secreto que hubiera (YCloud no lo devuelve al actualizar).
- `manual` — hay que darlo de alta a mano.

`needs_secret: true` significa que **no hay secreto guardado**, así que los
mensajes entrarían sin comprobar que los manda YCloud. Se calcula mirando lo
que hay en la base, no la rama por la que se pasó.

El alta **no pide** `webhook_secret`: lo crea este mismo endpoint al registrar
el webhook, y YCloud solo lo entrega ahí. Se acepta si llega (una instalación
que ya lo tenía apuntado), pero exigirlo pedía justo lo que se está a punto de
generar. Obligatorios en YCloud: `api_key` y `phone_number`.

No se llama a YCloud cuando la URL no se ve desde internet (desarrollo,
pruebas): no podría entregarle nada y solo dejaría basura en su panel.

**Eventos que YCloud no activa.** Se piden cuatro:
- `whatsapp.inbound_message.received`
- `whatsapp.message.updated`
- `whatsapp.smb.message.echoes`
- `whatsapp.smb.history`

Al grabar el video 09, YCloud dejó el endpoint sin el de ecos, aunque respondió 200. Para cubrir ese caso:
1. El alta compara los `enabledEvents` de la respuesta (o del listado) con lo pedido.
2. Si falta alguno, los vuelve a pedir una vez con un `PATCH`.
3. Si sigue faltando, el endpoint devuelve `webhook_eventos_faltantes` (`null` = YCloud no dijo cuáles quedaron) y lo guarda en `channels.config.webhook_eventos_faltantes`.

Desde ahí el aviso se ve en tres lugares:
- la tarjeta de WhatsApp;
- `GET /admin/channels/estado`, en el campo `avisos` de cada canal;
- `omni.py verify`, que lo imprime como aviso.

`POST /api/v1/admin/channels/whatsapp/comprobar-eventos` (admin, solo YCloud) vuelve a mirar el endpoint en YCloud y devuelve `{existe, faltan}`. Es el botón «Volver a comprobar» de la tarjeta, después de marcar los eventos a mano.

### Rotar el secreto de firma

`POST /api/v1/admin/channels/whatsapp/rotate-webhook-secret` (solo admin, solo
YCloud). Busca en YCloud el endpoint que apunta a nuestra URL, le pide un
secreto nuevo y lo guarda cifrado. Devuelve `endpoint_id`, `webhook_url` y
`rotado`; **el secreto nunca vuelve al navegador**.

Es la salida de `webhook_estado: "reutilizado"` con `needs_secret: true`:
YCloud solo entrega el secreto al CREAR el endpoint, así que reinstalar sobre
una cuenta que ya lo tenía deja los mensajes entrando sin poder validarse, y
antes no había forma de arreglarlo desde el panel. También es el camino cuando
el secreto se filtra: el endpoint no cambia, así que no hay que tocar la
consola de YCloud ni volver a conectar el canal.

Errores: `400` si el canal es de Meta (ahí el secreto es el App secret de su
app y se cambia en su panel) o si falta la API key; `404` si en YCloud no hay
ningún endpoint con esa URL; `502` si YCloud no contesta. Si la rotación falla,
el secreto anterior se queda como estaba — quedarse sin ninguno haría que la
instalación rechazara todo lo que entrase.
