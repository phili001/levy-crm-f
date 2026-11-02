"""Conectar la voz desde el CRM y proteger sus reglas, comprobado por HTTP (Fase 8.1).

Por HTTP y no llamando a las funciones: los permisos, la validación de la
entrada y los commits viven en el endpoint. Retell va detrás de su simulador
(`tests/mocks/retell_mock.py`) y Twilio detrás del falso de
`test_voz_numero_de_twilio_por_troncal.py`; solo se les cambia el transporte.

DB-gated: se salta si no hay Postgres.
"""
from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import text

from app.scripts.profiles import dental_mx


def _db_available() -> bool:
    try:
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

TOKEN_TWILIO = "token-de-twilio-que-no-se-guarda"


class _Sesion:
    def __init__(self) -> None:
        self.user_id: uuid.UUID | None = None
        self.token = ""
        self.agent_ids: list[str] = []
        self.token_ids: list[str] = []
        self.apartados: list[str] = []


async def _entrar() -> _Sesion:
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.models.user import User

    s = _Sesion()
    async with db_session() as db:
        u = User(
            email=f"voz-{uuid.uuid4().hex[:8]}@test.local",
            password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
            nombre="Admin Voz",
            role="admin",
        )
        db.add(u)
        await db.commit()
        s.user_id = u.id
    s.token = create_access_token(subject=str(s.user_id), extra={"role": "admin"})
    return s


async def _apartar_canales_de_voz(s: _Sesion) -> None:
    """El canal de voz es uno por instalación (se usa el más antiguo): los que
    ya hubiera se apartan y se devuelven al final."""
    from app.db.session import db_session

    async with db_session() as db:
        filas = await db.execute(text("SELECT id FROM channels WHERE type = 'retell_voice'"))
        s.apartados = [str(i) for (i,) in filas]
        for cid in s.apartados:
            await db.execute(text("UPDATE channels SET type = 'webchat' WHERE id = :i"), {"i": cid})
        await db.commit()


async def _salir(s: _Sesion) -> None:
    from app.db.session import db_session

    async with db_session() as db:
        await db.execute(text("DELETE FROM channels WHERE type = 'retell_voice'"))
        for cid in s.apartados:
            await db.execute(
                text("UPDATE channels SET type = 'retell_voice' WHERE id = :i"), {"i": cid}
            )
        for aid in s.agent_ids:
            await db.execute(text("DELETE FROM agents WHERE id = :i"), {"i": aid})
        for tid in s.token_ids:
            await db.execute(text("DELETE FROM agent_tokens WHERE id = :i"), {"i": tid})
        await db.execute(text("DELETE FROM audit_log WHERE user_id = :i"), {"i": str(s.user_id)})
        await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(s.user_id)})
        await db.commit()


def _cliente() -> httpx.AsyncClient:
    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _sesion_bd():
    from app.db.session import db_session

    return db_session()


@pytest.fixture
def proveedores(monkeypatch):
    """Retell y Twilio del CRM apuntando a sus simuladores en memoria."""
    from app.providers.voice import retell_admin, twilio_sip
    from app.services import voz_alta
    from tests.mocks import retell_mock
    from tests.test_voz_numero_de_twilio_por_troncal import _TwilioFalso

    retell = retell_mock.crear_app()
    a_retell = httpx.ASGITransport(app=retell)
    original = retell_admin.RetellAdmin

    def admin(clave, **_):
        return original(clave, base_url="http://retell", transport=a_retell)

    # `voz_alta` lo importó con su nombre; `enable_signed_recordings` lo busca
    # en el módulo al llamar. Se cambian los dos.
    monkeypatch.setattr(voz_alta, "RetellAdmin", admin)
    monkeypatch.setattr(retell_admin, "RetellAdmin", admin)

    twilio = _TwilioFalso()
    monkeypatch.setattr(
        voz_alta,
        "TwilioSip",
        lambda sid, token: twilio_sip.TwilioSip(sid, token, transport=twilio.transporte()),
    )
    control = httpx.AsyncClient(transport=a_retell, base_url="http://retell")
    return control, twilio


async def _canal_de_voz() -> tuple[dict, bytes | None]:
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.channel import Channel, ChannelType

    async with db_session() as db:
        ch = (
            await db.execute(select(Channel).where(Channel.type == ChannelType.retell_voice))
        ).scalar_one()
        return dict(ch.config or {}), ch.credentials_encrypted


