"""Coexistencia: la clínica usa su celular Y el CRM a la vez.

Sin esto pasaban dos cosas feas: el panel enseñaba la pregunta del paciente sin
ninguna respuesta (parecía que nadie contestó) y Sofía respondía encima de lo
que la persona ya había dicho, a veces contradiciéndola.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.providers.whatsapp.ycloud import YCloudProvider


def _db() -> bool:
    try:
        from sqlalchemy import text

        from app.db.session import db_session

        async def _ping():
            async with db_session() as db:
                await db.execute(text("SELECT 1"))

        asyncio.run(_ping())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db(), reason="sin Postgres")


def test_el_eco_sabe_que_el_cliente_esta_en_to_no_en_from():
    """En un eco, `from` es el NEGOCIO. Confundirlos crea la ficha del negocio."""
    ecos = YCloudProvider().parse_echoes({
        "type": "whatsapp.smb.message.echoes",
        "whatsappMessage": {
            "wamid": "wamid.ECO1",
            "from": "+529990000000",     # la clínica
            "to": "+529981234567",       # el paciente
            "type": "text",
            "text": {"body": "Te espero a las 4"},
        },
    })
    assert len(ecos) == 1
    assert ecos[0]["customer_id"] == "+529981234567"
    assert ecos[0]["text"] == "Te espero a las 4"


def test_un_eco_de_grupo_se_descarta():
    assert YCloudProvider().parse_echoes({
        "type": "whatsapp.smb.message.echoes",
        "whatsappMessage": {"wamid": "x", "to": "+52998", "groupId": "120@g.us",
                            "text": {"body": "hola"}},
    }) == []


def test_el_historial_distingue_lo_que_entro_de_lo_que_salio():
    eventos = [
        {
            "type": "whatsapp.smb.history",
            "whatsappInboundMessage": {
                "wamid": "wamid.H1", "from": "+529981234567", "type": "text",
                "text": {"body": "¿Abren el sábado?"},
            },
        },
        {
            "type": "whatsapp.smb.history",
            "whatsappMessage": {
                "wamid": "wamid.H2", "to": "+529981234567", "type": "text",
                "text": {"body": "No, de lunes a viernes"},
            },
        },
    ]
    historial = YCloudProvider().parse_history(eventos)
    assert [h["direccion"] for h in historial] == ["entrante", "saliente"]
    assert all(h["customer_id"] == "+529981234567" for h in historial)


def test_contestar_desde_el_celular_aparta_al_agente():
    """Lo que de verdad importa: el paciente no recibe dos respuestas."""
    from sqlalchemy import select, text

    from app.db.session import db_session
    from app.models.contact import Contact
    from app.models.conversation import Conversation, ConversationStatus
    from app.services.conversation import store_outgoing_echo

    telefono = f"+5299812{uuid.uuid4().int % 100000:05d}"

    async def _run():
        msg_id = await store_outgoing_echo(
            "whatsapp", f"wamid.{uuid.uuid4().hex[:10]}", telefono,
            "Ahí te veo a las 4", sent_by="whatsapp_business_app", tomar_control=True,
        )
        assert msg_id is not None
        async with db_session() as db:
            contacto = (
                await db.execute(select(Contact).where(Contact.telefono == telefono))
            ).scalar_one()
            conv = (
                await db.execute(
                    select(Conversation).where(Conversation.contact_id == contacto.id)
                )
            ).scalar_one()
            estado = conv.status
            marca = conv.derivada_a_humano_at
            ids = (contacto.id, conv.id)
        return estado, marca, ids

    async def _limpiar(ids):
        async with db_session() as db:
            await db.execute(text("DELETE FROM messages WHERE conversation_id = :c"),
                             {"c": str(ids[1])})
            await db.execute(text("DELETE FROM conversations WHERE id = :c"), {"c": str(ids[1])})
            await db.execute(text("DELETE FROM contacts WHERE id = :c"), {"c": str(ids[0])})
            await db.commit()

    estado, marca, ids = asyncio.run(_run())
    try:
        assert estado == ConversationStatus.humano, "Sofía seguiría contestando encima"
        assert marca is not None
    finally:
        asyncio.run(_limpiar(ids))


def test_el_eco_de_instagram_no_aparta_al_agente():
    """En Instagram el eco suele ser otra automatización, no una persona."""
    from sqlalchemy import select, text

    from app.db.session import db_session
    from app.models.contact import Contact
    from app.models.conversation import Conversation, ConversationStatus
    from app.services.conversation import store_outgoing_instagram_echo

    psid = f"ig:{uuid.uuid4().hex[:12]}"

    async def _run():
        await store_outgoing_instagram_echo(f"mid.{uuid.uuid4().hex[:10]}", psid, "Hola")
        async with db_session() as db:
            contacto = (
                await db.execute(select(Contact).where(Contact.telefono == psid))
            ).scalar_one()
            conv = (
                await db.execute(
                    select(Conversation).where(Conversation.contact_id == contacto.id)
                )
            ).scalar_one()
            return conv.status, (contacto.id, conv.id)

    async def _limpiar(ids):
        async with db_session() as db:
            await db.execute(text("DELETE FROM messages WHERE conversation_id = :c"),
                             {"c": str(ids[1])})
            await db.execute(text("DELETE FROM conversations WHERE id = :c"), {"c": str(ids[1])})
            await db.execute(text("DELETE FROM contacts WHERE id = :c"), {"c": str(ids[0])})
            await db.commit()

    estado, ids = asyncio.run(_run())
    try:
        assert estado != ConversationStatus.humano
    finally:
        asyncio.run(_limpiar(ids))
