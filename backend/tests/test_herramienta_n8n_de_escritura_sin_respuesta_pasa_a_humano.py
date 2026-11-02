"""Si una herramienta de escritura no confirma, la conversación pasa a una persona.

Una de escritura hace algo (aparta un pedido, registra un pago). Si n8n no
contesta, nadie sabe si se hizo: Sofía no puede decir «listo», y repetirla
podría hacerlo dos veces. Lo que toca es avisar al paciente y pasar la
conversación a una persona, aunque el agente no tenga `derivar_humano` entre
sus herramientas.
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest

from tests._fichas import telefono_mx
from tests._n8n import db_disponible, herramienta, n8n_falso, url_publica

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


def test_sin_respuesta_avisa_al_paciente_y_pasa_a_humano(monkeypatch):
    import asyncio

    from sqlalchemy import delete, select

    from app.db.session import db_session
    from app.models.contact import Contact, ContactOrigen
    from app.models.conversation import Conversation, ConversationCanal, ConversationStatus
    from app.models.message import Message
    from app.services.n8n_tools import llamar_herramienta

    url_publica(monkeypatch)
    n8n = n8n_falso(monkeypatch, httpx.ReadTimeout("n8n no contestó"))

    async def _run():
        async with db_session() as db:
            ficha = Contact(telefono=telefono_mx(), origen=ContactOrigen("whatsapp"))
            db.add(ficha)
            await db.flush()
            conv = Conversation(
                contact_id=ficha.id,
                canal=ConversationCanal.web,
                session_id=f"web-{uuid.uuid4().hex}",
                status=ConversationStatus.bot,
            )
            db.add(conv)
            await db.commit()
            ficha_id, conv_id = ficha.id, conv.id
        try:
            salida = json.loads(
                await llamar_herramienta(
                    herramienta(nombre="apartar_pedido", accion="escritura"),
                    {"numero": "77"},
                    {"conversation_id": str(conv_id), "canal": "web", "turno_id": "t1"},
                )
            )
            assert len(n8n.peticiones) == 1, "una de escritura se repitió"
            assert salida["confirmado"] is False and salida["derivado"] is True
            assert "NO digas que quedó hecho" in salida["instruccion"]

            async with db_session() as db:
                estado = (
                    await db.execute(select(Conversation.status).where(Conversation.id == conv_id))
                ).scalar_one()
                textos = (
                    await db.execute(select(Message.contenido).where(Message.conversation_id == conv_id))
                ).scalars().all()
            assert estado == ConversationStatus.humano, "la conversación sigue en manos de Sofía"
            assert any("persona del equipo" in (t or "") for t in textos), "el paciente no recibió el aviso"
        finally:
            async with db_session() as db:
                await db.execute(delete(Conversation).where(Conversation.id == conv_id))
                await db.execute(delete(Contact).where(Contact.id == ficha_id))
                await db.commit()

    asyncio.run(_run())