def test_conectar_la_voz_crea_el_agente_y_no_hay_nada_que_pegar(proveedores):
    control, _ = proveedores

    async def _run():
        s = await _entrar()
        await _apartar_canales_de_voz(s)
        try:
            async with _cliente() as c:
                h = {"Authorization": f"Bearer {s.token}"}
                url = "/api/v1/admin/channels/retell/provision"

                r = await c.post(url, headers=h, json={"create_agent": True})
                assert r.status_code == 400, "sin API key no se puede dar de alta"

                r = await c.post(url, headers=h, json={"api_key": "key-invalid", "create_agent": True})
                assert r.status_code == 400
                assert "API key" in r.json()["detail"]
                async with _sesion_bd() as db:
                    quedan = (
                        await db.execute(
                            text("SELECT count(*) FROM channels WHERE type = 'retell_voice'")
                        )
                    ).scalar_one()
                assert quedan == 0, "una llave que Retell rechaza no deja un canal a medias"

                r = await c.post(url, headers=h, json={
                    "api_key": "retell-key", "create_agent": True,
                    "transfer_number": "+52 998 111 2233",
                })
                assert r.status_code == 200, r.text
                d = r.json()
                assert d["agent_created"] is True and d["agent_managed"] is True
                assert [x["name"] for x in d["checks"] if not x["ok"]] == []
                agente = d["agent_id_retell"]
                config, cifrado = await _canal_de_voz()
                assert config["agent_id_retell"] == agente
                assert config["agente_gestionado"] is True
                assert config["transfer_number"] == "+529981112233"
                assert "api_key" not in config and cifrado, "la API key va cifrada"

                # Otra vez, sin volver a mandar la llave: mismo agente.
                r = await c.post(url, headers=h, json={"create_agent": True})
                assert r.status_code == 200, r.text
                assert r.json()["agent_created"] is False
                assert r.json()["agent_id_retell"] == agente
                assert list((await control.get("/_mock/agentes")).json()) == [agente]

                # Una llave mala tecleada encima no pisa la buena: con ella se
                # verifican las llamadas que entran.
                r = await c.post(url, headers=h, json={"api_key": "key-invalid", "create_agent": True})
                assert r.status_code == 400
                r = await c.post(url, headers=h, json={"create_agent": True})
                assert r.status_code == 200, "la llave guardada sigue siendo la buena"

                # Un número que no está en Retell: se explica y el agente sigue.
                r = await c.post(url, headers=h, json={
                    "create_agent": True, "phone_number": "+15550001111",
                })
                assert r.status_code == 400
                assert "no está en tu cuenta de Retell" in r.json()["detail"]
                assert (await _canal_de_voz())[0]["agent_id_retell"] == agente

                # Comprarlo: queda atado y guardado.
                r = await c.post(url, headers=h, json={
                    "create_agent": True, "buy_number": "us", "area_code": 305,
                })
                assert r.status_code == 200, r.text
                telefono = r.json()["phone_number"]
                assert telefono.startswith("+1305")
                assert (await _canal_de_voz())[0]["phone_number"] == telefono
                numero = (await control.get("/_mock/numeros")).json()[telefono]
                assert numero["inbound_agents"][0]["agent_id"] == agente

                r = await c.post(url, headers=h, json={
                    "create_agent": True, "transfer_number": "998 123",
                })
                assert r.status_code == 400, "la recepción va con + y lada de país"

                listado = (await c.get("/api/v1/admin/channels", headers=h)).json()
                voz = next(x for x in listado if x["type"] == "retell_voice")
                assert "retell-key" not in str(voz), "el listado no enseña la llave"
        finally:
            await _salir(s)

    asyncio.run(_run())


def test_un_agente_pegado_a_mano_no_se_repunta_al_crm(proveedores):
    control, _ = proveedores

    async def _run():
        s = await _entrar()
        await _apartar_canales_de_voz(s)
        await control.post("/_mock/agentes", json={"agent_id": "agent_a_mano"})
        try:
            async with _cliente() as c:
                h = {"Authorization": f"Bearer {s.token}"}
                url = "/api/v1/admin/channels/retell/provision"
                r = await c.post(url, headers=h, json={
                    "api_key": "retell-key", "agent_id_retell": "agent_a_mano",
                })
                assert r.status_code == 200, r.text
                assert r.json()["signed_recordings_enabled"] is True

                r = await c.post(url, headers=h, json={"create_agent": True})
                assert r.status_code == 200, r.text
                assert r.json()["agent_created"] is True
                assert r.json()["agent_id_retell"] != "agent_a_mano"
                a_mano = (await control.get("/_mock/agentes")).json()["agent_a_mano"]
                assert a_mano["response_engine"]["llm_websocket_url"] == "wss://a-mano", (
                    "el agente de otro (el del curso, por ejemplo) no se toca"
                )
        finally:
            await _salir(s)

    asyncio.run(_run())


