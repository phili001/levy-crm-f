# Non-Functional Requirements — Chatbot

## Performance

- **Latencia respuesta del agente:** <15s end-to-end (desde llegada del webhook hasta envío de respuesta a WhatsApp) en p95 con carga normal.
- **Latencia transcripción audio:** <10s para audios <60s de duración.
- **Latencia búsqueda RAG:** <500ms por query.
- **Concurrencia objetivo:** 30 conversaciones simultáneas sin degradación significativa.
- **Latencia WebSocket:** mensajes nuevos visibles en el inbox <1s desde envío.
- **Throughput webhook YCloud:** capaz de absorber ráfagas de 100 mensajes/min sin pérdida (vía cola Celery).

## Security

- **Autenticación:** JWT HS256 con `JWT_SECRET` mínimo 256 bits. Expiración 24h. Passwords bcrypt cost ≥12.
- **Autorización:** RBAC con tres roles (`admin`, `cliente`, `revisor`). Cada endpoint declara su requirement. Al `revisor` lo acota una lista blanca de rutas en `api/deps.py`, no una dependencia por endpoint. Tests unitarios sobre autorización.
- **Cifrado credenciales:** Fernet con `ENCRYPTION_KEY` en `.env`. Nunca expuestas en logs ni respuestas API.
- **Validación firma webhook YCloud:** HMAC SHA256 con `YCLOUD_WEBHOOK_SECRET`. Webhook sin firma válida → 401.
- **Rate limiting:**
  - `/auth/login`: 5 req/min por IP
  - Webhooks (`/api/v1/webhooks/*`): 120 req/min por IP
  - Chat web: 10 sesiones/min, 30 mensajes/min y 60 consultas de historial/min por IP, más un tope de 2000 mensajes al día por instalación
  - Tokens de agente (agent-api / MCP): 120 req/min por token
- **CORS:** restringido a dominio del frontend.
- **HTTPS obligatorio:** EasyPanel + Let's Encrypt.
- **Protección prompt injection:** sección dedicada en system prompt + validación de salida del agente. Heredada de la primera versión del sistema.
- **PII fuera de logs:** filtro centralizado en `core/logging.py` que enmascara teléfonos, emails y mensajes completos.
- **Niveles de credenciales:** Level 1 (.env), Level 2 (deployment), Level 3 (admin panel cifradas en DB).
- **Backups:** dump de Postgres con frecuencia configurable desde el panel (por defecto diario a las 04:00). Retención local por edad (`BACKUP_RETENTION_DAYS`, 7 días) **y** por número (`BACKUP_RETENTION_MAX_FILES`, 14 ficheros). Con bucket, retención propia de 7 diarias + 4 semanales. Detalle en `docs/backups.md`.

## Accessibility

- **WCAG 2.1 AA** en todo el panel.
- **Navegación teclado:** todos los interactivos accesibles con `Tab`. Foco visible.
- **Lectores de pantalla:** live regions para mensajes nuevos. Etiquetas ARIA en componentes custom.
- **Contraste:** mínimo 4.5:1 en texto normal, 3:1 en texto grande.
- **Atajos:** `/` enfoca buscador, `↑↓` navega lista, `Esc` cierra modals.

## Observability

- **Logging:** structlog con formato JSON. Niveles: DEBUG, INFO, WARNING, ERROR.
- **Correlation ID:** cada request HTTP / tarea Celery / mensaje WhatsApp lleva un `request_id` o `trace_id` para trazar el flujo completo.
- **Errores:** capturados centralizadamente. Últimos 100 visibles en `/admin/health`.
- **Métricas:** conteo de mensajes procesados, tareas Celery (en cola / completadas / fallidas), tiempo medio de respuesta, llamadas a OpenAI, tokens consumidos. Visibles en dashboard cliente y en `/admin/health`.
- **Alertas:** se publican **dentro de la aplicación**, en Sistema → Logs en vivo (`services/security_alerts.notify_security`), con throttling de 10 min por tipo de evento. Las que afectan al operador (derivación a humano, incidencias de copia de seguridad) van además como notificación push a la PWA. **No hay integración con Telegram ni con Slack: se retiraron del producto** para no exfiltrar datos de clientes a terceros (RGPD). Contrapartida asumida: nadie avisa fuera de la app, hay que entrar a mirar.

## Scalability

- **Crecimiento esperado:** ~5-10 mensajes/min en pico para un cliente típico (un comercio pequeño). Para clientes grandes, hasta 50/min.
- **Estrategia escalado:** vertical (más vCPU al VPS) en primera instancia; añadir más workers Celery si la cola se acumula. Postgres puede subir a réplica de lectura si hace falta (Fase 3+).
- **Volumen DB:** ~10MB/mes de mensajes para cliente típico. Para 1000 clientes activos: ~10GB/mes, manejable con Postgres estándar.
- **Storage audios:** estimado ~50KB por audio, ~100 audios/mes para cliente típico = 5MB/mes. Negligible.

