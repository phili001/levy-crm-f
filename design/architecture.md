# Arquitectura del sistema — Chatbot

> **Aviso.** Este documento es del diseño inicial: describe la intención, no
> el árbol de ficheros de hoy. Varias piezas acabaron en otro sitio (por
> ejemplo, todo lo de administración vive en `backend/app/api/admin.py`).
> Para saber dónde está algo, manda el código.

## Visión general

App de un cliente por instalación para atención al cliente con agente IA, RAG, CRM e inbox en tiempo real. Empezó siendo solo WhatsApp; hoy atiende cinco canales: WhatsApp, Instagram DM, chat web, correo y voz. Se despliega como un conjunto de contenedores Docker en EasyPanel, sobre un VPS por cliente.

```
┌─────────────────────────────────────────────────────────────────┐
│                          EasyPanel (VPS)                         │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │   FastAPI    │  │   Celery     │  │   Postgres   │           │
│  │  + WebSocket │  │   workers    │  │  + pgvector  │           │
│  │   (app)      │  │   (worker)   │  │     (db)     │           │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘           │
│         │                  │                                     │
│         └──────────┬───────┘                                     │
│                    │                                             │
│              ┌─────▼─────┐    ┌──────────┐                       │
│              │   Redis   │    │  Celery  │                       │
│              │           │    │   beat   │                       │
│              └───────────┘    └──────────┘                       │
└─────────────────────────────────────────────────────────────────┘
        ▲                                  │
        │ webhook YCloud                   │ outbound
        ▼                                  ▼
   WhatsApp, Instagram,           LLM (OpenAI / Anthropic /
   web, correo, voz                Gemini), embeddings y
                                   Whisper de OpenAI,
                                   Resend o SMTP
```

> El dibujo es del diseño inicial, cuando solo había WhatsApp por YCloud. Hoy
> entran cinco canales y el webhook de Meta. **Telegram y Slack se retiraron.**

## Piezas

### Backend — Python + FastAPI

