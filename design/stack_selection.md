# Elección del stack — Chatbot

> **Aviso.** Este documento es del diseño inicial: describe la intención, no
> el árbol de ficheros de hoy. Varias piezas acabaron en otro sitio (por
> ejemplo, todo lo de administración vive en `backend/app/api/admin.py`).
> Para saber dónde está algo, manda el código.

## Selecciones

| Capa | Tecnología | Razón |
|---|---|---|
| Backend | Python 3.12 + FastAPI | Ecosistema IA maduro, async I/O, OpenAPI gratis, más fácil de leer para perfiles no programadores |
| ORM | SQLAlchemy 2.x (async) | Estándar, soporte async, juega bien con FastAPI |
| Migraciones | Alembic | Estándar SQLAlchemy, versionado limpio |
| Validación | Pydantic v2 | Integrado con FastAPI, validación rápida y declarativa |
| Cola de tareas | Celery 5.x | Probado a escala, integración Redis sólida |
| Cola y caché | Redis 7 | Cola, buffer mensajes, pub/sub interno si hace falta |
| Base de datos | PostgreSQL 16 | Sólido, fiable, una sola DB para todo. Soporte hasta 2028 |
| Almacén de vectores | pgvector | Sin servicio extra, búsqueda híbrida en SQL |
| LLM | Interfaz propia `LLMProvider` con tres familias detrás: OpenAI (y cualquier pasarela compatible: OpenRouter, DeepSeek…), Anthropic y Gemini | El proveedor y el modelo **se eligen en el panel**, por agente, no en el código. Se guardan en la tabla `llm_providers` y se resuelven en cada turno (`providers/llm/openai_client.py: resolve_llm_provider`). La familia se deduce del `base_url` (`providers/llm/families.py`). El modelo por defecto de una instalación limpia es `DEFAULT_LLM_MODEL` en `core/config.py` |
| Embeddings | OpenAI `text-embedding-3-small` | Baratos, calidad muy buena (1536 dims) |
| Transcripción | API de audio de OpenAI. El modelo por defecto es `gpt-4o-mini-transcribe` (`OPENAI_WHISPER_MODEL` en `core/config.py`), no `whisper-1` | Más barato y con menos errores que `whisper-1` para notas de voz en español |
| WhatsApp | YCloud o Meta directamente, detrás de `WhatsAppProvider` | YCloud está dominado y el setup es rápido; Meta directo se añadió después y se elige con `providers/whatsapp/selector.py` |
| Correo saliente | SMTP si está configurado, si no Resend | Resend por API simple y capa gratuita; el SMTP se añadió después y **tiene prioridad** cuando hay credenciales (`providers/email/resend_client.py` decide) |
| Avisos al operador | **Web push** a la PWA del panel (`api/push.py`, `services/web_push.py`) + registro en vivo | ~~Telegram Bot API + Slack Incoming Webhooks~~ **RETIRADOS**: no hay proveedor de Telegram ni de Slack en el código. No los implementes |
| Sesión | JWT (HS256) + bcrypt | Estándar para una app de un cliente por instalación |
| Cifrado de credenciales | Fernet (cryptography) | Simple, autenticado, suficiente para credenciales de proveedores |
| Panel | React 18 + TypeScript + Vite | Estándar moderno y arranque rápido en desarrollo |
| Estado del panel | Zustand | Ligero, sin ceremonia, suficiente para esta complejidad |
| Estilos | Tailwind CSS con sistema propio (*Coro*) y componentes a mano | shadcn/ui **no se usa**: no hay `components/ui/`. Las clases (`.btn`, `.card`, `.input`…) están en `frontend/src/index.css` |
| Iconos | `lucide-react` | Único juego de iconos del panel |
| Rutas | React Router v6 | Estándar |
| WebSocket cliente | Nativo del navegador, con reconexión a mano en `frontend/src/hooks/useInboxSocket.ts` | Sin librería extra |
| Pruebas de backend | pytest + pytest-asyncio + httpx (`backend/requirements-dev.txt`) | Estándar Python async |
| Pruebas de panel | **No hay.** El `package.json` solo trae `dev`, `build`, `preview` y `lint` | El frontend se comprueba con `tsc` y con las pruebas de backend |
| Contenedores | Docker + Docker Compose | Estándar del proyecto |
| Despliegue | EasyPanel | Decisión del equipo: se encarga del proxy inverso, el SSL y los dominios |

## Alternativas consideradas

### Backend — Python vs Node vs Bun

- **Node + TypeScript + BullMQ**: mejor entrada/salida en paralelo, pero el ecosistema de IA va por detrás del de Python: los SDK salen antes en Python. Y Python se lee mejor para quien no programa a diario.
- **Bun + Hono + BullMQ**: muy rápido y moderno, pero demasiado nuevo. No para la producción de un cliente.

### RAG — pgvector vs servicios dedicados

- **Supabase**: heredado de la primera versión. Mismo motor (pgvector), pero con servicio externo, facturación aparte y una instalación más por cliente.
- **Pinecone / Qdrant**: búsqueda vectorial dedicada, más potente a gran escala. Innecesaria para este volumen, y añade servicio externo y coste.

### CRM — nativo vs Airtable

- **Airtable**: interfaz gratis y vistas tipo tablero, pero dependencia externa, coste por usuario y una instalación más por cliente. El negocio no quiere otra herramienta.

### WhatsApp — YCloud vs Meta vs Twilio

- **Meta Cloud API directo**: más barato a escala y con control total, a cambio de un alta laboriosa (Business Manager, verificaciones). Se descartó al principio por eso, y **se acabó añadiendo igualmente**: hoy están los dos (`providers/whatsapp/ycloud.py` y `meta.py`) y se elige uno en el panel.
- **Twilio**: muy fiable y con buena documentación, pero más caro y sin nada que YCloud no dé.
- **Wati / Wapichat**: parecidos a YCloud, no aportan nada sobre lo ya conocido.

### LLM — un solo proveedor vs varios

La decisión inicial fue "todo OpenAI" (LLM + embeddings + transcripción) para
simplificar facturación y depuración. **Se revirtió.** Hoy conviven tres
familias detrás de `LLMProvider` y el negocio elige la suya en el panel.

Lo que **sí** sigue atado a OpenAI, y conviene saberlo antes de configurar un
proveedor alternativo:

- **Embeddings** (`providers/embeddings/openai_embeddings.py`) — la base de
  conocimiento está indexada con `text-embedding-3-small`, 1536 dimensiones.
- **Transcripción de audio** (`providers/transcription/whisper_client.py`).

Es decir: se puede poner Anthropic o Gemini como cerebro del agente, pero la
clave de OpenAI sigue haciendo falta para el RAG y para las notas de voz.

## Decisiones por defecto

- Autenticación: JWT + bcrypt → mantenido
- Integración con modelos: se abandonó el "solo OpenAI" → interfaz `LLMProvider`
  con OpenAI, Anthropic y Gemini, elegibles desde el panel
- UI: ~~glassmorphism~~ **descartado.** El sistema visual real es *Coro*
  (papel + tinta + acento pistacho), con modo claro y oscuro. Está en
  `frontend/tailwind.config.js` y `frontend/src/index.css`, resumido en
  [`style_guide.md`](style_guide.md)
- Credenciales: 3 niveles (`.env` → configuración del despliegue → panel, cifradas) → respetado
