"""Correr código async desde código síncrono: las tareas de Celery y el seed.

Cada tarea corre su trabajo con `asyncio.run`, que crea un event loop y lo
cierra al terminar. Los clientes Redis se cachean por loop (`core/redis.py` y
el bus de eventos de `core/events.py`); si nadie los cierra, el recolector los
destruye más tarde, con el loop ya cerrado, y redis-async imprime
`RuntimeError: Event loop is closed` en los registros. No rompe nada, pero
quien mira los registros del worker o del primer arranque lo lee como un error.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def ejecutar_async(coro: Coroutine[Any, Any, T]) -> T:
    """`asyncio.run(coro)` que suelta los clientes Redis del loop antes de cerrarlo."""

    async def _y_soltar_redis() -> T:
        try:
            return await coro
        finally:
            # Importes aquí: los dos módulos importan la config y el logging, y
            # este helper lo usan módulos que se importan antes que ellos.
            from app.core.events import close_event_bus
            from app.core.redis import close_redis

            with contextlib.suppress(Exception):
                await close_event_bus()
            with contextlib.suppress(Exception):
                await close_redis()

    return asyncio.run(_y_soltar_redis())