- **api/** — endpoints REST y WebSocket por dominio. Hoy: auth, conversations, contacts, tags, knowledge_base, learning, admin, backups, agent_api, uploads, voice, webchat, push, oauth, webhooks y health
- **agents/** — `orchestrator.py` y `tools/` (kb_search, contact_lookup, contact_upsert, human_handoff, calendar, schedule_config, registry), más `internal/`, el agente interno del operador
- **providers/** — adaptadores externos: `whatsapp/` (ycloud, meta, selector), `llm/` (openai, anthropic, gemini), `transcription/`, `embeddings/`, `email/` (resend, smtp), `voice/` (retell), `instagram/`, `gmail/`. Cada uno con su interfaz base. **No existe `notifications/`:** los avisos al operador van por web push (`api/push.py`).
- **services/** — lógica de negocio: message_buffer, conversation, kb_indexer, audio_processor, channel_sender, runtime_config, agent_pause, classifier, dashboard, budget, learned_rules, backups y varios más
- **models/** — SQLAlchemy ORM
- **schemas/** — Pydantic (request/response)
- **db/** — engine, sesión, migraciones Alembic
- **core/** — config, security, logging, eventos internos (pub/sub para WebSocket)
- **tasks/** — tareas Celery: process_message, drain_buffer, index_document, transcribe_audio, outbound_send, poll_gmail, backup_db, purge_audios / purge_emails / purge_internal_logs, detect_correction_gaps, detect_faq_gaps, refresh_prices, web_push_send, notify_pending_check y ping. **No existe `send_email_summary`:** el correo de resumen se manda desde el propio endpoint de cierre

### Frontend — React

- **pages/admin/** — Inicio, Conexiones, Agentes, Aprendizajes, Envío masivo, Usuarios, Copias, Audit, Salud, Logs, Bloqueados, Ajustes
- **pages/client/** — Inbox, Contactos, ficha de contacto, Llamadas, Base de conocimiento
- **pages/revisor/** — Conexiones recortada para el revisor de Meta
- **components/** — MessageBubble, MediaBubble, TagPicker, DraftCard, Layout, PageHeader… **No existen `ChatView`, `AudioPlayer` ni `ContactCard`**, que estaban en el plan inicial
- **services/api.ts** — cliente REST (y `services/` por dominio: contacts, conversations, kb, admin, learning, backups, push)
- **hooks/useInboxSocket.ts** — cliente WebSocket. **No existe `services/websocket.ts`**
- Rutas con React Router (`src/router.tsx`). Estado con Zustand. Estilos con Tailwind y el sistema propio de `index.css`.

### Base de datos — PostgreSQL 16 + pgvector

- Una sola DB para todo: memoria conversacional, CRM, RAG.
- Extension `vector` activada en migración inicial.
- Dos funciones SQL: `match_chunks(...)`, solo vector, y `match_chunks_hybrid(...)`, vector más texto completo en español, que es la que usa el agente. Se definen en las migraciones, no en los modelos.
- Migraciones gestionadas con Alembic (`backend/app/db/migrations/`).

### Autenticación

- JWT (HS256) con bcrypt para el hash de las contraseñas.
- Tokens con expiración. Un cambio de contraseña invalida los emitidos antes (`password_changed_at`), y hay lista de revocados.
- Tres roles: `admin`, `cliente` y `revisor`. Los dos primeros se distinguen con dependencias por endpoint (`require_admin`); al tercero lo acota una lista blanca de rutas en `get_current_user`. Todo en `backend/app/api/deps.py`.
- Audit log de accesos sensibles.

### Cola de tareas — Celery + Redis

- Broker Redis (DB 1)
- Backend Redis (DB 2)
- Buffer mensajes Redis (DB 0)
- Workers escalables horizontal (cada uno procesa N tareas en paralelo)
- Celery beat para lo programado. El `beat_schedule` real está en `backend/app/tasks/__init__.py`: entrada de Gmail, purgas de retención, copias de seguridad, detección de huecos de aprendizaje, barrido de envíos colgados y refresco de precios de modelos

### WebSocket

- Endpoint `/api/v1/ws/inbox`. **La autenticación es por ticket efímero de un solo uso** (`POST /auth/ws-ticket`), no con el JWT en la query: el token acababa en los logs de los proxies.
- Hay un segundo WebSocket para el widget web, en `api/webchat.py`.
- Bus interno publish/subscribe en `core/events.py` que conecta procesamiento Celery → WebSocket.

## Los tres niveles de credenciales

- **Nivel 1, el `.env`** — secretos de infraestructura:
  - `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`
  - `JWT_SECRET`, `ENCRYPTION_KEY`
  - `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_PASSWORD` (solo para seed)

- **Nivel 2, la configuración del despliegue** — variables que dependen del entorno:
  - `APP_ENV`, `APP_BASE_URL`, `FRONTEND_BASE_URL`
  - `AUDIO_STORAGE_PATH`, defaults del agente (modelo, temperatura, buffer)

- **Nivel 3, el panel de administración, cifradas en la base** — claves de proveedores:
  - WhatsApp: YCloud (api key, webhook secret, phone number) o Meta
  - LLM: OpenAI, Anthropic o Gemini (api key y `base_url`)
  - Resend (api key, from email) o SMTP
  - Retell (voz), Meta/Instagram, Google (Calendar y entrada por Gmail)
  - ~~Telegram~~ y ~~Slack~~: **retirados**, no hay credenciales para ellos

## Integraciones

| Servicio | Uso | Tipo |
|---|---|---|
| YCloud | WhatsApp Business API: webhook entrante, envío de mensajes, descarga de audios | HTTP REST |
| OpenAI / Anthropic / Gemini | LLM con tool use, elegido en el panel por agente | HTTP REST |
| OpenAI | Embeddings (`text-embedding-3-small`) y transcripción Whisper. **Solo OpenAI**: no hay alternativa para estas dos | HTTP REST (SDK oficial) |
| Resend o SMTP | Correo transaccional (resumen al cierre). SMTP tiene prioridad si está configurado | HTTP REST / SMTP |
| Retell | Canal de voz | HTTP REST + webhook |
| Meta | WhatsApp directo e Instagram DM | Graph API + webhook |
| Google | Calendar y entrada de correo por Gmail | OAuth + HTTP REST |
| Web push | Aviso al operador (sustituye a Telegram y Slack) | VAPID |

Todos los proveedores se acceden a través de su interfaz abstracta en `providers/` para permitir cambios futuros con un solo adaptador.
