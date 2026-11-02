"""El perfil dental-mx deja a Sofía lista al primer arranque, y el segundo no pisa nada.

Si esto falla, el video 04 no puede mostrar a Sofía respondiendo sin
configurar nada: o faltan datos (prompt con huecos, sin horario, sin KB, sin
widget) o un reinicio duplica etiquetas y documentos.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, func, select, text


def _db_available() -> bool:
    try:
        async def _check():
            from app.db.session import db_session

            async with db_session() as db:
                await db.execute(text("SELECT 1"))

        asyncio.run(_check())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="DB no disponible en este entorno (se ejecuta en CI con Postgres)",
)

_CLAVES_PERFIL = (
    "prompt.bloque_comun",
    "calendar.timezone",
    "calendar.work_days",
    "calendar.work_hours",
    "calendar.holidays",
    "calendar.slot_step_min",
    "calendar.min_notice_min",
    "agenda.servicios",
    "voice.greeting",
    "ui.demo_publica",
)


async def _limpiar(email: str) -> None:
    from app.db.session import db_session
    from app.models.agent import Agent
    from app.models.app_setting import AppSetting
    from app.models.channel import Channel, ChannelType
    from app.models.document import Document
    from app.models.tag import Tag
    from app.models.user import User
    from app.scripts.profiles.dental_mx import ETIQUETAS, KB
    from app.scripts.seed import TEXT_AGENT_NAME, VOICE_AGENT_NAME

    nombres_docs = [f"{t[:250]}.txt" for t, _ in KB]
    async with db_session() as db:
        await db.execute(delete(Agent).where(Agent.name.in_([TEXT_AGENT_NAME, VOICE_AGENT_NAME])))
        await db.execute(delete(Tag).where(Tag.nombre.in_([n for n, _ in ETIQUETAS])))
        await db.execute(delete(Document).where(Document.nombre.in_(nombres_docs)))
        await db.execute(delete(Channel).where(Channel.type == ChannelType.webchat))
        await db.execute(delete(AppSetting).where(AppSetting.key.in_(_CLAVES_PERFIL)))
        await db.execute(delete(User).where(User.email == email))
        await db.commit()


async def _instalacion_limpia(db) -> bool:
    return False


async def _estado() -> dict:
    from app.db.session import db_session
    from app.models.agent import Agent
    from app.models.app_setting import AppSetting
    from app.models.channel import Channel, ChannelType
    from app.models.document import Document
    from app.models.tag import Tag
    from app.scripts.profiles.dental_mx import ETIQUETAS, KB
    from app.scripts.seed import TEXT_AGENT_NAME, VOICE_AGENT_NAME

    async with db_session() as db:
        texto = (
            await db.execute(select(Agent.prompt_system).where(Agent.name == TEXT_AGENT_NAME))
        ).scalar_one_or_none()
        voz = (
            await db.execute(select(Agent.prompt_system).where(Agent.name == VOICE_AGENT_NAME))
        ).scalar_one_or_none()
        ajustes = dict(
            (await db.execute(select(AppSetting.key, AppSetting.value).where(AppSetting.key.in_(_CLAVES_PERFIL)))).all()
        )
        etiquetas = (
            await db.execute(select(func.count()).select_from(Tag).where(Tag.nombre.in_([n for n, _ in ETIQUETAS])))
        ).scalar_one()
        docs = (
            await db.execute(
                select(func.count()).select_from(Document).where(Document.nombre.in_([f"{t[:250]}.txt" for t, _ in KB]))
            )
        ).scalar_one()
        widgets = (await db.execute(select(Channel).where(Channel.type == ChannelType.webchat))).scalars().all()
    return {
        "texto": texto,
        "voz": voz,
        "ajustes": ajustes,
        "etiquetas": etiquetas,
        "docs": docs,
        "widgets": [(w.enabled, dict(w.config or {})) for w in widgets],
    }


def test_dental_mx_siembra_todo_y_el_segundo_arranque_no_duplica(monkeypatch):
    from app.core.config import settings
    from app.scripts.seed import seed

    email = f"perfil-{uuid.uuid4().hex[:8]}@ejemplo.com"
    monkeypatch.setattr(settings, "INITIAL_ADMIN_EMAIL", email)
    monkeypatch.setattr(settings, "INITIAL_ADMIN_PASSWORD", "UnaClaveLargaDeVerdad2026")
    monkeypatch.setattr(settings, "SEED_PROFILE", "dental-mx")
    monkeypatch.setattr(settings, "FRONTEND_BASE_URL", "https://crm.sonrisaperfecta.mx")
    # La BD de pruebas la comparten otros tests que crean sus propios agentes;
    # para el seed eso parece "instalación en marcha" y no sembraría los suyos.
    monkeypatch.setattr("app.scripts.seed._ya_tiene_agentes_propios", _instalacion_limpia)

    async def _run():
        await _limpiar(email)
        try:
            await seed()
            e1 = await _estado()

            # Prompts sin huecos; el bloque común solo pide teléfono y dirección.
            assert e1["texto"] and "{{PENDIENTE" not in e1["texto"]
            assert e1["voz"] and "{{PENDIENTE" not in e1["voz"]
            assert e1["ajustes"]["prompt.bloque_comun"].count("{{PENDIENTE") == 2
            # Horario del caso ancla, en hora de Cancún.
            assert e1["ajustes"]["calendar.work_hours"] == "08:00-17:00"
            assert e1["ajustes"]["calendar.work_days"] == "1,2,3,4,5"
            assert e1["ajustes"]["calendar.timezone"] == "America/Cancun"
            assert e1["ajustes"]["calendar.min_notice_min"] == "120"
            assert "Cita de valoración" in e1["ajustes"]["agenda.servicios"]
            assert "se graba" in e1["ajustes"]["voice.greeting"]
            assert e1["ajustes"]["ui.demo_publica"] == "1"
            assert e1["etiquetas"] == 5
            assert e1["docs"] == 6
            # Widget en Activo y con el dominio del panel permitido (para /demo).
            assert len(e1["widgets"]) == 1
            activo, cfg = e1["widgets"][0]
            assert activo and cfg["api_key"] and cfg["allowed_domains"] == ["crm.sonrisaperfecta.mx"]

            # Segundo arranque: nada se duplica ni se pisa.
            await seed()
            e2 = await _estado()
            assert e2["etiquetas"] == 5 and e2["docs"] == 6 and len(e2["widgets"]) == 1
            assert e2["widgets"][0][1]["api_key"] == cfg["api_key"]
            assert e2["ajustes"] == e1["ajustes"]
        finally:
            await _limpiar(email)

    asyncio.run(_run())


def test_blank_deja_las_plantillas_con_huecos_y_sin_datos_de_clinica(monkeypatch):
    from app.core.config import settings
    from app.scripts.seed import seed

    email = f"perfil-{uuid.uuid4().hex[:8]}@ejemplo.com"
    monkeypatch.setattr(settings, "INITIAL_ADMIN_EMAIL", email)
    monkeypatch.setattr(settings, "INITIAL_ADMIN_PASSWORD", "UnaClaveLargaDeVerdad2026")
    monkeypatch.setattr(settings, "SEED_PROFILE", "blank")
    monkeypatch.setattr("app.scripts.seed._ya_tiene_agentes_propios", _instalacion_limpia)

    async def _run():
        await _limpiar(email)
        try:
            await seed()
            e = await _estado()
            assert "{{PENDIENTE" in e["texto"] and "{{PENDIENTE" in e["voz"]
            assert e["docs"] == 0 and e["widgets"] == []
            assert "prompt.bloque_comun" not in e["ajustes"]
            assert e["ajustes"].get("ui.demo_publica") == "0"
        finally:
            await _limpiar(email)

    asyncio.run(_run())
