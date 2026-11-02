"""Instagram: mensajes retirados por quien los envió ("deshacer envío").

- parse_deletions() extrae las retiradas; parse_webhook() y parse_echoes() las
  ignoran (antes entraban como un mensaje vacío que además despertaba al agente).
- mark_message_deleted() borra el contenido del mensaje ya guardado y lo deja
  marcado como eliminado, sin quitar la fila (el hueco en el hilo se queda, como
  en la propia app de Instagram). DB-gated.
"""
import asyncio
import uuid

import pytest

from app.providers.instagram.meta import DeletedMessage, InstagramProvider


def _deleted_event(mid: str, customer_psid: str, *, by_business: bool = False) -> dict:
    """Evento de retirada: el mensaje llega vacío, solo con su mid.

    Si lo borró la cuenta, Meta lo manda como eco (sender = cuenta).
    """
    msg: dict = {"mid": mid, "is_deleted": True}
    if by_business:
        msg["is_echo"] = True
        return {
            "sender": {"id": "BIZ"},
            "recipient": {"id": customer_psid},
            "timestamp": 1700000000000,
            "message": msg,
        }
    return {
        "sender": {"id": customer_psid},
        "recipient": {"id": "BIZ"},
        "timestamp": 1700000000000,
        "message": msg,
    }


def _incoming_event(mid: str, customer_psid: str, text: str = "buenas") -> dict:
    return {
        "sender": {"id": customer_psid},
        "recipient": {"id": "BIZ"},
        "timestamp": 1700000000000,
        "message": {"mid": mid, "text": text},
    }


def _payload(*events: dict) -> dict:
    return {"object": "instagram", "entry": [{"id": "BIZ", "messaging": list(events)}]}


# ---------------------------------------------------------------------------
# 1) Parsing puro (sin DB)
# ---------------------------------------------------------------------------


def test_parse_deletions_extracts_customer_delete():
    p = InstagramProvider()
    dels = p.parse_deletions(_payload(_deleted_event("mid.D1", "CUST1")))
    assert len(dels) == 1
    d = dels[0]
    assert isinstance(d, DeletedMessage)
    assert d.provider_message_id == "mid.D1"
    assert d.customer_id == "ig:CUST1"
    assert d.by_business is False


def test_parse_deletions_extracts_business_delete():
    """Si borra la cuenta, el cliente es el destinatario, no el emisor."""
    p = InstagramProvider()
    dels = p.parse_deletions(_payload(_deleted_event("mid.D2", "CUST2", by_business=True)))
    assert len(dels) == 1
    assert dels[0].customer_id == "ig:CUST2"
    assert dels[0].by_business is True


def test_deletion_is_not_an_incoming_message():
    """Una retirada NO puede entrar como mensaje: ni burbuja vacía ni agente."""
    p = InstagramProvider()
    payload = _payload(_deleted_event("mid.D1", "CUST1"))
    assert p.parse_webhook(payload) == []
    assert p.parse_echoes(payload) == []
    # Y la retirada de un saliente tampoco se refleja como eco nuevo.
    eco = _payload(_deleted_event("mid.D2", "CUST2", by_business=True))
    assert p.parse_echoes(eco) == []
    assert p.parse_webhook(eco) == []


def test_parse_deletions_ignores_normal_messages():
    p = InstagramProvider()
    assert p.parse_deletions(_payload(_incoming_event("mid.IN1", "CUST1"))) == []


def test_parse_deletions_mixed_payload():
    """Payload con un entrante y una retirada: cada parser coge lo suyo."""
    p = InstagramProvider()
    payload = _payload(
        _incoming_event("mid.IN1", "CUST1"),
        _deleted_event("mid.D1", "CUST1"),
    )
    assert len(p.parse_webhook(payload)) == 1
    dels = p.parse_deletions(payload)
    assert len(dels) == 1
    assert dels[0].provider_message_id == "mid.D1"


def test_parse_deletions_skips_malformed():
    p = InstagramProvider()
    bad = _payload(_deleted_event("", "CUST1"), _deleted_event("mid.X", ""))
    assert p.parse_deletions(bad) == []


# ---------------------------------------------------------------------------
# 2) Aplicar la retirada (DB-backed)
# ---------------------------------------------------------------------------


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


pytestmark_db = pytest.mark.skipif(
    not _db_available(),
    reason="DB no disponible en este entorno (se ejecuta en CI con Postgres)",
)


@pytestmark_db
@pytest.mark.asyncio
async def test_delete_wipes_content_and_keeps_row():
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.message import Message
    from app.providers.whatsapp import IncomingMessage
    from app.services.conversation import mark_message_deleted, store_incoming

    psid = f"DEL{uuid.uuid4().hex[:10]}"
    customer_id = f"ig:{psid}"
    mid = f"mid.in.{uuid.uuid4().hex}"

    assert (
        await store_incoming(
            IncomingMessage(
                provider_message_id=mid,
                from_phone=customer_id,
                to_phone="ig:BIZ",
                message_type="text",
                text="Esto lo voy a retirar",
                audio_url=None,
                audio_mime=None,
                customer_name=None,
                raw={},
            )
        )
        is not None
    )

    # 1) Retirada → borra el contenido y marca el mensaje.
    assert await mark_message_deleted(mid, customer_id) is not None

    async with db_session() as db:
        msg = (
            await db.execute(
                select(Message).where(Message.extra["provider_message_id"].astext == mid)
            )
        ).scalar_one()  # la fila sigue ahí: el hueco del hilo no se pierde
    assert msg.contenido is None
    assert msg.audio_transcript is None
    assert msg.audio_url is None
    assert msg.media_url is None
    assert msg.extra.get("is_deleted") is True
    assert msg.extra.get("deleted_at")

    # 2) Meta reintenta el webhook → idempotente.
    assert await mark_message_deleted(mid, customer_id) is None


@pytestmark_db
@pytest.mark.asyncio
async def test_delete_ignores_unknown_and_other_contact():
    from app.providers.whatsapp import IncomingMessage
    from app.services.conversation import mark_message_deleted, store_incoming

    # Mid que no tenemos guardado → no pasa nada.
    assert await mark_message_deleted(f"mid.{uuid.uuid4().hex}", "ig:NOPE") is None
    assert await mark_message_deleted("", None) is None

    # Mid real pero con OTRO cliente en el aviso → no se toca.
    psid = f"DEL{uuid.uuid4().hex[:10]}"
    mid = f"mid.in.{uuid.uuid4().hex}"
    assert (
        await store_incoming(
            IncomingMessage(
                provider_message_id=mid,
                from_phone=f"ig:{psid}",
                to_phone="ig:BIZ",
                message_type="text",
                text="No me toques",
                audio_url=None,
                audio_mime=None,
                customer_name=None,
                raw={},
            )
        )
        is not None
    )
    assert await mark_message_deleted(mid, f"ig:OTRO{uuid.uuid4().hex[:8]}") is None
