# Modelo de datos — Chatbot

> **Esto es un resumen, no un contrato.** La verdad son los modelos de
> `backend/app/models/` y las migraciones de
> `backend/app/db/migrations/versions/`. Si algo de aquí no cuadra con el
> código, manda el código.
>
> Documenta las tablas que se tocan a diario, de las **45** que hay (sin contar
> `alembic_version`). El resto están listadas al final.
>
> **Comprobado contra el código** contra una base de datos migrada hasta
> `0055_user_role_revisor`; las tres de la agenda, contra `0060_appointments`;
> la unificación de fichas, contra `0061_unificacion_fichas`; la cola de eventos
> de Google por borrar, contra `0066_eventos_google_por_borrar`, que es hoy la
> cabeza de migraciones.

## Cómo leer los tipos

- `timestamptz` es siempre `timestamp with time zone`.
- **`bytea` = columna cifrada** con Fernet (`backend/app/core/encrypted_type.py`).
  En Python se lee y se escribe como texto normal, pero **la base de datos no
  ve el contenido**: no se puede filtrar, ordenar ni indexar por ella. Van
  marcadas con **(cifrada)**.

## Tablas

### `users`

Usuarios del panel.

| Campo | Tipo | Constraints | Notas |
|---|---|---|---|
| id | uuid | PK | |
| email | varchar(255) | UNIQUE, NOT NULL | en minúsculas |
| password_hash | varchar(255) | NOT NULL | bcrypt |
| role | enum `user_role` | NOT NULL | `admin` \| `cliente` \| `revisor` |
| nombre | varchar(120) | | |
| activo | boolean | NOT NULL, default true | |
| last_login | timestamptz | | |
| password_changed_at | timestamptz | | |
| created_at | timestamptz | NOT NULL, default now() | |
| updated_at | timestamptz | NOT NULL, default now() | |

### `credentials`

Claves de proveedores externos, cifradas. Se rellenan desde el panel.

| Campo | Tipo | Constraints | Notas |
|---|---|---|---|
| id | uuid | PK | |
| key | varchar(80) | UNIQUE, NOT NULL | ej. `openai_api_key` |
| value_encrypted | bytea | NOT NULL | **(cifrada)** |
| descripcion | text | | texto de ayuda del panel |
| updated_at | timestamptz | NOT NULL, default now() | |
| updated_by | uuid | FK users.id | |

### `contacts`

El CRM. **22 columnas.** Tres van cifradas.

| Campo | Tipo | Constraints | Notas |
|---|---|---|---|
| id | uuid | PK | |
| telefono | varchar(320) | UNIQUE, NOT NULL | es la llave de la ficha. WhatsApp en E.164 (`+52…`); los canales sin teléfono usan un identificador con prefijo: `ig:`, `fb:`, `web:`, `email:`, `wa:`, `voice:` |
| email | varchar(255) | | |
| nombre | varchar(120) | | |
| social_handle | varchar(80) | | usuario de Instagram |
| wa_user_id | varchar(128) | UNIQUE parcial | **BSUID** de WhatsApp; el índice es `WHERE wa_user_id IS NOT NULL` |
| wa_parent_user_id | varchar(128) | | cuenta padre de Meta, solo traza |
| telefono_declarado | varchar(20) | índice parcial | el teléfono que la persona DIJO en un canal que no lo da, en E.164. No es la llave: solo sirve para proponer unir fichas (Fase 10.1) |
| identidad_canal | varchar(320) | UNIQUE parcial | el `ig:`/`fb:`/`web:`/`email:` de una ficha que se unió a otra con teléfono; la entrada de mensajes busca también por aquí |
| estado | enum `contact_estado` | NOT NULL, default `contacto` | `contacto` \| `solicitud_presupuesto` \| `seguimiento` \| `cliente` \| `perdido` \| `no_cualifica` |
| origen | enum `contact_origen` | NOT NULL | `whatsapp` \| `web` \| `manual` \| `instagram` \| `email` \| `messenger` |
| servicio_interes | varchar(200) | | |
| empresa | varchar(200) | | |
| cargo | varchar(120) | | |
| web | varchar(255) | | |
| nif | bytea | | **(cifrada)** |
| direccion | bytea | | **(cifrada)** |
| notas_internas | bytea | | **(cifrada)** |
| in_crm | boolean | NOT NULL, default true | si la ficha sale en el CRM o solo en el inbox |
| ultimo_mensaje_at | timestamptz | | |
| created_at | timestamptz | NOT NULL, default now() | |
| updated_at | timestamptz | NOT NULL, default now() | |

