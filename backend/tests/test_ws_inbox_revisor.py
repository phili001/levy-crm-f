"""El revisor solo recibe Instagram también por el WebSocket de la bandeja.

QUÉ PASABA (SPEC-21, A1 de la revisión de Francisco). Por HTTP el revisor solo
veía Instagram (`test_rol_revisor_http.py`), pero el WebSocket no miraba el rol:
se suscribía al canal global de la bandeja y le reenviaba TODO. El aviso de
cada mensaje entrante llevaba `from_phone`, así que con la pestaña abierta le
iban llegando los teléfonos de WhatsApp (y los correos) de clientes que por
HTTP no podía abrir.

Lo que se comprueba aquí:
  - al revisor no le llega nada de una conversación de WhatsApp;
  - lo de Instagram sí le llega, que es para lo que existe su cuenta;
  - los avisos sin conversación (agenda, fusiones) tampoco le llegan;
  - el admin conectado a la vez sigue recibiendo todo, con el mensaje real
    entrando por la puerta normal;
  - el aviso de mensaje nuevo ya no lleva el identificador del cliente.

Para que un «no llegó» no dependa de esperar lo bastante, después de cada
evento prohibido se publica uno de Instagram: los eventos de un canal llegan en
orden, así que cuando llega ese, el anterior ya habría llegado.

Se prueba el handler REAL contra Postgres y Redis, con el mismo doble del
WebSocket que `test_ws_inbox_fuga.py`.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from app.core.config import settings


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


def _redis_available() -> bool:
    try:
        import redis as redis_sync

        redis_sync.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1).ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_db_available() and _redis_available()),
    reason="Necesita Postgres y Redis (se ejecutan en CI)",
)


class FakeWebSocket:
    """Doble del WebSocket de Starlette (el de `test_ws_inbox_fuga.py`)."""

    def __init__(self) -> None:
        self.accepted = False
        self.closed_with: int | None = None
        self.sent: list[str] = []
        self._incoming: asyncio.Queue = asyncio.Queue()

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        if self.closed_with is None:
            self.closed_with = code

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def receive(self) -> dict:
        return await self._incoming.get()

    def cierra_la_pestana(self) -> None:
        self._incoming.put_nowait({"type": "websocket.disconnect", "code": 1001})

    def eventos(self) -> list[dict]:
        """Lo recibido, sin los latidos."""
        return [e for e in map(json.loads, self.sent) if e.get("type") != "ping"]


class _Escenario:
    def __init__(self) -> None:
        self.user_ids: list[uuid.UUID] = []
        self.contact_ids: list[uuid.UUID] = []
        self.telefonos: list[str] = []
        self.conv_ig = ""
        self.conv_wa = ""
        self.sockets: list[tuple[FakeWebSocket, asyncio.Task]] = []


async def _usuario_con_ticket(e: _Escenario, role: str) -> str:
    """Usuario del panel + ticket efímero válido (lo que emite /auth/ws-ticket)."""
    import secrets

    from app.core.redis import get_redis
    from app.core.security import hash_password
    from app.db.session import db_session
    from app.models.user import User

    suf = uuid.uuid4().hex[:8]
    async with db_session() as db:
        user = User(
            email=f"ws-{role}-{suf}@test.local",
            password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
            nombre=f"WS {role}",
            role=role,
        )
        db.add(user)
        await db.commit()
        e.user_ids.append(user.id)
    ticket = secrets.token_urlsafe(32)
    await get_redis().set(f"wsticket:{ticket}", str(user.id), ex=60)
    return ticket


async def _crear_conversaciones(e: _Escenario) -> None:
    """Un cliente con conversación de Instagram y otro con una de WhatsApp."""
    from app.db.session import db_session
    from app.models.contact import Contact, ContactOrigen
    from app.models.conversation import Conversation, ConversationCanal, ConversationStatus

    suf = uuid.uuid4().hex[:8]
    async with db_session() as db:
        ig = Contact(telefono=f"ig:PSID{suf}", origen=ContactOrigen.instagram)
        wa = Contact(telefono=f"+52998{uuid.uuid4().int % 10_000_000:07d}", origen=ContactOrigen.whatsapp)
        db.add_all([ig, wa])
        await db.flush()
        conv_ig = Conversation(
            contact_id=ig.id,
            canal=ConversationCanal.instagram_dm,
            session_id=f"ig-{suf}",
            status=ConversationStatus.bot,
        )
        conv_wa = Conversation(
            contact_id=wa.id,
            canal=ConversationCanal.whatsapp,
            session_id=f"wa-{suf}",
            status=ConversationStatus.bot,
        )
        db.add_all([conv_ig, conv_wa])
        await db.commit()
        e.contact_ids += [ig.id, wa.id]
        e.conv_ig, e.conv_wa = str(conv_ig.id), str(conv_wa.id)


async def _borrar(e: _Escenario) -> None:
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        for cid in e.contact_ids:
            await db.execute(text("DELETE FROM contacts WHERE id = :i"), {"i": str(cid)})
        for tel in e.telefonos:
            await db.execute(text("DELETE FROM contacts WHERE telefono = :t"), {"t": tel})
        for uid in e.user_ids:
            await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(uid)})
        await db.commit()


async def _conectar(
    e: _Escenario, ticket: str, suscritos: int
) -> tuple[FakeWebSocket, asyncio.Task]:
    """Abre el socket y espera a que haya `suscritos` pestañas en el canal."""
    from app.api import conversations as conv_api
    from app.core import events as bus_mod
    from app.core.events import inbox_channel

    ws = FakeWebSocket()
    tarea = asyncio.create_task(conv_api.ws_inbox(ws, ticket=ticket))
    e.sockets.append((ws, tarea))
    canal = inbox_channel()
    for _ in range(100):
        await asyncio.sleep(0.05)
        brokers = [b for (_l, b) in bus_mod._brokers.values()]
        if brokers and len(brokers[0]._subs.get(canal, ())) >= suscritos:
            break
    assert ws.accepted, "el socket no se aceptó"
    await asyncio.sleep(0.2)  # la suscripción en Redis llega un instante después
    return ws, tarea


async def _publicar(tipo: str, payload: dict) -> None:
    from app.core import events as bus_mod
    from app.core.events import inbox_channel

    await bus_mod.event_bus.publish(inbox_channel(), tipo, payload)


async def _esperar(ws: FakeWebSocket, conversation_id: str, tipo: str = "message.new") -> None:
    for _ in range(100):
        if any(
            ev["type"] == tipo and ev["payload"].get("conversation_id") == conversation_id
            for ev in ws.eventos()
        ):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"no llegó {tipo} de {conversation_id}")


async def _cerrar_sockets(e: _Escenario) -> None:
    """Cierra las pestañas aunque la prueba haya fallado a medias: un handler
    vivo al acabar el loop deja conexiones atadas a él y tumba la prueba
    siguiente con «Event loop is closed»."""
    for ws, tarea in e.sockets:
        if tarea.done():
            continue
        ws.cierra_la_pestana()
        try:
            await asyncio.wait_for(tarea, timeout=5)
        except TimeoutError:
            tarea.cancel()


def _conversaciones_recibidas(ws: FakeWebSocket) -> set[str]:
    return {str(ev["payload"].get("conversation_id")) for ev in ws.eventos()}


async def _entra_whatsapp(e: _Escenario) -> str:
    """Un WhatsApp por la puerta normal. Devuelve el id de su conversación."""
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.message import Message
    from app.providers.whatsapp.base import IncomingMessage
    from app.services.conversation import store_incoming

    telefono = f"+52998{uuid.uuid4().int % 10_000_000:07d}"
    e.telefonos.append(telefono)
    message_id = await store_incoming(
        IncomingMessage(
            provider_message_id=f"wamid.{uuid.uuid4().hex}",
            from_phone=telefono,
            to_phone="+529980000000",
            message_type="text",
            text="hola, ¿cuánto cuesta una limpieza?",
        )
    )
    assert message_id, "store_incoming no guardó el mensaje"
    async with db_session() as db:
        conv_id = (
            await db.execute(select(Message.conversation_id).where(Message.id == message_id))
        ).scalar_one()
    return str(conv_id)


# ---------------------------------------------------------------------------


def test_el_revisor_no_recibe_por_el_websocket_los_mensajes_de_whatsapp():
    from app.core import events as bus_mod

    async def _run():
        await bus_mod.close_event_bus()
        e = _Escenario()
        try:
            await _crear_conversaciones(e)
            revisor = await _conectar(e, await _usuario_con_ticket(e, "revisor"), 1)

            await _publicar("message.new", {"conversation_id": e.conv_wa, "rol": "user"})
            await _publicar("conversation.updated", {"conversation_id": e.conv_wa, "status": "humano"})
            await _publicar("message.new", {"conversation_id": e.conv_ig, "rol": "user"})
            await _esperar(revisor[0], e.conv_ig)

            assert e.conv_wa not in _conversaciones_recibidas(revisor[0]), (
                "al revisor le llegó una conversación de WhatsApp por el WebSocket"
            )
        finally:
            await _cerrar_sockets(e)
            await _borrar(e)
            await bus_mod.close_event_bus()

    asyncio.run(_run())


def test_el_revisor_si_recibe_los_de_instagram():
    from app.core import events as bus_mod

    async def _run():
        await bus_mod.close_event_bus()
        e = _Escenario()
        try:
            await _crear_conversaciones(e)
            revisor = await _conectar(e, await _usuario_con_ticket(e, "revisor"), 1)

            await _publicar("message.new", {"conversation_id": e.conv_ig, "rol": "user"})
            await _publicar("conversation.updated", {"conversation_id": e.conv_ig, "status": "humano"})
            await _esperar(revisor[0], e.conv_ig, "message.new")
            await _esperar(revisor[0], e.conv_ig, "conversation.updated")
        finally:
            await _cerrar_sockets(e)
            await _borrar(e)
            await bus_mod.close_event_bus()

    asyncio.run(_run())


def test_el_revisor_no_recibe_avisos_sin_conversacion():
    from app.core import events as bus_mod

    async def _run():
        await bus_mod.close_event_bus()
        e = _Escenario()
        try:
            await _crear_conversaciones(e)
            revisor = await _conectar(e, await _usuario_con_ticket(e, "revisor"), 1)

            await _publicar("agenda.changed", {})
            await _publicar("fusion.changed", {})
            await _publicar("agent.step", {"step": "clasificador"})
            await _publicar("message.new", {"conversation_id": "no-es-un-uuid"})
            await _publicar("message.new", {"conversation_id": e.conv_ig, "rol": "user"})
            await _esperar(revisor[0], e.conv_ig)

            tipos = [ev["type"] for ev in revisor[0].eventos()]
            assert tipos == ["message.new"], f"le llegaron avisos que no son suyos: {tipos}"
        finally:
            await _cerrar_sockets(e)
            await _borrar(e)
            await bus_mod.close_event_bus()

    asyncio.run(_run())


def test_con_admin_y_revisor_conectados_el_de_whatsapp_solo_le_llega_al_admin():
    from app.core import events as bus_mod

    async def _run():
        await bus_mod.close_event_bus()
        e = _Escenario()
        try:
            await _crear_conversaciones(e)
            admin = await _conectar(e, await _usuario_con_ticket(e, "admin"), 1)
            revisor = await _conectar(e, await _usuario_con_ticket(e, "revisor"), 2)

            conv_wa = await _entra_whatsapp(e)
            await _publicar("message.new", {"conversation_id": e.conv_ig, "rol": "user"})

            await _esperar(admin[0], conv_wa)
            await _esperar(admin[0], e.conv_ig)
            await _esperar(revisor[0], e.conv_ig)
            assert conv_wa not in _conversaciones_recibidas(revisor[0]), (
                "el WhatsApp que entró le llegó también al revisor"
            )
        finally:
            await _cerrar_sockets(e)
            await _borrar(e)
            await bus_mod.close_event_bus()

    asyncio.run(_run())


def test_el_aviso_de_mensaje_nuevo_no_lleva_el_telefono(monkeypatch):
    from app.services import conversation as conv_service

    publicados: list[tuple[str, dict]] = []

    async def _captura(_canal: str, tipo: str, payload: dict) -> None:
        publicados.append((tipo, payload))

    monkeypatch.setattr(conv_service.event_bus, "publish", _captura)

    async def _run():
        e = _Escenario()
        try:
            conv_wa = await _entra_whatsapp(e)
            avisos = [
                p for t, p in publicados if t == "message.new" and p.get("conversation_id") == conv_wa
            ]
            assert avisos, "store_incoming no avisó a la bandeja"
            aviso = avisos[0]
            assert aviso["canal"] == "whatsapp"
            assert "from_phone" not in aviso
            digitos = e.telefonos[0].lstrip("+")
            assert digitos not in json.dumps(aviso), "el teléfono sigue viajando en el aviso"
        finally:
            await _borrar(e)

    asyncio.run(_run())
