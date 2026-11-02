"""Messenger es un canal propio: lo que llega como `fb:<psid>` no se mezcla con nada.

POR QUÉ. En la Fase 7 los mensajes a la página de Facebook entran por Zernio
con el identificador `fb:<psid>`, igual que Instagram usa `ig:<psid>`. Antes no
había canal para ellos: sin el prefijo reconocido, un `fb:` caía en la rama por
defecto y se guardaba como WhatsApp, con su pestaña, su agente y su pausa. El
operador lo veía donde no era y Sofía contestaba por un canal que no existe.

Lo que se comprueba aquí:
  - el prefijo `fb:` da canal y origen Messenger (en la ingesta y en el buffer);
  - Messenger cuenta en todas las listas de canales (pausa, agente de texto,
    mensaje puente de la derivación);
  - los secretos de Zernio viajan cifrados y el panel los devuelve enmascarados;
  - un mensaje de Messenger crea su conversación con canal `messenger` y queda
    pendiente de encolar, como Instagram y WhatsApp.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest


def _db_available() -> bool:
    try:
        from sqlalchemy import text

        from app.db.session import db_session

        async def _check():
            async with db_session() as db:
                await db.execute(text("select 1"))

        asyncio.run(_check())
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(
    not _db_available(),
    reason="DB no disponible en este entorno (se ejecuta en CI con Postgres)",
)


def test_el_prefijo_fb_se_nombra_messenger_en_los_logs():
    from app.services.conversation import _canal_del_identificador

    assert _canal_del_identificador("fb:1234567890") == "Messenger"
    assert _canal_del_identificador("ig:1234567890") == "Instagram"


def test_el_buffer_de_un_contacto_fb_pide_el_agente_de_messenger():
    from app.services.conversation import _canal_for_buffer_key

    assert asyncio.run(_canal_for_buffer_key("fb:1234567890")) == "messenger"


def test_messenger_esta_en_todas_las_listas_de_canales():
    from app.agents.tools.human_handoff import _BRIDGE_CHANNELS
    from app.models.conversation import ConversationCanal
    from app.services.agent_pause import KNOWN_CHANNELS
    from app.services.runtime_config import AGENT_KIND_TEXT, kind_for_channel

    assert "messenger" in KNOWN_CHANNELS
    assert kind_for_channel("messenger") == AGENT_KIND_TEXT
    assert ConversationCanal.messenger in _BRIDGE_CHANNELS


def test_los_secretos_de_zernio_viajan_cifrados_en_instagram_y_messenger():
    from app.models.channel import ChannelType
    from app.services.channel_secrets import secret_keys_for

    for tipo in (ChannelType.instagram_dm, ChannelType.messenger):
        claves = secret_keys_for(tipo)
        assert "zernio_api_key" in claves
        assert "zernio_webhook_secret" in claves
    # Instagram no pierde las de la ruta oficial de Meta.
    assert "page_access_token" in secret_keys_for(ChannelType.instagram_dm)


def test_el_panel_no_devuelve_en_claro_los_secretos_de_zernio():
    from app.api.admin import _safe_channel_config

    cfg = {
        "transport": "zernio",
        "zernio_account_id": "acc_123",
        "zernio_api_key": "sk_live_muy_secreta_1234",
        "zernio_webhook_secret": "whsec_tambien_secreto_5678",
    }
    out = _safe_channel_config(cfg, "messenger")
    assert out["zernio_api_key"] != cfg["zernio_api_key"]
    assert out["zernio_webhook_secret"] != cfg["zernio_webhook_secret"]
    # Lo que no es secreto se ve tal cual: el panel lo necesita.
    assert out["transport"] == "zernio"
    assert out["zernio_account_id"] == "acc_123"


@needs_db
def test_un_mensaje_fb_crea_conversacion_messenger_pendiente_de_encolar():
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.contact import Contact, ContactOrigen
    from app.models.conversation import Conversation, ConversationCanal
    from app.models.message import Message
    from app.providers.whatsapp.base import IncomingMessage
    from app.services.conversation import store_incoming

    psid = f"fb:{uuid.uuid4().int % 10**16:016d}"

    async def _run():
        msg_id = await store_incoming(
            IncomingMessage(
                provider_message_id=f"m_{uuid.uuid4().hex}",
                from_phone=psid,
                to_phone="",
                message_type="text",
                text="hola, ¿tienen cita el sábado?",
            )
        )
        assert msg_id is not None
        async with db_session() as db:
            msg = (
                await db.execute(select(Message).where(Message.id == msg_id))
            ).scalar_one()
            conv = (
                await db.execute(
                    select(Conversation).where(Conversation.id == msg.conversation_id)
                )
            ).scalar_one()
            contact = (
                await db.execute(select(Contact).where(Contact.id == conv.contact_id))
            ).scalar_one()
        assert conv.canal == ConversationCanal.messenger
        assert contact.origen == ContactOrigen.messenger
        # El webhook de Zernio confirmará el encolado, como el de Instagram.
        assert (msg.extra or {}).get("agent_queued") is False

    asyncio.run(_run())