Índices: `ix_contacts_telefono`, `contacts_telefono_key` (único),
`ux_contacts_wa_user_id` (único parcial), `ux_contacts_identidad_canal` (único
parcial), `ix_contacts_telefono_declarado` (parcial), `ix_contacts_estado`,
`ix_contacts_in_crm`, `ix_contacts_ultimo_mensaje_at`.

Al unir dos fichas (`services/contact_merge.py`) la llave es el teléfono de
verdad, venga de la ficha que venga, y el identificador del canal que deja de
ser llave pasa a `identidad_canal`. Una ficha guarda una sola identidad de canal
además de su teléfono: si una fusión fuera a perder una segunda, se niega.

Para añadir un campo aquí hay que tocar trece sitios. La lista está en
[`../CLAUDE.md`](../CLAUDE.md), sección "Cómo añadir un campo a la ficha de
contacto".

### `tags` y `contact_tags`

Etiquetas configurables, relación N:M con contactos.

`tags`: `id` (uuid, PK), `nombre` (varchar(60), único), `color`
(varchar(7), hex `#RRGGBB`), `created_at`.

`contact_tags`: `contact_id` y `tag_id` (ambos FK con `ON DELETE CASCADE`) más
`created_at`. Clave primaria compuesta.

### `conversations`

Un hilo con un contacto por un canal. Ha crecido mucho: aquí van las columnas
que se usan siempre, y agrupadas las de cada función.

| Campo | Tipo | Constraints | Notas |
|---|---|---|---|
| id | uuid | PK | |
| contact_id | uuid | FK contacts.id, NOT NULL | |
| canal | enum `conversation_canal` | NOT NULL | `whatsapp` \| `web` \| `instagram_dm` \| `email` \| `retell_voice` \| `messenger` |
| session_id | varchar(320) | NOT NULL | identificador del hilo en su canal |
| status | enum `conversation_status` | NOT NULL, default `bot` | `bot` \| `humano` \| `cerrada` |
| started_at | timestamptz | NOT NULL, default now() | |
| ended_at | timestamptz | | |
| last_message_at | timestamptz | | |
| derivada_a_humano_at | timestamptz | | |
| seguimiento_enviado_at | timestamptz | | SPEC-18, tramo 3: el seguimiento a quien preguntó y no agendó (uno por conversación) |
| asignada_a | uuid | FK users.id | operador |
| archived_at | timestamptz | | |
| resumen | bytea | | **(cifrada)** — se genera al cerrar |
| rolling_summary | bytea | | **(cifrada)** — resumen que se va actualizando |
| rolling_summary_upto | int | NOT NULL, default 0 | hasta qué mensaje va el resumen |

Del clasificador: `quarantined_at`, `quarantine_reason`, `spam_reviewed`.
Del canal de correo: `gmail_thread_id`, `subject`.
Del canal de voz: `call_duration_seconds`, `call_ended_reason`, `call_cost_usd`,
`call_cost_source`, `call_recording_url` **(cifrada)**.

### `messages`

| Campo | Tipo | Constraints | Notas |
|---|---|---|---|
| id | uuid | PK | |
| conversation_id | uuid | FK conversations.id ON DELETE CASCADE, NOT NULL | |
| rol | enum `message_role` | NOT NULL | `user` \| `assistant` \| `operator` \| `system` |
| contenido | text | | |
| audio_url | varchar(500) | | ruta del fichero de audio |
| audio_transcript | bytea | | **(cifrada)** — transcripción |
| metadata | jsonb | NOT NULL, default `{}` | tool_calls, id del mensaje en el proveedor… |
| created_at | timestamptz | NOT NULL, default now() | |
| leido_at | timestamptz | | para las no leídas |

Adjuntos: `media_type`, `media_url`, `media_mime`, `media_size`,
`media_filename`, `media_duration_seconds`, `media_purged_at`.

Índices que conviene conocer, porque **no se ven en el modelo** (se crean con
SQL en las migraciones y `--autogenerate` los da por borrados):

- `ix_messages_conv_created` — `(conversation_id, created_at)`
- `ix_messages_provider_message_id` — sobre `metadata ->> 'provider_message_id'`
- `ix_messages_unread` — parcial: `WHERE rol = 'user' AND leido_at IS NULL`

### `documents` y `chunks`

La base de conocimiento.

