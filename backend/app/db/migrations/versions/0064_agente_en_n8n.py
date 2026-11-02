"""«Sofía en n8n»: un agente de texto puede pensar en un flujo de n8n (Fase 11, SPEC-07).

Hasta ahora el cerebro de cada agente era el del CRM: su prompt, su modelo y
sus herramientas. Quien ya tiene su agente armado en n8n no podía usar el
inbox, la ficha y los canales del CRM sin rehacerlo aquí. Con `motor = 'n8n'`
el CRM sigue recibiendo, guardando y mostrando todo, pero cada turno se lo pasa
al webhook de n8n y manda lo que n8n conteste.

Solo agentes de texto: la voz va por un WebSocket en tiempo real con Retell y
n8n no aguanta esa latencia (lo impide un CHECK). El secreto, como el de las
herramientas de n8n, va cifrado (`bytea`, Fernet).

Revision ID: 0064_agente_en_n8n
Revises: 0063_herramientas_n8n
"""
import sqlalchemy as sa
from alembic import op

revision = "0064_agente_en_n8n"
down_revision = "0063_herramientas_n8n"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("motor", sa.String(10), nullable=False, server_default="crm"))
    op.add_column("agents", sa.Column("n8n_url", sa.String(500), nullable=True))
    op.add_column("agents", sa.Column("n8n_secreto", sa.LargeBinary, nullable=True))
    op.add_column("agents", sa.Column("n8n_espera_s", sa.Integer, nullable=False, server_default="25"))
    op.create_check_constraint("ck_agents_motor", "agents", "motor IN ('crm', 'n8n')")
    op.create_check_constraint(
        "ck_agents_motor_solo_texto", "agents", "motor = 'crm' OR COALESCE(kind, 'text') = 'text'"
    )
    op.create_check_constraint("ck_agents_n8n_espera", "agents", "n8n_espera_s BETWEEN 5 AND 45")


def downgrade() -> None:
    op.drop_constraint("ck_agents_n8n_espera", "agents", type_="check")
    op.drop_constraint("ck_agents_motor_solo_texto", "agents", type_="check")
    op.drop_constraint("ck_agents_motor", "agents", type_="check")
    op.drop_column("agents", "n8n_espera_s")
    op.drop_column("agents", "n8n_secreto")
    op.drop_column("agents", "n8n_url")
    op.drop_column("agents", "motor")
