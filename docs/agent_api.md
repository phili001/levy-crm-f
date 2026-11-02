# API para agentes externos

API pensada para que los asistentes del operador monitoricen la app, revisen/editen
la base de conocimiento y mejoren los prompts de los agentes. **Pásale este documento
a tu asistente** junto con el token y la URL base.

## Autenticación

1. Crea un token en el panel: **Conexiones → API / MCP** (elige nombre y ámbitos).
   El token (`agt_…`) se muestra **una sola vez**.
2. Envía cada petición con la cabecera:

```
Authorization: Bearer agt_XXXXXXXX
```

- Base URL: `https://<tu-dominio>/api/v1/agent-api`
- Rate limit: 120 peticiones/minuto por token (HTTP 429 al superarlo).
- Token revocable al instante desde el panel; toda escritura queda auditada.

## Conectar por MCP

El CRM trae su servidor MCP dentro de la propia API (desde la 1.2.0), en el
mismo dominio que el panel:

```
https://<tu-dominio>/mcp
```

Transporte **Streamable HTTP**, sin estado y con respuestas JSON. El token va en
la cabecera `Authorization: Bearer agt_…`, igual que por HTTP. Con Claude Code,
el comando que enseña el panel en **Conexiones → API / MCP → Conectar por MCP**:

```bash
claude mcp add --transport http crm https://<tu-dominio>/mcp \
  --header "Authorization: Bearer agt_XXXXXXXX"
```

Cada herramienta hace **una** llamada a la agent-api con el token de quien
llama, así que los ámbitos, el límite de 120 por minuto y la auditoría son los
mismos que por HTTP. Sin token, las herramientas se listan pero ninguna hace
nada: devuelven un error. No hay token de reserva en el servidor.

| Ámbito | Herramientas MCP |
|---|---|
| `monitor:read` | `monitor_overview(range="7d")`, `get_llm_costs(range="7d")`, `get_channels_status()`, `get_health()` |
| `interactions:read` | `list_interactions(status?, canal?, limit=20)`, `get_interaction_metadata(conversation_id)` |
| `kb:read` | `list_kb_documents()`, `get_kb_document(document_id)`, `search_kb(query, top_k=5)` |
| `kb:write` | `create_kb_document(titulo, contenido)`, `update_kb_document(document_id, contenido)` |
| `prompts:write` | `list_agents()`, `update_agent_prompt(agent_id, prompt_system)` |
| `conversations:read` | `get_conversation_messages(conversation_id, limit=50)` |
| `conversations:reply` | `send_conversation_message(conversation_id, texto?, archivos?, idempotency_key?)`, `handoff_conversation(conversation_id, motivo?, mensaje_al_cliente?)` |
| `contacts:write` | `update_contact(contact_id, nombre?, email?, etapa?, etiquetas_agregar?)` |

`classifier:write` no tiene herramienta MCP: se usa por HTTP directo (abajo).

Todas devuelven el JSON de la API como texto. Si algo falla devuelven
`{"error": true, "status"?, "detail"}`: `status` es el código de la agent-api
(401, 403, 404, 409, 429…) o 422 si un identificador no es un UUID; sin
`status`, falta el token o no se pudo hablar con la API.

## Ámbitos

| Ámbito | Da acceso a |
|---|---|
| `monitor:read` | `GET /monitor/overview`, `GET /monitor/llm-costs`, `GET /monitor/channels`, `GET /monitor/health` |
| `interactions:read` | `GET /interactions`, `GET /interactions/{id}/metadata` |
| `kb:read` | `GET /kb/documents`, `GET /kb/documents/{id}`, `POST /kb/search` |
| `kb:write` | `POST /kb/documents`, `PUT /kb/documents/{id}` |
| `prompts:write` | `GET /agents`, `PUT /agents/{id}/prompt` |
| `classifier:write` | `GET`/`PUT /classifier`, `GET`/`POST /classifier/rules`, `DELETE /classifier/rules/{id}` |
| `conversations:read` | `GET /conversations/{id}/messages` (n8n) |
| `conversations:reply` | `POST /conversations/{id}/messages`, `POST /conversations/{id}/handoff` (n8n) |
| `contacts:write` | `PATCH /contacts/{id}` (n8n) |

