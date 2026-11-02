"""Horario ofrecido a una conversación (tabla `offered_slots`, Fase 9).

Es la memoria de lo que `consultar_disponibilidad` le enseñó al modelo. Sin
fila aquí (o con más de 30 minutos) `agendar_cita` no reserva: así el modelo
no puede confirmar una hora que se inventó. Se compara por instante exacto,
sin tolerancia: "las 10" con otro desfase pero el mismo instante vale; un
minuto de diferencia, no.

Se acumulan dentro de la ventana: si la persona pregunta por el martes, luego
por el miércoles y al final dice "mejor el martes a las 10", ese martes sigue
valiendo. La tarea `purge_offered_slots` borra las de más de un día.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class OfferedSlot(Base):
    __tablename__ = "offered_slots"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    offered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
