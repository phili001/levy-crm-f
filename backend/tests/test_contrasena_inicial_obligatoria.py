"""La contraseña con la que se instala no se queda puesta.

El administrador inicial entra con la contraseña que viaja en las variables del
despliegue: la ve cualquiera con acceso al panel del servidor, y en una
instalación guiada acaba también en el archivo local de quien instaló. Hasta
ahora el panel solo lo pedía como un paso del checklist, que se podía ignorar
para siempre.

DB-gated: se salta si no hay Postgres.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest


def _db() -> bool:
    try:
        from sqlalchemy import text

        from app.db.session import db_session

        async def _ping():
            async with db_session() as db:
                await db.execute(text("SELECT 1"))

        asyncio.run(_ping())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db(), reason="sin Postgres")


def _cliente():
    import httpx

    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _crear_admin(*, debe_cambiar: bool):
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.models.user import User

    async with db_session() as db:
        u = User(
            email=f"inicial-{uuid.uuid4().hex[:8]}@test.local",
            password_hash=hash_password("ContrasenaDeLaInstalacion1"),
            nombre="Admin inicial",
            role="admin",
            debe_cambiar_password=debe_cambiar,
        )
        db.add(u)
        await db.commit()
        uid = u.id
    return uid, create_access_token(subject=str(uid), extra={"role": "admin"})


async def _borrar(uid) -> None:
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        await db.execute(text("DELETE FROM audit_log WHERE user_id = :i"), {"i": str(uid)})
        await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(uid)})
        await db.commit()


def test_con_la_contrasena_de_la_instalacion_el_panel_no_deja_hacer_nada():
    async def _run():
        uid, token = await _crear_admin(debe_cambiar=True)
        cab = {"Authorization": f"Bearer {token}"}
        try:
            async with _cliente() as c:
                # Lo único que se puede: mirarse y salir.
                r = await c.get("/api/v1/auth/me", headers=cab)
                assert r.status_code == 200
                assert r.json()["debe_cambiar_password"] is True

                for ruta in (
                    "/api/v1/conversations",
                    "/api/v1/contacts",
                    "/api/v1/admin/onboarding",
                    "/api/v1/admin/channels",
                ):
                    r = await c.get(ruta, headers=cab)
                    assert r.status_code == 403, f"{ruta} respondió {r.status_code}"
                    assert "debe_cambiar_password" in r.text
        finally:
            await _borrar(uid)

    asyncio.run(_run())


def test_al_cambiarla_se_recupera_el_panel_entero():
    async def _run():
        uid, token = await _crear_admin(debe_cambiar=True)
        cab = {"Authorization": f"Bearer {token}"}
        try:
            async with _cliente() as c:
                r = await c.patch("/api/v1/auth/me", headers=cab, json={
                    "current_password": "ContrasenaDeLaInstalacion1",
                    "new_password": "OtraDistintaYLarga2026",
                })
                assert r.status_code == 200, r.text
                assert r.json()["debe_cambiar_password"] is False

                # El token viejo ya no vale (cambiar la contraseña los invalida),
                # así que se entra otra vez, como haría el panel.
                from app.core.security import create_access_token

                nuevo = create_access_token(subject=str(uid), extra={"role": "admin"})
                r = await c.get(
                    "/api/v1/admin/onboarding", headers={"Authorization": f"Bearer {nuevo}"}
                )
                assert r.status_code == 200
        finally:
            await _borrar(uid)

    asyncio.run(_run())


def test_a_los_demas_usuarios_no_les_afecta():
    """Los que crea un administrador desde el panel no llevan la bandera: si el
    corte fuera por `password_changed_at IS NULL`, se quedarían fuera."""
    async def _run():
        uid, token = await _crear_admin(debe_cambiar=False)
        try:
            async with _cliente() as c:
                r = await c.get(
                    "/api/v1/admin/onboarding", headers={"Authorization": f"Bearer {token}"}
                )
                assert r.status_code == 200
        finally:
            await _borrar(uid)

    asyncio.run(_run())


def test_el_sembrado_marca_al_administrador_inicial():
    """Es el único sitio que pone la bandera."""
    import inspect

    from app.scripts import seed

    fuente = inspect.getsource(seed)
    assert "debe_cambiar_password=True" in fuente
