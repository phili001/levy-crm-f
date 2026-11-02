"""El checklist de Inicio tiene siete pasos y no se pone en verde solo.

Si esto falla, o el checklist volvió a cuatro pasos, o el widget sembrado en
Activo (con el dominio del propio panel) cuenta como "canal conectado" y el
paso 5 sale en verde desde el primer arranque sin haber conectado nada.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, text


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


async def _admin() -> tuple[dict[str, str], uuid.UUID]:
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.models.user import User

    async with db_session() as db:
        admin = User(
            email=f"checklist-{uuid.uuid4().hex[:8]}@ejemplo.com",
            password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
            nombre="Admin Checklist",
            role="admin",
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        token = create_access_token(subject=str(admin.id), extra={"role": "admin"})
        return {"Authorization": f"Bearer {token}"}, admin.id


def test_siete_pasos_y_el_widget_del_panel_no_cuenta_como_canal(monkeypatch):
    import httpx

    from app.core.config import settings
    from app.db.session import db_session
    from app.main import app
    from app.models.channel import Channel, ChannelType
    from app.models.user import User

    monkeypatch.setattr(settings, "FRONTEND_BASE_URL", "https://crm.sonrisaperfecta.mx")

    async def _run():
        headers, admin_id = await _admin()
        canal_id = None
        try:
            async with db_session() as db:
                await db.execute(delete(Channel).where(Channel.type == ChannelType.webchat))
                ch = Channel(
                    type=ChannelType.webchat,
                    name="Chat en tu web",
                    enabled=True,
                    config={"api_key": "k", "allowed_domains": ["crm.sonrisaperfecta.mx"]},
                )
                db.add(ch)
                await db.commit()
                await db.refresh(ch)
                canal_id = ch.id

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/api/v1/admin/onboarding", headers=headers)
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["total"] == 7
                claves = [s["key"] for s in body["steps"]]
                assert claves == ["password", "ai", "datos", "kb", "channel", "prueba", "activo"]
                pasos = {s["key"]: s["done"] for s in body["steps"]}
                # Widget con el dominio del propio panel: NO es un canal conectado.
                assert pasos["channel"] is False
                assert pasos["activo"] is False

                # Con el dominio de la web de la clínica, sí cuenta.
                async with db_session() as db:
                    ch = await db.get(Channel, canal_id)
                    ch.config = {**ch.config, "allowed_domains": ["www.sonrisaperfecta.mx"]}
                    await db.commit()
                r = await client.get("/api/v1/admin/onboarding", headers=headers)
                pasos = {s["key"]: s["done"] for s in r.json()["steps"]}
                assert pasos["channel"] is True
                # …pero el widget nunca cierra el paso 7 (ese es para un canal real).
                assert pasos["activo"] is False
        finally:
            async with db_session() as db:
                if canal_id:
                    await db.execute(delete(Channel).where(Channel.id == canal_id))
                await db.execute(delete(User).where(User.id == admin_id))
                await db.commit()

    asyncio.run(_run())
