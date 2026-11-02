"""Tarea Celery que trae el historial de una cuenta recién conectada a Zernio.

Se lanza una vez al conectar el canal (no es periódica): el historial previo
no llega por webhook. La lógica vive en `services/zernio.importar_historial`;
esto es el molde Celery, igual que `poll_imap.py`.
"""
import uuid

from celery import shared_task

from app.core.asincrono import ejecutar_async
from app.core.logging import get_logger

logger = get_logger(__name__)


@shared_task(name="app.tasks.zernio_backfill.zernio_backfill", bind=True, max_retries=2)
def zernio_backfill(self, channel_id: str) -> dict:
    from app.services.zernio import importar_historial

    try:
        return ejecutar_async(importar_historial(uuid.UUID(channel_id)))
    except Exception as exc:
        logger.error("zernio_backfill.error", error=str(exc)[:200])
        # Es idempotente: reintentar no duplica nada.
        raise self.retry(exc=exc, countdown=60)
