"""Banderas de módulos (tabla `feature_flags`).

Cada fila enciende o apaga un módulo entero del producto que el curso no
enseña pero cuyo código se conserva. Las claves son fijas y viven en
`BANDERAS`: el panel las lista todas aunque alguna no tenga fila (cae al
default apagado), y `services/feature_flags.require_flag` las usa para
esconder rutas enteras de la API.

Por qué una tabla y no `app_settings`: ahí el valor es texto libre y no hay
forma de saber qué claves existen; aquí el conjunto es cerrado, el valor es
booleano y la migración siembra las filas para que el panel no invente nada.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

FLAG_AGENTE_INTERNO = "agente_interno"
FLAG_FLOW_DASHBOARD = "flow_dashboard"
FLAG_ENVIO_MASIVO = "envio_masivo"
FLAG_TARIFAS_OPENROUTER = "tarifas_openrouter"

# Clave → texto que ve el admin en Ajustes → Módulos. El orden es el del panel.
BANDERAS: dict[str, str] = {
    FLAG_AGENTE_INTERNO: "Agente interno del operador (pestaña en Agentes y sus endpoints)",
    FLAG_FLOW_DASHBOARD: "Flujo en vivo (pantalla de trazas del agente)",
    FLAG_ENVIO_MASIVO: "Envío masivo por WhatsApp (plantillas a listas)",
    FLAG_TARIFAS_OPENROUTER: "Tarifas de modelos desde OpenRouter (tarea diaria)",
}


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    clave: Mapped[str] = mapped_column(String(60), primary_key=True)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
