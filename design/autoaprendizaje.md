# Autoaprendizaje — cómo funciona

> El agente aprende de conversaciones reales, pero **quien atiende aprueba** lo
> que aprende. **Nunca se cambia solo** (descartado por seguridad: manipulación
> y deriva del prompt).

**Está construido y en producción.** Este documento era un plan con las fases 2
y 3 marcadas como pendientes; ya no lo son. Aquí va lo que hay, con los nombres
reales. La fuente de verdad es el código citado.

## Dónde vive

| Pieza | Fichero |
|---|---|
| API | `backend/app/api/learning.py` (prefijo `/admin/learning`, montado en `main.py`) |
| Pantalla | `frontend/src/pages/admin/LearningPage.tsx` → ruta `/admin/agent/learning`, menú "Aprendizajes" |
| Tablas | `knowledge_gaps` y `learned_rules` (modelos `models/knowledge_gap.py` y `models/learned_rule.py`) |
| Inyección de reglas en el prompt | `backend/app/services/learned_rules.py` |

## Las tres fases

### Fase 1 — "Corrige y re-redacta"

Botón **Corregir** en las sugerencias de Entrenamiento y en los borradores de
correo: quien atiende da una instrucción en lenguaje natural ("sé más cálida",
"no prometas plazos de entrega") y el agente re-redacta. Sigue sin enviarse
nada.

Endpoint: `POST /conversations/{id}/drafts/{message_id}/refine`
(`api/conversations.py`). Cada corrección se guarda cifrada en
`agent_corrections`.

### Fase 2 — De correcciones y huecos a conocimiento permanente

De las correcciones repetidas y de los huecos (cuando el agente no supo
responder) salen **propuestas** que quien atiende aprueba con un clic.

### Fase 3 — FAQ automática

Una vez al día se agrupan por similitud las preguntas de cliente más
frecuentes de todos los canales y se proponen las mejores como Q&A. Reutiliza
la misma pantalla y el mismo camino de aprobación.

## Cómo se crea un hueco

Una fila de `knowledge_gaps` nace de cuatro sitios. Los dos primeros son **en
caliente**, dentro del propio turno; los otros dos, en tareas periódicas de
Celery:

| `trigger` | Se crea en | Cuándo |
|---|---|---|
| `handoff` | `agents/tools/human_handoff.py` | el agente derivó a una persona (no supo resolver). La mejor respuesta es la que dio la persona después: cero invención |
| `kb_miss` | `agents/tools/kb_search.py` | `consultar_kb` buscó y devolvió cero resultados |
| `correction` | `tasks/detect_correction_gaps.py` | cada hora (minuto 15). Agrupa por similitud las correcciones nuevas de `agent_corrections` |
| `faq` | `tasks/detect_faq_gaps.py` | a diario a las 06:20. Agrupa las preguntas más repetidas |

El `beat_schedule` de las dos tareas está en `backend/app/tasks/__init__.py`.

## La tabla `knowledge_gaps`

En plural. Columnas reales (`backend/app/models/knowledge_gap.py`):

| Campo | Notas |
|---|---|
| `id` | uuid |
| `conversation_id` | FK con `ON DELETE SET NULL`, no CASCADE: si se borra la conversación, la traza del aprendizaje se conserva |
| `trigger` | `handoff` \| `kb_miss` \| `correction` \| `faq` |
| `proposal_kind` | `content` (crear Q&A e indexarla) o `style` (guardar una regla). Nulo = se trata como `content` |
| `question` | **cifrada.** La pregunta del cliente o el resumen de lo que se corrige |
| `suggested_answer` | **cifrada**, editable. Nula al crearse en los huecos de handoff/kb_miss |
| `status` | **`pendiente` \| `aprobado` \| `descartado`.** Son estos tres, no los cinco del plan viejo |
| `created_document_id` | FK a `documents`, el documento creado al aprobar |
| `approved_by` | FK a `users` |
| `created_at`, `resolved_at` | |

## La tabla `learned_rules`

Las reglas de estilo aprobadas. `text` (cifrado), `source_gap_id` (de qué hueco
salió), `active` (se desactiva sin borrar), `created_by`, fechas.

Se inyectan en el prompt de sistema de **todos** los agentes conversacionales
—negocio único, reglas globales— con un bloque delimitado "Reglas aprendidas
(estilo)". Hay topes para que el prompt no se infle: 15 reglas y 1500
caracteres (`services/learned_rules.py`).

## Endpoints

Todos bajo `/api/v1/admin/learning`, todos con `require_admin`. Verificados en
`api/learning.py`:

- `GET /gaps` — lista para revisar.
- `POST /gaps/{id}/approve` — según `proposal_kind`, crea el `Document` y lo
  indexa, o persiste la `LearnedRule`.
- `POST /gaps/{id}/discard`.
- `GET /corrections` — las correcciones de la Fase 1.
- `POST /corrections/{id}/promote` — subir una corrección a propuesta.
- `GET /rules` · `POST /rules/preview` · `PUT /rules/{id}` ·
  `POST /rules/{id}/deactivate` — gestión de las reglas de estilo activas.

Al aprobar un hueco de contenido se reutiliza el mismo almacenamiento que la
subida manual de documentos (`KB_STORAGE_ROOT` de `api/knowledge_base.py`), no
una ruta inventada.

## Notas de voz entrantes

Van por el mismo sitio que cualquier mensaje: se transcriben con Whisper
(`providers/transcription/whisper_client.py`, tarea `transcribe_audio`), quedan
en `messages.audio_transcript` cifrado y el agente las lee como texto. El
inbox muestra la transcripción bajo el reproductor.
