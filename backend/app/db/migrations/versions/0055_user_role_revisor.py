"""Rol «revisor»: cuenta de solo mirar para la revisión de la app en Meta.

Meta pide credenciales del panel para que su revisor pueda comprobar lo que
hace la app. Darle admin significaría entregarle el panel entero —claves,
agentes, contactos, copias— durante los días que dure la revisión. Este rol
existe para no tener que hacer eso: ve la bandeja de Instagram, responde en
ella y ve de qué cuenta de Instagram se trata. Nada más.

Qué puede tocar exactamente NO se decide aquí: está en la lista blanca de
`app/api/deps.py`, que es por donde pasan todas las peticiones con sesión.

`user_role` es un ENUM nativo de Postgres, así que el valor hay que añadirlo al
tipo. `IF NOT EXISTS` lo hace repetible sin romper.

Bajar no quita el valor: Postgres no sabe borrar valores de un ENUM, y aunque
supiera, quitarlo con un usuario que lo tuviera dejaría filas ilegibles. Se
queda, que no molesta a nadie.

Revision ID: 0055_user_role_revisor
Revises: 0054_channel_secrets_encrypted
"""
import sqlalchemy as sa
from alembic import op

revision = "0055_user_role_revisor"
down_revision = "0054_channel_secrets_encrypted"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'revisor'"))


def downgrade() -> None:
    pass
