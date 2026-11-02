"""Instagram: espejo de respuestas escritas fuera del panel (ecos is_echo).

- parse_echoes() extrae los salientes de la cuenta; parse_webhook() los ignora.
- store_outgoing_instagram_echo() guarda el eco como saliente (rol=operator) en
  la conversación existente, con dedupe por provider_message_id (no duplica lo
  que ya enviamos desde el panel/el agente). DB-gated.
"""
import asyncio
import uuid

import pytest

from app.providers.instagram.meta import InstagramProvider, OutgoingEcho


def _echo_event(mid: str, customer_psid: str, text: str | None = "hola") -> dict:
    """Evento de eco: el sender es la cuenta, el recipient es el cliente."""
    msg: dict = {"is_echo": True, "mid": mid}
    if text is not None:
        msg["text"] = text
    return {
        "sender": {"id": "BIZ"},
        "recipient": {"id": customer_psid},
        "timestamp": 1700000000000,
        "message": msg,
    }


def _incoming_event(mid: str, customer_psid: str, text: str = "buenas") -> dict:
    """Evento entrante normal: el sender es el cliente."""
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


def test_parse_echoes_extracts_outgoing():
    p = InstagramProvider()
    echoes = p.parse_echoes(_payload(_echo_event("mid.E1", "CUST1", "te respondo")))
    assert len(echoes) == 1
    e = echoes[0]
    assert isinstance(e, OutgoingEcho)
    assert e.provider_message_id == "mid.E1"
    assert e.customer_id == "ig:CUST1"  # el recipient = cliente, con prefijo ig:
    assert e.text == "te respondo"


def test_parse_webhook_ignores_echoes():
    """Un eco NO debe entrar como mensaje entrante (no dispara el agente)."""
    p = InstagramProvider()
    assert p.parse_webhook(_payload(_echo_event("mid.E1", "CUST1"))) == []


def test_incoming_is_not_an_echo():
    p = InstagramProvider()
    incomings = p.parse_webhook(_payload(_incoming_event("mid.IN1", "CUST1")))
    assert len(incomings) == 1
    assert incomings[0].from_phone == "ig:CUST1"
    # Y al revés: un entrante no aparece como eco.
    assert p.parse_echoes(_payload(_incoming_event("mid.IN1", "CUST1"))) == []


def test_parse_echoes_mixed_payload():
    """Payload con un entrante y un eco a la vez: cada parser coge lo suyo."""
    p = InstagramProvider()
    payload = _payload(
        _incoming_event("mid.IN1", "CUST1"),
        _echo_event("mid.E1", "CUST1", "respondo"),
    )
    assert len(p.parse_webhook(payload)) == 1
    echoes = p.parse_echoes(payload)
    assert len(echoes) == 1
    assert echoes[0].provider_message_id == "mid.E1"


def test_parse_echoes_skips_malformed():
    p = InstagramProvider()
    # Sin mid o sin recipient → se descarta.
    bad = _payload(_echo_event("", "CUST1"), _echo_event("mid.X", ""))
    assert p.parse_echoes(bad) == []


# ---------------------------------------------------------------------------
# 2) Guardado + dedupe (DB-backed)
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
async def test_echo_stores_as_operator_and_dedupes():
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.message import Message, MessageRole
    from app.providers.whatsapp import IncomingMessage
    from app.services.conversation import store_incoming, store_outgoing_instagram_echo

    psid = f"ECHO{uuid.uuid4().hex[:10]}"
    customer_id = f"ig:{psid}"

    # 1) Crea contacto + conversación de Instagram con un entrante del cliente.
    in_mid = f"mid.in.{uuid.uuid4().hex}"
    incoming = IncomingMessage(
        provider_message_id=in_mid,
        from_phone=customer_id,
        to_phone="ig:BIZ",
        message_type="text",
        text="Hola",
        audio_url=None,
        audio_mime=None,
        customer_name=None,
        raw={},
    )
    assert await store_incoming(incoming) is not None

    # 2) Eco NUEVO (respuesta a mano desde la app) → se guarda como saliente.
    echo_mid = f"mid.echo.{uuid.uuid4().hex}"
    stored = await store_outgoing_instagram_echo(
        echo_mid, customer_id, "Te respondo desde la app"
    )
    assert stored is not None

    # 3) El mismo eco otra vez → dedupe (None).
    assert (
        await store_outgoing_instagram_echo(echo_mid, customer_id, "Te respondo desde la app")
        is None
    )

    # 4) Eco cuyo mid coincide con un mensaje ya guardado (simula lo enviado
    #    desde el panel/el agente: ya teníamos ese provider_message_id) → None.
    assert await store_outgoing_instagram_echo(in_mid, customer_id, "dup") is None

    # El mensaje guardado es rol=operator con el texto del eco.
    async with db_session() as db:
        msg = (
            await db.execute(
                select(Message).where(
                    Message.extra["provider_message_id"].astext == echo_mid
                )
            )
        ).scalar_one()
    assert msg.rol == MessageRole.operator
    assert "desde la app" in msg.contenido


