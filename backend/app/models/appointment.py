"""Cita de la agenda propia (tabla `appointments`, Fase 9).

La cita vive en el CRM: Google Calendar, si está conectado, solo recibe una
copia (`google_event_id`, `google_sync_pendiente`; ver `tasks/calendar_sync.py`).

Lo que protege la agenda no está en este modelo sino en la migración
`0060_appointments`: la restricción de exclusión `ex_appointments_sin_encimar`
rechaza dos citas activas (agendada o reagendada) que se crucen en el mismo
recurso. Una cita cancelada libera su hueco sin borrarse: queda para la
historia de la ficha.

- `contact_id` con ON DELETE CASCADE: al borrar el contacto (RGPD) se van sus
  citas.
- `conversation_id` con ON DELETE SET NULL: la conversación se puede purgar y
  la cita sigue en pie.
- `notas` cifradas en reposo, igual que las notas del contacto.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.encrypted_type import EncryptedText
from app.db.session import Base
from app.models.conversation import ConversationCanal, conv_canal_enum

ESTADO_AGENDADA = "agendada"
ESTADO_CANCELADA = "cancelada"
# La cita nueva que sale de mover otra. La vieja queda como `cancelada`.
ESTADO_REAGENDADA = "reagendada"
ESTADO_COMPLETADA = "completada"
ESTADO_NO_ASISTIO = "no_asistio"

# Las que ocupan hueco. Tiene que coincidir con el WHERE de la exclusión.
ESTADOS_ACTIVOS = (ESTADO_AGENDADA, ESTADO_REAGENDADA)

CREADA_POR_AGENTE = "agente"
CREADA_POR_OPERADOR = "operador"

RECURSO_PRINCIPAL = "principal"


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    canal: Mapped[ConversationCanal | None] = mapped_column(conv_canal_enum)
    servicio: Mapped[str] = mapped_column(String(120), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default=ESTADO_AGENDADA)
    notas: Mapped[str | None] = mapped_column(EncryptedText)
    creada_por: Mapped[str] = mapped_column(String(10), nullable=False, default=CREADA_POR_AGENTE)
    google_event_id: Mapped[str | None] = mapped_column(String(200))
    google_sync_pendiente: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    recurso: Mapped[str] = mapped_column(String(60), nullable=False, default=RECURSO_PRINCIPAL)
    # Recordatorios y seguimientos (SPEC-18, migración 0065). Se apuntan ANTES de
    # mandar, con un UPDATE condicionado: es lo que impide un aviso doble.
    recordatorio_enviado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    inasistencia_enviada_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmada_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Cómo acabó cada aviso: {"recordatorio": {"resultado": "enviado"|"omitido",
    # "canal"|"motivo": …}}. Las fechas de arriba también se apuntan cuando no
    # se pudo mandar; esto es lo que dice cuál de las dos fue.
    avisos: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def activa(self) -> bool:
        return self.estado in ESTADOS_ACTIVOS
