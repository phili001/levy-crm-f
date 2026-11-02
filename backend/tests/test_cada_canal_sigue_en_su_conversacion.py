"""Cada canal sigue en su propia conversación aunque la ficha tenga varias.

Salió en el dry-run 2 (C-1, en la viva): la entrada de mensajes cogía la última
conversación abierta de la ficha sin mirar el canal. Tras una llamada (su
conversación de voz queda abierta, en humano), lo que la persona escribía por
WhatsApp, o por Instagram después de unir las fichas, caía en la conversación
de voz: Sofía no contestaba y el mensaje no salía en el hilo de su canal.
"""
from __future__ import annotations

import uuid

import pytest

from tests._fichas import borrar, conversacion_y_ficha, entra, llave_ig, telefono_mx
from tests.test_mensaje_tras_fusionar_cae_en_la_ficha_unida import _une
from tests.test_voice_gating import pytestmark_db


async def _llamada(contact_id: uuid.UUID) -> uuid.UUID:
    """Una conversación de voz abierta y más reciente que las demás, como deja
    una llamada que acabó en recado."""
    from app.db.session import db_session
    from app.models.conversation import Conversation, ConversationCanal, ConversationStatus

    async with db_session() as db:
        conv = Conversation(
            contact_id=contact_id,
            canal=ConversationCanal.retell_voice,
            session_id=f"call_{uuid.uuid4().hex[:10]}",
            status=ConversationStatus.humano,
        )
        db.add(conv)
        await db.commit()
        return conv.id


@pytestmark_db
@pytest.mark.asyncio
async def test_despues_de_una_llamada_el_whatsapp_sigue_en_su_conversacion():
    telefono = telefono_mx()
    conv_wa, wa = await conversacion_y_ficha(await entra(telefono, "hola por WhatsApp"))
    try:
        voz = await _llamada(wa)
        conv_despues, _ = await conversacion_y_ficha(await entra(telefono, "¿me agendas mañana?"))
        assert conv_despues == conv_wa, "el WhatsApp cayó en la conversación de la llamada"
        assert conv_despues != voz
    finally:
        await borrar(wa)


@pytestmark_db
@pytest.mark.asyncio
async def test_tras_unir_fichas_el_dm_de_instagram_no_cae_en_la_llamada():
    telefono, llave = telefono_mx(), llave_ig()
    _conv_wa, wa = await conversacion_y_ficha(await entra(telefono, "hola por WhatsApp"))
    conv_ig, ig = await conversacion_y_ficha(await entra(llave, "hola por Instagram"))
    try:
        await _une(wa, ig)
        await _llamada(wa)
        conv_despues, ficha_despues = await conversacion_y_ficha(
            await entra(llave, "agéndame mañana a las 16")
        )
        assert ficha_despues == wa
        assert conv_despues == conv_ig, "el DM cayó en otra conversación de la ficha"
    finally:
        await borrar(wa, ig)
