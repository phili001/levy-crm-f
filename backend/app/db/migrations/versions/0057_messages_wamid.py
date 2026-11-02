"""Índice por el wamid de WhatsApp en los mensajes.

Por qué: hasta ahora el identificador que guardábamos de cada mensaje de
WhatsApp era el que devolviera la API de YCloud (`id`), que es SUYO. El `wamid`
es el de Meta y es el que viaja en los estados de entrega, en los ecos de la
app WhatsApp Business y en el historial. Con solo el id de YCloud, un ✓✓ o un
eco del celular no casaban con el mensaje que ya teníamos y acababan
duplicando la conversación.

Se guarda dentro de `metadata` (no hay columna nueva: los mensajes de otros
canales no tienen wamid y una columna suelta estaría vacía en la mayoría de las
filas). El índice es PARCIAL, solo sobre las filas que lo tienen, para no pesar
en una tabla que crece con cada mensaje de todos los canales.

Revision ID: 0057_messages_wamid
Revises: 0056_feature_flags
"""
from alembic import op

revision = "0057_messages_wamid"
down_revision = "0056_feature_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_messages_wamid
        ON messages ((metadata->>'wamid'))
        WHERE metadata->>'wamid' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_wamid")
