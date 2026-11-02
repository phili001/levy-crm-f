"""Correo por IMAP/SMTP: cómo sale la respuesta y cuándo no sale.

  - La respuesta va en el MISMO hilo del cliente y se copia en Enviados.
  - Sofía deja borrador por defecto; con «Sofía envía sola», envía; en
    Entrenamiento, nunca.
  - El borrador editado desde el Inbox sale por SMTP y deja la corrección.
  - La lista de no-responder y los correos de listas no se contestan.

El SMTP y el IMAP son dobles: se prueba lo que construimos, no la red.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.services.correo_smtp import construir_respuesta, en_no_responder


def test_la_respuesta_lleva_el_hilo_y_un_id_del_dominio_del_buzon():
    msg = construir_respuesta(
        desde="recepcion@clinica.mx",
        para="paciente@example.com",
        asunto="Cita",
        cuerpo="Hola",
        in_reply_to="<b@example.com>",
        references="<raiz@example.com> <b@example.com>",
    )
    assert msg["Subject"] == "Re: Cita"
    assert msg["In-Reply-To"] == "<b@example.com>"
    assert msg["References"] == "<raiz@example.com> <b@example.com>"
    assert msg["Message-ID"].endswith("@clinica.mx>")
    assert "recepcion@clinica.mx" in msg["From"]


def test_la_lista_de_no_responder_acepta_direcciones_y_dominios():
    lista = ["facturas@proveedor.com", "@banco.mx", "avisos.sat.gob.mx"]
    assert en_no_responder("Facturas@Proveedor.com", lista)
    assert en_no_responder("alertas@banco.mx", lista)
    assert en_no_responder("x@avisos.sat.gob.mx", lista)
    assert not en_no_responder("paciente@example.com", lista)
    assert not en_no_responder("paciente@example.com", None)


def test_un_correo_de_lista_no_se_contesta():
    from app.services.classifier import email_is_automated

    auto, motivo = email_is_automated("boletin@tienda.com", {"list_id": "<ofertas.tienda.com>"})
    assert auto and "lista" in motivo


# --- Con base de datos --------------------------------------------------------------


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
    not _db_available(), reason="DB no disponible (se ejecuta en CI con Postgres)"
)


class _Buzon:
    """Doble del SMTP y de la copia en Enviados."""

    def __init__(self) -> None:
        self.enviados: list = []
        self.copias: list = []


def _preparar(monkeypatch, tmp_path, *, auto_send: bool = False) -> _Buzon:
    from app.services import correo_smtp

    buzon = _Buzon()
    monkeypatch.setattr("app.core.config.settings.UPLOADS_PATH", str(tmp_path))
    monkeypatch.setattr(
        correo_smtp, "_enviar_sync", lambda servidor, usuario, pwd, msg: buzon.enviados.append(msg)
    )
    monkeypatch.setattr(
        "app.providers.email.imap_client.detectar_enviados", lambda cfg: "INBOX.Sent"
    )
    monkeypatch.setattr(
        "app.providers.email.imap_client.guardar_en_enviados",
        lambda cfg, carpeta, crudo: buzon.copias.append((carpeta, crudo)),
    )
    buzon.auto_send = auto_send  # type: ignore[attr-defined]
    return buzon


async def _canal(auto_send: bool = False):
    from sqlalchemy import update

    from app.db.session import db_session
    from app.models.channel import Channel, ChannelType
    from app.services.channel_secrets import apply_channel_values

    async with db_session() as db:
        # Solo un buzón IMAP activo durante la prueba: `canal_imap` coge el
        # más antiguo, y los de otras pruebas no pueden colarse.
        await db.execute(update(Channel).where(Channel.type == ChannelType.email).values(enabled=False))
        ch = Channel(type=ChannelType.email, name=f"Correo {uuid.uuid4().hex[:6]}", enabled=True, config={})
        apply_channel_values(
            ch,
            {
                "transport": "imap",
                "usuario": "recepcion@clinica.mx",
                "imap_password": "secreta",
                "imap_host": "imap.clinica.mx",
                "imap_port": 993,
                "imap_seguridad": "ssl",
                "smtp_host": "smtp.clinica.mx",
                "smtp_port": 465,
                "smtp_seguridad": "ssl",
                "auto_send": auto_send,
            },
        )
        db.add(ch)
        await db.commit()
        await db.refresh(ch)
        return ch


async def _borrar(ch_id):
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        await db.execute(text("DELETE FROM channels WHERE id = :i"), {"i": str(ch_id)})
        await db.commit()


async def _llega_correo(raiz: str, reply_to: str | None = None):
    """Un correo del cliente por la ingesta IMAP. Devuelve la conversación."""
    from email.message import EmailMessage

    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.conversation import Conversation
    from app.models.message import Message
    from app.services.conversation import store_incoming
    from app.services.imap_ingest import a_incoming, parsear_correo

    m = EmailMessage()
    m["From"] = f"Paciente <p{uuid.uuid4().hex[:6]}@example.com>"
    m["To"] = "recepcion@clinica.mx"
    m["Subject"] = "Cita"
    m["Message-ID"] = raiz
    if reply_to:
        m["Reply-To"] = reply_to
    m.set_content("¿Tienen cita el jueves?")
    crudo = m.as_bytes()
    msg_id = await store_incoming(a_incoming(parsear_correo(crudo), crudo))
    async with db_session() as db:
        msg = (await db.execute(select(Message).where(Message.id == msg_id))).scalar_one()
        return (
            await db.execute(select(Conversation).where(Conversation.id == msg.conversation_id))
        ).scalar_one()


@con_db
def test_la_respuesta_sale_por_smtp_en_el_mismo_hilo_y_se_copia_en_enviados(monkeypatch, tmp_path):
    from app.services.channel_sender import send_text_to_conversation

    buzon = _preparar(monkeypatch, tmp_path)
    raiz = f"<{uuid.uuid4().hex}@example.com>"

    async def _run():
        ch = await _canal()
        try:
            conv = await _llega_correo(raiz)
            message_id = await send_text_to_conversation(conv, "Sí, a las 10:00.")
            [enviado] = buzon.enviados
            assert enviado["In-Reply-To"] == raiz
            assert raiz in enviado["References"]
            assert enviado["Subject"] == "Re: Cita"
            assert enviado["Message-ID"] == message_id
            assert "Sí, a las 10:00." in enviado.get_content()
            [(carpeta, crudo)] = buzon.copias
            assert carpeta == "INBOX.Sent" and message_id.encode() in crudo
        finally:
            await _borrar(ch.id)

    asyncio.run(_run())


@con_db
@pytest.mark.parametrize(
    "auto_send,entrenamiento,sale",
    [(False, False, False), (True, False, True), (True, True, False)],
)
def test_sofia_deja_borrador_salvo_que_envie_sola_y_nunca_en_entrenamiento(
    monkeypatch, tmp_path, auto_send, entrenamiento, sale
):
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.conversation import Conversation
    from app.models.message import Message, MessageRole
    from app.services.conversation import _responder_correo_imap

    buzon = _preparar(monkeypatch, tmp_path)

    async def _run():
        ch = await _canal(auto_send=auto_send)
        try:
            conv = await _llega_correo(f"<{uuid.uuid4().hex}@example.com>")
            async with db_session() as db:
                c = (await db.execute(select(Conversation).where(Conversation.id == conv.id))).scalar_one()
                await _responder_correo_imap(
                    db, c, "Claro, el jueves a las 10.", entrenamiento, SimpleNamespace(id=None)
                )
            async with db_session() as db:
                [resp] = (
                    await db.execute(
                        select(Message).where(
                            Message.conversation_id == conv.id, Message.rol == MessageRole.assistant
                        )
                    )
                ).scalars().all()
            assert bool(buzon.enviados) is sale
            assert bool((resp.extra or {}).get("is_draft")) is (not sale)
        finally:
            await _borrar(ch.id)

    asyncio.run(_run())


@con_db
def test_con_un_reply_to_distinto_sofia_deja_borrador_aunque_envie_sola(monkeypatch, tmp_path):
    """Salió en el dry-run 2 (SEC-10): un correo con el From de un paciente y el
    Reply-To de otro se contestaba solo, y la respuesta le llegaba al otro."""
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.conversation import Conversation
    from app.models.message import Message, MessageRole
    from app.services.conversation import _responder_correo_imap

    buzon = _preparar(monkeypatch, tmp_path)

    async def _run():
        ch = await _canal(auto_send=True)
        try:
            conv = await _llega_correo(
                f"<{uuid.uuid4().hex}@example.com>", reply_to="otro@ejemplo-ajeno.com"
            )
            async with db_session() as db:
                c = (await db.execute(select(Conversation).where(Conversation.id == conv.id))).scalar_one()
                await _responder_correo_imap(
                    db, c, "Tu cita es el jueves a las 10.", False, SimpleNamespace(id=None)
                )
            async with db_session() as db:
                [resp] = (
                    await db.execute(
                        select(Message).where(
                            Message.conversation_id == conv.id, Message.rol == MessageRole.assistant
                        )
                    )
                ).scalars().all()
            assert buzon.enviados == []
            assert (resp.extra or {}).get("is_draft") is True
        finally:
            await _borrar(ch.id)

    asyncio.run(_run())


@con_db
def test_el_borrador_editado_en_el_inbox_sale_por_smtp_y_deja_la_correccion(monkeypatch, tmp_path):
    import httpx
    from sqlalchemy import select, text

    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.main import app
    from app.models.agent_correction import AgentCorrection
    from app.models.message import Message, MessageRole
    from app.models.user import User

    buzon = _preparar(monkeypatch, tmp_path)

    async def _run():
        ch = await _canal()
        suf = uuid.uuid4().hex[:8]
        try:
            conv = await _llega_correo(f"<{uuid.uuid4().hex}@example.com>")
            async with db_session() as db:
                u = User(email=f"op-{suf}@test.local", password_hash=hash_password("UnaClaveLargaDeVerdad2026"), role="cliente")
                borrador = Message(
                    conversation_id=conv.id,
                    rol=MessageRole.assistant,
                    contenido="Sí hay cita.",
                    extra={"is_draft": True, "draft_sent": False},
                )
                db.add_all([u, borrador])
                await db.commit()
                token = create_access_token(subject=str(u.id), extra={"role": "cliente"})
                user_id, draft_id = u.id, borrador.id
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                r = await c.post(
                    f"/api/v1/conversations/{conv.id}/drafts/{draft_id}/send",
                    json={"text": "Sí hay cita el jueves a las 10:00."},
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert r.status_code == 200, r.text
            [enviado] = buzon.enviados
            assert "jueves a las 10:00" in enviado.get_content()
            async with db_session() as db:
                msg = (await db.execute(select(Message).where(Message.id == draft_id))).scalar_one()
                correcciones = (
                    await db.execute(select(AgentCorrection).where(AgentCorrection.conversation_id == conv.id))
                ).scalars().all()
            assert msg.extra["draft_sent"] is True
            assert correcciones
            async with db_session() as db:
                await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(user_id)})
                await db.commit()
        finally:
            await _borrar(ch.id)

    asyncio.run(_run())
