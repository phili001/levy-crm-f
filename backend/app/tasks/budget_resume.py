"""Tarea Celery: vuelve a encender a Sofía cuando el tope de gasto ya no se supera.

El corte por gasto solo se revisa después de cada turno con IA, y con el bot
pausado por el tope no hay turnos: al empezar un mes nuevo nadie lo volvía a
encender. Cada hora mira si la pausa la puso el tope y si el gasto del mes ya
está por debajo; si es así, reanuda (ver `budget.reanudar_si_la_pausa_era_del_tope`).
Una pausa que puso una persona no se toca.
"""
from __future__ import annotations

from app.core.asincrono import ejecutar_async
from app.core.logging import get_logger
from app.tasks import celery_app

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.budget_resume.budget_resume")
def budget_resume() -> dict:
    async def _run() -> dict:
        from app.services.budget import reanudar_si_la_pausa_era_del_tope

        return {"reanudado": await reanudar_si_la_pausa_era_del_tope()}

    try:
        return ejecutar_async(_run())
    except Exception as e:  # noqa: BLE001
        logger.warning("budget_resume.error", error=str(e))
        return {"reanudado": False, "error": str(e)}
