"""Rotar el secreto de firma del webhook de YCloud desde el panel.

El agujero que tapaba: el alta del webhook es idempotente, así que reinstalar
sobre una cuenta que ya lo tenía devuelve `reused=true` — y YCloud SOLO entrega
el secreto al crear el endpoint, nunca al actualizarlo. Quien caía ahí se
quedaba con el canal conectado, los mensajes entrando y la firma sin poder
validarse, y no había ninguna forma de arreglarlo desde el panel: la llamada de
rotación existía en el proveedor y no la exponía nadie.

Por HTTP y no llamando a la función: los permisos viven en las dependencias y
no se ejecutan si invocas el endpoint a mano.

DB-gated: se salta si no hay Postgres.
"""
from __future__ import annotations

import asyncio
import uuid

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

_CLAVES = (
    "ycloud_api_key",
    "ycloud_webhook_secret",
    "ycloud_phone_number",
    "meta_wa_app_secret",
)
SECRETO_VIEJO = "whsec_el_de_antes_000000"
SECRETO_NUEVO = "whsec_recien_rotado_1111"
RUTA = "/api/v1/admin/channels/whatsapp/rotate-webhook-secret"


def _client():
    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _crear_usuario(role: str) -> tuple[uuid.UUID, str]:
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.models.user import User

    async with db_session() as db:
        u = User(
            email=f"wa-rot-{uuid.uuid4().hex[:8]}@test.local",
            password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
            nombre=f"Usuario {role}",
            role=role,
        )
        db.add(u)
        await db.commit()
        uid = u.id
    return uid, create_access_token(subject=str(uid), extra={"role": role})


def _ycloud_simulado(monkeypatch, *, endpoints, rotar) -> None:
    """Dobla las dos llamadas del proveedor a YCloud.

    Se doblan los métodos y no el transporte HTTP: `httpx.AsyncClient` es la
    misma clase con la que se monta el cliente ASGI del panel y de la que
    hereda el SDK de OpenAI, así que sustituirla entera revienta el import de
    `app.main`. Lo que hace cada método contra la API de verdad ya está
    cubierto en `test_ycloud_webhook_alta_y_firma.py`; aquí se prueba el
    endpoint: permisos, qué se guarda y qué error sale por cada fallo.

    `endpoints` y `rotar` aceptan una excepción en vez de un valor, y entonces
    se lanza.
    """
    from app.providers.whatsapp.ycloud import YCloudProvider

    async def _list(self):
        if isinstance(endpoints, Exception):
            raise endpoints
        return endpoints

    async def _rotate(self, endpoint_id: str):
        if isinstance(rotar, Exception):
            raise rotar
        return rotar

    monkeypatch.setattr(YCloudProvider, "list_webhook_endpoints", _list)
    monkeypatch.setattr(YCloudProvider, "rotate_webhook_secret", _rotate)


class _Aislamiento:
    """Aparta el canal de WhatsApp y las credenciales globales, y los repone."""

    def __init__(self) -> None:
        self.canales: list[uuid.UUID] = []
        self.creds: dict[str, bytes] = {}
        self.user_ids: list[uuid.UUID] = []
        self.creados: list[uuid.UUID] = []

    async def __aenter__(self):
        from sqlalchemy import select, text

        from app.db.session import db_session
        from app.models.channel import Channel, ChannelType
        from app.models.credential import Credential

        async with db_session() as db:
            self.canales = list(
                (
                    await db.execute(
                        select(Channel.id).where(Channel.type == ChannelType.whatsapp)
                    )
                ).scalars().all()
            )
            for cid in self.canales:
                await db.execute(
                    text("UPDATE channels SET type = 'webchat' WHERE id = :i"), {"i": str(cid)}
                )
            self.creds = {
                c.key: c.value_encrypted
                for c in (
                    await db.execute(select(Credential).where(Credential.key.in_(_CLAVES)))
                ).scalars().all()
            }
            await db.execute(
                text("DELETE FROM credentials WHERE key = ANY(:k)"), {"k": list(_CLAVES)}
            )
            await db.commit()
        return self

    async def __aexit__(self, *_exc):
        from sqlalchemy import text

        from app.db.session import db_session
        from app.models.credential import Credential
        from app.providers.whatsapp import invalidate_provider_cache
        from app.services.credentials import invalidate_credential_cache

        async with db_session() as db:
            for cid in self.creados:
                await db.execute(text("DELETE FROM channels WHERE id = :i"), {"i": str(cid)})
            await db.execute(
                text("DELETE FROM credentials WHERE key = ANY(:k)"), {"k": list(_CLAVES)}
            )
            for key, blob in self.creds.items():
                db.add(Credential(key=key, value_encrypted=blob))
            for cid in self.canales:
                await db.execute(
                    text("UPDATE channels SET type = 'whatsapp' WHERE id = :i"), {"i": str(cid)}
                )
            for uid in self.user_ids:
                await db.execute(
                    text("DELETE FROM audit_log WHERE user_id = :i"), {"i": str(uid)}
                )
                await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(uid)})
            await db.commit()
        for key in _CLAVES:
            await invalidate_credential_cache(key)
        await invalidate_provider_cache()


