"""Unificación de fichas: teléfono declarado, identidad de canal y propuestas de fusión — Fase 10.1

Revision ID: 0061_unificacion_fichas
Revises: 0060_appointments
Create Date: 2026-09-26

Por qué: el mismo paciente escribe por WhatsApp, pregunta por Instagram y llama
por teléfono, y el CRM le abría una ficha por canal. Instagram, Messenger, el
chat web y el correo no dan el teléfono, así que no había nada con qué cruzarlas.

- `contacts.telefono_declarado`: el teléfono que la persona DICE en un canal que
  no lo da (lo guarda Sofía con `crear_actualizar_contacto`). No es la llave:
  nadie lo verifica y cualquiera puede escribir el de otra persona. Solo sirve
  para PROPONER al equipo unir dos fichas; la fusión la decide una persona.
- `contacts.identidad_canal`: cuando una ficha de Instagram, Messenger, chat web
  o correo se une a otra que tiene teléfono, la llave pasa a ser el teléfono y
  el identificador del canal (`ig:…`, `fb:…`, `web:…`, `email:…`) se guarda
  aquí. Sin esto, el siguiente mensaje por ese canal no encontraba la ficha
  unida y volvía a abrir la duplicada. Único parcial, igual que `wa_user_id`
  (0053): no indexa las fichas que no lo tienen. Uno solo por ficha a propósito
  (sin tabla de identidades): para el curso basta teléfono + una cuenta más, y
  una fusión que fuera a perder una segunda se niega (`contact_merge.py`).
- `merge_suggestions`: la propuesta «¿Es el mismo paciente?». `contact_id` es la
  ficha que desaparecería y `candidate_id` la que se queda. Una descartada se
  conserva para no volver a proponer el mismo par; el índice único va sobre el
  par SIN orden, así que tampoco se cuela la propuesta al revés. Las dos claves
  ajenas son CASCADE: fusionar o borrar una de las fichas se lleva la propuesta,
  que ya no tiene sentido.

Bajar borra la tabla y las dos columnas: las fichas ya fusionadas pierden su
identidad de canal y el siguiente mensaje de Instagram de esa persona abriría
otra ficha. Solo contra una base de datos de pruebas.
"""
import sqlalchemy as sa
from alembic import op

revision = "0061_unificacion_fichas"
down_revision = "0060_appointments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("telefono_declarado", sa.String(20), nullable=True))
    op.add_column("contacts", sa.Column("identidad_canal", sa.String(320), nullable=True))
    op.execute(
        "CREATE INDEX ix_contacts_telefono_declarado ON contacts (telefono_declarado) "
        "WHERE telefono_declarado IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_contacts_identidad_canal ON contacts (identidad_canal) "
        "WHERE identidad_canal IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE merge_suggestions (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          contact_id    UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
          candidate_id  UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
          motivo        VARCHAR(20) NOT NULL,
          dato          VARCHAR(320) NOT NULL,
          estado        VARCHAR(20) NOT NULL DEFAULT 'pendiente',
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          resuelta_at   TIMESTAMPTZ NULL,
          CONSTRAINT ck_merge_suggestions_motivo CHECK (motivo IN ('telefono', 'email')),
          CONSTRAINT ck_merge_suggestions_estado CHECK (estado IN ('pendiente', 'descartada')),
          CONSTRAINT ck_merge_suggestions_dos_fichas CHECK (contact_id <> candidate_id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_merge_suggestions_par ON merge_suggestions "
        "(LEAST(contact_id, candidate_id), GREATEST(contact_id, candidate_id))"
    )
    op.execute("CREATE INDEX ix_merge_suggestions_contact_id ON merge_suggestions (contact_id)")
    op.execute("CREATE INDEX ix_merge_suggestions_candidate_id ON merge_suggestions (candidate_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS merge_suggestions")
    op.execute("DROP INDEX IF EXISTS ux_contacts_identidad_canal")
    op.execute("DROP INDEX IF EXISTS ix_contacts_telefono_declarado")
    op.drop_column("contacts", "identidad_canal")
    op.drop_column("contacts", "telefono_declarado")
