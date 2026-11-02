"""Ayudas para las pruebas de la agenda propia (Fase 9).

Un horario fijo (el del perfil dental: L-V de 8 a 17 en Cancún), un "ahora"
fijo y una persona con su conversación en la base de datos. La agenda es una
sola para toda la instalación, así que cada prueba borra lo suyo al terminar:
el contacto se lleva sus citas y sus horarios ofrecidos en cascada, y los
bloqueos se borran a mano.

`fijar_agenda` deja la copia en Google apagada y sin encolar nada: la prueba
de sincronización la enciende a propósito.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

from app.agents.tools.schedule_config import build_config
from app.models.conversation import ConversationCanal

HORARIO = build_config("America/Cancun", "1,2,3,4,5", "08:00-17:00", "", "30", "0")
# Lunes 28 de septiembre de 2026, 9:00 en Cancún.
LUNES_9 = datetime(2026, 9, 28, 9, 0, tzinfo=HORARIO.tz)
MARTES = LUNES_9.date() + timedelta(days=1)


def a_las(dia, hora: int, minuto: int = 0) -> datetime:
    return datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=HORARIO.tz)


def fijar_agenda(monkeypatch, *, ahora: datetime = LUNES_9, horario=HORARIO, servicios=None):
    from app.agents.tools import calendar as cal
    from app.agents.tools import contact_lookup, schedule_config
    from app.api import agenda as api_agenda
    from app.services import agenda, calendar_sync
    from app.services.agenda import Servicio

    async def _horario():
        return horario

    for modulo in (schedule_config, cal, contact_lookup, api_agenda):
        monkeypatch.setattr(modulo, "get_schedule_config", _horario)
        monkeypatch.setattr(modulo, "now_in", lambda _tz: ahora)

    async def _catalogo():
        return servicios or [
            Servicio("Cita de valoración", 30),
            Servicio("Limpieza dental", 45),
            Servicio("Implante dental", 120),
        ]

    async def _apagada():
        return calendar_sync.MODO_OFF

    monkeypatch.setattr(agenda, "servicios", _catalogo)
    monkeypatch.setattr(calendar_sync, "modo", _apagada)
    monkeypatch.setattr(calendar_sync, "encolar", lambda _ids: None)


async def persona(
    canal: ConversationCanal = ConversationCanal.whatsapp, nombre: str = "Paciente de prueba"
) -> tuple[uuid.UUID, uuid.UUID]:
    """Un contacto con una conversación abierta. Devuelve (conversación, contacto)."""
    from app.db.session import db_session
    from app.models.contact import Contact, ContactOrigen
    from app.models.conversation import Conversation

    telefono = f"+52998{random.randint(1000000, 9999999)}"
    async with db_session() as db:
        contacto = Contact(telefono=telefono, nombre=nombre, origen=ContactOrigen.whatsapp)
        db.add(contacto)
        await db.flush()
        conv = Conversation(contact_id=contacto.id, canal=canal, session_id=telefono)
        db.add(conv)
        await db.commit()
        return conv.id, contacto.id


def ctx(conv_id: uuid.UUID, *, voz: bool = False) -> dict:
    datos = {"conversation_id": str(conv_id)}
    if voz:
        datos["channel"] = ConversationCanal.retell_voice.value
    return datos


async def citas_de(contact_id: uuid.UUID) -> list:
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.appointment import Appointment

    async with db_session() as db:
        return list(
            (
                await db.execute(
                    select(Appointment)
                    .where(Appointment.contact_id == contact_id)
                    .order_by(Appointment.created_at)
                )
            ).scalars().all()
        )


async def bloquear(inicio: datetime, fin: datetime, motivo: str = "prueba") -> uuid.UUID:
    from app.db.session import db_session
    from app.models.schedule_block import ScheduleBlock

    async with db_session() as db:
        bloqueo = ScheduleBlock(starts_at=inicio, ends_at=fin, motivo=motivo)
        db.add(bloqueo)
        await db.commit()
        return bloqueo.id


async def limpiar(contact_ids: list[uuid.UUID], bloqueos: list[uuid.UUID] | None = None) -> None:
    from sqlalchemy import delete

    from app.db.session import db_session
    from app.models.contact import Contact
    from app.models.schedule_block import ScheduleBlock

    async with db_session() as db:
        if contact_ids:
            await db.execute(delete(Contact).where(Contact.id.in_(contact_ids)))
        if bloqueos:
            await db.execute(delete(ScheduleBlock).where(ScheduleBlock.id.in_(bloqueos)))
        await db.commit()
