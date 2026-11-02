"""Subir el tope de gasto vuelve a encender a Sofía si fue el tope quien la apagó.

El fallo (salió al preparar el video 06): al pasarse del tope, Sofía se pausa y
el aviso rojo de Rendimiento dice «Sube el tope en Asistentes → Tarifas y límites
para reactivar». Subirlo no la reactivaba: el corte solo se revisaba después
de cada turno con IA y, con el bot pausado, no hay turnos. Sofía se quedaba
apagada hasta pulsar «Reactivar todo». Con el mes nuevo pasaba lo mismo.

Ahora el guardado del tope y una tarea de beat cada hora llaman a
`reanudar_si_la_pausa_era_del_tope`. Lo que NO puede hacer: levantar una pausa
que puso una persona, ni encender a ciegas si no se puede medir el gasto.
"""
from __future__ import annotations

import pytest

from app.services import agent_pause, budget


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def entorno(monkeypatch):
    """Redis en memoria y un gasto/tope que cada prueba fija a mano."""
    redis = _FakeRedis()
    estado = {"gasto": 0.0, "tope": 5.0, "gasto_falla": False}

    async def _gasto() -> float:
        if estado["gasto_falla"]:
            raise RuntimeError("BD caída al sumar el gasto")
        return estado["gasto"]

    async def _tope():
        return estado["tope"]

    async def _nada(*a, **k):
        return None

    monkeypatch.setattr(budget, "get_redis", lambda: redis)
    monkeypatch.setattr(agent_pause, "get_redis", lambda: redis)
    monkeypatch.setattr(budget, "current_month_cost_usd", _gasto)
    monkeypatch.setattr(budget, "_get_budget_usd", _tope)
    monkeypatch.setattr(budget, "_remember_last_known", _nada)
    monkeypatch.setattr(budget, "push_runtime_log", _nada)
    monkeypatch.setattr(budget, "notify_security", _nada)
    return estado


async def _pasarse_del_tope(estado) -> None:
    estado["gasto"], estado["tope"] = 10.0, 5.0
    await budget.check_budget_and_maybe_pause()


async def test_subir_el_tope_reactiva_a_sofia(entorno):
    await _pasarse_del_tope(entorno)
    assert await agent_pause.is_agent_paused(), "pasarse del tope tiene que pausar"

    entorno["tope"] = 20.0
    assert await budget.reanudar_si_la_pausa_era_del_tope() is True
    assert not await agent_pause.is_agent_paused(), (
        "subir el tope no reactiva: Sofía sigue callada aunque el aviso diga que basta con subirlo"
    )
    assert not await budget.is_budget_pause_active()


async def test_quitar_el_tope_tambien_reactiva(entorno):
    await _pasarse_del_tope(entorno)
    entorno["tope"] = None
    assert await budget.reanudar_si_la_pausa_era_del_tope() is True
    assert not await agent_pause.is_agent_paused()


async def test_con_el_gasto_aun_por_encima_sigue_pausada(entorno):
    await _pasarse_del_tope(entorno)
    entorno["tope"] = 8.0  # sube, pero no lo bastante
    assert await budget.reanudar_si_la_pausa_era_del_tope() is False
    assert await agent_pause.is_agent_paused()
    assert await budget.is_budget_pause_active(), "la marca de gasto superado se mantiene"


async def test_una_pausa_manual_anterior_no_se_levanta(entorno):
    # La dueña pausó a mano y luego se acabó el presupuesto: el tope no la pausó.
    await agent_pause.set_agent_paused(True)
    await _pasarse_del_tope(entorno)

    entorno["tope"] = 20.0
    assert await budget.reanudar_si_la_pausa_era_del_tope() is False
    assert await agent_pause.is_agent_paused(), "subir el tope pisó una pausa que puso una persona"
    assert not await budget.is_budget_pause_active(), "la marca de gasto superado sí se retira"


async def test_si_una_persona_toco_el_interruptor_la_pausa_es_suya(entorno):
    await _pasarse_del_tope(entorno)
    # «Reactivar todo» y luego «Pausar todo»: el endpoint olvida quién pausó.
    await agent_pause.set_agent_paused(False)
    await budget.olvidar_pausa_del_tope()
    await agent_pause.set_agent_paused(True)
    await budget.olvidar_pausa_del_tope()

    entorno["tope"] = 20.0
    await budget.reanudar_si_la_pausa_era_del_tope()
    assert await agent_pause.is_agent_paused()


async def test_si_no_puede_medir_el_gasto_no_enciende_a_ciegas(entorno):
    await _pasarse_del_tope(entorno)
    entorno["tope"] = 20.0
    entorno["gasto_falla"] = True
    assert await budget.reanudar_si_la_pausa_era_del_tope() is False
    assert await agent_pause.is_agent_paused()


async def test_sin_pausa_del_tope_no_hace_nada(entorno):
    await agent_pause.set_agent_paused(True)
    assert await budget.reanudar_si_la_pausa_era_del_tope() is False
    assert await agent_pause.is_agent_paused()


def test_la_tarea_de_cada_hora_reactiva_con_el_mes_nuevo(entorno):
    """Con el mes nuevo nadie guarda el tope: lo hace la tarea de beat."""
    import asyncio

    from app.tasks import celery_app
    from app.tasks.budget_resume import budget_resume

    asyncio.run(_pasarse_del_tope(entorno))
    entorno["gasto"] = 0.0  # primer día del mes: el gasto vuelve a cero

    assert budget_resume() == {"reanudado": True}
    assert not asyncio.run(agent_pause.is_agent_paused())
    tareas = {e["task"] for e in celery_app.conf.beat_schedule.values()}
    assert "app.tasks.budget_resume.budget_resume" in tareas, "la tarea no está en el beat"
