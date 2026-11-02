"""Tarea Celery periódica que trae el correo de los buzones IMAP.

Corre cada 2 minutos (ver `beat_schedule` en app/tasks/__init__.py). Toda la
lógica vive en `services/imap_ingest.sincronizar_buzones`; esto es el molde
Celery, igual que `poll_gmail.py`.
"""

from celery import shared_task

from app.core.asincrono import ejecutar_async
from app.core.logging import get_logger

logger = get_logger(__name__)


@shared_task(name="app.tasks.poll_imap.poll_imap", bind=True, max_retries=2)
def poll_imap(self) -> None:
    from app.services.imap_ingest import sincronizar_buzones

    try:
        result = ejecutar_async(sincronizar_buzones())
        logger.info("poll_imap.done", **(result or {}))
    except Exception as exc:
        logger.error("poll_imap.error", error=str(exc))
        # El siguiente beat (2 min) reintenta igualmente: no hace falta más.
        raise self.retry(exc=exc, countdown=30)
