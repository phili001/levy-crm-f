"""Tarea Celery: ¿contestó n8n? (Fase 11, SPEC-07, «Sofía en n8n»).

Cuando la respuesta directa de n8n no llega a tiempo, o n8n dice que contesta
después por la API, se programa esta comprobación. Si para entonces no hay
ningún mensaje nuevo de Sofía ni de una persona y la conversación sigue en
manos del bot, pasa a una persona con el mensaje puente: el paciente no puede
quedarse esperando a un flujo que se cayó.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from app.core.asincrono import ejecutar_async
from app.core.logging import get_logger
from app.tasks import celery_app

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.n8n_motor_check.comprobar_respuesta_n8n")
def comprobar_respuesta_n8n(conversation_id: str, desde_iso: str) -> dict:
    async def _run() -> dict:
        from app.services.n8n_motor import comprobar_respuesta

        derivada = await comprobar_respuesta(uuid.UUID(conversation_id), datetime.fromisoformat(desde_iso))
        return {"derivada": derivada}

    try:
        return ejecutar_async(_run())
    except Exception as e:  # noqa: BLE001
        logger.error("n8n_motor_check.error", error=str(e)[:200])
        return {"derivada": False, "error": str(e)[:200]}
