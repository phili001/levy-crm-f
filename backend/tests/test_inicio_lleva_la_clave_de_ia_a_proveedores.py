"""Cada paso del Inicio lleva a la pantalla donde se hace y dice lo que pasa.

Dos textos que salieron al preparar el video 06:
  - «Agregar clave de IA» abría Conexiones → Servicios, y la clave de OpenAI
    se pone en Conexiones → Proveedores LLM. Quien seguía el botón se quedaba
    buscando un campo que no estaba ahí.
  - El paso del canal decía «Los canales nuevos nacen en Entrenamiento», y
    nacen en Activo: Sofía contesta sola desde que se conecta el canal.
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


def test_la_clave_de_ia_lleva_a_proveedores_y_el_canal_nace_en_activo():
    import httpx

    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.main import app
    from app.models.user import User

    async def _run():
        async with db_session() as db:
            admin = User(
                email=f"inicio-{uuid.uuid4().hex[:8]}@ejemplo.com",
                password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
                nombre="Admin Inicio",
                role="admin",
            )
            db.add(admin)
            await db.commit()
            await db.refresh(admin)
        token = create_access_token(subject=str(admin.id), extra={"role": "admin"})
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/api/v1/admin/onboarding", headers={"Authorization": f"Bearer {token}"}
                )
            assert r.status_code == 200, r.text
            pasos = {s["key"]: s for s in r.json()["steps"]}

            assert pasos["ai"]["action_path"] == "/admin/connections?tab=providers", (
                "el botón de la clave de IA no lleva a Proveedores LLM, donde se pone"
            )
            activo = pasos["activo"]
            assert "nacen en Activo" in activo["description"]
            assert "nacen en Entrenamiento" not in activo["description"]
        finally:
            async with db_session() as db:
                await db.execute(delete(User).where(User.id == admin.id))
                await db.commit()

    asyncio.run(_run())
