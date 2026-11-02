"""Recordatorios y seguimientos: lo que ya se le mandó a cada cita (SPEC-18).

Los recordatorios salen solos, desde una tarea del beat, y lo peor que puede
pasar es mandarle dos veces el mismo aviso a un paciente. Cada envío se apunta
EN LA FILA antes de salir (`UPDATE … WHERE … IS NULL`), así que dos workers o un
reintento no pueden mandarlo dos veces:

- `appointments.recordatorio_enviado_at`: el recordatorio antes de la cita.
- `appointments.inasistencia_enviada_at`: el «te esperábamos» tras marcarla
  como «No asistió».
- `appointments.avisos`: cómo acabó cada aviso, `{"recordatorio": {"resultado":
  "enviado", "canal": "whatsapp"}}` o `"omitido"` con su motivo. Las fechas de
  arriba se apuntan también cuando no se pudo mandar (para no reintentarlo cada
  10 minutos), así que sin esto la Agenda no distinguiría «enviado» de «no se
  le pudo avisar».
- `appointments.confirmada_at`: el paciente confirmó (botón «Confirmo» o lo
  dijo con sus palabras y Sofía usó `confirmar_cita`). Es lo que la Agenda
  enseña como «✓ Confirmó».
- `conversations.seguimiento_enviado_at`: el seguimiento a quien preguntó y no
  agendó (tramo 3). Uno por conversación.

Además, `confirmar_cita` entra en las listas explícitas de herramientas de los
agentes que ya podían agendar, igual que la 0060 hizo con las de la agenda: sin
eso, en una instalación que ya existe Sofía no podría confirmar.

Revision ID: 0065_recordatorios
Revises: 0064_agente_en_n8n
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0065_recordatorios"
down_revision = "0064_agente_en_n8n"
branch_labels = None
depends_on = None

_COLUMNAS_CITA = ("recordatorio_enviado_at", "inasistencia_enviada_at", "confirmada_at")


def upgrade() -> None:
    for columna in _COLUMNAS_CITA:
        op.add_column("appointments", sa.Column(columna, sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "appointments",
        sa.Column("avisos", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "conversations",
        sa.Column("seguimiento_enviado_at", sa.DateTime(timezone=True), nullable=True),
    )
    # El barrido de recordatorios solo mira citas activas que aún no lo
    # recibieron: un índice parcial lo deja en unas pocas filas aunque la
    # agenda tenga años de historia.
    op.execute(
        "CREATE INDEX ix_appointments_recordatorio_pendiente ON appointments (starts_at) "
        "WHERE recordatorio_enviado_at IS NULL AND estado IN ('agendada', 'reagendada')"
    )
    op.execute(
        sa.text(
            "UPDATE agents SET tools_enabled = tools_enabled || jsonb_build_array('confirmar_cita') "
            "WHERE jsonb_typeof(tools_enabled) = 'array' "
            "AND tools_enabled @> jsonb_build_array('agendar_cita') "
            "AND NOT tools_enabled @> jsonb_build_array('confirmar_cita')"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE agents SET tools_enabled = tools_enabled - 'confirmar_cita' "
            "WHERE jsonb_typeof(tools_enabled) = 'array' "
            "AND tools_enabled @> jsonb_build_array('confirmar_cita')"
        )
    )
    op.execute("DROP INDEX IF EXISTS ix_appointments_recordatorio_pendiente")
    op.drop_column("conversations", "seguimiento_enviado_at")
    op.drop_column("appointments", "avisos")
    for columna in reversed(_COLUMNAS_CITA):
        op.drop_column("appointments", columna)
