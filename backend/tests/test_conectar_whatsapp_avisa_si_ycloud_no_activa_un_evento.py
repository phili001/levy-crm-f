"""Conectar WhatsApp comprueba qué eventos dejó activos YCloud y avisa si falta uno.

Al grabar el video 09, el endpoint que el CRM creó en YCloud quedó SIN el
evento de ecos (`whatsapp.smb.message.echoes`): YCloud respondió 200 y se lo
quedó callado. El canal salía en verde, pero lo que la clínica contestaba desde
su celular no llegaba al inbox ni apartaba a Sofía. Hubo que marcarlo a mano.

Ahora el alta mira los `enabledEvents` que devuelve YCloud, lo pide otra vez
si falta algo y, si sigue faltando, lo guarda para que la tarjeta de WhatsApp y
`/admin/channels/estado` (lo que mira `omni.py verify`) lo digan.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import delete, select, text

from tests.mocks import wa_mock
from tests.test_simuladores_proveedores import _Servidor

URL = "https://crm.ejemplo.mx/api/v1/webhooks/ycloud"
ECOS = "whatsapp.smb.message.echoes"


@pytest.fixture
def ycloud(monkeypatch):
    from app.core.config import settings
    from app.providers.whatsapp import ycloud as modulo

    credenciales = {"ycloud_api_key": "mock-key", "ycloud_phone_number": wa_mock.NUMERO_POR_DEFECTO}

    async def leer(clave, **_):
        return credenciales.get(clave)

    monkeypatch.setattr(modulo, "get_credential", leer)
    monkeypatch.setattr(modulo, "push_runtime_log", AsyncMock())
    with _Servidor(wa_mock.crear_app()) as servidor:
        monkeypatch.setattr(settings, "YCLOUD_BASE_URL", f"{servidor.url}/v2")
        yield modulo.YCloudProvider(), servidor


async def test_si_ycloud_se_calla_un_evento_al_crear_se_pide_otra_vez(ycloud):
    proveedor, servidor = ycloud
    httpx.put(f"{servidor.url}/_mock/eventos-ignorados", json={"eventos": [ECOS], "solo_al_crear": True})

    r = await proveedor.create_or_update_webhook_endpoint(URL)

    assert r["eventos_faltantes"] == [], "con un segundo intento YCloud sí lo activa: no hay que avisar"
    endpoint = (await proveedor.list_webhook_endpoints())[0]
    assert ECOS in endpoint["enabledEvents"]


async def test_si_ycloud_no_lo_activa_se_dice_cual_falta(ycloud):
    proveedor, servidor = ycloud
    httpx.put(f"{servidor.url}/_mock/eventos-ignorados", json={"eventos": [ECOS]})

    r = await proveedor.create_or_update_webhook_endpoint(URL)
    assert r["eventos_faltantes"] == [ECOS], "el endpoint quedó sin ecos y nadie lo dice"
    assert (await proveedor.eventos_faltantes(URL)) == {"existe": True, "faltan": [ECOS]}

    # Alguien lo marca a mano en la consola de YCloud y le da a «Volver a comprobar».
    httpx.put(f"{servidor.url}/_mock/eventos-ignorados", json={"eventos": []})
    endpoint = (await proveedor.list_webhook_endpoints())[0]
    httpx.patch(
        f"{servidor.url}/v2/webhookEndpoints/{endpoint['id']}",
        headers={"X-API-Key": "mock-key"},
        json={"enabledEvents": list(proveedor.EVENTOS_WEBHOOK)},
    )
    assert (await proveedor.eventos_faltantes(URL)) == {"existe": True, "faltan": []}


async def test_sin_webhook_en_ycloud_se_dice_que_no_existe(ycloud):
    proveedor, _ = ycloud
    r = await proveedor.eventos_faltantes(URL)
    assert r["existe"] is False


# ---------------------------------------------------------------------------
# El aviso llega a /admin/channels/estado y «Volver a comprobar» lo quita
# ---------------------------------------------------------------------------


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


@pytest.mark.skipif(not _db_available(), reason="DB no disponible (se ejecuta en CI con Postgres)")
def test_el_aviso_sale_en_el_estado_y_volver_a_comprobar_lo_quita(monkeypatch):
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.main import app
    from app.models.channel import Channel, ChannelType
    from app.models.user import User
    from app.providers.whatsapp import ycloud as modulo

    respuesta = {"existe": True, "faltan": []}

    async def _comprobar(self, url):
        return respuesta

    monkeypatch.setattr(modulo.YCloudProvider, "eventos_faltantes", _comprobar)

    async def _run():
        async with db_session() as db:
            ya_hay = (
                await db.execute(select(Channel.id).where(Channel.type == ChannelType.whatsapp))
            ).first()
            if ya_hay:
                pytest.skip("la base de pruebas ya tiene un canal de WhatsApp")
            admin = User(
                email=f"eventos-{uuid.uuid4().hex[:8]}@ejemplo.com",
                password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
                nombre="Admin Eventos",
                role="admin",
            )
            ch = Channel(
                type=ChannelType.whatsapp,
                name="WhatsApp",
                enabled=True,
                config={"provider": "ycloud", "webhook_eventos_faltantes": [ECOS]},
            )
            db.add_all([admin, ch])
            await db.commit()
            await db.refresh(admin)
            await db.refresh(ch)
        h = {"Authorization": f"Bearer {create_access_token(subject=str(admin.id), extra={'role': 'admin'})}"}
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/api/v1/admin/channels/estado", headers=h)
                assert r.status_code == 200, r.text
                wa = next(c for c in r.json()["channels"] if c["canal"] == "whatsapp")
                assert wa["avisos"] and ECOS in wa["avisos"][0], wa

                r = await client.post("/api/v1/admin/channels/whatsapp/comprobar-eventos", headers=h)
                assert r.status_code == 200, r.text
                assert r.json() == {"existe": True, "faltan": []}

                r = await client.get("/api/v1/admin/channels/estado", headers=h)
                wa = next(c for c in r.json()["channels"] if c["canal"] == "whatsapp")
                assert wa["avisos"] == [], "marcado a mano y comprobado, el aviso sigue ahí"
        finally:
            async with db_session() as db:
                await db.execute(delete(Channel).where(Channel.id == ch.id))
                await db.execute(delete(User).where(User.id == admin.id))
                await db.commit()

    asyncio.run(_run())
