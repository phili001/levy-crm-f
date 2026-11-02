"""/webchat/demo entrega el widget solo si la demo pública está encendida.

Si esto falla, una instalación que apagó la demo seguiría exponiendo la
api_key de su widget en una URL pública, o la página /demo del curso no
tendría con qué cargar el chat.
"""
from __future__ import annotations

import asyncio

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


def test_apagada_404_y_encendida_devuelve_la_clave_del_widget():
    import httpx

    from app.db.session import db_session
    from app.main import app
    from app.models.app_setting import AppSetting
    from app.models.channel import Channel, ChannelType
    from app.services.app_settings import set_app_setting

    async def _run():
        canal_id = None
        try:
            async with db_session() as db:
                await db.execute(delete(Channel).where(Channel.type == ChannelType.webchat))
                ch = Channel(
                    type=ChannelType.webchat,
                    name="Chat en tu web",
                    enabled=True,
                    config={"api_key": "clave-demo", "title": "Sonrisa Perfecta", "greeting": "Hola, soy Sofía"},
                )
                db.add(ch)
                await set_app_setting("ui.demo_publica", "0", db)
                await db.commit()
                await db.refresh(ch)
                canal_id = ch.id

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/api/v1/webchat/demo")
                assert r.status_code == 404, r.text

                async with db_session() as db:
                    await set_app_setting("ui.demo_publica", "1", db)
                    await db.commit()
                r = await client.get("/api/v1/webchat/demo")
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["api_key"] == "clave-demo"
                assert body["title"] == "Sonrisa Perfecta"
                assert body["greeting"] == "Hola, soy Sofía"
                assert body["api_base"]
        finally:
            async with db_session() as db:
                if canal_id:
                    await db.execute(delete(Channel).where(Channel.id == canal_id))
                await db.execute(delete(AppSetting).where(AppSetting.key == "ui.demo_publica"))
                await db.commit()

    asyncio.run(_run())
