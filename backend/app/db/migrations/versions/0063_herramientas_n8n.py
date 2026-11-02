"""Herramientas de n8n para Sofía: tabla `n8n_tools` (Fase 11, SPEC-06).

Sofía solo podía usar las herramientas que trae el código (buscar en la base de
conocimiento, agendar, derivar…). Para que haga algo más —mandar la foto de un
tratamiento, buscar un pedido en la base de datos de la clínica— había que
programarlo. Con esto, el administrador da de alta en el panel una herramienta
que es un flujo de n8n: nombre y descripción (lo que lee Sofía para decidir
cuándo usarla), los datos que pide y la URL del webhook. El CRM lo llama con
un secreto en una cabecera, que es lo que n8n sabe comprobar sin código.

El secreto va cifrado (`bytea`, Fernet, como el resto de credenciales). El
nombre es único: Sofía las distingue por nombre, y dos con el mismo serían la
misma para el modelo.

Revision ID: 0063_herramientas_n8n
Revises: 0062_indice_hnsw_chunks
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0063_herramientas_n8n"
down_revision = "0062_indice_hnsw_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "n8n_tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("nombre", sa.String(40), nullable=False, unique=True),
        sa.Column("descripcion", sa.Text, nullable=False),
        sa.Column("parametros", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("secreto", sa.LargeBinary, nullable=False),
        sa.Column("accion", sa.String(10), nullable=False, server_default="escritura"),
        sa.Column("espera_s", sa.Integer, nullable=False, server_default="8"),
        sa.Column("datos_contacto", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("activa", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint("accion IN ('lectura', 'escritura')", name="ck_n8n_tools_accion"),
        sa.CheckConstraint("espera_s BETWEEN 1 AND 15", name="ck_n8n_tools_espera"),
    )


def downgrade() -> None:
    op.drop_table("n8n_tools")
