# Tareas de infraestructura y recorridos de usuario — Chatbot

> **Aviso.** Este documento es del diseño inicial: describe la intención, no
> el árbol de ficheros de hoy. Varias piezas acabaron en otro sitio (por
> ejemplo, todo lo de administración vive en `backend/app/api/admin.py`).
> Para saber dónde está algo, manda el código.

## Tareas de infraestructura

### IT-01: Docker Compose + servicios base

- **Para qué:** Infraestructura corriendo localmente y en EasyPanel (app, worker, beat, db, redis).
- **Piezas:** `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, healthchecks.
- **Criterio de aceptación:** `docker compose up` levanta todos los servicios sin errores. `docker compose ps` los muestra "healthy".
- **Seguridad:** Postgres con password de `.env`. Puertos internos no expuestos al host salvo `app` (8000) y `frontend` (5173).

### IT-02: Esqueleto FastAPI

- **Para qué:** Servidor HTTP arrancando con endpoint `/health`.
- **Piezas:** `backend/app/main.py`, `core/config.py` (pydantic-settings), `core/logging.py` (structlog), `db/session.py` (async engine SQLAlchemy).
- **Criterio de aceptación:** `GET /health` devuelve 200 con `{ status: "ok", db: "ok" }`.

### IT-03: Alembic + migración inicial

- **Para qué:** Esquema BD completo + función `match_chunks` SQL.
- **Piezas:** `alembic.ini`, `backend/app/db/migrations/`, primera migración con todas las tablas + extension vector.
- **Criterio de aceptación:** `alembic upgrade head` aplica todas las migraciones desde DB vacía. Todas las tablas y la función existen.

### IT-04: Auth JWT + RBAC

- **Para qué:** Login, emisión token, decoders, guards por rol.
- **Piezas:** `core/security.py`, `api/auth.py`, dependencias FastAPI `get_current_user`, `require_role(...)`.
- **Criterio de aceptación:** Login con seed admin funciona. Endpoint protegido sin token → 401. Con rol incorrecto → 403.
- **Seguridad:** bcrypt cost 12. JWT con expiración. Rate limit en `/auth/login`.

### IT-05: Esqueleto Celery

- **Para qué:** Workers y beat corriendo, conectados a Redis.
- **Piezas:** `backend/app/tasks/__init__.py` (Celery app), worker dockerfile, beat schedule básica.
- **Criterio de aceptación:** Tarea `ping()` se encola desde un endpoint test y el worker la procesa, log visible.

### IT-06: Esqueleto frontend

- **Para qué:** App React arrancando con login + rutas vacías por rol.
- **Piezas:** `frontend/` con Vite + React + TS + Tailwind + React Router + Zustand. **shadcn/ui no se usa:** no hay `components/ui/`; las clases del sistema visual están a mano en `frontend/src/index.css`.
- **Criterio de aceptación:** `npm run dev` levanta el frontend. Login funcional contra backend. Redirige según rol.

### IT-07: Cliente HTTP centralizado

- **Para qué:** Llamadas API uniformes, refresh token, manejo errores.
- **Piezas:** `frontend/src/services/api.ts`, interceptors, store auth.

### IT-08: Servicio cifrado credenciales

- **Para qué:** Cifrar/descifrar valores en tabla `credentials` con Fernet.
- **Piezas:** `backend/app/services/credentials.py` (el fichero se llama así, no `credentials_service.py`) y `core/encrypted_type.py`, con la clave en `core/config.py` (`ENCRYPTION_KEY`).
- **Criterio de aceptación:** Endpoint admin guarda credencial, valor en DB es bytea cifrado, lectura devuelve plaintext.

### IT-09: Tests automáticos críticos

- **Para qué:** Cobertura mínima en código sensible.
- **Piezas:** `backend/tests/` con suites para auth, agente (con el LLM simulado), kb_search y webhooks (firma HMAC). Hoy son más de 130 ficheros de prueba.
- **Criterio de aceptación:** `pytest` pasa todos los tests, cobertura ≥70% en módulos críticos.

### IT-10: Seed con datos demo realistas

- **Para qué:** Despliegue limpio tiene usuarios, prompt, etiquetas, ejemplo conversación.
- **Piezas:** `backend/app/scripts/seed.py`, que ejecuta solo `backend/entrypoint.sh` al arrancar. A mano: `docker compose exec app python -m app.scripts.seed`. Es idempotente.

### IT-11: Audit log automático

- **Para qué:** Registrar cambios sensibles sin tener que llamar manualmente.
- **Piezas:** Decorador / middleware que captura mutaciones en endpoints admin.

### IT-12: EasyPanel + healthchecks

- **Para qué:** App lista para producción en EasyPanel.
- **Piezas:** `docker-compose.easypanel.yml` y `.env.example` (ambos en la raíz), healthchecks Docker y la guía `DEPLOY_EASYPANEL.md`. **No existe** `deployment/easypanel/`: la carpeta `deployment/` solo tiene un README que remite a la guía.

---

## Recorridos de usuario

### UJ-01: Webhook entrante

- **Descripción:** el proveedor envía un webhook con el mensaje. La app valida la firma, persiste el mensaje crudo y encola la tarea de procesamiento. Hoy hay varias entradas en `api/webhooks.py`, no solo la de YCloud: también Meta (WhatsApp e Instagram).
- **Backend:** `api/webhooks.py POST /webhooks/ycloud`, modelo `messages`, tarea Celery `process_message`. **No hay `services/webhook_validator.py`:** la firma la comprueba el propio proveedor, con `verify_webhook_signature` en `providers/whatsapp/ycloud.py` y `meta.py`.
- **Panel:** N/A.
- **Criterio de aceptación:** Webhook real desde YCloud llega, mensaje queda en DB en estado pre-procesado, tarea encolada.
- **Seguridad:** Validación HMAC obligatoria. Rate limit por IP. 401 si firma inválida.

### UJ-02: Agente IA procesa y responde

- **Descripción:** Worker Celery construye contexto, llama al modelo con tool use y envía la respuesta por el canal. El proveedor de LLM lo resuelve `resolve_llm_provider` según el agente (OpenAI, Anthropic o Gemini); no está fijado a OpenAI.
- **Backend:** `agents/orchestrator.py`, `agents/tools/*`, `providers/llm/openai_client.py`, `providers/whatsapp/ycloud.py`.
- **Panel:** N/A.
- **Criterio de aceptación:** Mensaje "hola" → bot responde con saludo en <15s. Tool `consultar_kb` puede invocarse con éxito.
- **Pruebas:** Mock OpenAI, simular invocación de tools, validar prompt enviado.

### UJ-03: Buffer Redis 5s

- **Descripción:** Si llegan varios mensajes del mismo número en ráfaga, se acumulan antes de procesarse.
- **Backend:** `services/message_buffer.py` con Redis (clave por teléfono, TTL 5s configurable).
- **Criterio de aceptación:** Tres mensajes seguidos del mismo número en <5s → el bot responde una sola vez al texto combinado.

### UJ-04: Memoria conversacional

- **Descripción:** El agente tiene contexto de los últimos N mensajes (default 20) de esa conversación.
- **Backend:** Query a `messages` filtrada por `conversation_id`, ordenada por fecha, limitada a `context_window`.
- **Criterio de aceptación:** Mensaje 21 sigue teniendo contexto desde el mensaje 1 si entra dentro del límite. Mensaje 100 solo tiene 20 últimos.

### UJ-05: Subir documento KB (PDF/DOCX/TXT/MD)

- **Descripción:** Cliente sube documento, se chunkea y se indexa en background.
- **Backend:** `api/knowledge_base.py POST /kb/documents`, `services/kb_indexer.py`, tarea Celery `index_document`, libs: `pypdf`, `python-docx`.
- **Panel:** Página KB con uploader drag-drop, lista con estados.
- **Criterio de aceptación:** Subir un PDF → aparece "Procesando…", al terminar "Indexado" con nº chunks. Búsqueda RAG recupera contenido del PDF.

### UJ-06: Subir documento KB (CSV/XLSX)

- **Descripción:** Cada fila se convierte en un chunk con formato "Columna1: valor | Columna2: valor".
- **Backend:** Extensión de `kb_indexer.py` con `pandas` para CSV/XLSX.
- **Criterio de aceptación:** Subir CSV de catálogo → cada fila = un chunk. Búsqueda por nombre de producto devuelve la fila correcta.

### UJ-07: Búsqueda de prueba KB

- **Descripción:** Cliente prueba qué responde el RAG ante una pregunta antes de que lo haga el bot.
- **Backend:** `api/knowledge_base.py POST /kb/search`. Ojo: **todo `/kb/*` es `require_admin`**, incluido este endpoint y los listados.
- **Panel:** Panel collapsible en página KB con input + resultados.
- **Criterio de aceptación:** Pregunta devuelve top 5 chunks con similarity score y referencia al documento.

### UJ-08: Auto-crear contacto desde mensaje

- **Descripción:** Cuando entra un mensaje de un número desconocido, se crea automáticamente un contacto.
- **Backend:** En `services/conversation.py` antes de procesar el mensaje. Upsert por `telefono`.
- **Criterio de aceptación:** primer mensaje de un número nuevo → el contacto existe en la base con `estado=contacto` y `origen=whatsapp`. Con varios canales, `origen` puede ser también `web`, `instagram`, `email` o `manual`, y la llave del contacto lleva prefijo (`ig:`, `web:`, `email:`) cuando no hay teléfono.

### UJ-09: CRUD manual contactos

- **Descripción:** Cliente puede crear, editar, ver y borrar contactos.
- **Backend:** `api/contacts.py` CRUD completo.
- **Panel:** Página `/contacts` con tabla + modal/drawer ficha.
- **Criterio de aceptación:** Crear contacto manualmente, editarlo, asignarle etiquetas, verlo en inbox.

### UJ-10: CRUD etiquetas

- **Descripción:** Cliente gestiona etiquetas con nombre y color, las asigna a contactos.
- **Backend:** `api/tags.py`, `api/contacts.py` (`POST/DELETE /contacts/{id}/tags/{tag_id}`; el `tag_id` va en la RUTA, no en el cuerpo).
- **Panel:** **la página `/tags` se eliminó.** Las etiquetas se gestionan desde la ficha de contacto con `frontend/src/components/TagPicker.tsx`.
- **Criterio de aceptación:** Crear etiqueta "VIP" rojo, asignar a 3 contactos, filtrar inbox por "VIP" → solo se ven esos.

### UJ-11: Listado inbox conversaciones

- **Descripción:** Lista con filtros y paginación.
- **Backend:** `GET /conversations` con filtros.
- **Panel:** Columna izquierda inbox.
- **Criterio de aceptación:** Filtrar por "humano" muestra solo las derivadas. Paginar funciona. Buscar por nombre filtra.

### UJ-12: Vista chat WhatsApp Web

- **Descripción:** Burbujas, agrupación por día, scroll infinito hacia mensajes antiguos.
- **Backend:** `GET /conversations/{id}/messages` con cursor pagination.
- **Panel:** `frontend/src/pages/client/Inbox.tsx` con `components/MessageBubble.tsx`. **No existe un componente `ChatView`.**
- **Criterio de aceptación:** Conversación con 200 mensajes carga progresivamente al scrollear arriba.

### UJ-13: WebSocket inbox tiempo real

- **Descripción:** Nuevos mensajes aparecen en el inbox sin recargar.
- **Backend:** WebSocket en `/api/v1/ws/inbox` + bus de eventos en `core/events.py`. Cada `process_message` publica un evento.
- **Panel:** `frontend/src/hooks/useInboxSocket.ts` con reconexión. **No existe `services/websocket.ts`.** La conexión se autentica con un ticket de un solo uso (`POST /auth/ws-ticket`), no con el JWT en la query.
- **Criterio de aceptación:** Dos pestañas abiertas en `/inbox` → mensaje nuevo se ve en ambas <1s.

### UJ-14: Ficha contacto lateral

- **Descripción:** Panel derecho con datos, etiquetas, notas, botones de acción.
- **Panel:** el panel lateral del inbox (`pages/client/Inbox.tsx`) y la ficha completa en `pages/client/ContactDetail.tsx`. **No existe un componente `ContactCard`.**
- **Criterio de aceptación:** Cambiar etiqueta o nota se persiste con autosave. Botones cambian estado conversación.

### UJ-15: Derivación humana

- **Descripción:** El agente invoca tool `derivar_humano`. Conversación pasa a `humano`. Bot deja de responder.
- **Backend:** herramienta en `agents/tools/human_handoff.py`. Cambia `conversations.status` y, de paso, deja una fila en `knowledge_gaps` con `trigger="handoff"` para el autoaprendizaje.
- **Criterio de aceptación:** Pedir al bot "quiero hablar con una persona" → tool invocado, status cambia, próximo mensaje NO procesado por bot.

### UJ-16: Aviso al equipo al derivar — ~~Telegram + Slack~~ RETIRADO

**Telegram y Slack se sacaron del producto.** No existe
`providers/notifications/`, ni cliente de Telegram, ni de Slack, ni
credenciales para ellos. Si lees este recorrido y te pones a implementarlo,
estás añadiendo algo que se quitó a propósito: **no lo hagas.**

Lo que sí hay hoy, y hace el mismo trabajo:

- **Web push a la PWA del panel** — `backend/app/api/push.py`,
  `services/web_push.py`, tarea `web_push_send`, tabla `push_subscription`. El
  botón para suscribirse es `frontend/src/components/PushButton.tsx`.
- **Barrido de pendientes** — tarea `notify_pending_check`, que avisa de lo que
  lleva sin contestar.
- **Registro en vivo del panel** — `/admin/system/logs`.

### UJ-17: Respuesta manual operador

- **Descripción:** el operador responde desde el inbox y el texto sale por el canal de la conversación (`services/channel_sender.py`). La ventana de 24 h es cosa de WhatsApp; el canal de voz no admite respuesta desde el panel y devuelve 400.
- **Backend:** `POST /conversations/{id}/messages`. Valida ventana 24h.
- **Panel:** Caja respuesta abajo del chat. Disabled si fuera de ventana.
- **Criterio de aceptación:** Operador escribe y envía → mensaje llega a WhatsApp del cliente. Si >24h, botón desactivado con aviso.

### UJ-18: Transcripción audio

- **Descripción:** las notas de voz se descargan y se transcriben con Whisper. Vale para WhatsApp **y para Instagram** (`providers/instagram/meta.py` captura los adjuntos de audio).
- **Backend:** Tarea Celery `transcribe_audio`. Guarda audio en volumen + transcripción en `messages.audio_transcript`.
- **Criterio de aceptación:** Mensaje audio entra → en <10s la transcripción está en DB. Agente usa la transcripción como texto.

### UJ-19: Reproductor audio + transcripción en inbox

- **Descripción:** En el chat, los audios tienen play + transcripción visible.
- **Panel:** `frontend/src/components/MediaBubble.tsx`, desde `MessageBubble.tsx`. **No existe un componente `AudioPlayer`.**
- **Criterio de aceptación:** Audio se reproduce inline. Transcripción se muestra debajo (colapsable).

### UJ-20: Admin CRUD usuarios

- **Descripción:** Admin gestiona usuarios y roles.
- **Backend:** `backend/app/api/admin.py`. **No existe el directorio `api/admin/`:** es un fichero suelto con todo dentro.
- **Panel:** `pages/admin/UsersPage.tsx`, ruta `/admin/users`.

### UJ-21: Admin credenciales cifradas

- **Descripción:** Editar claves de proveedores. Cifrado al guardar. Test conexión.
- **Backend:** `backend/app/api/admin.py` usando `services/credentials.py`.
- **Panel:** `pages/admin/ConnectionsPage.tsx`, ruta `/admin/connections`. La ruta vieja `/admin/credentials` ya solo es una redirección.
- **Criterio de aceptación:** Guardar key OpenAI → en DB está cifrada. "Probar conexión" devuelve ok/error real.

### UJ-22: Admin editor prompt versionado

- **Descripción:** Editor texto + historial versiones + activar.
- **Backend:** `backend/app/api/admin.py` (configuración e histórico; la tabla es `agent_prompt_history`). **No existe `api/admin/agent.py`.**
- **Panel:** `pages/admin/AgentsPage.tsx`, ruta `/admin/agent/agents`. El prompt es **por agente**, no global. La ruta vieja `/admin/agent/prompt` ya solo es una redirección.

### UJ-23: Admin configuración modelo

- **Descripción:** Form con modelo, temperatura, buffer, etc.
- **Backend:** `backend/app/api/admin.py`.
- **Panel:** pestaña "Tarifas y límites" de `AgentsPage` (`/admin/agent/agents?tab=model`). La ruta vieja `/admin/agent/config` ya solo es una redirección.

### UJ-24: Admin audit log

- **Descripción:** Tabla filtrable con cambios sensibles.
- **Backend:** `GET /admin/audit` con filtros (`api/admin.py`).
- **Panel:** `pages/admin/AuditPage.tsx`, ruta `/admin/audit`.

### UJ-25: Admin salud del sistema

- **Descripción:** Estado servicios, métricas, últimos errores.
- **Backend:** `GET /admin/health` (`api/admin.py`). El `/health` público, sin prefijo, está en `api/health.py`.
- **Panel:** `pages/admin/HealthPage.tsx`, ruta `/admin/health`.

### UJ-26: Email resumen al cerrar conversación

- **Descripción:** botón "Cerrar y enviar resumen" en el inbox → correo de salida.
- **Backend:** `POST /conversations/{id}/close` con el cuerpo `{ resumen?, enviar_resumen_email }`. El envío entra por `providers/email/resend_client.py`, que **prefiere SMTP si está configurado** (`smtp_client.py`) y solo si no lo está usa Resend.
- **Criterio de aceptación:** Cerrar conversación con flag → llega email al contacto con resumen generado.

### UJ-27: Dashboard (solo admin)

- **Descripción:** KPIs + gráficos según rango.
- **Backend:** `GET /api/v1/admin/dashboard/summary` y `/admin/dashboard/attention`, ambos con `require_admin` (`api/admin.py` + `services/dashboard.py`). **No existe `/dashboard/summary` sin el `/admin`.**
- **Panel:** `pages/admin/AdminHome.tsx`, ruta `/admin`. **No existe la ruta `/dashboard`.** Los gráficos son SVG a mano, sin Recharts ni ninguna librería de gráficos.
