"""Propuesta «¿Es el mismo paciente?» (tabla `merge_suggestions`, Fase 10.1).

La deja `services/identity_linking.py` cuando algo que dijo la persona (su
teléfono por Instagram, el correo desde el que escribe) coincide con otra
ficha. Nunca fusiona sola: la fusión la decide un administrador desde el panel.

- `contact_id`: la ficha que desaparecería al fusionar (la del canal que no da
  el teléfono).
- `candidate_id`: la que se queda (la que ya tiene ese teléfono o ese correo).
- `dato`: el teléfono o el correo que coincidió, para enseñarlo en el aviso.
- Una `descartada` se guarda para no volver a proponer el mismo par. El índice
  único sobre el par sin orden vive en la migración 0061.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

MOTIVO_TELEFONO = "telefono"
MOTIVO_EMAIL = "email"

ESTADO_PENDIENTE = "pendiente"
ESTADO_DESCARTADA = "descartada"


class MergeSuggestion(Base):
    __tablename__ = "merge_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    motivo: Mapped[str] = mapped_column(String(20), nullable=False)
    dato: Mapped[str] = mapped_column(String(320), nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default=ESTADO_PENDIENTE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resuelta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
