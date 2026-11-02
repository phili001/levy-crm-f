"""Tarea Celery: refresca los precios de modelos desde OpenRouter.

Corre una vez al día (ver beat_schedule en app/tasks/__init__.py, 06:40). Trae
el listado público de OpenRouter y actualiza llm_model_price para los modelos
que la app usa. Best-effort: si OpenRouter no responde, se loguea y no pasa nada
(la estimación sigue con los precios que ya había).
"""
from __future__ import annotations

from app.core.asincrono import ejecutar_async
from app.core.logging import get_logger
from app.tasks import celery_app

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.refresh_prices.refresh_prices")
def refresh_prices() -> dict:
    async def _run() -> dict:
        from app.db.session import db_session
        from app.models.feature_flag import FLAG_TARIFAS_OPENROUTER
        from app.services.feature_flags import is_enabled
        from app.services.llm_pricing import refresh_price_cache
        from app.services.openrouter_pricing import refresh_model_prices

        # El módulo de tarifas de OpenRouter viene apagado en el MVP del curso:
        # la instalación arranca con una sola key de OpenAI y no tiene por qué
        # llamar a OpenRouter cada mañana. Se enciende desde Ajustes → Módulos.
        if not await is_enabled(FLAG_TARIFAS_OPENROUTER):
            logger.info("refresh_prices.skipped", reason="modulo_apagado")
            return {"skipped": True, "reason": "modulo_apagado"}

        async with db_session() as db:
            summary = await refresh_model_prices(db)
            await db.commit()
        # Recarga la caché del proceso worker (informativo; el proceso API se
        # recarga en su propio arranque + cuando se pulsa "Actualizar" en el panel).
        await refresh_price_cache()
        return summary

    try:
        return ejecutar_async(_run())
    except Exception as e:
        logger.warning("refresh_prices.error", error=str(e))
        return {"updated": [], "unmatched": [], "error": str(e)}