`documents`: `id`, `nombre` (varchar(255)), `formato` (enum: `pdf`, `docx`,
`txt`, `md`, `csv`, `xlsx`), `tamano_bytes`, `status` (enum: `procesando`,
`indexado`, `error`, `indexado_sin_semantica`), `error_msg`, `num_chunks`,
`storage_path`, `uploaded_at`, `uploaded_by` (FK users.id).

`chunks`: `id`, `document_id` (FK ON DELETE CASCADE), `contenido` (text),
`embedding` (`vector(1536)`, `text-embedding-3-small`), `metadata` (jsonb),
`created_at`.

Dos índices, uno por cada mitad de la búsqueda híbrida:

```sql
CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_chunks_fts_es  ON chunks USING gin (to_tsvector('spanish', contenido));
```

Hasta la 0062 el de los embeddings era un IVFFlat creado con la tabla vacía:
sus listas no servían y, cuando Postgres lo usaba, la búsqueda perdía casi
todo lo parecido. HNSW no depende de lo que haya al crearse (pgvector ≥ 0.5).

### `audit_log`

Quién hizo qué en el panel.

`id`, `user_id` (FK, nulo en acciones del sistema), `action` (varchar(80), ej.
`contact.data_erased`), `entity`, `entity_id`, `before` y `after` (jsonb), `ip`,
`user_agent`, `created_at`.

### `appointments`, `schedule_blocks` y `offered_slots` (agenda propia)

Desde la Fase 9 las citas viven en el CRM (`services/agenda.py`); Google
Calendar, si está conectado, recibe una copia (`services/calendar_sync.py`).

`appointments`: la cita.

| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| id | uuid | NOT NULL | PK |
| contact_id | uuid | NOT NULL | FK contacts ON DELETE CASCADE |
| conversation_id | uuid | NULL | FK conversations ON DELETE SET NULL: la conversación de la que salió |
| canal | enum `conversation_canal` | NULL | nulo si la creó un operador desde el panel |
| servicio | varchar(120) | NOT NULL | nombre del catálogo (`app_settings["agenda.servicios"]`) o lo que dijo la persona |
| starts_at / ends_at | timestamptz | NOT NULL | `ends_at` = inicio + duración del servicio |
| estado | varchar(20) | NOT NULL | `agendada` \| `reagendada` \| `cancelada` \| `completada` \| `no_asistio` |
| notas | bytea | NULL | **(cifrada)** |
| creada_por | varchar(10) | NOT NULL | `agente` \| `operador` |
| google_event_id | varchar(200) | NULL | id de la copia en Google (el de la cita en hexadecimal) |
| google_sync_pendiente | boolean | NOT NULL | la copia en Google falta por crear, mover o borrar |
| recurso | varchar(60) | NOT NULL | `principal` (una sola agenda por instalación, por ahora) |
| recordatorio_enviado_at / inasistencia_enviada_at | timestamptz | NULL | SPEC-18: el aviso ya se procesó (se apunta ANTES de mandarlo, con `UPDATE … WHERE … IS NULL`, para que no salga dos veces). También se apunta cuando no se pudo mandar |
| avisos | jsonb | NOT NULL | cómo acabó cada aviso: `{"recordatorio": {"resultado": "enviado", "canal": "whatsapp"}}` u `"omitido"` con `motivo` |
| confirmada_at | timestamptz | NULL | el paciente confirmó (`confirmar_cita`): la Agenda enseña «✓» |
| created_at / updated_at | timestamptz | NOT NULL | `updated_at` es la hora de referencia de «no llegó» (se marca y se mueve) |

Lo que protege la agenda es la restricción de exclusión, no el código:

```sql
EXCLUDE USING gist (recurso WITH =, tstzrange(starts_at, ends_at) WITH &&)
  WHERE (estado IN ('agendada', 'reagendada'))   -- ex_appointments_sin_encimar
```

Dos citas activas encimadas no entran aunque lleguen a la vez por dos canales.
Mover una cita deja la vieja `cancelada` y crea una nueva `reagendada`, en una
transacción.

`schedule_blocks`: tramos cerrados a mano (comida, vacaciones). `id`,
`starts_at`, `ends_at`, `motivo`, `recurso`, `created_at`. Los huecos los
descuentan y, por teléfono, durante un bloqueo se toma el recado en vez de
pasar la llamada.

`offered_slots`: lo que `consultar_disponibilidad` le enseñó al modelo en cada
conversación. PK (`conversation_id`, `starts_at`), `offered_at`. `agendar_cita`
y `reagendar_cita` solo aceptan un horario ofrecido en los últimos 30 minutos;
lo de más de un día lo borra el beat (`purge_offered_slots`).