Con los ámbitos de siempre, esta API **no** expone contenido de conversaciones,
contactos, credenciales ni configuración: lo máximo es métricas agregadas,
metadata de conversaciones, KB, prompts y clasificador. Los tres de n8n son la
excepción a propósito: existen para que un flujo de n8n que hace de cerebro de
un agente («Sofía en n8n») conteste, y el panel los da con el preset «Para n8n». No existe un `health:read` aparte: la salud
operativa (BD/Redis/colas/canales) es la misma clase de riesgo que las métricas
agregadas (cero PII, cero contenido) y va dentro de `monitor:read`.

## Endpoints

### Monitorización

```
GET /monitor/overview?range=today|7d|30d|90d
```
Devuelve agregados: conversaciones por estado/canal, handoffs, volumen de mensajes
por rol, coste LLM del rango y canales pausados. Sin contenido de mensajes.

```
GET /monitor/llm-costs?range=24h|7d|30d|month
```
Coste/uso LLM con la MISMA query que el dashboard admin (los números cuadran con
el panel): totales, desglose por agente (`by_agent`), por modelo (`by_model`) y
serie por día (`series`).

```
GET /monitor/channels
```
Estado operativo por canal: pausado/activo, configurado/habilitado, agente
asignado y última entrada de cliente (`last_inbound_at`, proxy del último
webhook). Incluye `recent_errors` del runtime, saneados (solo ts/level/event/canal).

```
GET /monitor/health
```
Salud del backend: `{status, db, redis, queues: {celery_backlog,
outbound_jobs_pending}}`.

### Interacciones (solo metadata, sin PII)

```
GET /interactions?status=bot|humano|cerrada&canal=whatsapp|web|instagram_dm|email|retell_voice&contact_id=<uuid>&limit=20
GET /interactions/{id}/metadata
```
`contact_id` acota la lista a las conversaciones de un contacto concreto. Sin
él hay que paginar, y las conversaciones sin actividad (`last_activity_at`
nulo) quedan siempre al final del orden.

Metadata operativa de conversaciones: id, canal, estado, `created_at`,
`last_activity_at`, `seconds_in_status`, `assigned` (bool) y el contacto SOLO
como UUID (`contact_id`). El detalle añade `ended_at`, `derivada_a_humano_at`,
`archived`/`quarantined` y conteo de mensajes por rol. **Nunca** devuelve texto
de mensajes ni nombre/teléfono/email del contacto.

### Base de conocimiento

```
GET  /kb/documents                     → lista (id, nombre, formato, editable…)
GET  /kb/documents/{id}                → contenido completo (texto)
POST /kb/search      {"query": "...", "top_k": 5}
POST /kb/documents   {"titulo": "...", "contenido": "..."}     (crea .txt indexado)
PUT  /kb/documents/{id} {"contenido": "..."}                   (solo txt/md)
```

Notas para el asistente:
- Antes de editar, **lee el documento** (`GET /kb/documents/{id}`): el `PUT`
  reemplaza el contenido ENTERO, no hace parches.
- Cada edición guarda la versión anterior (el operador puede restaurar desde el panel)
  y reindexa automáticamente (chunks + embeddings).
- Los formatos binarios (pdf/docx/xlsx) se leen pero no se editan (HTTP 409):
  propón al operador crear un documento de texto nuevo si hace falta corregirlos.

### Prompts de agentes

```
GET /agents                            → agentes con su prompt_system actual
PUT /agents/{id}/prompt {"prompt_system": "..."}
```