async def _canal_ycloud(iso: _Aislamiento) -> None:
    """Deja el canal de YCloud dado de alta con un secreto viejo guardado."""
    from app.core.encryption import get_encryption_service
    from app.db.session import db_session
    from app.models.channel import Channel, ChannelType
    from app.models.credential import Credential
    from app.services.credentials import invalidate_credential_cache

    enc = get_encryption_service()
    async with db_session() as db:
        for key, valor in (
            ("ycloud_api_key", "ycl_de_prueba"),
            ("ycloud_webhook_secret", SECRETO_VIEJO),
            ("ycloud_phone_number", "+529981234567"),
        ):
            db.add(Credential(key=key, value_encrypted=enc.encrypt(valor)))
        ch = Channel(
            type=ChannelType.whatsapp,
            name="WhatsApp (YCloud)",
            enabled=True,
            config={"provider": "ycloud", "phone_number": "+529981234567"},
        )
        db.add(ch)
        await db.commit()
        iso.creados.append(ch.id)
    for key in _CLAVES:
        await invalidate_credential_cache(key)


def _url_webhook() -> str:
    from app.core.config import settings

    return f"{settings.APP_BASE_URL.rstrip('/')}/api/v1/webhooks/ycloud"


# ---------------------------------------------------------------------------


def test_rotar_guarda_el_secreto_nuevo_y_no_lo_devuelve(monkeypatch):
    """Lo que de verdad importa: que el secreto nuevo quede cifrado en la base
    de datos y que NO viaje al navegador. Si volviera en la respuesta acabaría
    en una captura de pantalla o en el log del proxy."""
    from sqlalchemy import select

    from app.core.encryption import get_encryption_service
    from app.db.session import db_session
    from app.models.credential import Credential

    url = _url_webhook()
    _ycloud_simulado(
        monkeypatch,
        endpoints=[{"id": "wh_7", "url": url}],
        rotar=SECRETO_NUEVO,
    )

    async def _run():
        async with _Aislamiento() as iso:
            uid, token = await _crear_usuario("admin")
            iso.user_ids.append(uid)
            await _canal_ycloud(iso)
            async with _client() as c:
                r = await c.post(RUTA, headers=_headers(token))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["endpoint_id"] == "wh_7"
            assert body["rotado"] is True
            assert body["webhook_url"] == url
            assert SECRETO_NUEVO not in r.text
            assert "secret" not in {k.lower() for k in body}

            enc = get_encryption_service()
            async with db_session() as db:
                cred = (
                    await db.execute(
                        select(Credential).where(Credential.key == "ycloud_webhook_secret")
                    )
                ).scalar_one()
            assert enc.decrypt(cred.value_encrypted) == SECRETO_NUEVO

    asyncio.run(_run())


def test_sin_webhook_dado_de_alta_no_rota_nada(monkeypatch):
    """Si en YCloud no hay endpoint con nuestra URL, rotar no tiene sentido: se
    dice qué pasa y se manda a conectar, en vez de crear uno a medias."""
    _ycloud_simulado(monkeypatch, endpoints=[], rotar=SECRETO_NUEVO)

    async def _run():
        async with _Aislamiento() as iso:
            uid, token = await _crear_usuario("admin")
            iso.user_ids.append(uid)
            await _canal_ycloud(iso)
            async with _client() as c:
                r = await c.post(RUTA, headers=_headers(token))
            assert r.status_code == 404, r.text
            assert "conectar whatsapp" in r.json()["detail"].lower()

    asyncio.run(_run())


