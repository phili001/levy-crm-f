"""Al guardar la clave de OpenAI, los documentos indexados sin semántica se reindexan solos.

Si esto falla, la base de conocimiento que siembra el perfil (sin clave en el
primer arranque) se queda para siempre buscando solo por texto, y el admin
tendría que reindexar documento por documento.
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


def test_guardar_openai_api_key_encola_los_documentos_degradados(monkeypatch):
    from app.api import admin as admin_api
    from app.core.security import hash_password
    from app.db.session import db_session
    from app.models.credential import Credential
    from app.models.user import User

    llamadas: list[str] = []

    async def falso_reindex():
        llamadas.append("reindex")
        return 3

    monkeypatch.setattr("app.services.kb_indexer.reindexar_sin_semantica", falso_reindex)

    async def _run():
        async with db_session() as db:
            user = User(
                email=f"kb-{uuid.uuid4().hex[:8]}@ejemplo.com",
                password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
                nombre="Admin KB",
                role="admin",
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            user_id = user.id
        try:
            async with db_session() as db:
                user = await db.get(User, user_id)
                await admin_api._write_credential_value(db, "openai_api_key", "sk-prueba", user)
            assert llamadas == ["reindex"]

            # Otra credencial no dispara nada.
            async with db_session() as db:
                user = await db.get(User, user_id)
                await admin_api._write_credential_value(db, "ycloud_api_key", "yc-prueba", user)
            assert llamadas == ["reindex"]
        finally:
            async with db_session() as db:
                await db.execute(delete(Credential).where(Credential.key.in_(["openai_api_key", "ycloud_api_key"])))
                await db.execute(delete(User).where(User.id == user_id))
                await db.commit()

    asyncio.run(_run())
