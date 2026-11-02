"""Purga diaria de los horarios ofrecidos (Fase 9, T-9.1.2b).

`offered_slots` guarda lo que `consultar_disponibilidad` le enseñó al modelo en
cada conversación. Solo vale 30 minutos para agendar, así que pasado un día ya
no sirve para nada y se borra (beat, 04:40). Idempotente: si no hay nada que
borrar, no hace nada.
"""

from celery import shared_task

from app.core.asincrono import ejecutar_async
from app.core.logging import get_logger
from app.services.agenda import purgar_ofertas

logger = get_logger(__name__)


@shared_task(name="app.tasks.purge_offered_slots.purge_offered_slots")
def purge_offered_slots() -> int:
    borradas = ejecutar_async(purgar_ofertas())
    logger.info("purge_offered_slots.done", borradas=borradas)
    return borradas