## Reliability

- **Uptime objetivo:** 99% (Fase 1 — VPS único, sin redundancia activa).
- **Recuperación de fallos:** Celery con retry exponential backoff (3 intentos) y dead-letter queue. Tareas fallidas reintentables manualmente desde panel admin.
- **Idempotencia:** webhook YCloud puede recibir duplicados — procesamiento idempotente por `message_id` de YCloud.
- **Graceful shutdown:** workers Celery esperan a terminar tareas en curso antes de cerrarse (max 30s).

## Maintainability

- **Tests:**
  - Backend: pytest, unas 1.100 pruebas en `backend/tests/`, y la CI las corre contra una base de datos real en las dos versiones de Postgres vivas (16 y 17). `pytest-cov` está instalado, pero **no hay umbral de cobertura que bloquee**.
  - Frontend: **no hay tests**. Lo que comprueba la CI es que el proyecto tipa (`tsc --noEmit`), que compila (`npm run build`) y que el HTML no lleva scripts en línea (los bloquearía la CSP).
- **Linting:** `ruff check` en el backend (configurado en `backend/pyproject.toml`). En la CI corre en modo **informativo**: no bloquea, porque el repo arrastra más de 600 avisos. En el frontend **no hay linter**: ni eslint ni prettier ni script `lint`.
- **Type checking:** en el frontend, `npx tsc --noEmit` — y ese sí **bloquea** la CI. En el backend hay mypy instalado y configurado con `strict = false`, pero **no lo ejecuta la CI**: se lanza a mano.
- **Documentación:** OpenAPI auto-generada por FastAPI. Storybook opcional para componentes UI (Fase 2).

## Compliance

- **RGPD:** todos los datos personales (mensajes, contactos, audios) se manejan según el Reglamento. El cliente final firma DPA con sus usuarios. La app facilita:
  - Exportación de todos los datos de un contacto
  - Borrado completo de un contacto y todos sus mensajes/audios, los adjuntos que mandó
    (también los de correo IMAP, que se guardan en el servidor) y la copia de sus citas en
    Google Calendar (desde la 1.3.3; si Google no contesta, se reintenta cada 10 minutos)
  - Aviso visible al subir documentos sobre datos personales
- **Cookies / Analytics:** el panel no usa analytics de terceros. Solo cookie de sesión necesaria.
- **Retención:** hay caducidad automática, y está encendida por defecto. Tres tareas diarias (`purge_audios`, `purge_emails`, `purge_internal_logs`) borran lo que ya no aporta. Los valores por defecto están en `backend/app/core/config.py` y se cambian por variable de entorno:

  | Qué se purga | Ajuste | Por defecto | Qué queda después |
  |---|---|---|---|
  | Ficheros de notas de voz | `AUDIO_RETENTION_DAYS` | 270 días | La transcripción (cifrada); desaparece el audio |
  | Adjuntos en disco (imágenes, vídeos, documentos; también los del correo IMAP) | `MEDIA_RETENTION_DAYS` | 180 días | La fila del mensaje: qué llegó y cuándo |
  | Contenido de los correos en BD | `EMAIL_RETENTION_MONTHS` | 6 meses | Entrada ligera (id de Gmail, asunto, cabeceras); el cuerpo sigue en Gmail |
  | Trazas del agente (`agent_trace_event`) | `TRACE_RETENTION_DAYS` | 30 días | Nada |
  | Uso de LLM (`llm_usage_log`) | `LLM_USAGE_RETENTION_DAYS` | 90 días | Nada |
  | Auditoría (`audit_log`) | `AUDIT_RETENTION_DAYS` | 365 días | Nada |
  | Envíos masivos terminados | `OUTBOUND_RETENTION_DAYS` | 180 días | Nada |
  | Huecos de conocimiento ya resueltos | `RESOLVED_GAP_RETENTION_DAYS` | 180 días | Nada; los pendientes no caducan |

  `0` desactiva la purga en **todas** las de la tabla, también en las cinco internas (`TRACE_`, `LLM_USAGE_`, `AUDIT_`, `OUTBOUND_` y `RESOLVED_GAP_`): con 0 no se calcula fecha de corte y no se borra nada (`_cutoff_o_desactivada`, `backend/app/tasks/purge_internal_logs.py`). Lo que **no** caduca solo: conversaciones, mensajes de los canales que no son email, contactos y base de conocimiento. Para esos, el borrado va por contacto (exportación y borrado desde la ficha, ver arriba). Si necesitas conservar la auditoría más de un año, súbelo antes: no hay archivado ni botón de exportar (`SECURITY.md` § 5).
