"""Una sugerencia que escribió n8n no se re-redacta con el modelo del CRM.

«Corregir» y «Re-redactar» llaman a Sofía con el prompt del agente. Con
«Sofía en n8n» ese prompt no se usa: re-redactar pondría a hablar a otro
cerebro y, además, gastaría el modelo del CRM. Se rechaza con 409 sin tocar el
cupo diario de re-redacciones; la sugerencia se edita a mano.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests._motor_n8n import mensajes, whatsapp_entrante
from tests._n8n import admin, cliente, db_disponible, limpiar

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


@pytest.mark.asyncio
async def test_409_sin_llamar_al_modelo_ni_gastar_el_cupo():
    from app.db.session import db_session
    from app.models.message import Message, MessageRole

    cab, admin_id = await admin()
    try:
        conv, _msg, _clave = await whatsapp_entrante()
        async with db_session() as db:
            borrador = Message(
                conversation_id=conv.id, rol=MessageRole.assistant, contenido="Te propongo el martes.",
                extra={"is_draft": True, "draft_sent": False, "training": True, "motor": "n8n"},
            )
            db.add(borrador)
            await db.commit()
            borrador_id = borrador.id

        cupo = AsyncMock(return_value=(True, 1))
        with patch("app.api.conversations.consume_user_daily_quota", cupo), \
                patch("app.agents.orchestrator.run_agent", AsyncMock(side_effect=AssertionError("llamó al modelo"))):
            async with cliente() as c:
                r = await c.post(f"/api/v1/conversations/{conv.id}/drafts/{borrador_id}/refine",
                                 headers=cab, json={"instruction": "más corto"})
        assert r.status_code == 409 and "n8n" in r.text, r.text
        cupo.assert_not_awaited()
        sigue = [m for m in await mensajes(conv.id) if m.id == borrador_id][0]
        assert sigue.contenido == "Te propongo el martes."
    finally:
        await limpiar(admin_id)
