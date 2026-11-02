"""canal Messenger (páginas de Facebook) — Fase 7

Revision ID: 0059_messenger_channel
Revises: 0058_debe_cambiar_password
Create Date: 2026-09-24

Por qué: la Fase 7 conecta Instagram y Messenger a través de Zernio, que habla
con Meta como una sola app ya aprobada; la clínica solo hace OAuth y se ahorra
el App Review. Instagram ya tenía su valor (`instagram_dm`), pero los mensajes
a la página de Facebook no cabían en ningún canal: sin su propio valor se
mezclarían con Instagram en el inbox, en las pausas por canal y en la
resolución del agente.

Añade 'messenger' a los tres enums (channel_type, conversation_canal,
contact_origen). El identificador del cliente llega como `fb:<psid>`, igual que
Instagram usa `ig:<psid>`.

Lo propio de Zernio (API key, secreto del webhook, cuenta, perfil y la clave
`transport` = "meta" | "zernio") vive en `channels.config` y en
`credentials_encrypted`, sin DDL: ver `SECRET_KEYS` en
`app/services/channel_secrets.py`.

ALTER TYPE ADD VALUE va con autocommit_block (mismo patrón que 0015).
"""
from alembic import op

revision = "0059_messenger_channel"
down_revision = "0058_debe_cambiar_password"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE channel_type ADD VALUE IF NOT EXISTS 'messenger'")
        op.execute("ALTER TYPE conversation_canal ADD VALUE IF NOT EXISTS 'messenger'")
        op.execute("ALTER TYPE contact_origen ADD VALUE IF NOT EXISTS 'messenger'")


def downgrade() -> None:
    # No-op a propósito. Postgres no sabe quitar un valor de un enum sin
    # recrear el tipo entero y reescribir cada columna que lo usa; y si ya hay
    # filas con 'messenger', recrearlo fallaría o obligaría a borrarlas. El
    # valor sobrante no molesta al código anterior, que simplemente no lo usa.
    # Mismo criterio que 0010 y 0015.
    pass
