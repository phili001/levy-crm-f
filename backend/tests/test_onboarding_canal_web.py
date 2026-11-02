"""El paso 3 del recuadro de bienvenida cuenta también el chat web.

El fallo: "Conecta un canal" solo se daba por hecho si existía una CREDENCIAL de
canal (`ycloud_api_key`, `instagram_oauth_client_id` o `resend_api_key`). El
widget web no deja credencial —su api_key vive en `Channel.config`, la escribe
`POST /channels/webchat/provision`—, así que quien atiende solo por la web se
quedaba en 3/4 para siempre y el recuadro no desaparecía nunca.

DB-gated: se salta si no hay Postgres (en CI sí lo hay).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest


def _db_available() -> bool:
    try:
        from sqlalchemy import text

        from app.db.session import db_session

        async def _check():
            async with db_session() as db:
                await db.execute(text("select 1"))

        asyncio.run(_check())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="DB no disponible en este entorno (se ejecuta en CI con Postgres)",
)


async def _paso_canal(monkeypatch, *, con_webchat: bool) -> bool:
    """Ejecuta el checklist sin ninguna credencial de canal y devuelve el paso 3."""
    from sqlalchemy import text

    from app.api import admin as mod
    from app.db.session import db_session
    from app.models.channel import Channel, ChannelType
    from app.models.user import User, UserRole

    async def _sin_credenciales(_clave):
        return None

    monkeypatch.setattr(mod, "get_credential", _sin_credenciales)

    suf = uuid.uuid4().hex[:8]
    canal_id = None
    async with db_session() as db:
        # Se parte de limpio: cualquier webchat que hubiera dejado otro test
        # falsearía el resultado en los dos sentidos.
        await db.execute(text("DELETE FROM channels WHERE type = 'webchat'"))
        if con_webchat:
            # Con el dominio de la web del negocio: el widget sembrado (que solo
            # permite el dominio del propio panel) no cuenta como canal, o el
            # paso saldría en verde desde el primer arranque.
            canal = Channel(
                type=ChannelType.webchat,
                name=f"Widget Web {suf}",
                enabled=True,
                config={
                    "api_key": "clave-publica-de-prueba",
                    "allowed_domains": ["www.negocio-de-prueba.mx"],
                },
            )
            db.add(canal)
            await db.flush()
            canal_id = canal.id
        await db.commit()

    try:
        async with db_session() as db:
            yo = User(
                email=f"admin-onboarding-{suf}@ejemplo.com",
                password_hash="x",
                nombre="Admin Test",
                role=UserRole.admin,
            )
            estado = await mod.onboarding_status(db=db, me=yo)
        paso = next(p for p in estado.steps if p.key == "channel")
        return paso.done
    finally:
        async with db_session() as db:
            if canal_id:
                await db.execute(
                    text("DELETE FROM channels WHERE id = :i"), {"i": str(canal_id)}
                )
            await db.commit()


async def test_un_canal_web_con_el_dominio_del_negocio_cuenta_como_canal_conectado(monkeypatch):
    assert await _paso_canal(monkeypatch, con_webchat=True) is True


async def test_sin_ningun_canal_el_paso_sigue_pendiente(monkeypatch):
    """No se regala el paso: sin canal de ningún tipo sigue sin estar hecho."""
    assert await _paso_canal(monkeypatch, con_webchat=False) is False
