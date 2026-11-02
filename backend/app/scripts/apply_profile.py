"""Aplica un perfil de instalación (app/scripts/profiles/) sobre la base de datos.

Lo llama el seed después de sembrar admin y agentes. Todo es create-only: si
la clave, la etiqueta, el documento o el canal ya existen, no se tocan. Así el
segundo arranque (y el vigésimo) no pisa nada de lo que el panel haya editado,
que es la misma regla que sigue el resto del seed.

Cada bloque va en su propia sesión y sus fallos se registran sin tumbar el
arranque: un documento de la base de conocimiento que no se pudo indexar no
debe dejar la instalación sin administrador.
"""
from __future__ import annotations

import json
import secrets
from urllib.parse import urlparse

from sqlalchemy import func, select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import db_session
from app.models.app_setting import AppSetting
from app.models.channel import Channel, ChannelType
from app.models.document import Document
from app.models.tag import Tag
from app.models.user import User
from app.services.agenda import SETTING_AGENDA_SERVICIOS
from app.services.app_settings import set_app_setting
from app.services.runtime_config import SETTING_VOICE_GREETING

logger = get_logger(__name__)

SETTING_DEMO_PUBLICA = "ui.demo_publica"


async def _set_si_no_existe(db, key: str, value: str) -> bool:
    """Escribe la clave solo si no hay fila: lo editado en el panel manda."""
    existe = (
        await db.execute(select(AppSetting.key).where(AppSetting.key == key))
    ).scalar_one_or_none()
    if existe is not None:
        return False
    await set_app_setting(key, value, db)
    return True


async def _ajustes(perfil: dict) -> int:
    from app.services.runtime_config import SETTING_PROMPT_BLOQUE_COMUN

    pares: list[tuple[str, str]] = []
    if perfil.get("bloque_comun"):
        pares.append((SETTING_PROMPT_BLOQUE_COMUN, perfil["bloque_comun"].strip()))
    for key, value in (perfil.get("horario") or {}).items():
        pares.append((key, str(value)))
    if perfil.get("servicios"):
        pares.append((SETTING_AGENDA_SERVICIOS, json.dumps(perfil["servicios"], ensure_ascii=False)))
    if perfil.get("saludo_voz"):
        pares.append((SETTING_VOICE_GREETING, perfil["saludo_voz"]))
    pares.append((SETTING_DEMO_PUBLICA, "1" if perfil.get("demo_publica") else "0"))

    escritas = 0
    async with db_session() as db:
        for key, value in pares:
            if await _set_si_no_existe(db, key, value):
                escritas += 1
        await db.commit()
    return escritas


async def _etiquetas(perfil: dict) -> int:
    etiquetas = perfil.get("etiquetas")
    if not etiquetas:
        return 0
    creadas = 0
    async with db_session() as db:
        for nombre, color in etiquetas:
            existe = (
                await db.execute(select(Tag.id).where(Tag.nombre == nombre))
            ).scalar_one_or_none()
            if existe is None:
                db.add(Tag(nombre=nombre, color=color))
                creadas += 1
        await db.commit()
    return creadas


async def _base_de_conocimiento(perfil: dict, admin_id) -> int:
    """Documentos de texto del perfil. Se indexan en el acto; sin clave de IA
    quedan `indexado_sin_semantica` (búsqueda por texto) y se reindexan solos
    cuando el admin pega su clave de OpenAI."""
    docs = perfil.get("kb") or []
    if not docs:
        return 0
    # Import tardío: el módulo crea la carpeta de la KB al importarse y en el
    # seed solo hace falta si el perfil trae documentos.
    from app.api.knowledge_base import create_text_document

    creados = 0
    for titulo, contenido in docs:
        nombre = titulo[:250]
        if not nombre.lower().endswith((".txt", ".md")):
            nombre = f"{nombre}.txt"
        try:
            async with db_session() as db:
                existe = (
                    await db.execute(select(Document.id).where(Document.nombre == nombre))
                ).scalar_one_or_none()
                if existe is not None:
                    continue
                await create_text_document(db, titulo, contenido, admin_id)
                creados += 1
        except Exception as exc:  # noqa: BLE001 — un documento no tumba el arranque
            logger.warning("seed.perfil.kb.error", titulo=titulo, error=str(exc))
    return creados


def _host_del_panel() -> str:
    """Dominio del panel para `allowed_domains` del widget: así /demo funciona
    desde el primer arranque. El checklist no cuenta este dominio como "canal
    conectado": hace falta el de la web de la clínica."""
    host = urlparse(settings.FRONTEND_BASE_URL).hostname or ""
    return host.lower()


async def _widget(perfil: dict) -> bool:
    cfg = perfil.get("widget") or {}
    if not cfg.get("activo"):
        return False
    async with db_session() as db:
        existe = (
            await db.execute(select(Channel.id).where(Channel.type == ChannelType.webchat).limit(1))
        ).scalar_one_or_none()
        if existe is not None:
            return False
        config = {
            "api_key": secrets.token_urlsafe(32),
            "greeting": cfg.get("saludo") or "",
            "title": cfg.get("titulo") or "",
            "allowed_domains": [h for h in [_host_del_panel()] if h],
        }
        db.add(Channel(type=ChannelType.webchat, name="Chat en tu web", enabled=True, config=config))
        await db.commit()
    return True


async def aplicar_perfil(perfil: dict) -> dict:
    """Aplica el perfil entero y devuelve un resumen para el log."""
    async with db_session() as db:
        admin_id = (
            await db.execute(
                select(User.id).where(User.email == settings.INITIAL_ADMIN_EMAIL.lower())
            )
        ).scalar_one_or_none()

    resumen = {
        "perfil": perfil.get("nombre"),
        "ajustes": await _ajustes(perfil),
        "etiquetas": await _etiquetas(perfil),
        "kb": await _base_de_conocimiento(perfil, admin_id),
        "widget": await _widget(perfil),
    }
    logger.info("seed.perfil.aplicado", **resumen)
    return resumen


async def documentos_sin_semantica() -> int:
    """Cuántos documentos quedaron indexados sin embeddings (sin clave de IA)."""
    from app.models.document import DocumentStatus

    async with db_session() as db:
        return (
            await db.execute(
                select(func.count())
                .select_from(Document)
                .where(Document.status == DocumentStatus.indexado_sin_semantica)
            )
        ).scalar_one()
