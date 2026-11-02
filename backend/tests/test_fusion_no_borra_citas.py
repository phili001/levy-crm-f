"""Fusionar dos fichas no puede llevarse por delante las citas.

QUÉ PASABA (0.8.0). La fusión movía conversaciones, notas y actividad de la
ficha que desaparece a la que se queda, pero no las citas. Como
`appointments.contact_id` es ON DELETE CASCADE (migración 0060), al borrar la
ficha de origen sus citas se borraban con ella, sin aviso, y su copia en Google
se quedaba allí sin cita en el CRM.

Pasaba por los dos caminos que fusionan:
  - el botón Fusionar de Contactos (`POST /contacts/{id}/merge`);
  - la fusión automática de WhatsApp, cuando el mismo cliente tenía una ficha
    por su identificador de Meta y otra por su teléfono.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

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


async def _cita_para(contact_id: uuid.UUID) -> uuid.UUID:
    """Una cita activa en un recurso propio de la prueba: la agenda es global y
    la exclusión de citas encimadas va por recurso."""
    from app.db.session import db_session
    from app.models.appointment import Appointment

    inicio = datetime(2031, 3, 3, 10, 0, tzinfo=timezone.utc)
    async with db_session() as db:
        cita = Appointment(
            contact_id=contact_id,
            servicio="Limpieza dental",
            starts_at=inicio,
            ends_at=inicio + timedelta(minutes=45),
            recurso=f"prueba-{uuid.uuid4().hex[:12]}",
        )
        db.add(cita)
        await db.commit()
        return cita.id


async def _dueno_de_la_cita(cita_id: uuid.UUID) -> uuid.UUID | None:
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.appointment import Appointment

    async with db_session() as db:
        return (
            await db.execute(select(Appointment.contact_id).where(Appointment.id == cita_id))
        ).scalar_one_or_none()


async def _borrar(*contact_ids) -> None:
    from sqlalchemy import delete

    from app.db.session import db_session
    from app.models.contact import Contact

    async with db_session() as db:
        await db.execute(delete(Contact).where(Contact.id.in_(list(contact_ids))))
        await db.commit()


@needs_db
@pytest.mark.asyncio
async def test_fusionar_a_mano_conserva_las_citas_de_la_ficha_que_desaparece():
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.contact import Contact, ContactOrigen
    from app.services.contact_merge import fusionar_contactos

    async with db_session() as db:
        destino = Contact(telefono=f"+52998{uuid.uuid4().int % 10**7:07d}", origen=ContactOrigen.whatsapp)
        origen = Contact(telefono=f"ig:{uuid.uuid4().hex[:16]}", origen=ContactOrigen.instagram)
        db.add_all([destino, origen])
        await db.commit()
        destino_id, origen_id = destino.id, origen.id

    cita_id = await _cita_para(origen_id)
    try:
        async with db_session() as db:
            d = (await db.execute(select(Contact).where(Contact.id == destino_id))).scalar_one()
            o = (await db.execute(select(Contact).where(Contact.id == origen_id))).scalar_one()
            resumen = await fusionar_contactos(db, d, o)
            await db.commit()

        assert await _dueno_de_la_cita(cita_id) == destino_id, (
            "la cita se borró con la ficha que desaparece"
        )
        assert resumen["citas_movidas"] == 1
    finally:
        await _borrar(destino_id, origen_id)


@needs_db
@pytest.mark.asyncio
async def test_la_fusion_automatica_de_whatsapp_no_borra_citas():
    """El cliente agendó cuando Meta solo mandaba su identificador; luego Meta
    revela el teléfono, que ya tenía ficha propia, y las dos se unen solas."""
    from app.providers.whatsapp.base import WA_USER_PREFIX, IncomingMessage
    from app.services.conversation import store_incoming
    from tests.test_contacto_bsuid import _bsuid, _ficha_de, _telefono

    async def _entra(telefono, bsuid, texto):
        return await store_incoming(
            IncomingMessage(
                provider_message_id=f"wamid.{uuid.uuid4().hex}",
                from_phone=telefono or f"{WA_USER_PREFIX}{bsuid}",
                to_phone="+34900000000",
                message_type="text",
                text=texto,
                from_user_id=bsuid,
            )
        )

    bsuid, telefono = _bsuid(), _telefono()
    ficha_usuario = await _ficha_de(await _entra(None, bsuid, "quiero cita"))
    cita_id = await _cita_para(ficha_usuario.id)
    ficha_telefono = await _ficha_de(await _entra(telefono, None, "hola"))
    assert ficha_usuario.id != ficha_telefono.id

    superviviente = await _ficha_de(await _entra(telefono, bsuid, "soy yo"))
    try:
        assert superviviente.id == ficha_telefono.id
        assert await _dueno_de_la_cita(cita_id) == superviviente.id, (
            "la fusión automática borró la cita del cliente"
        )
    finally:
        await _borrar(ficha_usuario.id, ficha_telefono.id)
