"""Fuera de horario, Sofía toma el recado y la llamada queda en «Para hacer» (Fase 8.2).

US-20 AC1: fuera de horario, o sin teléfono de recepción en el canal, no se
pasa la llamada: `derivar_humano` deja la conversación en `humano`, Sofía dice
que le devolverán la llamada y cuelga, y al colgar la conversación NO se
archiva: queda en «Para hacer» para que alguien la devuelva. Una llamada que sí
se pasó a recepción, en cambio, se archiva al colgar (ya la atendió alguien).

Con base de datos: lo que se comprueba es justo lo que queda guardado.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from app.agents.tools import human_handoff, schedule_config
from app.agents.tools.schedule_config import build_config
from tests.test_voice_gating import _cleanup, pytestmark_db

HORARIO = build_config("America/Cancun", "1,2,3,4,5", "08:00-17:00", "", "30", "0")
MARTES_10 = datetime(2026, 9, 29, 10, 0, tzinfo=HORARIO.tz)
MARTES_22 = MARTES_10.replace(hour=22)


def _entorno(monkeypatch, *, recepcion: str | None, ahora: datetime) -> None:
    from app.services import runtime_config

    async def _canal(_tipo):
        return {"transfer_number": recepcion} if recepcion else {}

    async def _horario():
        return HORARIO

    monkeypatch.setattr(runtime_config, "get_channel_config", _canal)
    monkeypatch.setattr(schedule_config, "get_schedule_config", _horario)
    monkeypatch.setattr(schedule_config, "now_in", lambda _tz: ahora)


async def _llamada(call_id: str):
    """Una llamada en curso: contacto, conversación de voz y el turno de quien llama."""
    from app.api.voice import _resolve_contact_and_conversation

    return await _resolve_contact_and_conversation(
        call_id, None, "quiero hablar con alguien", datetime.now(timezone.utc)
    )


async def _estado(conv_id):
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.conversation import Conversation
    from app.services.inbox_pending import conversation_needs_action

    async with db_session() as db:
        conv = (await db.execute(select(Conversation).where(Conversation.id == conv_id))).scalar_one()
        return conv, await conversation_needs_action(db, conv_id)


async def _decir(conv_id, texto: str) -> None:
    """Lo último que dijo Sofía antes de colgar (como lo guarda el turno)."""
    from app.db.session import db_session
    from app.models.message import Message, MessageRole

    async with db_session() as db:
        db.add(Message(conversation_id=conv_id, rol=MessageRole.assistant, contenido=texto))
        await db.commit()


def _derivar(conv_id, voz: dict) -> dict:
    ctx = {"conversation_id": str(conv_id), "channel": "retell_voice", "voice": voz}
    return json.loads(
        asyncio.run(human_handoff.derivar_humano({"motivo": "quiere hablar con el doctor"}, ctx))
    )


@pytestmark_db
def test_fuera_de_horario_se_toma_el_recado_y_queda_en_para_hacer(monkeypatch):
    from app.api.voice import _close_call_conversation
    from app.models.conversation import ConversationStatus

    _entorno(monkeypatch, recepcion="+529981112233", ahora=MARTES_22)
    call_id = f"call_{uuid.uuid4().hex[:10]}"
    conv_id, contact_id = asyncio.run(_llamada(call_id))
    try:
        voz: dict = {"transfer_number": None}
        respuesta = _derivar(conv_id, voz)
        assert respuesta["accion"] == "recado"
        assert respuesta["por_que"] == "fuera del horario de atención"
        assert voz["transfer_number"] is None, "fuera de horario no se pasa la llamada"

        asyncio.run(_decir(conv_id, "Te devuelven la llamada mañana a primera hora."))
        asyncio.run(_close_call_conversation(call_id))
        conv, pendiente = asyncio.run(_estado(conv_id))
        assert conv.status == ConversationStatus.humano
        assert conv.derivada_a_humano_at is not None
        assert conv.archived_at is None, "archivarla escondería el recado"
        assert pendiente, "tiene que salir en «Para hacer» para devolver la llamada"
    finally:
        asyncio.run(_cleanup([contact_id]))


def test_con_el_recado_sofia_se_despide_sin_pedir_mas_datos():
    """V-5 en vivo: tras el recado, Sofía ofrecía «¿tomo tus datos?». Al «sí»
    la conversación ya era del equipo y la llamada se cortaba con el mensaje
    fijo de derivación. El número ya queda con la llamada: se despide."""
    from app.api.voice import instrucciones_de_voz

    texto = instrucciones_de_voz(["derivar_humano"])
    assert "sin pedirle más datos" in texto


@pytestmark_db
def test_sin_telefono_de_recepcion_tambien_se_toma_el_recado(monkeypatch):
    _entorno(monkeypatch, recepcion=None, ahora=MARTES_10)
    call_id = f"call_{uuid.uuid4().hex[:10]}"
    conv_id, contact_id = asyncio.run(_llamada(call_id))
    try:
        respuesta = _derivar(conv_id, {"transfer_number": None})
        assert respuesta["accion"] == "recado"
        assert "recepción" in respuesta["por_que"]
    finally:
        asyncio.run(_cleanup([contact_id]))


@pytestmark_db
def test_la_llamada_pasada_a_recepcion_se_archiva_al_colgar(monkeypatch):
    from app.api.voice import _close_call_conversation
    from app.models.conversation import ConversationStatus

    _entorno(monkeypatch, recepcion="+529981112233", ahora=MARTES_10)
    call_id = f"call_{uuid.uuid4().hex[:10]}"
    conv_id, contact_id = asyncio.run(_llamada(call_id))
    try:
        voz: dict = {"transfer_number": None}
        assert _derivar(conv_id, voz)["accion"] == "transferir"
        asyncio.run(_decir(conv_id, "Te paso con recepción, un momento."))
        asyncio.run(_close_call_conversation(call_id))
        conv, pendiente = asyncio.run(_estado(conv_id))
        assert conv.status == ConversationStatus.bot
        assert conv.archived_at is not None, "ya la atendió una persona en directo"
        assert not pendiente
    finally:
        asyncio.run(_cleanup([contact_id]))
