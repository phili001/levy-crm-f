"""Bandera para obligar a cambiar la contraseña inicial.

Por qué no vale `password_changed_at IS NULL`: ese campo también está vacío en
los usuarios que crea un administrador desde el panel, y en los de cualquier
instalación anterior a esta migración. Usarlo como "tiene que cambiarla"
dejaría fuera de la app a gente que no tiene nada que cambiar.

La bandera la pone SOLO el sembrado, al crear el administrador inicial con la
contraseña que viene en las variables de entorno. Esa contraseña la ha visto
todo el que tenga acceso al panel de despliegue, así que la instalación no
puede quedarse con ella para siempre. Se apaga al cambiarla.

Las instalaciones que ya existen quedan en `false`: no se echa de su panel a
nadie por desplegar una versión nueva.

Revision ID: 0058_debe_cambiar_password
Revises: 0057_messages_wamid
"""
import sqlalchemy as sa
from alembic import op

revision = "0058_debe_cambiar_password"
down_revision = "0057_messages_wamid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "debe_cambiar_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "debe_cambiar_password")
