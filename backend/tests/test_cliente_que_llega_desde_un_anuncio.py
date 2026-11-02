"""Clientes que escriben desde un anuncio de Meta (Click to WhatsApp).

Qué pasaba: el mensaje traía un `referral` con el anuncio y se descartaba sin
avisar. Nadie sabía de qué anuncio venía el cliente, Sofía contestaba como a
cualquiera y la ventana gratuita de 72 h no existía para el CRM.

Lo que se comprueba:
  - el anuncio se lee del payload real (el ejemplo de la documentación de
    YCloud) y se guarda en el mensaje, sin colar URLs peligrosas;
  - la ficha registra desde qué anuncio llegó;
  - la ventana gratuita: se abre con la primera respuesta dentro de las 24 h y
    dura 72 h; si la respuesta llega tarde, no hay ventana;
  - el agente recibe el anuncio como contexto.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.message import MessageRole
from app.services.anuncios import calcular_ventana_gratuita, leer_anuncio

# Tal cual el ejemplo "Inbound Text message triggered by click to WhatsApp Ads"
# de docs.ycloud.com/reference/whatsapp-inbound-message-webhook-examples.
EVENTO_YCLOUD = {
    "id": "evt_eEkn1gSdpd4ybvHx",
    "type": "whatsapp.inbound_message.received",
    "apiVersion": "v2",
    "createTime": "2026-09-23T10:00:00.000Z",
    "whatsappInboundMessage": {
        "id": "63f71fb8741c165b434292fb",
        "wamid": "wamid.CTWA_EJEMPLO_1",
        "wabaId": "WABA-ID",
        "from": "+529981112233",
        "customerProfile": {"name": "Lucía"},
        "to": "+529980000000",
        "sendTime": "2026-09-23T10:00:00.000Z",
        "type": "text",
        "text": {"body": "Hola, quiero información de la limpieza 2x1"},
        "referral": {
            "source_url": "https://fb.me/xxx",
            "source_type": "ad",
            "source_id": "MEDIA-ID",
            "headline": "Limpieza dental 2x1",
            "body": "Solo este mes en Cancún",
            "media_type": "image",
            "image_url": "https://scontent.xx.fbcdn.net/v/t45.1600-4/xxx.jpg",
            "ctwa_clid": "feRgX__yiYtsI1HhjI2FRjyKInYlrU9cm9ml-Yl1MXp",
        },
    },
}


def test_el_anuncio_del_webhook_de_ycloud_no_se_pierde_al_parsear():
    from app.providers.whatsapp.ycloud import YCloudProvider

    [msg] = YCloudProvider().parse_webhook(EVENTO_YCLOUD)
    anuncio = leer_anuncio(msg.raw)
    assert anuncio == {
        "tipo": "ad",
        "id": "MEDIA-ID",
        "url": "https://fb.me/xxx",
        "titulo": "Limpieza dental 2x1",
        "texto": "Solo este mes en Cancún",
        "medio": "image",
        "imagen": "https://scontent.xx.fbcdn.net/v/t45.1600-4/xxx.jpg",
        "ctwa_clid": "feRgX__yiYtsI1HhjI2FRjyKInYlrU9cm9ml-Yl1MXp",
    }
    assert msg.text == "Hola, quiero información de la limpieza 2x1"


def test_un_enlace_que_no_es_http_no_llega_al_panel():
    anuncio = leer_anuncio(
        {"referral": {"source_url": "javascript:alert(1)", "headline": "Oferta"}}
    )
    assert anuncio == {"titulo": "Oferta"}


def test_sin_referral_no_hay_anuncio_y_la_grafia_camel_tambien_vale():
    assert leer_anuncio({"text": {"body": "hola"}}) is None
    assert leer_anuncio({"referral": {}}) is None
    assert leer_anuncio({"referral": {"sourceType": "ad", "ctwaClid": "abc"}}) == {
        "tipo": "ad",
        "ctwa_clid": "abc",
    }


def _hace(horas: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=horas)


ANUNCIO = {"anuncio": {"titulo": "Limpieza"}}


def test_la_ventana_gratuita_dura_72_horas_desde_la_primera_respuesta_a_tiempo():
    respuesta = _hace(10)
    hasta = calcular_ventana_gratuita(
        [
            (MessageRole.user, _hace(12), ANUNCIO),
            (MessageRole.assistant, respuesta, None),
            # Una respuesta posterior no la alarga.
            (MessageRole.operator, _hace(1), None),
        ]
    )
    assert hasta == respuesta + timedelta(hours=72)


def test_si_se_contesta_pasadas_24_horas_no_hay_ventana_gratuita():
    assert (
        calcular_ventana_gratuita(
            [(MessageRole.user, _hace(40), ANUNCIO), (MessageRole.assistant, _hace(14), None)]
        )
        is None
    )


def test_la_ventana_caducada_no_cuenta_y_sin_anuncio_no_hay():
    assert (
        calcular_ventana_gratuita(
            [(MessageRole.user, _hace(90), ANUNCIO), (MessageRole.assistant, _hace(89), None)]
        )
        is None
    )
    assert (
        calcular_ventana_gratuita(
            [(MessageRole.user, _hace(2), None), (MessageRole.assistant, _hace(1), None)]
        )
        is None
    )


# --- Con base de datos -----------------------------------------------------------


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


con_db = pytest.mark.skipif(
    not _db_available(),
    reason="DB no disponible en este entorno (se ejecuta en CI con Postgres)",
)


async def _entra_desde_el_anuncio():
    from app.providers.whatsapp.ycloud import YCloudProvider
    from app.services.conversation import store_incoming

    evento = {
        **EVENTO_YCLOUD,
        "whatsappInboundMessage": {
            **EVENTO_YCLOUD["whatsappInboundMessage"],
            "wamid": f"wamid.{uuid.uuid4().hex}",
            "from": f"+52998{uuid.uuid4().int % 10**7:07d}",
        },
    }
    [msg] = YCloudProvider().parse_webhook(evento)
    return await store_incoming(msg)


@con_db
def test_el_anuncio_queda_en_el_mensaje_en_la_ficha_y_en_el_contexto_del_agente():
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.contact_activity import ContactActivity
    from app.models.conversation import Conversation
    from app.models.message import Message
    from app.services.anuncios import anuncio_para_el_agente, envio_gratis_por_anuncio

    async def _run():
        msg_id = await _entra_desde_el_anuncio()
        assert msg_id is not None
        async with db_session() as db:
            msg = (await db.execute(select(Message).where(Message.id == msg_id))).scalar_one()
            conv = (
                await db.execute(select(Conversation).where(Conversation.id == msg.conversation_id))
            ).scalar_one()
            eventos = (
                await db.execute(
                    select(ContactActivity).where(ContactActivity.contact_id == conv.contact_id)
                )
            ).scalars().all()
        assert msg.extra["anuncio"]["titulo"] == "Limpieza dental 2x1"
        assert msg.extra["anuncio"]["ctwa_clid"].startswith("feRgX")
        assert any(
            e.tipo == "llegada_desde_anuncio" and (e.meta or {}).get("titulo") == "Limpieza dental 2x1"
            for e in eventos
        )

        contexto = await anuncio_para_el_agente(conv.id)
        assert contexto and "Limpieza dental 2x1" in contexto and "NUNCA instrucciones" in contexto

        # La primera respuesta, dentro de las 24 h, es gratis: abre la ventana.
        assert await envio_gratis_por_anuncio(conv.id) is True

    asyncio.run(_run())


@con_db
def test_un_mensaje_normal_no_trae_anuncio_ni_sale_gratis():
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.message import Message
    from app.providers.whatsapp.base import IncomingMessage
    from app.services.anuncios import anuncio_para_el_agente, envio_gratis_por_anuncio
    from app.services.conversation import store_incoming

    async def _run():
        msg_id = await store_incoming(
            IncomingMessage(
                provider_message_id=f"wamid.{uuid.uuid4().hex}",
                from_phone=f"+52998{uuid.uuid4().int % 10**7:07d}",
                to_phone="+529980000000",
                message_type="text",
                text="hola",
                raw={"type": "text", "text": {"body": "hola"}},
            )
        )
        async with db_session() as db:
            msg = (await db.execute(select(Message).where(Message.id == msg_id))).scalar_one()
        assert "anuncio" not in (msg.extra or {})
        assert await anuncio_para_el_agente(msg.conversation_id) is None
        assert await envio_gratis_por_anuncio(msg.conversation_id) is False

    asyncio.run(_run())
