"""«Probar el RAG» dice qué pasa en vez de quedarse callado.

Sin clave de OpenAI, la búsqueda recibía un vector de ceros y respondía una
lista vacía: en el panel parecía que el documento no tenía nada, cuando lo que
faltaba era la clave. Y si OpenAI fallaba, el endpoint reventaba con un 500 que
el panel no enseñaba. Ahora: sin clave, 409 con dónde ponerla (sin gastar cupo);
con OpenAI caído, 502 con un mensaje que se entiende.
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


def test_sin_clave_409_y_con_openai_caido_502(monkeypatch):
    import httpx

    from app.api import knowledge_base as kb_api
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.main import app
    from app.models.user import User

    estado = {"con_clave": False, "llamadas": 0}

    async def _configurado():
        return estado["con_clave"]

    async def _openai_caido(textos):
        estado["llamadas"] += 1
        raise RuntimeError("401 Incorrect API key provided")

    monkeypatch.setattr(kb_api, "embeddings_configured", _configurado)
    monkeypatch.setattr(kb_api, "embed_texts_with_status", _openai_caido)

    async def _run():
        async with db_session() as db:
            admin = User(
                email=f"rag-{uuid.uuid4().hex[:8]}@ejemplo.com",
                password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
                nombre="Admin RAG",
                role="admin",
            )
            db.add(admin)
            await db.commit()
            await db.refresh(admin)
        h = {"Authorization": f"Bearer {create_access_token(subject=str(admin.id), extra={'role': 'admin'})}"}
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post("/api/v1/kb/search", headers=h, json={"query": "horario"})
                assert r.status_code == 409, r.text
                assert "Falta la clave de OpenAI" in r.text
                assert "Proveedores LLM" in r.text
                assert estado["llamadas"] == 0, "sin clave no debería ni intentar generar el vector"

                estado["con_clave"] = True
                r = await client.post("/api/v1/kb/search", headers=h, json={"query": "horario"})
                assert r.status_code == 502, r.text
                assert "OpenAI no respondió" in r.text
        finally:
            async with db_session() as db:
                await db.execute(delete(User).where(User.id == admin.id))
                await db.commit()

    asyncio.run(_run())
