"""Las banderas de módulos esconden de verdad lo que el curso no enseña.

Con la instalación recién sembrada, el envío masivo y el agente interno tienen
que responder 404 (para quien sigue los videos no existen). Cuando el admin
enciende la bandera desde Ajustes, la ruta vuelve. Si esto falla, o bien un
módulo "apagado" sigue a la vista, o bien encenderlo no lo devuelve.
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


async def _admin_headers() -> tuple[dict[str, str], uuid.UUID]:
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.models.user import User

    async with db_session() as db:
        admin = User(
            email=f"banderas-{uuid.uuid4().hex[:8]}@ejemplo.com",
            password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
            nombre="Admin Banderas",
            role="admin",
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        token = create_access_token(subject=str(admin.id), extra={"role": "admin"})
        return {"Authorization": f"Bearer {token}"}, admin.id


async def _limpiar(admin_id: uuid.UUID) -> None:
    from sqlalchemy import delete

    from app.db.session import db_session
    from app.models.feature_flag import BANDERAS, FeatureFlag
    from app.models.user import User

    async with db_session() as db:
        # Las banderas vuelven a apagadas para no contaminar a otros tests.
        for clave in BANDERAS:
            flag = await db.get(FeatureFlag, clave)
            if flag is not None:
                flag.activa = False
        await db.execute(delete(User).where(User.id == admin_id))
        await db.commit()


def test_modulo_apagado_responde_404_y_encendido_vuelve():
    import httpx

    from app.main import app
    from app.models.feature_flag import BANDERAS, FLAG_ENVIO_MASIVO

    async def _run():
        headers, admin_id = await _admin_headers()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # Recién sembrado: todas las banderas conocidas, todas apagadas.
                r = await client.get("/api/v1/admin/feature-flags", headers=headers)
                assert r.status_code == 200, r.text
                estado = {f["clave"]: f["activa"] for f in r.json()["flags"]}
                assert set(estado) == set(BANDERAS)
                assert not any(estado.values())

                # Apagado: el envío masivo y el agente interno no existen.
                r = await client.get("/api/v1/admin/outbound/jobs", headers=headers)
                assert r.status_code == 404, r.text
                r = await client.get("/api/v1/admin/internal-agent/status", headers=headers)
                assert r.status_code == 404, r.text

                # Una clave inventada se rechaza en vez de guardarse en silencio.
                r = await client.put(
                    "/api/v1/admin/feature-flags",
                    headers=headers,
                    json={"flags": {"modulo_inventado": True}},
                )
                assert r.status_code == 422, r.text

                # Encendido: solo vuelve el módulo que se encendió.
                r = await client.put(
                    "/api/v1/admin/feature-flags",
                    headers=headers,
                    json={"flags": {FLAG_ENVIO_MASIVO: True}},
                )
                assert r.status_code == 200, r.text
                r = await client.get("/api/v1/admin/outbound/jobs", headers=headers)
                assert r.status_code == 200, r.text
                r = await client.get("/api/v1/admin/internal-agent/status", headers=headers)
                assert r.status_code == 404, r.text
        finally:
            await _limpiar(admin_id)

    asyncio.run(_run())


def test_la_tarea_de_tarifas_no_llama_a_openrouter_con_el_modulo_apagado():
    from app.tasks.refresh_prices import refresh_prices

    resultado = refresh_prices.run()
    assert resultado == {"skipped": True, "reason": "modulo_apagado"}
