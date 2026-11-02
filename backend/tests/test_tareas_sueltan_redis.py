"""Las tareas sueltan sus clientes Redis antes de cerrar su event loop.

Cada tarea de Celery (y el seed) corre su trabajo con `asyncio.run`, y los
clientes Redis se cachean por loop (`core/redis.py` y el bus de eventos). Si
nadie los cierra, el recolector los destruye con el loop ya cerrado y
redis-async imprime `RuntimeError: Event loop is closed` en los registros del
worker y del primer arranque: salió en el ensayo de la instalación en un clic
(crm2, 0.12.0) y quien mira los registros lo lee como un error.
"""
from __future__ import annotations

import asyncio
import gc
import sys

import pytest


def _redis_disponible() -> bool:
    import redis

    from app.core.config import settings

    try:
        cliente = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        cliente.ping()
        cliente.close()
        return True
    except Exception:
        return False


necesita_redis = pytest.mark.skipif(
    not _redis_disponible(), reason="Redis no disponible en este entorno (en la CI sí)"
)


@necesita_redis
def test_una_tarea_tras_otra_no_imprime_event_loop_is_closed():
    from app.core import events
    from app.core import redis as redis_mod
    from app.core.asincrono import ejecutar_async

    async def tarea_que_usa_redis() -> int:
        await redis_mod.get_redis().ping()
        await events.event_bus.publish("prueba:tareas", "ping", {})
        return id(asyncio.get_running_loop())

    async def tarea_siguiente() -> None:
        # Como en el worker: la siguiente tarea poda los clientes de loops
        # cerrados. Si el de la anterior siguiera abierto, al recolectarlo
        # saldría el error.
        redis_mod.get_redis()
        await events.event_bus._get_publisher()

    # Lo que otras pruebas dejaron en loops ya cerrados (las que no pasan por
    # `ejecutar_async`) se suelta ANTES de escuchar: si no, la poda de la tarea
    # siguiente lo recolectaría aquí dentro y se contaría como de estas dos.
    for clave, (loop, _cliente) in list(redis_mod._clients.items()):
        if loop.is_closed():
            redis_mod._clients.pop(clave, None)
    events._prune_dead_loops()
    events._prune_dead_broker_loops()
    gc.collect()

    errores = []
    previo = sys.unraisablehook
    sys.unraisablehook = errores.append
    try:
        loop_id = ejecutar_async(tarea_que_usa_redis())
        ejecutar_async(tarea_siguiente())
        gc.collect()
    finally:
        sys.unraisablehook = previo

    assert loop_id not in redis_mod._clients
    assert not [clave for clave in events._clients if clave[0] == loop_id]
    assert not errores, [repr(e.exc_value) for e in errores]


def test_devuelve_lo_de_la_tarea_y_deja_pasar_sus_errores():
    from app.core.asincrono import ejecutar_async

    async def vale() -> str:
        return "hecho"

    async def falla() -> None:
        raise ValueError("de la tarea")

    assert ejecutar_async(vale()) == "hecho"
    with pytest.raises(ValueError, match="de la tarea"):
        ejecutar_async(falla())
