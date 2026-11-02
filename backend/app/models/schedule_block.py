"""Bloqueo de la agenda (tabla `schedule_blocks`, Fase 9).

Un tramo en el que no se dan citas aunque caiga dentro del horario: la hora
de comer, unas vacaciones, un curso. Los huecos lo descuentan y, en una
llamada, tampoco se pasa a recepción durante un bloqueo (se toma el recado).
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.appointment import RECURSO_PRINCIPAL


class ScheduleBlock(Base):
    __tablename__ = "schedule_blocks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    motivo: Mapped[str | None] = mapped_column(String(200))
    recurso: Mapped[str] = mapped_column(String(60), nullable=False, default=RECURSO_PRINCIPAL)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
