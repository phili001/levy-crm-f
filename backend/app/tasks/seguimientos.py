"""Recordatorios y «no llegó» (SPEC-18): la vuelta del beat cada 10 minutos.

La lógica está en `services/seguimientos.py`; aquí solo el molde de Celery. Con
todo apagado (lo de fábrica) no llama a nadie: lee la configuración y sale.
"""
from celery import shared_task

from app.core.asincrono import ejecutar_async
from app.core.logging import get_logger

logger = get_logger(__name__)


@shared_task(name="app.tasks.seguimientos.tick")
def tick() -> dict:
    from app.services.seguimientos import barrer

    resultados = ejecutar_async(barrer())
    salida = {tipo: vars(r) for tipo, r in resultados.items()}
    if any(r["enviados"] or r["omitidos"] for r in salida.values()):
        logger.info("seguimientos.vuelta", **salida)
    return salida