def test_conectar_un_numero_de_twilio_no_guarda_su_token(proveedores):
    control, twilio = proveedores

    async def _run():
        from app.db.session import db_session
        from app.services.channel_secrets import read_channel_secrets
        from tests.test_voz_numero_de_twilio_por_troncal import MX, SID

        s = await _entrar()
        await _apartar_canales_de_voz(s)
        try:
            async with _cliente() as c:
                h = {"Authorization": f"Bearer {s.token}"}
                cuerpo = {"account_sid": SID, "auth_token": "token-de-pruebas-123", "phone_number": MX}
                r = await c.post("/api/v1/admin/channels/retell/twilio", headers=h, json=cuerpo)
                assert r.status_code == 400, "sin agente todavía no hay a quién atar el número"
                assert "Primero conecta la voz" in r.json()["detail"]

                r = await c.post("/api/v1/admin/channels/retell/provision", headers=h, json={
                    "api_key": "retell-key", "create_agent": True,
                })
                agente = r.json()["agent_id_retell"]
                r = await c.post("/api/v1/admin/channels/retell/twilio", headers=h, json=cuerpo)
                assert r.status_code == 200, r.text
                d = r.json()
                assert d["trunk_domain"].endswith(".pstn.twilio.com")
                assert [x["name"] for x in d["checks"] if not x["ok"]] == []
                assert twilio.numeros[MX]["trunk_sid"] == d["trunk_sid"]
                numero = (await control.get("/_mock/numeros")).json()[MX]
                assert numero["inbound_agents"][0]["agent_id"] == agente
                assert numero["sip_outbound_trunk_config"]["termination_uri"] == d["trunk_domain"]

                config, _ = await _canal_de_voz()
                assert config["phone_number"] == MX
                assert config["twilio_trunk_sid"] == d["trunk_sid"]
            async with db_session() as db:
                from sqlalchemy import select

                from app.models.channel import Channel, ChannelType

                ch = (
                    await db.execute(select(Channel).where(Channel.type == ChannelType.retell_voice))
                ).scalar_one()
                guardado = str(ch.config) + str(read_channel_secrets(ch))
                auditoria = str(
                    (
                        await db.execute(
                            text("SELECT after FROM audit_log WHERE user_id = :u"),
                            {"u": str(s.user_id)},
                        )
                    ).all()
                )
            assert "token-de-pruebas-123" not in guardado, "el token de Twilio no se guarda"
            assert "token-de-pruebas-123" not in auditoria
        finally:
            await _salir(s)

    asyncio.run(_run())


def _agente(kind: str, prompt: str) -> dict:
    return {
        "name": f"Voz {uuid.uuid4().hex[:6]}",
        "kind": kind,
        "prompt_system": prompt,
        "model_name": "gpt-4.1-mini",
        "buffer_seconds": 8,
        "response_split_max_parts": 3,
        "context_window": 12,
        "is_active": False,
    }


_SIN_SECCION = dental_mx.PROMPT_VOZ.split("======================================\n5. SEGURIDAD")[0]


def test_ni_el_panel_ni_el_historial_ni_la_agent_api_borran_las_reglas_de_voz():
    async def _run():
        s = await _entrar()
        try:
            async with _cliente() as c:
                h = {"Authorization": f"Bearer {s.token}"}
                r = await c.post("/api/v1/admin/agents", headers=h, json=_agente("voice", _SIN_SECCION))
                assert r.status_code == 200, r.text
                voz = r.json()["id"]
                s.agent_ids.append(voz)

                # Añadir la sección protegida se puede (así se pone al día una
                # instalación vieja) y deja la versión sin ella en el historial.
                r = await c.patch(
                    f"/api/v1/admin/agents/{voz}", headers=h,
                    json=_agente("voice", dental_mx.PROMPT_VOZ),
                )
                assert r.status_code == 200, r.text

                r = await c.patch(
                    f"/api/v1/admin/agents/{voz}", headers=h, json=_agente("voice", _SIN_SECCION)
                )
                assert r.status_code == 400
                assert "SEGURIDAD — NO TOCAR" in r.json()["detail"]

                historial = (
                    await c.get(f"/api/v1/admin/agents/{voz}/prompt-history", headers=h)
                ).json()
                viejo = next(x["id"] for x in historial if x["prompt_system"] == _SIN_SECCION)
                r = await c.post(
                    f"/api/v1/admin/agents/{voz}/prompt-history/{viejo}/restore", headers=h
                )
                assert r.status_code == 400, "restaurar la versión sin reglas es borrarlas"

                r = await c.post("/api/v1/admin/agent-tokens", headers=h, json={
                    "name": "asistente", "scopes": ["prompts:write"],
                })
                assert r.status_code == 200, r.text
                s.token_ids.append(r.json()["id"])
                api = {"Authorization": f"Bearer {r.json()['token']}"}
                r = await c.put(
                    f"/api/v1/agent-api/agents/{voz}/prompt", headers=api,
                    json={"prompt_system": _SIN_SECCION},
                )
                assert r.status_code == 400, "un asistente externo no es la puerta de atrás"

                r = await c.patch(
                    f"/api/v1/admin/agents/{voz}", headers=h,
                    json=_agente("voice", dental_mx.PROMPT_VOZ + "- Nunca hablas de la competencia.\n"),
                )
                assert r.status_code == 200, "añadir reglas sí se puede"

                # En los agentes de texto no aplica (solo lo pide la voz, US-19 AC3).
                r = await c.post(
                    "/api/v1/admin/agents", headers=h, json=_agente("text", dental_mx.PROMPT_VOZ)
                )
                texto = r.json()["id"]
                s.agent_ids.append(texto)
                r = await c.patch(
                    f"/api/v1/admin/agents/{texto}", headers=h, json=_agente("text", _SIN_SECCION)
                )
                assert r.status_code == 200, r.text
        finally:
            await _salir(s)

    asyncio.run(_run())
