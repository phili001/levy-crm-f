"""Conectar el buzón desde el panel: lo que se guarda, lo que no y lo que nunca
sale por la API.

Por HTTP con un JWT de verdad (los permisos viven en las dependencias). IMAP,
SMTP y DNS son dobles: se prueba la regla, no la red.
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
    not _db_available(), reason="DB no disponible (se ejecuta en CI con Postgres)"
)

URL = "/api/v1/admin/channels/email/imap"
BUZON = {"preset": "hostinger", "usuario": "recepcion@clinica.mx", "password": "clave-de-app"}


class _Red:
    def __init__(self) -> None:
        self.falla_smtp = False
        self.smtp_hosts: list[str] = []
        self.imap_hosts: list[str] = []


def _dobles(monkeypatch) -> _Red:
    from app.providers.email import imap_client
    from app.services.correo_smtp import EnvioSmtpError

    red = _Red()
    monkeypatch.setattr(
        imap_client, "es_microsoft", lambda correo, **k: correo.endswith(("@hotmail.com", "@m365.mx"))
    )
    monkeypatch.setattr(imap_client, "probar", lambda cfg: red.imap_hosts.append(cfg.servidor.host))
    monkeypatch.setattr(imap_client, "detectar_enviados", lambda cfg: "INBOX.Sent")

    def smtp(servidor, usuario, password):
        red.smtp_hosts.append(servidor.host)
        if red.falla_smtp:
            raise EnvioSmtpError("SMTP: usuario o contraseña incorrectos.")

    monkeypatch.setattr("app.api.correo_imap.probar_smtp", smtp)
    return red


def _correr(prueba, monkeypatch):
    import httpx
    from sqlalchemy import text, update

    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.main import app
    from app.models.channel import Channel, ChannelType
    from app.models.user import User

    red = _dobles(monkeypatch)

    async def _run():
        suf = uuid.uuid4().hex[:8]
        async with db_session() as db:
            # Un solo canal de correo: el que cree esta prueba.
            await db.execute(update(Channel).where(Channel.type == ChannelType.email).values(type=ChannelType.webchat, enabled=False))
            admin = User(email=f"adm-{suf}@test.local", password_hash=hash_password("UnaClaveLargaDeVerdad2026"), role="admin")
            db.add(admin)
            await db.commit()
            token = create_access_token(subject=str(admin.id), extra={"role": "admin"})
            admin_id = admin.id
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                await prueba(c, {"Authorization": f"Bearer {token}"}, red)
        finally:
            async with db_session() as db:
                await db.execute(text("DELETE FROM channels WHERE type = 'email'"))
                await db.execute(text("DELETE FROM audit_log WHERE user_id = :i"), {"i": str(admin_id)})
                await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(admin_id)})
                await db.commit()

    asyncio.run(_run())


def test_outlook_y_microsoft_365_se_bloquean_antes_de_intentar(monkeypatch):
    async def prueba(c, h, red):
        r = await c.post(f"{URL}/probar", json={**BUZON, "usuario": "clinica@hotmail.com"}, headers=h)
        assert r.status_code == 200
        cuerpo = r.json()
        assert not cuerpo["imap_ok"] and "Microsoft" in cuerpo["errores"][0]
        assert red.imap_hosts == [] and red.smtp_hosts == []

    _correr(prueba, monkeypatch)


def test_si_la_prueba_falla_no_se_guarda_nada(monkeypatch):
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.channel import Channel, ChannelType

    async def prueba(c, h, red):
        red.falla_smtp = True
        r = await c.post(URL, json=BUZON, headers=h)
        assert r.status_code == 400
        assert "usuario o contraseña" in r.json()["detail"]
        async with db_session() as db:
            assert (
                await db.execute(select(Channel).where(Channel.type == ChannelType.email))
            ).scalar_one_or_none() is None

    _correr(prueba, monkeypatch)


def test_el_buzon_conectado_no_ensena_su_contrasena_y_usa_los_servidores_del_preset(monkeypatch):
    async def prueba(c, h, red):
        r = await c.post(
            URL, json={**BUZON, "auto_send": True, "no_responder": ["@banco.mx", " "]}, headers=h
        )
        assert r.status_code == 200, r.text
        assert r.json()["carpeta_enviados"] == "INBOX.Sent"
        assert red.imap_hosts == ["imap.hostinger.com"] and red.smtp_hosts == ["smtp.hostinger.com"]

        canales = (await c.get("/api/v1/admin/channels", headers=h)).json()
        [correo] = [x for x in canales if x["type"] == "email"]
        cfg = correo["config"]
        assert cfg["transport"] == "imap" and cfg["auto_send"] is True
        assert cfg["no_responder"] == ["@banco.mx"]
        assert "clave-de-app" not in str(correo)

    _correr(prueba, monkeypatch)


def test_la_contrasena_guardada_no_se_reutiliza_contra_otro_servidor(monkeypatch):
    async def prueba(c, h, red):
        assert (await c.post(URL, json=BUZON, headers=h)).status_code == 200
        sin_clave = {k: v for k, v in BUZON.items() if k != "password"}

        # Editar el mismo buzón sin reescribir la contraseña: vale.
        assert (await c.post(f"{URL}/probar", json=sin_clave, headers=h)).status_code == 200

        # Apuntar a otro servidor sin escribirla: no se le manda la guardada.
        otro = {
            **sin_clave,
            "preset": "otro",
            "imap": {"host": "imap.atacante.test", "port": 993, "seguridad": "ssl"},
            "smtp": {"host": "smtp.atacante.test", "port": 465, "seguridad": "ssl"},
        }
        r = await c.post(f"{URL}/probar", json=otro, headers=h)
        assert r.status_code == 422
        assert "imap.atacante.test" not in red.imap_hosts

    _correr(prueba, monkeypatch)
