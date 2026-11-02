"""Instagram y Messenger por Zernio, de punta a punta contra la BD.

Por HTTP contra la app real: webhook firmado → mensaje guardado → agente
encolado, y de vuelta el envío por Zernio con el transporte simulado. Los
eventos salen de los reales de `tests/fixtures/zernio/`, con ids nuevos en
cada prueba (y firmados otra vez) para no chocar con pasadas anteriores.

Si esto falla:
  - firma mala o ausente que entra: cualquiera podría meter mensajes;
  - un reintento de Zernio que duplica: el paciente recibe dos respuestas;
  - Messenger que cae en Instagram, o WhatsApp que entra sin el interruptor;
  - el eco de la app que no aparta a Sofía: contesta encima de una persona;
  - el envío que guarda un id que no es el `mid` real, o que da por enviado
    lo que Zernio rechazó (F-1: nada de "enviado" falso).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from pathlib import Path
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

FIXTURES = Path(__file__).parent / "fixtures" / "zernio"
SECRETO = "secreto-de-prueba-zernio"
CUENTA_IG = "000000000000000000000007"
CUENTA_FB = "000000000000000000000012"


def _evento(nombre: str, *, participante: str, mid: str | None = None) -> dict:
    """Evento real con ids nuevos: el participante, el mid y el id de evento."""
    payload = json.loads((FIXTURES / f"{nombre}.json").read_text())
    viejo = payload["conversation"]["participantId"]
    payload["id"] = str(uuid.uuid4())
    payload["conversation"]["participantId"] = participante
    if payload["message"]["sender"]["id"] == viejo:
        payload["message"]["sender"]["id"] = participante
    payload["message"]["platformMessageId"] = mid or f"m_{uuid.uuid4().hex}"
    return payload


def _firmar(payload: dict, secreto: str = SECRETO) -> tuple[bytes, dict]:
    cuerpo = json.dumps(payload).encode()
    firma = hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
    return cuerpo, {"Content-Type": "application/json", "X-Zernio-Signature": firma}


async def _crear_canales() -> list[uuid.UUID]:
    from app.db.session import db_session
    from app.models.channel import Channel, ChannelType
    from app.services.channel_secrets import apply_channel_values

    ids = []
    async with db_session() as db:
        for tipo, cuenta in ((ChannelType.instagram_dm, CUENTA_IG), (ChannelType.messenger, CUENTA_FB)):
            ch = Channel(type=tipo, name=f"Zernio prueba {tipo.value}", enabled=True, config={})
            apply_channel_values(
                ch,
                {
                    "transport": "zernio",
                    "zernio_account_id": cuenta,
                    "zernio_api_key": "sk_prueba",
                    "zernio_webhook_secret": SECRETO,
                },
            )
            db.add(ch)
            await db.flush()
            ids.append(ch.id)
        await db.commit()
    return ids


async def _borrar(canales: list[uuid.UUID], identificadores: list[str]) -> None:
    from sqlalchemy import delete

    from app.db.session import db_session
    from app.models.channel import Channel
    from app.models.contact import Contact

    async with db_session() as db:
        await db.execute(delete(Channel).where(Channel.id.in_(canales)))
        await db.execute(delete(Contact).where(Contact.telefono.in_(identificadores)))
        await db.commit()


async def _conversacion_de(identificador: str):
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.contact import Contact
    from app.models.conversation import Conversation
    from app.models.message import Message

    async with db_session() as db:
        contacto = (
            await db.execute(select(Contact).where(Contact.telefono == identificador))
        ).scalar_one_or_none()
        if contacto is None:
            return None, []
        conv = (
            await db.execute(select(Conversation).where(Conversation.contact_id == contacto.id))
        ).scalar_one_or_none()
        mensajes = (
            await db.execute(
                select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at)
            )
        ).scalars().all() if conv else []
    return conv, mensajes


def _psid() -> str:
    return str(uuid.uuid4().int % 10**16)


def _correr(escenario):
    """Canales de prueba + cliente HTTP contra la app, y limpieza al final."""
    from app.main import app

    async def _run():
        canales = await _crear_canales()
        creados: list[str] = []
        encolar = MagicMock()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                with patch("app.api.webhooks.task_process", encolar):
                    await escenario(c, creados, encolar)
        finally:
            await _borrar(canales, creados)

    asyncio.run(_run())


URL = "/api/v1/webhooks/zernio"


def test_firma_mala_o_ausente_da_401_y_no_guarda_nada():
    async def escenario(c, creados, encolar):
        psid = _psid()
        creados.append(f"ig:{psid}")
        cuerpo, cab = _firmar(_evento("ig_message_received", participante=psid), "otro-secreto")
        assert (await c.post(URL, content=cuerpo, headers=cab)).status_code == 401
        sin = {"Content-Type": "application/json"}
        assert (await c.post(URL, content=cuerpo, headers=sin)).status_code == 401
        conv, _ = await _conversacion_de(f"ig:{psid}")
        assert conv is None
        encolar.delay.assert_not_called()

    _correr(escenario)


def test_el_aviso_de_prueba_de_zernio_firmado_da_200():
    """«Probar aviso» del panel: Zernio manda `webhook.test` con `message` como
    texto. Con la firma buena tiene que dar 200, o Zernio da la prueba por
    fallida («Test webhook failed»)."""

    async def escenario(c, creados, encolar):
        cuerpo, cab = _firmar({"id": f"evt_{uuid.uuid4().hex}", "event": "webhook.test",
                               "message": "This is a test webhook from Zernio",
                               "timestamp": "2026-10-12T00:00:00Z"})
        assert (await c.post(URL, content=cuerpo, headers=cab)).status_code == 200
        encolar.delay.assert_not_called()

    _correr(escenario)


def test_instagram_entra_encola_y_la_reentrega_no_duplica():
    async def escenario(c, creados, encolar):
        from app.models.conversation import ConversationCanal

        psid = _psid()
        creados.append(f"ig:{psid}")
        cuerpo, cab = _firmar(_evento("ig_message_received", participante=psid))
        r = await c.post(URL, content=cuerpo, headers=cab)
        assert r.status_code == 200, r.text
        assert r.json()["encolado"] is True
        # Zernio reintenta el MISMO evento (mismo id, mismo cuerpo).
        r2 = await c.post(URL, content=cuerpo, headers=cab)
        assert r2.status_code == 200 and r2.json().get("repetido") is True

        conv, mensajes = await _conversacion_de(f"ig:{psid}")
        assert conv.canal == ConversationCanal.instagram_dm
        assert len(mensajes) == 1
        z = mensajes[0].extra["zernio"]
        assert z["conversation_id"] == "000000000000000000000003"
        assert z["account_id"] == CUENTA_IG
        assert encolar.delay.call_count == 1

    _correr(escenario)


def test_messenger_va_a_su_canal_y_whatsapp_sin_interruptor_se_ignora():
    async def escenario(c, creados, encolar):
        from app.models.conversation import ConversationCanal

        psid = _psid()
        creados.append(f"fb:{psid}")
        cuerpo, cab = _firmar(_evento("fb_message_received", participante=psid))
        assert (await c.post(URL, content=cuerpo, headers=cab)).status_code == 200
        conv, _ = await _conversacion_de(f"fb:{psid}")
        assert conv.canal == ConversationCanal.messenger

        wa = _evento("fb_message_received", participante="5219980000000")
        wa["account"]["platform"] = "whatsapp"
        wa["message"]["platform"] = "whatsapp"
        cuerpo, cab = _firmar(wa)
        r = await c.post(URL, content=cuerpo, headers=cab)
        assert r.status_code == 200 and r.json()["ignorado"] == "plataforma_no_habilitada"

    _correr(escenario)


def test_una_cuenta_que_no_es_la_nuestra_no_entra():
    async def escenario(c, creados, encolar):
        psid = _psid()
        creados.append(f"ig:{psid}")
        ajeno = _evento("ig_message_received", participante=psid)
        ajeno["account"]["id"] = ajeno["account"]["accountId"] = "cuenta-de-otra-clinica"
        cuerpo, cab = _firmar(ajeno)
        r = await c.post(URL, content=cuerpo, headers=cab)
        assert r.status_code == 200 and "ignorado" in r.json()
        conv, _ = await _conversacion_de(f"ig:{psid}")
        assert conv is None

    _correr(escenario)


def test_el_eco_de_la_app_se_guarda_como_operador_y_aparta_a_sofia():
    async def escenario(c, creados, encolar):
        from app.models.conversation import ConversationStatus
        from app.models.message import MessageRole

        psid = _psid()
        creados.append(f"fb:{psid}")
        cuerpo, cab = _firmar(_evento("fb_message_received", participante=psid))
        await c.post(URL, content=cuerpo, headers=cab)
        eco = _evento("fb_message_sent", participante=psid)
        eco["message"]["text"] = "Le contesto yo desde el celular"
        cuerpo, cab = _firmar(eco)
        assert (await c.post(URL, content=cuerpo, headers=cab)).status_code == 200

        conv, mensajes = await _conversacion_de(f"fb:{psid}")
        assert [m.rol for m in mensajes] == [MessageRole.user, MessageRole.operator]
        assert mensajes[1].contenido == "Le contesto yo desde el celular"
        assert conv.status == ConversationStatus.humano

    _correr(escenario)


def _transporte_zernio(respuestas: list[httpx.Response], vistas: list[httpx.Request]):
    def _h(req: httpx.Request) -> httpx.Response:
        vistas.append(req)
        return respuestas[min(len(vistas), len(respuestas)) - 1]

    return httpx.MockTransport(_h)


def test_la_respuesta_sale_por_zernio_con_el_mid_real_y_sin_enviado_falso():
    async def escenario(c, creados, encolar):
        from app.providers.zernio.client import ZernioClient
        from app.services.channel_sender import (
            MessagingWindowClosed,
            SendNotConfirmed,
            send_text_to_conversation,
        )

        psid = _psid()
        creados.append(f"fb:{psid}")
        cuerpo, cab = _firmar(_evento("fb_message_received", participante=psid))
        await c.post(URL, content=cuerpo, headers=cab)
        conv, _ = await _conversacion_de(f"fb:{psid}")

        def _con(respuestas):
            vistas: list[httpx.Request] = []
            zc = ZernioClient("sk_prueba", transport=_transporte_zernio(respuestas, vistas))

            async def _cliente():
                return zc

            return patch("app.services.zernio.cliente", _cliente), vistas

        parche, vistas = _con(
            [httpx.Response(200, json={"data": {"messageId": "m_mid_real_1"}})]
        )
        with parche:
            assert await send_text_to_conversation(conv, "¡Hola Ana!") == "m_mid_real_1"
        req = vistas[0]
        assert req.url.path == "/api/v1/inbox/conversations/00000000000000000000000e/messages"
        assert json.loads(req.content)["accountId"] == CUENTA_FB
        assert req.headers["idempotency-key"]

        parche, vistas = _con(
            [httpx.Response(403, json={"code": "platform_api_error", "error": "window"})]
        )
        with parche, pytest.raises(MessagingWindowClosed):
            await send_text_to_conversation(conv, "tarde")

        parche, vistas = _con([httpx.Response(500, json={"error": "caido"})])
        with parche, pytest.raises(SendNotConfirmed):
            await send_text_to_conversation(conv, "Zernio caído")
        assert len(vistas) == 1  # un 500 de envío no se reintenta

    _correr(escenario)


def test_el_historial_entra_leido_archivado_y_sin_despertar_al_agente():
    async def escenario(c, creados, encolar):
        from app.providers.zernio.client import ZernioClient
        from app.services.zernio import importar_historial

        psid = _psid()
        creados.append(f"ig:{psid}")

        def _h(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/inbox/conversations"):
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": "hilo-1", "participantId": psid, "participantName": "Vieja"}],
                        "pagination": {"hasMore": False},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {"id": f"m_old_{psid}_1", "direction": "incoming", "message": "¿Precio?"},
                        {"id": f"m_old_{psid}_2", "direction": "outgoing", "message": "Son $500"},
                    ],
                    "pagination": {"hasMore": False},
                },
            )

        from sqlalchemy import select

        from app.db.session import db_session
        from app.models.channel import Channel, ChannelType

        async with db_session() as db:
            canal_ig = (
                await db.execute(
                    select(Channel.id).where(
                        Channel.type == ChannelType.instagram_dm,
                        Channel.name == "Zernio prueba instagram_dm",
                    )
                )
            ).scalars().first()

        real = ZernioClient
        with patch(
            "app.services.zernio.cliente_historial",
            lambda llave: real(llave, transport=httpx.MockTransport(_h)),
        ):
            res = await importar_historial(canal_ig)
            # Idempotente: una segunda pasada no duplica.
            await importar_historial(canal_ig)
        assert res["entrantes"] == 1 and res["ecos"] == 1

        conv, mensajes = await _conversacion_de(f"ig:{psid}")
        assert [m.contenido for m in mensajes] == ["¿Precio?", "Son $500"]
        assert mensajes[0].leido_at is not None
        assert conv.archived_at is not None
        encolar.delay.assert_not_called()

    _correr(escenario)


def test_el_historial_no_cuenta_como_rafaga_ni_archiva_lo_que_ya_estaba():
    """Un paciente con muchos mensajes viejos no es una ráfaga (antes contaba
    para el tope por minuto y podía acabar bloqueado), y una conversación que
    el CRM ya tenía abierta no se esconde en archivadas al importar."""

    async def escenario(c, creados, encolar):
        from sqlalchemy import select

        from app.db.session import db_session
        from app.models.channel import Channel, ChannelType
        from app.providers.zernio.client import ZernioClient
        from app.services.zernio import importar_historial

        muchos, abierta = _psid(), _psid()
        creados += [f"ig:{muchos}", f"ig:{abierta}"]
        # La conversación "abierta" ya existe: entra por el webhook antes.
        cuerpo, cab = _firmar(_evento("ig_message_received", participante=abierta))
        await c.post(URL, content=cuerpo, headers=cab)

        def _h(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/inbox/conversations"):
                return httpx.Response(200, json={
                    "data": [
                        {"id": "h-muchos", "participantId": muchos},
                        {"id": "h-abierta", "participantId": abierta},
                    ],
                    "pagination": {"hasMore": False},
                })
            quien = muchos if "h-muchos" in req.url.path else abierta
            n = 40 if quien == muchos else 1
            return httpx.Response(200, json={
                "messages": [
                    {"id": f"m_hist_{quien}_{i}", "direction": "incoming", "message": f"viejo {i}"}
                    for i in range(n)
                ],
                "pagination": {"hasMore": False},
            })

        async with db_session() as db:
            canal_ig = (
                await db.execute(
                    select(Channel.id).where(
                        Channel.type == ChannelType.instagram_dm,
                        Channel.name == "Zernio prueba instagram_dm",
                    )
                )
            ).scalars().first()

        real = ZernioClient
        with patch(
            "app.services.zernio.cliente_historial",
            lambda llave: real(llave, transport=httpx.MockTransport(_h)),
        ):
            res = await importar_historial(canal_ig)

        assert res["entrantes"] == 41, "algún mensaje viejo se contó como ráfaga"
        conv_muchos, msgs = await _conversacion_de(f"ig:{muchos}")
        assert len(msgs) == 40
        assert not any((m.extra or {}).get("rate_limited") for m in msgs)
        assert conv_muchos.archived_at is not None, "la nueva sí nace archivada"
        conv_abierta, _ = await _conversacion_de(f"ig:{abierta}")
        assert conv_abierta.archived_at is None, "se archivó una conversación en curso"

    _correr(escenario)


def test_relanzar_el_historial_arregla_una_importacion_cortada_a_medias():
    """Zernio cortó la primera importación con un 429 (pasó en vivo): los
    mensajes viejos ya estaban guardados pero sin leer y la conversación sin
    archivar. Volver a lanzarla tiene que dejarla bien aunque no guarde nada."""

    async def escenario(c, creados, encolar):
        from sqlalchemy import select, update

        from app.db.session import db_session
        from app.models.channel import Channel, ChannelType
        from app.models.conversation import Conversation
        from app.models.message import Message
        from app.providers.zernio.client import ZernioClient
        from app.services.zernio import importar_historial

        psid = _psid()
        creados.append(f"ig:{psid}")

        def _h(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/inbox/conversations"):
                return httpx.Response(200, json={
                    "data": [{"id": "h-corte", "participantId": psid}],
                    "pagination": {"hasMore": False},
                })
            return httpx.Response(200, json={
                "messages": [
                    {"id": f"m_corte_{psid}_{i}", "direction": "incoming", "message": f"viejo {i}"}
                    for i in range(3)
                ] + [{"id": f"m_corte_{psid}_r", "direction": "outgoing", "message": "respuesta vieja"}],
                "pagination": {"hasMore": False},
            })

        async with db_session() as db:
            canal_ig = (
                await db.execute(
                    select(Channel.id).where(
                        Channel.type == ChannelType.instagram_dm,
                        Channel.name == "Zernio prueba instagram_dm",
                    )
                )
            ).scalars().first()

        real = ZernioClient
        with patch(
            "app.services.zernio.cliente_historial",
            lambda llave: real(llave, transport=httpx.MockTransport(_h)),
        ):
            await importar_historial(canal_ig)
            # Así quedó en vivo tras el corte: sin leer y en la bandeja.
            conv, msgs = await _conversacion_de(f"ig:{psid}")
            async with db_session() as db:
                await db.execute(
                    update(Message).where(Message.conversation_id == conv.id).values(leido_at=None)
                )
                await db.execute(
                    update(Conversation).where(Conversation.id == conv.id).values(archived_at=None)
                )
                await db.commit()
            res = await importar_historial(canal_ig)

        assert res["entrantes"] == 0, "relanzar no puede duplicar"
        conv, msgs = await _conversacion_de(f"ig:{psid}")
        assert conv.archived_at is not None
        assert all(m.leido_at is not None for m in msgs if m.rol.value == "user")
        assert len(msgs) == 4

    _correr(escenario)


def test_con_instagram_apagado_no_entra_nada_ni_contesta_sofia():
    """Instagram apagado porque ManyChat sigue en la cuenta (caso real): lo que
    llega por Zernio no se guarda, no se encola y no aparta a nadie."""

    async def escenario(c, creados, encolar):
        from sqlalchemy import update

        from app.db.session import db_session
        from app.models.channel import Channel, ChannelType

        async with db_session() as db:
            await db.execute(
                update(Channel)
                .where(Channel.type == ChannelType.instagram_dm, Channel.name == "Zernio prueba instagram_dm")
                .values(enabled=False)
            )
            await db.commit()
        psid = _psid()
        creados.append(f"ig:{psid}")
        for nombre in ("ig_message_received", "ig_message_sent"):
            eco = _evento(nombre, participante=psid)
            if nombre == "ig_message_sent":
                eco["message"]["text"] = "Respuesta de un flujo de ManyChat"
            cuerpo, cab = _firmar(eco)
            r = await c.post(URL, content=cuerpo, headers=cab)
            assert r.status_code == 200 and r.json().get("ignorado") == "plataforma_no_habilitada"
        conv, _ = await _conversacion_de(f"ig:{psid}")
        assert conv is None
        encolar.delay.assert_not_called()

    _correr(escenario)