Ajustes de la agenda en `app_settings`: el horario (`calendar.*`), el catálogo
de servicios con su duración (`agenda.servicios`) y el modo de la copia en
Google (`calendar.google_sync`: `off` \| `push` \| `push_and_busy`; sin la clave,
`push_and_busy` si hay cuenta de Google Calendar y `off` si no).

### `calendar_event_deletions` (eventos de Google por borrar)

Desde la 1.3.3 (SPEC-21, migración `0066_eventos_google_por_borrar`). Borrar un
contacto se lleva sus citas por el cascade, y con ellas la única pista de su
copia en Google Calendar. Antes del cascade, `data_erasure.erase_contact` apunta
aquí cada evento; `calendar_sync.borrar_eventos_pendientes` (la tarea
`borrar_eventos` y la barrida del beat) los borra en Google.

| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| id | uuid | NOT NULL | PK |
| event_id | varchar(200) | NOT NULL | único: el id del evento (el de la cita en hexadecimal). Apuntarlo otra vez lo reabre |
| contact_id | uuid | NOT NULL | el contacto borrado, sin FK (ya no existe). Indexado |
| intentos | int | NOT NULL | veces que Google no dejó borrarlo |
| ultimo_error | varchar(300) | NULL | `Google respondió 503`, sin el cuerpo de la respuesta |
| borrado_at | timestamptz | NULL | Google confirmó el borrado |
| created_at | timestamptz | NOT NULL | |

No guarda datos personales. Las filas de un contacto se quitan juntas cuando ya
no le queda ninguna pendiente, y entonces se audita `contact.calendar_erased`
con `eventos_borrados`.

### `merge_suggestions` (¿es el mismo paciente?)

Desde la Fase 10.1: la propuesta de unir dos fichas del mismo paciente, que deja
`services/identity_linking.py` cuando alguien dice por Instagram, Messenger, el
chat web o el correo un teléfono (o escribe desde un correo) que ya es de otra
ficha. Nunca se fusiona sola: decide un administrador.

| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| id | uuid | NOT NULL | PK |
| contact_id | uuid | NOT NULL | FK contacts ON DELETE CASCADE: la ficha que desaparecería |
| candidate_id | uuid | NOT NULL | FK contacts ON DELETE CASCADE: la que se queda |
| motivo | varchar(20) | NOT NULL | `telefono` \| `email` |
| dato | varchar(320) | NOT NULL | el teléfono o el correo que coincidió |
| estado | varchar(20) | NOT NULL | `pendiente` \| `descartada` (una descartada no se vuelve a proponer) |
| created_at / resuelta_at | timestamptz | | |

`ux_merge_suggestions_par` es único sobre el par SIN orden
(`LEAST`/`GREATEST`): no se cuela la misma propuesta al revés. Aceptarla es
fusionar, y la fila se va sola con la ficha absorbida.

### `n8n_tools` (herramientas de n8n para Sofía)

Desde la Fase 11 (SPEC-06, migración `0063_herramientas_n8n`): herramientas que
el administrador da de alta en Conexiones → n8n. Sofía las ve como una más y,
al usarlas, el CRM llama al webhook de n8n con el secreto en `X-Omnicanal-Token`.

| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| id | uuid | NOT NULL | PK |
| nombre | varchar(40) | NOT NULL | único; `^[a-z0-9_]{3,40}$`; no puede ser el de una herramienta del CRM |
| descripcion | text | NOT NULL | lo que lee Sofía para decidir cuándo usarla (≤ 500) |
| parametros | jsonb | NOT NULL | lista de `{clave, tipo: texto\|numero\|si_no\|opciones, descripcion, obligatorio, opciones?, sensible?}` (≤ 10) |
| url | varchar(500) | NOT NULL | HTTPS y pública (`url_guard`), al guardar y en cada llamada |
| secreto | bytea | NOT NULL | `EncryptedText` (Fernet); lo genera el CRM |
| accion | varchar(10) | NOT NULL | `lectura` (1 reintento) \| `escritura` (sin reintento; sin respuesta → humano) |
| espera_s | int | NOT NULL | 1–15 (en llamadas de voz se recorta a 6) |
| datos_contacto | bool | NOT NULL | con sí, a n8n le llegan nombre, teléfono y correo además del id |
| activa | bool | NOT NULL | |
| created_at / updated_at | timestamptz | | |
| created_by | uuid | | FK users ON DELETE SET NULL |

Tope de 20 por instalación. Cada agente las activa en su `tools_enabled`, como
las del CRM.