def test_el_secreto_viejo_sigue_puesto_si_ycloud_falla(monkeypatch):
    """Si la rotación revienta a mitad, lo peor sería quedarse sin secreto: la
    instalación rechazaría TODO lo que entrara. Se conserva el de antes."""
    from sqlalchemy import select

    from app.core.encryption import get_encryption_service
    from app.db.session import db_session
    from app.models.credential import Credential

    url = _url_webhook()
    _ycloud_simulado(
        monkeypatch,
        endpoints=[{"id": "wh_7", "url": url}],
        rotar=RuntimeError("YCloud devolvió 500"),
    )

    async def _run():
        async with _Aislamiento() as iso:
            uid, token = await _crear_usuario("admin")
            iso.user_ids.append(uid)
            await _canal_ycloud(iso)
            async with _client() as c:
                r = await c.post(RUTA, headers=_headers(token))
            assert r.status_code == 502, r.text

            enc = get_encryption_service()
            async with db_session() as db:
                cred = (
                    await db.execute(
                        select(Credential).where(Credential.key == "ycloud_webhook_secret")
                    )
                ).scalar_one()
            assert enc.decrypt(cred.value_encrypted) == SECRETO_VIEJO

    asyncio.run(_run())


def test_en_meta_no_se_rota_por_api(monkeypatch):
    """El secreto de Meta es el App secret de su app: se cambia en su panel."""
    from sqlalchemy import text

    from app.db.session import db_session

    _ycloud_simulado(monkeypatch, endpoints=[], rotar=SECRETO_NUEVO)

    async def _run():
        async with _Aislamiento() as iso:
            uid, token = await _crear_usuario("admin")
            iso.user_ids.append(uid)
            await _canal_ycloud(iso)
            async with db_session() as db:
                await db.execute(
                    text(
                        "UPDATE channels SET config = jsonb_set(config, '{provider}', '\"meta\"') "
                        "WHERE id = :i"
                    ),
                    {"i": str(iso.creados[0])},
                )
                await db.commit()
            async with _client() as c:
                r = await c.post(RUTA, headers=_headers(token))
            assert r.status_code == 400, r.text
            assert "App secret" in r.json()["detail"]

    asyncio.run(_run())


def test_una_operadora_sin_admin_no_puede_rotar_el_secreto(monkeypatch):
    """Rotar corta la entrada de mensajes si algo sale mal: es cosa de admin."""
    url = _url_webhook()
    _ycloud_simulado(
        monkeypatch,
        endpoints=[{"id": "wh_7", "url": url}],
        rotar=SECRETO_NUEVO,
    )

    async def _run():
        async with _Aislamiento() as iso:
            uid, token = await _crear_usuario("cliente")
            iso.user_ids.append(uid)
            await _canal_ycloud(iso)
            async with _client() as c:
                r = await c.post(RUTA, headers=_headers(token))
            assert r.status_code == 403, r.text

    asyncio.run(_run())


def test_si_ycloud_rota_pero_no_se_guarda_se_dice_con_todas_las_letras(monkeypatch):
    """El único estado de verdad malo: YCloud ya cambió la clave y aquí no se
    pudo guardar. La instalación queda rechazando todo lo que entre y el
    síntoma (nada llega al inbox) está lejísimos de la causa, así que la
    respuesta tiene que decirlo y mandar a reintentar."""
    url = _url_webhook()
    _ycloud_simulado(
        monkeypatch,
        endpoints=[{"id": "wh_7", "url": url}],
        rotar=SECRETO_NUEVO,
    )

    async def _explota(*_a, **_k):
        raise RuntimeError("la base de datos se cayó justo aquí")

    monkeypatch.setattr("app.api.admin._write_credential_value", _explota)

    async def _run():
        async with _Aislamiento() as iso:
            uid, token = await _crear_usuario("admin")
            iso.user_ids.append(uid)
            await _canal_ycloud(iso)
            async with _client() as c:
                r = await c.post(RUTA, headers=_headers(token))
            assert r.status_code == 500, r.text
            detalle = r.json()["detail"]
            assert "rechazar" in detalle, detalle
            assert "Vuelve a darle al botón" in detalle, detalle
            # Y nunca el secreto, ni siquiera cuando todo se va al traste.
            assert SECRETO_NUEVO not in r.text

    asyncio.run(_run())
