"""El panel enseña las novedades de la versión instalada.

Por HTTP: que el endpoint exija sesión de administrador es una dependencia, y
las dependencias no se ejecutan si llamas a la función del endpoint a mano.

DB-gated: se salta si no hay Postgres (crear el administrador necesita base).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest


def _db_available() -> bool:
    try:
        from sqlalchemy import text

        from app.db.session import db_session

        async def _ping() -> None:
            async with db_session() as db:
                await db.execute(text("SELECT 1"))

        asyncio.run(_ping())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="sin Postgres")


def _client():
    import httpx

    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _crear_admin():
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.models.user import User

    async with db_session() as db:
        u = User(
            email=f"novedades-{uuid.uuid4().hex[:8]}@test.local",
            password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
            nombre="Admin Novedades",
            role="admin",
        )
        db.add(u)
        await db.commit()
        uid = u.id
    return uid, create_access_token(subject=str(uid), extra={"role": "admin"})


async def _borrar(uid) -> None:
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(uid)})
        await db.commit()


def test_las_novedades_llegan_al_panel():
    async def _run():
        uid, token = await _crear_admin()
        try:
            async with _client() as c:
                r = await c.get(
                    "/api/v1/admin/changelog", headers={"Authorization": f"Bearer {token}"}
                )
                assert r.status_code == 200
                datos = r.json()
                # El archivo viaja con la imagen: si no llega, la pantalla se
                # queda vacía sin que nadie se entere.
                assert "Novedades" in datos["contenido"]
                assert datos["version"]
        finally:
            await _borrar(uid)

    asyncio.run(_run())


def test_sin_sesion_no_se_ven_las_novedades():
    async def _run():
        async with _client() as c:
            r = await c.get("/api/v1/admin/changelog")
            assert r.status_code in (401, 403)

    asyncio.run(_run())
