"""Eventos de Google Calendar por borrar tras borrar un contacto (SPEC-21, A4).

Borrar un contacto se llevaba sus citas por el cascade, pero no su copia en
Google Calendar, que lleva «servicio · nombre». Y una vez borradas las filas ya
no había desde dónde terminar el trabajo: la barrida solo recorre citas que
existen. El informe del borrado, además, lo daba por completo.

`calendar_event_deletions` es la cola que sobrevive al cascade. El borrado
apunta cada evento en la misma transacción, y la tarea y la barrida del beat
lo borran en Google aunque esté caído o el permiso revocado. Solo guarda el id
del evento (el de la cita en hexadecimal) y el del contacto borrado: ningún
dato personal.

- `event_id` único: apuntar dos veces el mismo evento (una copia que estaba en
  marcha al borrar) reabre la fila en vez de duplicarla.
- `borrado_at`: Google confirmó el borrado. Las filas de un contacto se quitan
  juntas cuando no le queda ninguna, y se audita.

Revision ID: 0066_eventos_google_por_borrar
Revises: 0065_recordatorios
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0066_eventos_google_por_borrar"
down_revision = "0065_recordatorios"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calendar_event_deletions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", sa.String(200), nullable=False, unique=True),
        sa.Column("contact_id", UUID(as_uuid=True), nullable=False),
        sa.Column("intentos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ultimo_error", sa.String(300), nullable=True),
        sa.Column("borrado_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_calendar_event_deletions_contact_id", "calendar_event_deletions", ["contact_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_calendar_event_deletions_contact_id", table_name="calendar_event_deletions")
    op.drop_table("calendar_event_deletions")
