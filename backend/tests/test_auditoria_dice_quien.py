"""Plataforma → Registros → Auditoría dice quién hizo cada cambio.

La pantalla prometía «quién lo hizo y cuándo» y solo enseñaba la fecha, la
acción, la entidad y un ID: el autor no salía por ningún lado. Ahora cada
entrada trae `quien`, ya en palabras, y aquí se fija cómo se decide.
"""
from types import SimpleNamespace

from app.api.admin import quien_hizo


def test_una_persona_sale_con_su_nombre():
    persona = SimpleNamespace(nombre="Ana López", email="ana@clinica.mx")
    assert quien_hizo(persona, None) == "Ana López"


def test_sin_nombre_sale_su_correo():
    persona = SimpleNamespace(nombre=None, email="ana@clinica.mx")
    assert quien_hizo(persona, None) == "ana@clinica.mx"


def test_un_token_del_mcp_sale_con_el_nombre_del_token():
    after = {"agent_token": "Claude Code", "agent_token_id": "x"}
    assert quien_hizo(None, after) == "Token «Claude Code»"


def test_lo_demas_lo_hizo_el_sistema():
    assert quien_hizo(None, None) == "Sistema"
    assert quien_hizo(None, {"campo": "valor"}) == "Sistema"


def _db_available() -> bool:
    import asyncio

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


import pytest  # noqa: E402

needs_db = pytest.mark.skipif(
    not _db_available(),
    reason="DB no disponible en este entorno (se ejecuta en CI con Postgres)",
)


@needs_db
def test_el_endpoint_de_auditoria_trae_quien_lo_hizo():
    """La consulta de verdad: la entrada se une con su usuario y la del token sale con su nombre."""
    import asyncio
    import uuid

    import httpx
    from sqlalchemy import text

    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.main import app
    from app.models.user import User
    from app.services.audit import record_audit

    async def _run():
        suf = uuid.uuid4().hex[:8]
        accion_persona, accion_token = f"prueba.persona.{suf}", f"prueba.token.{suf}"
        async with db_session() as db:
            admin = User(
                email=f"admin-{suf}@test.local",
                password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
                nombre="Admin Auditoría",
                role="admin",
            )
            db.add(admin)
            await db.flush()
            await record_audit(db, user_id=admin.id, action=accion_persona, entity="prueba")
            await record_audit(
                db, user_id=None, action=accion_token, entity="prueba",
                after={"agent_token": "Claude Code", "agent_token_id": "x"},
            )
            await db.commit()
            admin_id = admin.id
        token = create_access_token(subject=str(admin_id), extra={"role": "admin"})
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                r = await c.get(
                    "/api/v1/admin/audit",
                    params={"entity": "prueba", "page_size": 200},
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert r.status_code == 200, r.text
            quien = {i["action"]: i["quien"] for i in r.json()["items"]}
            assert quien[accion_persona] == "Admin Auditoría"
            assert quien[accion_token] == "Token «Claude Code»"
        finally:
            async with db_session() as db:
                await db.execute(text("DELETE FROM audit_log WHERE action IN (:a, :b)"), {"a": accion_persona, "b": accion_token})
                await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(admin_id)})
                await db.commit()

    asyncio.run(_run())
