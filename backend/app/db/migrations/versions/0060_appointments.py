"""Agenda propia: citas, bloqueos y horarios ofrecidos — Fase 9.1

Revision ID: 0060_appointments
Revises: 0059_messenger_channel
Create Date: 2026-09-25

Por qué: hasta ahora `agendar_cita` escribía directo en Google Calendar. Sin
Google conectado, Sofía no podía agendar nada; y con Google, la cita no quedaba
ligada ni al contacto ni a la conversación, así que el CRM no sabía quién venía
ni cuándo. Desde aquí la cita vive en el CRM, que es la fuente de verdad, y
Google pasa a ser una copia opcional.

- `appointments`: la restricción de exclusión (extensión `btree_gist`) impide
  dos citas activas encimadas en el mismo recurso aunque lleguen a la vez por
  dos canales. La segunda escritura la rechaza Postgres, no el código: no hay
  carrera que ganar. `tstzrange` es [inicio, fin), así que una cita que empieza
  justo cuando termina otra sí cabe. `notas` va cifrada (BYTEA, como las notas
  del contacto): ahí cabe "le duele una muela". `google_sync_pendiente` marca
  las citas cuya copia en Google falta por crear, mover o borrar; la tarea de
  sincronización la baja cuando Google contesta.
- `schedule_blocks`: tramos cerrados a mano (comida, vacaciones, un curso).
- `offered_slots`: lo que se le ofreció a cada conversación. `agendar_cita` y
  `reagendar_cita` solo aceptan un horario ofrecido en los últimos 30 minutos:
  el modelo no puede inventarse una hora.

Además, a los agentes que ya agendan se les activan `reagendar_cita` y
`cancelar_cita`, y a los que ya actualizan la ficha, `actualizar_estado_lead`.
Sin esto, en una instalación que ya existía Sofía seguiría sin poder mover ni
cancelar una cita hasta que alguien marcara las casillas en Agentes.

Bajar borra las tres tablas (y las citas con ellas), quita esas herramientas
de los agentes y retira la extensión. Solo contra una base de datos de pruebas.
"""
import sqlalchemy as sa
from alembic import op

revision = "0060_appointments"
down_revision = "0059_messenger_channel"
branch_labels = None
depends_on = None

# (herramienta que ya tiene el agente, herramientas nuevas que la acompañan)
_HERRAMIENTAS_NUEVAS = (
    ("agendar_cita", ("reagendar_cita", "cancelar_cita")),
    ("crear_actualizar_contacto", ("actualizar_estado_lead",)),
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        CREATE TABLE appointments (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          contact_id            UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
          conversation_id       UUID NULL REFERENCES conversations(id) ON DELETE SET NULL,
          canal                 conversation_canal NULL,
          servicio              VARCHAR(120) NOT NULL,
          starts_at             TIMESTAMPTZ NOT NULL,
          ends_at               TIMESTAMPTZ NOT NULL,
          estado                VARCHAR(20) NOT NULL DEFAULT 'agendada',
          notas                 BYTEA NULL,
          creada_por            VARCHAR(10) NOT NULL DEFAULT 'agente',
          google_event_id       VARCHAR(200) NULL,
          google_sync_pendiente BOOLEAN NOT NULL DEFAULT false,
          recurso               VARCHAR(60) NOT NULL DEFAULT 'principal',
          created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_appointments_fin_despues_del_inicio CHECK (ends_at > starts_at),
          CONSTRAINT ck_appointments_estado CHECK (
            estado IN ('agendada', 'cancelada', 'reagendada', 'completada', 'no_asistio')
          ),
          CONSTRAINT ck_appointments_creada_por CHECK (creada_por IN ('agente', 'operador')),
          CONSTRAINT ex_appointments_sin_encimar EXCLUDE USING gist (
            recurso WITH =, tstzrange(starts_at, ends_at) WITH &&
          ) WHERE (estado IN ('agendada', 'reagendada'))
        )
        """
    )
    op.execute("CREATE INDEX ix_appointments_contact ON appointments (contact_id)")
    op.execute("CREATE INDEX ix_appointments_starts ON appointments (starts_at)")

    op.execute(
        """
        CREATE TABLE schedule_blocks (
          id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          starts_at  TIMESTAMPTZ NOT NULL,
          ends_at    TIMESTAMPTZ NOT NULL,
          motivo     VARCHAR(200) NULL,
          recurso    VARCHAR(60) NOT NULL DEFAULT 'principal',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_schedule_blocks_fin_despues_del_inicio CHECK (ends_at > starts_at)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE offered_slots (
          conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          starts_at       TIMESTAMPTZ NOT NULL,
          offered_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (conversation_id, starts_at)
        )
        """
    )

    for base, nuevas in _HERRAMIENTAS_NUEVAS:
        for nueva in nuevas:
            op.execute(
                sa.text(
                    "UPDATE agents SET tools_enabled = tools_enabled || jsonb_build_array(CAST(:nueva AS text)) "
                    "WHERE jsonb_typeof(tools_enabled) = 'array' "
                    "AND tools_enabled @> jsonb_build_array(CAST(:base AS text)) "
                    "AND NOT tools_enabled @> jsonb_build_array(CAST(:nueva AS text))"
                ).bindparams(base=base, nueva=nueva)
            )


def downgrade() -> None:
    for _base, nuevas in _HERRAMIENTAS_NUEVAS:
        for nueva in nuevas:
            op.execute(
                sa.text(
                    "UPDATE agents SET tools_enabled = tools_enabled - CAST(:nueva AS text) "
                    "WHERE jsonb_typeof(tools_enabled) = 'array' "
                    "AND tools_enabled @> jsonb_build_array(CAST(:nueva AS text))"
                ).bindparams(nueva=nueva)
            )
    op.execute("DROP TABLE IF EXISTS offered_slots")
    op.execute("DROP TABLE IF EXISTS schedule_blocks")
    op.execute("DROP TABLE IF EXISTS appointments")
    # Sin CASCADE: si algo más dependiera de la extensión, mejor que falle a
    # que se lo lleve por delante en silencio.
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
