from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.logging import configure_logging

# El worker y el beat no pasan por app.main: sin esto structlog se queda con su
# configuración de fábrica, que imprime los eventos sin `_scrub_pii` (teléfonos
# y correos en claro en los registros) y en otro formato que el de la API.
configure_logging("INFO" if settings.is_production else "DEBUG")

celery_app = Celery(
    "chatbot",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Misma zona que el resto de la app: si el negocio la cambia por variable,
    # las horas del beat (purgas, precios) la siguen.
    timezone=settings.DASHBOARD_TIMEZONE,
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_retry_delay=10,
    task_default_max_retries=3,
    # Celery apunta lo que se imprime como WARNING, y los eventos de structlog se
    # imprimen: cada `info` salía en los registros como un aviso.
    worker_redirect_stdouts_level="INFO",
    # Lo que ya hace hoy (reintentar la conexión con Redis al arrancar), dicho
    # explícitamente: sin esto Celery 5 imprime un aviso de obsolescencia en cada
    # arranque del worker, que en los registros parece un error.
    broker_connection_retry_on_startup=True,
)

celery_app.autodiscover_tasks(["app.tasks"], force=True)


# Importar tareas para registrarlas
from app.tasks import process_message, index_document, transcribe_audio, ping, drain_buffer, poll_gmail, poll_imap, purge_emails, purge_internal_logs, purge_audios, backup_db, outbound_send, web_push_send, notify_pending_check, detect_correction_gaps, detect_faq_gaps, refresh_prices, zernio_backfill, analyze_call, calendar_sync, purge_offered_slots, budget_resume, n8n_motor_check, seguimientos  # noqa: E402,F401


celery_app.conf.beat_schedule = {
    "ping-every-5min": {
        "task": "app.tasks.ping.ping",
        "schedule": crontab(minute="*/5"),
    },
    # Si el tope de gasto apagó a Sofía y el gasto del mes ya está por debajo
    # (mes nuevo), la vuelve a encender. Al subir el tope lo hace el guardado
    # de Tarifas y límites; esto cubre el cambio de mes, en que nadie guarda.
    "budget-resume": {
        "task": "app.tasks.budget_resume.budget_resume",
        "schedule": crontab(minute=5),
    },
    # F5a — sondea Gmail cada 2 minutos en busca de correo entrante nuevo.
    "poll-gmail": {
        "task": "app.tasks.poll_gmail.poll_gmail",
        "schedule": crontab(minute="*/2"),
    },
    # Fase 5 — buzones IMAP (hosting, Zoho, Gmail con contraseña de app).
    "poll-imap": {
        "task": "app.tasks.poll_imap.poll_imap",
        "schedule": crontab(minute="*/2"),
    },
    # Retención del canal Email: una vez al día (03:30) purga el contenido de los
    # correos más viejos que EMAIL_RETENTION_MONTHS (minimización; Gmail es el
    # archivo). Idempotente.
    "purge-emails": {
        "task": "app.tasks.purge_emails.purge_emails",
        "schedule": crontab(hour=3, minute=30),
    },
    # I8 — purga diaria de logs internos (disco): agent_trace_event y
    # llm_usage_log > 90 días, audit_log > 365 días. Por lotes e idempotente.
    "purge-internal-logs": {
        "task": "app.tasks.purge_internal_logs.purge_internal_logs",
        "schedule": crontab(hour=4, minute=10),
    },
    # Retención de notas de voz en disco (RGPD): borra los ficheros de audio
    # > AUDIO_RETENTION_DAYS conservando la transcripción. Diario, 04:25.
    "purge-audios": {
        "task": "app.tasks.purge_audios.purge_audios",
        "schedule": crontab(hour=4, minute=25),
    },
    # Copias de seguridad de Postgres con frecuencia CONFIGURABLE desde el
    # panel (Copias de seguridad): el tick corre cada 10 min, lee la config de
    # la BD (cada hora / 6h / 12h / diaria + hora de la diaria, defecto 04:00
    # la zona del negocio) y encola backup_db cuando toca. backup_db hace pg_dump a
    # BACKUP_DIR (retención local) y, si hay bucket configurado, sube el dump
    # CIFRADO a chatbot/<fecha>.dump.enc con retención remota. Restauración:
    # botón "Restaurar…" del panel o docs/backups.md.
    "backup-tick": {
        "task": "app.tasks.backup_db.backup_tick",
        "schedule": crontab(minute="*/10"),
    },
    # Autoaprendizaje · Fase 2 — disparador #3: cada hora agrupa las
    # correcciones repetidas de la operadora y propone reglas de estilo / Q&A en
    # "Aprendizajes" (con aprobación humana). Barata e idempotente: solo analiza
    # las correcciones nuevas (processed_at IS NULL).
    "detect-correction-gaps": {
        "task": "app.tasks.detect_correction_gaps.detect_correction_gaps",
        "schedule": crontab(minute=15),
    },
    # Autoaprendizaje · Fase 3 — FAQ automática: una vez al día (06:20, tras los
    # backups) agrupa las preguntas de cliente más frecuentes de TODOS los
    # canales (ventana móvil) y propone las top-N como Q&A en "Aprendizajes" (con
    # aprobación humana). La frecuencia no necesita ser frecuente → cadencia
    # diaria. Barata e idempotente: ventana móvil + dedupe estricto (vs KB y vs
    # huecos pendientes), con tope de mensajes y de propuestas por corrida.
    "detect-faq-gaps": {
        "task": "app.tasks.detect_faq_gaps.detect_faq_gaps",
        "schedule": crontab(hour=6, minute=20),
    },
    # Envíos masivos: barrido de campañas colgadas. Si el trabajo en segundo
    # plano deja de ejecutarse (reintentos agotados con la BD caída, mensaje de
    # re-encolado perdido en un reinicio de Redis), el job se quedaba "en curso"
    # para siempre. Cada 15 min, los `running` sin actividad desde hace mucho
    # pasan a `failed` con su motivo (ver outbound_send.stale_after_seconds).
    "outbound-reap-stale-jobs": {
        "task": "app.tasks.outbound_send.reap_stale_jobs",
        "schedule": crontab(minute="*/15"),
    },
    # Precios de modelos LLM: una vez al día (06:40) refresca llm_model_price
    # desde OpenRouter para que la estimación de coste del dashboard sea real.
    "refresh-prices": {
        "task": "app.tasks.refresh_prices.refresh_prices",
        "schedule": crontab(hour=6, minute=40),
    },
    # Agenda (Fase 9): lo ofrecido a cada conversación solo vale 30 minutos; lo
    # de más de un día se borra a las 04:40.
    "agenda-purga-ofertas": {
        "task": "app.tasks.purge_offered_slots.purge_offered_slots",
        "schedule": crontab(hour=4, minute=40),
    },
    # Copia en Google Calendar: lo que quedó pendiente (Google caído, permiso
    # revocado) se pone al día solo. Sin cuenta activa no hace nada.
    "agenda-google-pendientes": {
        "task": "app.tasks.calendar_sync.sync_pending",
        "schedule": crontab(minute="*/10"),
    },
    # Recordatorios de cita y «no llegó» (SPEC-18). Apagados de fábrica: sin
    # nada encendido en Asistente → Seguimientos, solo lee la configuración.
    "seguimientos": {
        "task": "app.tasks.seguimientos.tick",
        "schedule": crontab(minute="*/10"),
    },
}
