"""Herramientas de n8n para Sofía (tabla `n8n_tools`, Fase 11, SPEC-06).

Cada fila es una herramienta que el administrador da de alta en Conexiones →
n8n: Sofía la ve como una más (con el `nombre` y la `descripcion` que se
escriben aquí) y, cuando la usa, el CRM llama al webhook de n8n con los datos
que pide la herramienta. Lo que contesta el flujo vuelve a Sofía como dato.

- `parametros`: lista de `{clave, tipo, descripcion, obligatorio, opciones?,
  sensible?}`; `services/n8n_tools.py` la convierte en el JSON Schema que ve el
  modelo. `tipo` es `texto`, `numero`, `si_no` u `opciones`.
- `secreto`: lo genera el CRM y va en la cabecera `X-Omnicanal-Token`; n8n lo
  comprueba con el «Header Auth» de su nodo Webhook. Cifrado en reposo.
- `accion`: `lectura` (se reintenta una vez si falla) o `escritura` (no se
  reintenta: hacerla dos veces podría duplicar un pedido; si no hay respuesta,
  la conversación pasa a una persona).
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.encrypted_type import EncryptedText
from app.db.session import Base

ACCION_LECTURA = "lectura"
ACCION_ESCRITURA = "escritura"


class N8nTool(Base):
    __tablename__ = "n8n_tools"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    parametros: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    secreto: Mapped[str] = mapped_column(EncryptedText, nullable=False)
    accion: Mapped[str] = mapped_column(String(10), nullable=False, default=ACCION_ESCRITURA)
    espera_s: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    datos_contacto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
