"""Eventos de Google Calendar por borrar (tabla `calendar_event_deletions`, SPEC-21).

Al borrar un contacto, sus citas caen por el cascade y con ellas la única pista
de su copia en Google, que lleva «servicio · nombre». Antes del cascade, el
borrado apunta aquí cada evento; la tarea `calendar_sync.borrar_eventos` y la
barrida del beat lo borran en Google aunque esté caído o el permiso revocado.

No guarda ningún dato personal: el id del evento es el de la cita en
hexadecimal, y el del contacto borrado solo sirve para dejar en la auditoría
cuándo terminó su borrado en Google.

- `borrado_at`: Google confirmó el borrado. Las filas de un contacto se quitan
  todas juntas cuando ya no le queda ninguna pendiente (y entonces se audita).
- `intentos` y `ultimo_error`: lo que se ve si Google no deja borrarlo.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CalendarEventDeletion(Base):
    __tablename__ = "calendar_event_deletions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    # Sin FK: el contacto ya no existe cuando la fila se procesa.
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    intentos: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ultimo_error: Mapped[str | None] = mapped_column(String(300))
    borrado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
