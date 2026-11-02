"""Banderas de módulos: apagar lo que el curso no enseña sin borrar código.

El producto hereda módulos que en el MVP del curso no se muestran (el agente
interno del operador, el flujo en vivo, el envío masivo y las tarifas de
OpenRouter). Borrarlos sería tirar código probado que alguien puede querer;
dejarlos a la vista confunde al que instala siguiendo los videos. Una tabla de
banderas resuelve las dos cosas: el código se queda, la ruta y el menú se
esconden, y quien lo necesite lo enciende desde Ajustes.

No va en `app_settings` (clave-valor de texto) porque aquí el valor es un
booleano con nombre fijo y conviene que el panel liste TODAS las banderas
conocidas, existan o no todavía en la base: por eso se siembran las cuatro con
`ON CONFLICT DO NOTHING`, que hace la migración repetible.

Bajar borra la tabla entera: las banderas son configuración, no datos del
negocio, y su valor por defecto (apagadas) es exactamente lo que queda al
volver a subir.

Revision ID: 0056_feature_flags
Revises: 0055_user_role_revisor
"""
import sqlalchemy as sa
from alembic import op

revision = "0056_feature_flags"
down_revision = "0055_user_role_revisor"
branch_labels = None
depends_on = None

_BANDERAS_INICIALES = (
    "agente_interno",
    "flow_dashboard",
    "envio_masivo",
    "tarifas_openrouter",
)


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("clave", sa.String(60), primary_key=True),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    for clave in _BANDERAS_INICIALES:
        op.execute(
            sa.text(
                "INSERT INTO feature_flags (clave, activa) VALUES (:clave, false) "
                "ON CONFLICT (clave) DO NOTHING"
            ).bindparams(clave=clave)
        )


def downgrade() -> None:
    op.drop_table("feature_flags")
