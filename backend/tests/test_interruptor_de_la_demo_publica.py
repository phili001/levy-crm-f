"""El administrador puede encender y apagar /demo desde el panel.

`ui.demo_publica` solo lo ponía el perfil de instalación: con el perfil
`blank` no había forma de encender /demo, y la propia página apagada decía
«un administrador puede encenderla desde Ajustes», donde no había nada. Ahora
el interruptor está en la ventana «Chat en tu web» de Conexiones (`/admin/channels/webchat/demo`)
y deja auditoría.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, select, text


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


def test_el_interruptor_enciende_y_apaga_la_demo():
    import httpx

    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.main import app
    from app.models.app_setting import AppSetting
    from app.models.audit_log import AuditLog
    from app.models.channel import Channel, ChannelType
    from app.models.user import User
    from app.services.app_settings import set_app_setting

    async def _run():
        canal_id = None
        async with db_session() as db:
            admin = User(
                email=f"demo-{uuid.uuid4().hex[:8]}@ejemplo.com",
                password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
                nombre="Admin Demo",
                role="admin",
            )
            db.add(admin)
            await db.execute(delete(Channel).where(Channel.type == ChannelType.webchat))
            ch = Channel(
                type=ChannelType.webchat,
                name="Chat en tu web",
                enabled=True,
                config={"api_key": "clave-demo"},
            )
            db.add(ch)
            # Como el perfil `blank`: la demo nace apagada.
            await set_app_setting("ui.demo_publica", "0", db)
            await db.commit()
            await db.refresh(admin)
            await db.refresh(ch)
            canal_id = ch.id
        h = {"Authorization": f"Bearer {create_access_token(subject=str(admin.id), extra={'role': 'admin'})}"}
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/api/v1/admin/channels/webchat/demo", headers=h)
                assert r.status_code == 200, r.text
                assert r.json() == {"encendida": False}
                assert (await client.get("/api/v1/webchat/demo")).status_code == 404

                r = await client.put(
                    "/api/v1/admin/channels/webchat/demo", headers=h, json={"encendida": True}
                )
                assert r.status_code == 200, r.text
                assert (await client.get("/api/v1/webchat/demo")).status_code == 200, (
                    "el interruptor no enciende /demo"
                )

                r = await client.put(
                    "/api/v1/admin/channels/webchat/demo", headers=h, json={"encendida": False}
                )
                assert r.status_code == 200, r.text
                assert (await client.get("/api/v1/webchat/demo")).status_code == 404

                # Sin sesión de admin no se toca.
                r = await client.put("/api/v1/admin/channels/webchat/demo", json={"encendida": True})
                assert r.status_code in (401, 403)

            async with db_session() as db:
                acciones = (
                    await db.execute(select(AuditLog.action).where(AuditLog.user_id == admin.id))
                ).scalars().all()
            assert "demo_publica.encendida" in acciones and "demo_publica.apagada" in acciones
        finally:
            async with db_session() as db:
                if canal_id:
                    await db.execute(delete(Channel).where(Channel.id == canal_id))
                await db.execute(delete(AppSetting).where(AppSetting.key == "ui.demo_publica"))
                await db.execute(delete(AuditLog).where(AuditLog.user_id == admin.id))
                await db.execute(delete(User).where(User.id == admin.id))
                await db.commit()

    asyncio.run(_run())
