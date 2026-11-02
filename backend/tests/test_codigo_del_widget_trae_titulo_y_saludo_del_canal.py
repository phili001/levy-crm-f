"""El código del chat web sale con el título, el saludo y el color de la instalación.

Salió al preparar el video 08: el código para pegar en la web de la clínica
llevaba siempre «Chat», un saludo genérico y un color que no era el de nadie,
aunque el canal tuviera su título y su saludo (los que usa /demo y siembra el
perfil). La web enseñaba un chat distinto del de /demo.
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


def test_el_codigo_lleva_titulo_saludo_y_color_de_la_instalacion():
    import httpx

    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.main import app
    from app.models.channel import Channel, ChannelType
    from app.models.user import User
    from app.services import apariencia

    async def _run():
        canal_id = None
        async with db_session() as db:
            antes = await apariencia.leer(db)
            admin = User(
                email=f"widget-{uuid.uuid4().hex[:8]}@ejemplo.com",
                password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
                nombre="Admin Widget",
                role="admin",
            )
            db.add(admin)
            await db.execute(delete(Channel).where(Channel.type == ChannelType.webchat))
            ch = Channel(
                type=ChannelType.webchat,
                name="Chat en tu web",
                enabled=True,
                config={
                    "api_key": "clave-widget",
                    "title": 'Sonrisa "Perfecta"',
                    "greeting": "Hola, soy Sofía. ¿En qué te ayudo?",
                },
            )
            db.add(ch)
            await apariencia.guardar(
                db,
                apariencia.validar({**antes.a_dict(), "nombre": "Clínica Sonrisa Perfecta", "color_marca": "#8BE3C4"}),
            )
            await db.commit()
            await db.refresh(admin)
            await db.refresh(ch)
            canal_id = ch.id
        h = {"Authorization": f"Bearer {create_access_token(subject=str(admin.id), extra={'role': 'admin'})}"}
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/api/v1/admin/channels/webchat/snippet", headers=h)
                assert r.status_code == 200, r.text
                snippet = r.json()["snippet"]
                # Las comillas del título no rompen el atributo.
                assert 'data-title="Sonrisa &quot;Perfecta&quot;"' in snippet, snippet
                assert 'data-greeting="Hola, soy Sofía. ¿En qué te ayudo?"' in snippet
                assert 'data-brand="' in snippet and "8BE3C4" in snippet.upper()
                assert 'data-title="Chat"' not in snippet

                # Sin título en el canal, el nombre de Apariencia.
                async with db_session() as db:
                    canal = await db.get(Channel, canal_id)
                    canal.config = {"api_key": "clave-widget"}
                    await db.commit()
                r = await client.get("/api/v1/admin/channels/webchat/snippet", headers=h)
                snippet = r.json()["snippet"]
                assert 'data-title="Clínica Sonrisa Perfecta"' in snippet
                assert 'data-greeting="Hola, ¿en qué te puedo ayudar?"' in snippet
        finally:
            async with db_session() as db:
                if canal_id:
                    await db.execute(delete(Channel).where(Channel.id == canal_id))
                await db.execute(delete(User).where(User.id == admin.id))
                await apariencia.guardar(db, antes)
                await db.commit()

    asyncio.run(_run())