@pytestmark_db
@pytest.mark.asyncio
async def test_echo_sin_texto_no_guarda_nada():
    from app.services.conversation import store_outgoing_instagram_echo

    # Texto vacío → None (ecos sin texto: fotos, stories compartidas).
    assert (
        await store_outgoing_instagram_echo(f"mid.{uuid.uuid4().hex}", "ig:whatever", "   ")
        is None
    )


@pytestmark_db
@pytest.mark.asyncio
async def test_echo_de_manychat_abre_la_conversacion_con_contexto():
    """El caso real de Instagram: una automatización externa (ManyChat) contesta
    a una palabra clave ANTES de que exista nada nuestro, y la persona responde
    a ESO. El eco tiene que abrir la conversación para que el agente lo vea.

    Y mientras el hilo sea un monólogo nuestro, no aparece en la bandeja: nace
    archivado y lo desarchiva el primer mensaje de la persona.
    """
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.contact import Contact
    from app.models.conversation import Conversation
    from app.models.message import Message, MessageRole
    from app.providers.whatsapp import IncomingMessage
    from app.services.conversation import store_incoming, store_outgoing_instagram_echo

    psid = f"MC{uuid.uuid4().hex[:10]}"
    customer_id = f"ig:{psid}"

    # 1) Lo primero que ocurre es NUESTRO saliente (ManyChat), sin contacto ni
    #    conversación previos. Antes se descartaba; ahora se guarda.
    echo_mid = f"mid.echo.{uuid.uuid4().hex}"
    assert (
        await store_outgoing_instagram_echo(
            echo_mid, customer_id, "Aquí tienes el enlace, te lo dejo abajo"
        )
        is not None
    )

    async with db_session() as db:
        contact = (
            await db.execute(select(Contact).where(Contact.telefono == customer_id))
        ).scalar_one()
        conv = (
            await db.execute(
                select(Conversation).where(Conversation.contact_id == contact.id)
            )
        ).scalar_one()
        conv_id = conv.id
        # Fuera del CRM (como cualquier contacto de Instagram) y fuera de la
        # bandeja mientras solo hablemos nosotros.
        assert contact.in_crm is False
        assert conv.archived_at is not None

    # 2) Un segundo saliente nuestro tampoco la saca de archivadas.
    assert (
        await store_outgoing_instagram_echo(
            f"mid.echo.{uuid.uuid4().hex}", customer_id, "¿Te ha llegado?"
        )
        is not None
    )
    async with db_session() as db:
        conv = (
            await db.execute(select(Conversation).where(Conversation.id == conv_id))
        ).scalar_one()
        assert conv.archived_at is not None

    # 3) Ahora contesta la persona: misma conversación, ya visible.
    incoming = IncomingMessage(
        provider_message_id=f"mid.in.{uuid.uuid4().hex}",
        from_phone=customer_id,
        to_phone="ig:BIZ",
        message_type="text",
        text="Abajo de dónde?",
        audio_url=None,
        audio_mime=None,
        customer_name=None,
        raw={},
    )
    assert await store_incoming(incoming) is not None

    async with db_session() as db:
        conv = (
            await db.execute(select(Conversation).where(Conversation.id == conv_id))
        ).scalar_one()
        assert conv.archived_at is None
        msgs = (
            (
                await db.execute(
                    select(Message)
                    .where(Message.conversation_id == conv_id)
                    .order_by(Message.created_at)
                )
            )
            .scalars()
            .all()
        )
    # El contexto que le faltaba al agente está en el hilo, y va ANTES de la
    # pregunta de la persona.
    assert [m.rol for m in msgs] == [
        MessageRole.operator,
        MessageRole.operator,
        MessageRole.user,
    ]
    assert "te lo dejo abajo" in msgs[0].contenido
