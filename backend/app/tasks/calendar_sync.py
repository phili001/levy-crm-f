"""Tareas de la copia en Google Calendar (Fase 9, T-9.1.5).

- `sync_appointment`: copia una cita en cuanto se crea, se mueve o se cancela.
  Reintenta con espera creciente (30 s, 1, 2, 4 y 8 min); si Google sigue sin
  contestar, la cita se queda pendiente y la recoge la barrida.
- `sync_pending` (beat, cada 10 min): la barrida. Es lo que pone la agenda al
  día sola al reconectar Google o si el broker perdió un encargo.
- `borrar_eventos`: borra en Google los eventos de un contacto recién borrado
  (SPEC-21). Sin reintentos propios: lo que no entre lo repite la barrida.

La lógica está en `services/calendar_sync.py`; aquí solo el molde de Celery.
"""
import uuid

import httpx
from celery import shared_task

from app.core.asincrono import ejecutar_async
from app.core.logging import get_logger
from app.services.calendar_sync import (
    GoogleNoDisponible,
    barrer_pendientes,
    borrar_eventos_pendientes,
    sincronizar,
)

logger = get_logger(__name__)


@shared_task(name="app.tasks.calendar_sync.sync_appointment", bind=True, max_retries=5)
def sync_appointment(self, appointment_id: str) -> str:
    try:
        return ejecutar_async(sincronizar(uuid.UUID(appointment_id)))
    except (GoogleNoDisponible, httpx.HTTPError) as exc:
        if self.request.retries >= self.max_retries:
            logger.warning("calendar_sync.se_queda_pendiente", cita_id=appointment_id, error=str(exc))
            return "pendiente"
        raise self.retry(exc=exc, countdown=30 * 2**self.request.retries) from exc


@shared_task(name="app.tasks.calendar_sync.sync_pending")
def sync_pending() -> int:
    return ejecutar_async(barrer_pendientes())


@shared_task(name="app.tasks.calendar_sync.borrar_eventos")
def borrar_eventos() -> int:
    return ejecutar_async(borrar_eventos_pendientes())
