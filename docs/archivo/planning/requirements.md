# Requisitos — Chatbot

> **Aviso.** Este documento es del diseño inicial: describe la intención, no
> el árbol de ficheros de hoy. Varias piezas acabaron en otro sitio (por
> ejemplo, todo lo de administración vive en `backend/app/api/admin.py`).
> Para saber dónde está algo, manda el código.

## Requisitos funcionales (Fase 1)

### Mensajería y agente

- **FR-01** — La app recibe mensajes entrantes por webhook con validación HMAC de la firma. Era solo WhatsApp por YCloud; hoy `backend/app/api/webhooks.py` atiende además a Meta (WhatsApp directo e Instagram), y hay entradas propias para voz (`api/voice.py`), chat web (`api/webchat.py`) y correo (sondeo de Gmail).
- **FR-02** — Mensajes del mismo número que llegan en ráfaga se agrupan con buffer Redis (5s configurables) antes de procesarse.
- **FR-03** — Los audios entrantes se transcriben con OpenAI Whisper. La transcripción se persiste junto al audio original.
- **FR-04** — El agente principal procesa la conversación con tool use: `consultar_kb`, `buscar_contacto`, `crear_actualizar_contacto`, `derivar_humano` (más las de agenda que se añadieron después, en `backend/app/agents/tools/`). El modelo no está fijado en el código: se elige por agente desde el panel sobre la interfaz `LLMProvider` (OpenAI, Anthropic o Gemini). El valor por defecto está en `DEFAULT_LLM_MODEL` (`backend/app/core/config.py`).
- **FR-05** — La memoria conversacional persiste en Postgres usando el teléfono como `session_id`. Context window configurable (default 20 mensajes).
- **FR-06** — La respuesta del agente puede dividirse en hasta N partes (default 3) enviadas con espera para parecer natural.
- **FR-07** — Cuando el agente decide derivar a humano, la conversación cambia a estado `humano` y el bot se calla. ~~Se notifica al equipo por Telegram y/o Slack.~~ **RETIRADO:** el aviso al equipo no va por Telegram ni por Slack. Hoy se avisa por web push a la PWA del panel (`backend/app/api/push.py`, `services/web_push.py`) y por el registro en vivo del panel. No hay ningún proveedor de Telegram ni de Slack en el código: no lo implementes.

### Base de Conocimiento (RAG)

- **FR-08** — El cliente puede subir documentos al panel en formatos: PDF, DOCX, TXT, MD, CSV, XLSX.
- **FR-09** — Cada documento se chunkea, se generan embeddings (OpenAI `text-embedding-3-small`) y se almacenan en Postgres con pgvector.
- **FR-10** — El cliente puede borrar documentos. Al borrar, todos sus chunks se eliminan.
- **FR-11** — Se puede hacer "búsqueda de prueba" en el panel para ver qué chunks devuelve el RAG ante una pregunta. Ojo: **todo `/kb/*` acabó siendo `require_admin`**, incluida esta búsqueda, porque gasta embeddings de pago.
- **FR-12** — La indexación se ejecuta en background (Celery). El estado del documento es visible: procesando, indexado, error.

### CRM nativo

- **FR-13** — Cada conversación crea o actualiza automáticamente un contacto en el CRM (por teléfono).
- **FR-14** — El cliente puede editar manualmente datos del contacto: nombre, email, estado, etiquetas, notas internas.
- **FR-15** — El cliente puede crear y editar etiquetas (nombre + color) y asignarlas a contactos. **La página `/tags` se eliminó:** se hace desde la ficha de contacto, con `frontend/src/components/TagPicker.tsx`.

### Inbox (panel cliente)

- **FR-16** — Lista de conversaciones con búsqueda, filtros (estado bot/humano/cerrada, etiquetas, no leídas) y paginación.
- **FR-17** — Vista de chat tipo WhatsApp Web con burbujas, agrupación temporal, reproductor de audios inline y transcripción debajo.
- **FR-18** — Ficha de contacto lateral con datos, etiquetas editables, notas internas y botones "Tomar control" / "Devolver al bot" / "Cerrar conversación".
- **FR-19** — Caja de respuesta manual del operador cuando la conversación está en humano. Si ha pasado más de 24h desde el último mensaje del usuario, el botón se desactiva con aviso.
- **FR-20** — El inbox se actualiza en tiempo real vía WebSocket (mensajes nuevos, cambios de estado).

### Email resumen

- **FR-21** — Al cerrar una conversación, opción de enviar resumen automático al cliente final por correo. Sale por SMTP si está configurado y, si no, por Resend.

### Panel admin

- **FR-22** — CRUD de usuarios y asignación de roles.
- **FR-23** — Gestión de credenciales cifradas en DB con Fernet, con máscara y prueba de conexión en el panel. La lista de proveedores creció y **ya no incluye Telegram ni Slack** (retirados): hoy son WhatsApp (YCloud o Meta), OpenAI / Anthropic / Gemini, Resend o SMTP, Retell (voz), Meta/Instagram y Google (Calendar y entrada por Gmail). La pantalla es Conexiones (`/admin/connections`), no una página de credenciales suelta.
- **FR-24** — Editor del prompt con vista previa, versionado básico y restaurar versión anterior (tabla `agent_prompt_history`). El prompt es **por agente**, no uno global: se edita en `/admin/agent/agents`.
- **FR-25** — Configuración del modelo: proveedor y modelo por agente, temperatura, max_tokens, buffer_seconds. Vive en la pestaña "Tarifas y límites" de Agentes (`/admin/agent/agents?tab=model`), no en una página propia.
- **FR-26** — Audit log filtrable por usuario, acción, fecha.
- **FR-27** — Panel de salud del sistema: estado servicios (DB, Redis, Celery workers), últimos errores.

## Requisitos no funcionales

Ver `docs/nfr.md` para detalle (concurrencia, latencia, disponibilidad, seguridad).

## Roles de usuario

Son **tres**, definidos en `backend/app/models/user.py` (enum `UserRole`). El
mapa de qué puede hacer cada uno está comentado en `backend/app/api/deps.py`;
esa es la fuente de verdad.

- **`admin`** — lo irreversible, lo masivo y lo que cuesta dinero: borrar
  contactos y etiquetas, exportar e importar la base de contactos, archivar en
  lote, sacar del antispam, escribir en la base de conocimiento y todo
  `/admin/*`. Sí ve el inbox.
- **`cliente`** — operador de bandeja. El día a día de una conversación: leer,
  responder, adjuntar, tomar el control, devolver al bot, archivar o cerrar una
  conversación, marcar leído, trabajar los borradores, y ver y editar la ficha
  de un contacto con sus notas, su actividad y sus etiquetas. No entra en
  `/admin/*`.
- **`revisor`** — cuenta de solo mirar para quien revisa la app en Meta. Ve la
  bandeja de Instagram, responde en ella y ve de qué cuenta de Instagram se
  trata. Nada más. Se le acota con una lista blanca de rutas en
  `get_current_user` (`api/deps.py`), no con una dependencia por endpoint. La
  cuenta se crea para una revisión y se borra al acabar.

La distinción entre `admin` y `cliente` **no** es "ve más / ve menos": es qué
puede hacer.
