"""Lo que YCloud manda de más: reentregas y grupos.

YCloud reintenta hasta 7 veces si no contestamos rápido, con el MISMO evento.
Sin dedupe, un pico de latencia se traducía en el paciente recibiendo la
respuesta del agente tres veces.
"""
from __future__ import annotations

import asyncio

import pytest

from app.providers.whatsapp.ycloud import YCloudProvider


def _entrante(wamid: str = "wamid.AAA", **extra) -> dict:
    msg = {
        "id": wamid,
        "from": "+529981234567",
        "to": "+529990000000",
        "type": "text",
        "text": {"body": "Hola"},
    }
    msg.update(extra)
    return {
        "id": "evt_123",
        "type": "whatsapp.inbound_message.received",
        "whatsappInboundMessage": msg,
    }


def test_un_mensaje_de_grupo_no_entra_al_crm():
    """El agente contestando en un grupo de vecinos es peor que no estar."""
    parsed = YCloudProvider().parse_webhook(_entrante(groupId="120363@g.us"))
    assert parsed == []


def test_un_mensaje_normal_sigue_entrando():
    parsed = YCloudProvider().parse_webhook(_entrante())
    assert len(parsed) == 1
    assert parsed[0].text == "Hola"


def test_el_estado_trae_al_destinatario_para_poder_emparejarlo():
    """Desde 2026 Meta puede no mandar el teléfono: queda el BSUID."""
    estados = YCloudProvider().parse_statuses({
        "type": "whatsapp.message.updated",
        "whatsappMessage": {
            "id": "yc_1",
            "wamid": "wamid.BBB",
            "status": "read",
            "recipientUserId": "bsuid_abc",
        },
    })
    assert len(estados) == 1
    assert estados[0]["recipient_user_id"] == "bsuid_abc"
    assert estados[0]["status"] == "read"


def _db_disponible() -> bool:
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


@pytest.mark.skipif(not _db_disponible(), reason="sin Postgres")
def test_una_reentrega_del_mismo_evento_no_se_procesa_dos_veces():
    """El dedupe vive en Redis y cubre entrantes, estados, ecos e historial."""
    from app.api.webhooks import _evento_nuevo

    async def _run():
        import uuid

        ev = f"evt_{uuid.uuid4().hex[:12]}"
        primera = await _evento_nuevo(ev)
        segunda = await _evento_nuevo(ev)
        return primera, segunda

    primera, segunda = asyncio.run(_run())
    assert primera is True
    assert segunda is False, "la reentrega se habría procesado otra vez"


@pytest.mark.skipif(not _db_disponible(), reason="sin Postgres")
def test_sin_redis_se_deja_pasar_el_evento():
    """Un mensaje duplicado es malo; perderlo del todo es peor."""
    import app.api.webhooks as wh
    from app.core import redis as redis_mod

    class _RedisRoto:
        async def set(self, *a, **k):
            raise RuntimeError("Redis caído")

    original = redis_mod.get_redis
    redis_mod.get_redis = lambda: _RedisRoto()
    try:
        assert asyncio.run(wh._evento_nuevo("evt_sin_redis")) is True
    finally:
        redis_mod.get_redis = original


@pytest.mark.skipif(not _db_disponible(), reason="sin Postgres")
def test_el_filtro_por_numero_deja_pasar_lo_propio_y_descarta_lo_ajeno():
    """Una clave de YCloud puede tener varios números: sin filtro, el agente
    contestaría en nombre de otra clínica."""
    from sqlalchemy import text

    from app.api.webhooks import _numero_del_canal_whatsapp
    from app.db.session import db_session
    from app.models.channel import Channel, ChannelType

    async def _run():
        async with db_session() as db:
            previos = (await db.execute(text("SELECT id FROM channels WHERE type='whatsapp'"))).fetchall()
            if previos:
                await db.execute(text("DELETE FROM channels WHERE type='whatsapp'"))
            db.add(Channel(
                type=ChannelType.whatsapp, name="WhatsApp", enabled=True,
                config={"provider": "ycloud", "phone_number": "+529990000000"},
            ))
            await db.commit()
        try:
            return await _numero_del_canal_whatsapp()
        finally:
            async with db_session() as db:
                await db.execute(text("DELETE FROM channels WHERE type='whatsapp'"))
                await db.commit()

    numero, filtrar = asyncio.run(_run())
    assert numero == "+529990000000"
    assert filtrar is True


@pytest.mark.skipif(not _db_disponible(), reason="sin Postgres")
def test_si_guardar_falla_el_reintento_de_ycloud_si_se_procesa(monkeypatch):
    """Salió en el dry-run 2 (O-1): la marca de «ya visto» se ponía antes de
    guardar y no se soltaba si algo fallaba. YCloud reintentaba, el reintento se
    tiraba como repetido y el mensaje del paciente se perdía sin aviso."""
    import json
    import uuid

    from fastapi.testclient import TestClient

    import app.api.webhooks as wh
    from app.main import app

    class _ProveedorFirmado(YCloudProvider):
        async def verify_webhook_signature(self, headers, body):
            return True

    guardados = []

    async def _guardar(incoming, **_):
        guardados.append(incoming.provider_message_id)
        if len(guardados) == 1:
            raise RuntimeError("la base no contesta")
        return None

    async def _sin_filtro_de_numero():
        return "", False

    monkeypatch.setattr(wh, "get_ycloud_provider", lambda: _ProveedorFirmado())
    monkeypatch.setattr(wh, "store_incoming", _guardar)
    monkeypatch.setattr(wh, "_numero_del_canal_whatsapp", _sin_filtro_de_numero)

    evento = _entrante(wamid=f"wamid.{uuid.uuid4().hex[:10]}")
    evento["id"] = f"evt_{uuid.uuid4().hex[:12]}"
    cuerpo = json.dumps(evento)
    cliente = TestClient(app, raise_server_exceptions=False)
    cabeceras = {"content-type": "application/json"}

    primera = cliente.post("/api/v1/webhooks/ycloud", content=cuerpo, headers=cabeceras)
    reintento = cliente.post("/api/v1/webhooks/ycloud", content=cuerpo, headers=cabeceras)

    assert primera.status_code == 500
    assert reintento.status_code == 200
    assert "repetidos" not in reintento.json()
    assert len(guardados) == 2