Notas para el asistente:
- El prompt anterior queda en el historial (restaurable desde Agentes en el panel).
- Las reglas de seguridad del sistema (anti-inyección, transparencia IA) **no forman
  parte del prompt**: están fijas en código y no se pueden alterar por esta vía.
- Cambios de prompt afectan al bot que habla con clientes **al instante**: haz cambios
  incrementales y avisa al operador de qué cambiaste y por qué.

### Clasificador de no deseados

```
GET    /classifier                     → enabled, canales, instrucciones, modelo
PUT    /classifier {"instructions": "..."}
GET    /classifier/rules               → reglas duras (sin LLM)
POST   /classifier/rules {"campo": "remitente|dominio|asunto", "valor": "...",
                          "canal": "instagram_dm", "nota": "..."}
DELETE /classifier/rules/{id}
```

Notas para el asistente:
- Lo que se afina aquí decide qué **no** se contesta. Pasarse de ancho silencia a un
  cliente real y nadie se entera: la conversación se queda retenida hasta que alguien
  la libera a mano en el panel. Ante la duda, que pase.
- El **modelo**, el **encendido** y la **acción sobre Gmail** no se tocan por esta vía:
  son decisiones de coste y de buzón, y siguen siendo solo del panel.
- Las reglas duras van antes del modelo y no cuestan tokens: para cuentas concretas
  (un bot de otra empresa que escribe todos los días) son mejores que instruir al
  modelo. El `campo` `asunto` solo existe en email.
- Todo surte efecto al momento y queda auditado con el token que lo hizo.

### Conversaciones y fichas (para n8n)

```
GET   /conversations/{id}/messages?limit=50
      → {conversation_id, canal, estado, mensajes: [{id, rol: paciente|sofia|operador,
         texto, tipo, fecha, archivo_url?, sugerencia_sin_enviar?}]}
POST  /conversations/{id}/messages {"texto": "...", "archivos": [{tipo, url, pie?, nombre?}],
                                    "idempotency_key": "..."}
      → {enviados, sugerencia, archivos: [{tipo, nombre, estado, motivo?}]}
POST  /conversations/{id}/handoff {"motivo": "...", "mensaje_al_cliente": "..."}
PATCH /contacts/{id} {"nombre", "email", "etapa", "etiquetas_agregar": []}
      → {cambios: [...]}
```

Notas:
- **409** si la conversación no está en manos del bot (la tomó una persona o está cerrada), si la ventana de 24 h de WhatsApp está cerrada (solo vale una plantilla) o si es de correo (por correo se contesta en la respuesta directa del webhook).
- **En Entrenamiento**, lo que se manda queda como sugerencia.
- **Una `idempotency_key` repetida** (24 h) devuelve `{"repetido": true}` sin mandar nada.
- **Los mensajes salen marcados como de n8n** en el inbox.
- **La ficha** nunca cambia el teléfono, que es su llave. Las etiquetas que no existen se crean.
- **El servidor MCP** los expone como `get_conversation_messages`, `send_conversation_message`, `handoff_conversation` y `update_contact`.

## Ejemplos

```bash
BASE="https://<tu-dominio>/api/v1/agent-api"
TOKEN="agt_XXXX"

curl -s -H "Authorization: Bearer $TOKEN" "$BASE/monitor/overview?range=7d"

curl -s -H "Authorization: Bearer $TOKEN" "$BASE/kb/documents"

curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "horario de atención"}' "$BASE/kb/search"

curl -s -X PUT -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"contenido": "Atendemos de 9 a 20h de lunes a viernes."}' \
  "$BASE/kb/documents/<document_id>"
```

## Errores

| Código | Significado |
|---|---|
| 401 | Token ausente, inválido, revocado o caducado |
| 403 | El token no tiene el ámbito necesario |
| 404 | Recurso no encontrado |
| 409 | Documento no editable (binario); conversación que no está en manos del bot, ventana cerrada o de correo |
| 429 | Rate limit del token superado |
