"""Los adjuntos del correo IMAP entran en el borrado y en la retención.

QUÉ PASABA (SPEC-21, A5 de la revisión de Francisco). La ingesta IMAP guarda
cada adjunto en `uploads/` al llegar, porque IMAP no deja pedirlo después, y lo
apunta solo en `extra.attachments` con `local: true`. El mensaje entra como
texto, sin `media_url`. El borrado del contacto miraba `audio_url` y
`media_url`; la retención de adjuntos, solo `media_url`. Así que un PDF que
mandó un paciente por correo sobrevivía a su borrado (que se declaraba
completo) y a cualquier retención.

Lo que se comprueba aquí:
  - borrar el contacto borra del disco el PDF que entró por la ingesta real;
  - la retención borra los adjuntos viejos y deja su nombre en el hilo;
  - la retención no toca los recientes;
  - un adjunto de Gmail (no está en disco: se pide a Gmail) no cuenta;
  - una ficha con un nombre manipulado no borra nada fuera de `uploads/`.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.services.data_erasure import _purgar_fichas_locales, archivos_del_mensaje


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


async def _mensaje_con_adjunto(
    nombre_en_disco: str, *, hace_dias: int
) -> tuple[uuid.UUID, uuid.UUID]:
    """Un correo de un paciente con un PDF local, como lo deja la ingesta IMAP.
    Devuelve (contacto, mensaje)."""
    from app.db.session import db_session
    from app.models.contact import Contact, ContactOrigen
    from app.models.conversation import Conversation, ConversationCanal, ConversationStatus
    from app.models.message import Message, MessageRole

    async with db_session() as db:
        c = Contact(
            telefono=f"email:p{uuid.uuid4().hex[:8]}@example.com", origen=ContactOrigen.email
        )
        db.add(c)
        await db.flush()
        conv = Conversation(
            contact_id=c.id,
            canal=ConversationCanal.email,
            session_id=f"mail-{uuid.uuid4().hex[:8]}",
            status=ConversationStatus.bot,
        )
        db.add(conv)
        await db.flush()
        msg = Message(
            conversation_id=conv.id,
            rol=MessageRole.user,
            contenido="Adjunto mi receta\n\n[adjunto: receta.pdf]",
            extra={
                "subject": "Receta",
                "attachments": [
                    {
                        "filename": "receta.pdf",
                        "mime_type": "application/pdf",
                        "size": 14,
                        "attachment_id": nombre_en_disco,
                        "local": True,
                    }
                ],
                "inline_attachments": [],
            },
            created_at=datetime.now(UTC) - timedelta(days=hace_dias),
        )
        db.add(msg)
        await db.commit()
        return c.id, msg.id


async def _borrar_contacto(contact_id: uuid.UUID) -> None:
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        await db.execute(text("DELETE FROM contacts WHERE id = :i"), {"i": str(contact_id)})
        await db.commit()


async def _extra_de(message_id: uuid.UUID) -> dict:
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.message import Message

    async with db_session() as db:
        return (
            await db.execute(select(Message.extra).where(Message.id == message_id))
        ).scalar_one()


# ---------------------------------------------------------------------------


@con_db
async def test_borrar_el_contacto_borra_del_disco_sus_adjuntos_imap(monkeypatch, tmp_path):
    """Por la puerta de verdad: el correo entra por `sincronizar_canal` con el
    doble del servidor IMAP de `test_correo_imap_ingesta.py`."""
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.conversation import Conversation
    from app.models.message import Message
    from app.providers.email import imap_client
    from app.services import data_erasure, imap_ingest
    from tests.test_correo_imap_ingesta import _borrar_canal, _canal_imap, _correo, _ImapFalso

    monkeypatch.setattr("app.core.config.settings.UPLOADS_PATH", str(tmp_path))
    monkeypatch.setattr("app.tasks.process_message.process_message.delay", lambda mid: None)
    message_id = f"<{uuid.uuid4().hex}@example.com>"
    falso = _ImapFalso(
        "500",
        {
            1: _correo(
                de=f"receta{uuid.uuid4().hex[:6]}@example.com",
                message_id=message_id,
                adjunto=("receta.pdf", b"%PDF-1.4 datos"),
            )
        },
    )
    monkeypatch.setattr(imap_client, "_conectar", lambda cfg: falso)

    ch = await _canal_imap("500")
    try:
        await imap_ingest.sincronizar_canal(ch)
    finally:
        await _borrar_canal(ch.id)

    async with db_session() as db:
        msg = (
            await db.execute(
                select(Message).where(
                    Message.extra["provider_message_id"].astext == f"imap:{message_id}"
                )
            )
        ).scalar_one()
        contact_id = (
            await db.execute(
                select(Conversation.contact_id).where(Conversation.id == msg.conversation_id)
            )
        ).scalar_one()
    [ficha] = msg.extra["attachments"]
    assert msg.media_url is None, "el PDF de IMAP no va en media_url: es lo que lo escondía"
    archivo = tmp_path / ficha["attachment_id"]
    assert archivo.read_bytes() == b"%PDF-1.4 datos"

    async with db_session() as db:
        report = await data_erasure.erase_contact(db, contact_id)
        await db.commit()
    await data_erasure.finish_erasure(report)

    assert not archivo.exists(), "el PDF del paciente sobrevivió a su borrado"
    assert report["files_deleted"] == 1


@con_db
async def test_la_retencion_borra_los_adjuntos_imap_viejos_y_deja_su_nombre(monkeypatch, tmp_path):
    from app.services.data_erasure import purge_old_media_files

    monkeypatch.setattr("app.core.config.settings.UPLOADS_PATH", str(tmp_path))
    nombre = f"{uuid.uuid4().hex}.pdf"
    (tmp_path / nombre).write_bytes(b"%PDF-1.4 viejo")
    contact_id, message_id = await _mensaje_con_adjunto(nombre, hace_dias=200)
    try:
        res = await purge_old_media_files(retention_days=180)

        assert not (tmp_path / nombre).exists(), "la retención no borró el adjunto de IMAP"
        assert res["freed_bytes"] >= len(b"%PDF-1.4 viejo")
        extra = await _extra_de(message_id)
        [ficha] = extra["attachments"]
        assert ficha["filename"] == "receta.pdf", "el hilo tiene que seguir diciendo qué llegó"
        assert ficha["attachment_id"] is None and ficha["local"] is False
        assert ficha["purged_at"]
        assert extra["subject"] == "Receta", "la purga solo toca las fichas"
    finally:
        await _borrar_contacto(contact_id)


@con_db
async def test_la_retencion_no_toca_los_adjuntos_imap_recientes(monkeypatch, tmp_path):
    from app.services.data_erasure import purge_old_media_files

    monkeypatch.setattr("app.core.config.settings.UPLOADS_PATH", str(tmp_path))
    nombre = f"{uuid.uuid4().hex}.pdf"
    (tmp_path / nombre).write_bytes(b"%PDF-1.4 reciente")
    contact_id, message_id = await _mensaje_con_adjunto(nombre, hace_dias=10)
    try:
        await purge_old_media_files(retention_days=180)

        assert (tmp_path / nombre).read_bytes() == b"%PDF-1.4 reciente"
        [ficha] = (await _extra_de(message_id))["attachments"]
        assert ficha["attachment_id"] == nombre and ficha["local"] is True
    finally:
        await _borrar_contacto(contact_id)


def test_un_adjunto_de_gmail_no_cuenta_como_archivo_local(monkeypatch, tmp_path):
    """Gmail guarda el adjunto allí y se pide por su id. Aunque en `uploads/`
    hubiera un fichero que se llamara igual, no es suyo."""
    monkeypatch.setattr("app.core.config.settings.UPLOADS_PATH", str(tmp_path))
    (tmp_path / "ANGjdJ8_gmail").write_bytes(b"de otro mensaje")
    extra = {"attachments": [{"filename": "receta.pdf", "attachment_id": "ANGjdJ8_gmail"}]}

    assert archivos_del_mensaje(None, None, extra) == []
    _purgar_fichas_locales(extra)
    assert (tmp_path / "ANGjdJ8_gmail").exists()


def test_una_ficha_con_un_nombre_manipulado_no_borra_fuera_de_uploads(monkeypatch, tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr("app.core.config.settings.UPLOADS_PATH", str(uploads))
    fuera = tmp_path / "fuera.txt"
    fuera.write_bytes(b"no es un adjunto")
    extra = {
        "attachments": [
            {"filename": "x", "attachment_id": "../fuera.txt", "local": True},
            {"filename": "y", "attachment_id": str(fuera), "local": True},
        ]
    }

    assert archivos_del_mensaje(None, None, extra) == []
    _purgar_fichas_locales(extra)
    assert fuera.exists(), "una ficha manipulada borró un fichero fuera de uploads/"
