"""Un turno de «Sofía en n8n» deja su línea en «Actividad del agente».

Con el turno en n8n no hay llamada al modelo del CRM que enseñar. Sin esta
línea, quien abre la actividad de la conversación ve el mensaje del paciente y
la respuesta, pero no sabe que pasó por n8n, cuánto tardó ni si n8n falló.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from tests._motor_n8n import agente_en_n8n, n8n_que_contesta, turno, whatsapp_entrante
from tests._n8n import db_disponible

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


async def _actividad(conv_id) -> list:
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.agent_trace import AgentTraceEvent

    async with db_session() as db:
        return list(
            (
                await db.execute(
                    select(AgentTraceEvent)
                    .where(AgentTraceEvent.conversation_id == conv_id)
                    .order_by(AgentTraceEvent.created_at)
                )
            ).scalars().all()
        )


@pytest.mark.asyncio
async def test_respuesta_directa_y_n8n_caido_se_ven_en_la_actividad(monkeypatch):
    n8n_que_contesta(monkeypatch, (200, {"respuesta": "Sí, hay hueco."}))
    conv, _msg, clave = await whatsapp_entrante()
    await turno(clave, agente_en_n8n(), AsyncMock(return_value="wamid.OK"))

    lineas = [e for e in await _actividad(conv.id) if (e.summary or "").startswith("Turno enviado a n8n")]
    assert len(lineas) == 1, "el turno en n8n no dejó su línea en «Actividad del agente»"
    assert lineas[0].summary.endswith("· respuesta directa")
    assert lineas[0].latency_ms is not None and lineas[0].level.value == "info"

    n8n_que_contesta(monkeypatch, httpx.ConnectError("n8n caído"))
    conv2, _m, clave2 = await whatsapp_entrante()
    await turno(clave2, agente_en_n8n(), AsyncMock(return_value="wamid.PUENTE"))

    lineas = [e for e in await _actividad(conv2.id) if (e.summary or "").startswith("Turno enviado a n8n")]
    assert lineas and lineas[0].level.value == "error" and lineas[0].summary.endswith("· sin respuesta")