## Relaciones

- `contacts` 1—N `conversations` 1—N `messages`
- `contacts` 1—N `appointments`; `conversations` 1—N `offered_slots`
- `contacts` 1—N `merge_suggestions` (por los dos lados)
- `contacts` N—N `tags` (vía `contact_tags`), 1—N `contact_note`, 1—N `contact_activity`
- `documents` 1—N `chunks`, 1—N `document_versions`
- `users` 1—N `audit_log`, `documents.uploaded_by`, `conversations.asignada_a`
- `channels` N—1 `agents`

## Búsqueda en la base de conocimiento

Dos funciones SQL, creadas por las migraciones:

- `match_chunks(query_embedding, match_threshold, match_count)` — solo vector.
- `match_chunks_hybrid(...)` — vector más texto completo en español, que es la
  que usa el agente.

Ninguna de las dos está en `backend/app/models/`. Se definen en las migraciones.

## Datos iniciales (`backend/app/scripts/seed.py`)

El primer arranque aplica el perfil de `SEED_PROFILE` (`backend/app/scripts/profiles/`):
`dental-mx` deja bloque común, prompts, seis documentos de KB, etiquetas,
servicios (`app_settings["agenda.servicios"]`), horario (`calendar.*`), saludo de
voz (`voice.greeting`), `ui.demo_publica` y el canal de chat web en Activo;
`blank` es la instalación de fábrica. Todo create-only.

Los crea `backend/entrypoint.sh` al arrancar. Es idempotente: repetirlo no
duplica nada.

- El usuario administrador, con lo que haya en `INITIAL_ADMIN_*` del `.env`.
- Dos agentes de plantilla, "Agente de Texto" (`kind=text`) y "Agente de Voz"
  (`kind=voice`), con el prompt escrito y los datos del negocio marcados con
  `{{PENDIENTE: …}}`.
- Etiquetas de ejemplo.

## Las otras 29 tablas

Sin detallar aquí. Cada una tiene su modelo en `backend/app/models/` y la
migración que la creó explica en la cabecera por qué existe.

Agentes: `agents`, `agent_config`, `agent_prompt_history`, `agent_tokens`,
`internal_agent_config`, `agent_trace_event`, `agent_corrections`.
`agents` tiene, desde la 0064 («Sofía en n8n»):
- `motor` (`crm` | `n8n`, `CHECK` solo texto);
- `n8n_url`;
- `n8n_secreto` (`EncryptedText`);
- `n8n_espera_s` (5–45, por defecto 25).
Canales: `channels`, `external_apis`, `whatsapp_template_var`.
Clasificador: `classifier_config`, `classifier_rules`.
Difusiones: `outbound_job`, `outbound_job_recipient`, `outbound_optout`.
Base de conocimiento y aprendizaje: `document_versions`, `kb_edit_proposals`,
`knowledge_gaps`, `learned_rules`.
Coste: `llm_providers`, `llm_model_price`, `llm_usage_log`.
Copias: `backup_settings`, `backup_runs`.
Otras: `contact_note`, `contact_activity`, `app_settings`, `push_subscription`,
`feature_flags` (banderas de módulos apagables: agente interno, flujo en vivo,
envío masivo, tarifas de OpenRouter; apagadas por defecto, se encienden en
Ajustes → Módulos y sus rutas responden 404 mientras tanto).

Los mensajes de WhatsApp que llegan desde un anuncio de Meta guardan el anuncio
en `messages.extra["anuncio"]` (título, texto, enlace, imagen, `ctwa_clid`), sin
columna ni migración. Ver `backend/app/services/anuncios.py`.

Los correos guardan sus adjuntos en `messages.extra["attachments"]` (nombre,
tipo, tamaño y `attachment_id`). Los de IMAP están además en disco, en
`uploads/`, y la ficha lleva `local: true`: entran en el borrado del contacto y
en la retención de adjuntos (`data_erasure.archivos_del_mensaje`). Los de Gmail
se piden a Gmail por su id.

La marca blanca vive en `app_settings`, sin tabla propia: `apariencia` (JSON con
nombre, paleta, color, tipografía y trazo) y `apariencia_logo` (JSON con el tipo,
la huella y la imagen en base64, máx. 256 KB). Van en la base de datos y no en el
volumen para que entren en las copias. Ver `backend/app/services/apariencia.py`.

Aparte va `alembic_version`, que es la tabla de control de Alembic: guarda una
sola fila con el identificador de la última migración aplicada.
