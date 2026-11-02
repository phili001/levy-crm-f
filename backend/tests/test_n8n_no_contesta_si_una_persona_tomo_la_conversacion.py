"""Si una persona tomó la conversación, n8n ya no contesta en ella.

El inbox es de quien atiende. Si la operadora entró a contestar (la conversación
pasó a `humano`), un mensaje que n8n mande después por la API se rechaza con
409 en vez de pisarla delante del paciente. Lo mismo con la ventana de 24 h de
WhatsApp cerrada: ahí solo vale una plantilla.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests._motor_n8n import borrar_token, token, whatsapp_entrante
from tests._n8n import cliente, db_disponible

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


@pytest.mark.asyncio
async def test_409_con_una_persona_y_con_la_ventana_cerrada():
    from app.db.session import db_session
    from app.models.conversation import Conversation, ConversationStatus
    from app.services.channel_sender import MessagingWindowClosed

    cab, token_id = await token("conversations:reply")
    enviar = AsyncMock(return_value="wamid.X")
    try:
        conv, _msg, _clave = await whatsapp_entrante()
        async with db_session() as db:
            fila = await db.get(Conversation, conv.id)
            fila.status = ConversationStatus.humano
            await db.commit()
        async with cliente() as c:
            with patch("app.services.channel_sender.send_text_to_conversation", enviar):
                r = await c.post(f"/api/v1/agent-api/conversations/{conv.id}/messages", headers=cab,
                                 json={"texto": "Hola, soy n8n"})
        assert r.status_code == 409 and "persona" in r.text
        enviar.assert_not_awaited()

        conv2, _m, _c = await whatsapp_entrante()
        cerrada = AsyncMock(side_effect=MessagingWindowClosed("24 h"))
        async with cliente() as c:
            with patch("app.services.channel_sender.send_text_to_conversation", cerrada):
                r = await c.post(f"/api/v1/agent-api/conversations/{conv2.id}/messages", headers=cab,
                                 json={"texto": "Hola, soy n8n"})
        assert r.status_code == 409 and "plantilla" in r.text
    finally:
        await borrar_token(token_id)
