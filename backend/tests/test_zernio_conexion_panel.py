"""Conectar Instagram y Messenger por Zernio desde el panel, y el aviso de privacidad.

Por HTTP contra la app real, con el simulador de Zernio montado en memoria
(sin puertos). Si esto falla:
  - una llave mala se guardaría como buena y no entraría nada;
  - conectar no registraría el aviso de mensajes, o dejaría el viejo vivo y
    Zernio mandaría cada mensaje dos veces;
  - la API key o el secreto del webhook saldrían en claro en el panel;
  - el historial no se traería al conectar;
  - las páginas /privacidad y /terminos no se podrían leer sin login, o
    cualquiera podría cambiar a quién van las solicitudes de datos.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import MagicMock, patch

import httpx
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

_TIPOS = ("instagram_dm", "messenger", "whatsapp")


async def _admin() -> tuple[uuid.UUID, str]:
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.models.user import User

    async with db_session() as db:
        user = User(
            email=f"zernio-{uuid.uuid4().hex[:8]}@test.local",
            password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
            nombre="Admin Zernio",
            role="admin",
        )
        db.add(user)
        await db.commit()
        return user.id, create_access_token(subject=str(user.id), extra={"role": "admin"})


async def _foto_canales() -> dict:
    """Los canales que la conexión puede tocar, tal cual están, para dejarlos
    igual al terminar: la BD de pruebas la comparten otras pruebas."""
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.channel import Channel, ChannelType

    async with db_session() as db:
        filas = (
            await db.execute(select(Channel).where(Channel.type.in_([ChannelType(t) for t in _TIPOS])))
        ).scalars().all()
        return {
            c.id: (dict(c.config or {}), c.credentials_encrypted, c.enabled, c.name, c.agent_id)
            for c in filas
        }


async def _restaurar(foto: dict, user_id: uuid.UUID) -> None:
    from sqlalchemy import delete, select

    from app.db.session import db_session
    from app.models.channel import Channel, ChannelType
    from app.models.user import User

    async with db_session() as db:
        filas = (
            await db.execute(select(Channel).where(Channel.type.in_([ChannelType(t) for t in _TIPOS])))
        ).scalars().all()
        for c in filas:
            if c.id not in foto:
                await db.delete(c)
                continue
            c.config, c.credentials_encrypted, c.enabled, c.name, c.agent_id = foto[c.id]
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


def _correr(escenario):
    from app.main import app
    from app.providers.zernio.client import ZernioClient
    from tests.mocks import zernio_mock

    simulador = zernio_mock.crear_app()
    real = ZernioClient

    def _cliente(llave, **_):
        return real(llave, base_url="http://zernio/api/v1", transport=httpx.ASGITransport(app=simulador))

    async def _run():
        foto = await _foto_canales()
        user_id, token = await _admin()
        backfill = MagicMock()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                with patch("app.api.zernio_admin.ZernioClient", _cliente), patch(
                    "app.tasks.zernio_backfill.zernio_backfill", backfill
                ):
                    await escenario(c, {"Authorization": f"Bearer {token}"}, simulador, backfill)
        finally:
            await _restaurar(foto, user_id)

    asyncio.run(_run())


def test_una_llave_mala_se_explica_y_una_buena_ensena_las_cuentas():
    async def escenario(c, cab, simulador, backfill):
        r = await c.post("/api/v1/admin/zernio/probar", json={"api_key": "zk-invalid"}, headers=cab)
        assert r.status_code == 400 and "API key" in r.json()["detail"]
        r = await c.post("/api/v1/admin/zernio/probar", json={"api_key": "zk-buena"}, headers=cab)
        assert r.status_code == 200
        assert {x["platform"] for x in r.json()} == {"instagram", "facebook"}

    _correr(escenario)


def test_conectar_crea_los_canales_registra_el_aviso_y_trae_el_historial():
    async def escenario(c, cab, simulador, backfill):
        from app.api.zernio_admin import webhook_url

        r = await c.post("/api/v1/admin/zernio", json={"api_key": "zk-buena"}, headers=cab)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["webhook_registrado"] is True and out["webhook_secret_manual"] is None
        assert {x["tipo"] for x in out["canales"]} == {"instagram_dm", "messenger"}
        assert backfill.delay.call_count == 2

        registrados = list(simulador.state.zernio.webhooks.values())
        assert len(registrados) == 1
        assert registrados[0]["url"] == webhook_url()
        assert set(registrados[0]["events"]) >= {"message.received", "message.sent"}

        # Ni la llave ni el secreto salen en claro por el panel.
        canales = (await c.get("/api/v1/admin/channels", headers=cab)).json()
        for ch in canales:
            if ch["type"] in ("instagram_dm", "messenger") and ch["config"].get("transport") == "zernio":
                assert ch["config"].get("zernio_api_key") != "zk-buena"
                assert ch["config"].get("zernio_webhook_secret") != registrados[0]["secret"]

        # Reconectar no deja el aviso viejo vivo (si no, llegaría todo dos veces).
        r = await c.post("/api/v1/admin/zernio", json={}, headers=cab)
        assert r.status_code == 200, r.text
        assert len(simulador.state.zernio.webhooks) == 1
        assert backfill.delay.call_count == 2, "misma cuenta: el historial ya se trajo"

        # Si una importación se cortó, se puede volver a pedir a mano, también
        # con los canales apagados (el historial no despierta a Sofía).
        from sqlalchemy import update

        from app.db.session import db_session
        from app.models.channel import Channel

        ids = [x["channel_id"] for x in out["canales"]]
        async with db_session() as db:
            await db.execute(update(Channel).where(Channel.id.in_(ids)).values(enabled=False))
            await db.commit()
        r = await c.post("/api/v1/admin/zernio/historial", headers=cab)
        assert r.status_code == 200 and r.json()["canales"] == 2
        assert backfill.delay.call_count == 4

    _correr(escenario)


def test_sin_aviso_por_api_se_devuelven_url_y_secreto_para_pegarlo():
    async def escenario(c, cab, simulador, backfill):
        from app.providers.zernio.client import ZernioClient, ZernioError

        async def _falla(*a, **k):
            raise ZernioError("no disponible", status=503)

        with patch.object(ZernioClient, "register_webhook", _falla):
            r = await c.post("/api/v1/admin/zernio", json={"api_key": "zk-buena"}, headers=cab)
        out = r.json()
        assert r.status_code == 200 and out["webhook_registrado"] is False
        assert out["webhook_secret_manual"] and out["webhook_url"].endswith("/webhooks/zernio")

    _correr(escenario)


def test_el_aviso_de_privacidad_se_lee_sin_login_y_solo_lo_cambia_un_admin():
    async def escenario(c, cab, simulador, backfill):
        datos = {
            "responsable": "Clínica Prueba S.A. de C.V.",
            "domicilio": "Av. Tulum 1, Cancún",
            "correo": "privacidad@clinica-prueba.mx",
            "telefono": "",
        }
        assert (await c.put("/api/v1/legal", json=datos)).status_code == 401
        malo = await c.put("/api/v1/legal", json={**datos, "correo": "no-es-correo"}, headers=cab)
        assert malo.status_code == 422
        r = await c.put("/api/v1/legal", json=datos, headers=cab)
        assert r.status_code == 200 and r.json()["completo"] is True
        publico = (await c.get("/api/v1/legal")).json()
        assert publico["correo"] == "privacidad@clinica-prueba.mx" and publico["actualizado"]

    async def _limpiar():
        from sqlalchemy import delete

        from app.db.session import db_session
        from app.models.app_setting import AppSetting

        async with db_session() as db:
            await db.execute(delete(AppSetting).where(AppSetting.key.like("legal.%")))
            await db.commit()

    try:
        _correr(escenario)
    finally:
        asyncio.run(_limpiar())
