"""Ayudas para las pruebas de la unificación de fichas (Fase 10.1).

Fichas creadas a mano (sin pasar por la entrada de mensajes) o por la puerta
normal (`store_incoming`), y lo que hace falta para mirarlas y borrarlas. Cada
prueba borra sus fichas al terminar: las propuestas, citas y conversaciones se
van con ellas en cascada.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone


def telefono_mx() -> str:
    return f"+52998{random.randint(1000000, 9999999)}"


def llave_ig() -> str:
    return f"ig:{uuid.uuid4().int % 10**16:016d}"


async def ficha(telefono: str, origen: str = "whatsapp", **campos) -> uuid.UUID:
    from app.db.session import db_session
    from app.models.contact import Contact, ContactOrigen

    async with db_session() as db:
        contacto = Contact(telefono=telefono, origen=ContactOrigen(origen), **campos)
        db.add(contacto)
        await db.commit()
        return contacto.id


async def leer(contact_id: uuid.UUID):
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.contact import Contact

    async with db_session() as db:
        return (
            await db.execute(select(Contact).where(Contact.id == contact_id))
        ).scalar_one_or_none()


async def propuestas_de(contact_id: uuid.UUID) -> list:
    """Todas las propuestas (pendientes o no) en las que está la ficha."""
    from sqlalchemy import or_, select

    from app.db.session import db_session
    from app.models.merge_suggestion import MergeSuggestion

    async with db_session() as db:
        return list(
            (
                await db.execute(
                    select(MergeSuggestion).where(
                        or_(
                            MergeSuggestion.contact_id == contact_id,
                            MergeSuggestion.candidate_id == contact_id,
                        )
                    )
                )
            ).scalars().all()
        )


async def cita_para(contact_id: uuid.UUID) -> uuid.UUID:
    """Una cita por venir, en un recurso propio de la prueba (la exclusión de
    citas encimadas va por recurso y la agenda es de toda la instalación)."""
    from app.db.session import db_session
    from app.models.appointment import Appointment

    inicio = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
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


async def dueno_de_la_cita(cita_id: uuid.UUID) -> uuid.UUID | None:
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.appointment import Appointment

    async with db_session() as db:
        return (
            await db.execute(select(Appointment.contact_id).where(Appointment.id == cita_id))
        ).scalar_one_or_none()


async def entra(from_phone: str, texto: str = "hola", **campos):
    """Un mensaje por la puerta normal. Devuelve el id del mensaje."""
    from app.providers.whatsapp.base import IncomingMessage
    from app.services.conversation import store_incoming

    return await store_incoming(
        IncomingMessage(
            provider_message_id=f"prueba.{uuid.uuid4().hex}",
            from_phone=from_phone,
            to_phone="+529980000000",
            message_type="text",
            text=texto,
            **campos,
        )
    )


async def conversacion_y_ficha(msg_id) -> tuple[uuid.UUID, uuid.UUID]:
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.conversation import Conversation
    from app.models.message import Message

    async with db_session() as db:
        conv_id = (
            await db.execute(select(Message.conversation_id).where(Message.id == msg_id))
        ).scalar_one()
        contact_id = (
            await db.execute(select(Conversation.contact_id).where(Conversation.id == conv_id))
        ).scalar_one()
        return conv_id, contact_id


async def borrar(*contact_ids) -> None:
    from sqlalchemy import delete

    from app.db.session import db_session
    from app.models.contact import Contact

    ids = [c for c in contact_ids if c]
    if not ids:
        return
    async with db_session() as db:
        await db.execute(delete(Contact).where(Contact.id.in_(ids)))
        await db.commit()
