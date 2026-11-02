"""Ayudas para las pruebas de «Sofía en n8n» (Fase 11, SPEC-07).

- `agente_en_n8n()`: la configuración de un agente de texto con motor n8n.
- `whatsapp_entrante()`: un WhatsApp que entra por la puerta normal, en la cola
  del buffer, listo para `process_buffered_messages`.
- `n8n_que_contesta(...)`: sustituye la llamada HTTP a n8n y apunta lo que se
  le mandó.
- `turno(...)`: procesa el buffer con ese agente.
- `token(...)`: un token de la API de agentes con esos ámbitos.
"""
from __future__ import annotations

import uuid

import httpx

URL = "https://n8n.ejemplo.mx/webhook/sofia"


def agente_en_n8n(**campos):
    from app.services.runtime_config import AgentRuntime

    datos = dict(
        id=uuid.uuid4(),
        name="Sofía en n8n",
        prompt_system="(no se usa)",
        model_name="gpt-5.4-mini",
        temperature=1.0,
        max_tokens=0,
        buffer_seconds=1,
        response_split_max_parts=3,
        context_window=20,
        handoff_bridge_message=None,
        monthly_budget_usd=None,
        tools_enabled=None,
        source="agents",
        motor="n8n",
        n8n_url=URL,
        n8n_secreto="secreto-del-motor",
        n8n_espera_s=25,
    )
    datos.update(campos)
    return AgentRuntime(**datos)


async def whatsapp_entrante(texto: str = "¿Tienen cita mañana?"):
    """Un WhatsApp que entra por `store_incoming`; devuelve (conv, msg_id, clave)."""
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.conversation import Conversation, ConversationStatus
    from app.models.message import Message
    from app.providers.whatsapp.base import IncomingMessage
    from app.services import message_buffer
    from app.services.conversation import store_incoming

    telefono = f"+52998{uuid.uuid4().int % 10_000_000:07d}"
    msg_id = await store_incoming(
        IncomingMessage(
            provider_message_id=f"wamid.{uuid.uuid4().hex}",
            from_phone=telefono,
            to_phone="+529980000000",
            message_type="text",
            text=texto,
        )
    )
    async with db_session() as db:
        conv_id = (await db.execute(select(Message.conversation_id).where(Message.id == msg_id))).scalar_one()
        conv = (await db.execute(select(Conversation).where(Conversation.id == conv_id))).scalar_one()
        conv.status = ConversationStatus.bot
        conv.spam_reviewed = True
        await db.commit()
        await db.refresh(conv)
        db.expunge(conv)
    clave = message_buffer.conversation_key(conv.id)
    await message_buffer.push_message(clave, str(msg_id))
    return conv, msg_id, clave


async def turno(clave, agente, enviar, *extra) -> None:
    """Procesa el buffer con ese agente, sin LLM ni moderación del CRM (si se
    llaman, la prueba falla) y con `enviar` en lugar del canal."""
    from contextlib import ExitStack
    from unittest.mock import AsyncMock, patch

    from app.services import message_buffer
    from app.services.conversation import process_buffered_messages

    parches = [
        patch("app.services.conversation.get_runtime_for_channel", AsyncMock(return_value=agente)),
        patch("app.services.conversation.run_agent", AsyncMock(side_effect=AssertionError("no debía llamarse"))),
        patch("app.services.conversation.moderate", AsyncMock(side_effect=AssertionError("no debía moderar"))),
        patch("app.services.channel_sender.send_text_to_conversation", enviar),
        patch("app.services.conversation.is_channel_paused", AsyncMock(return_value=False)),
        patch.object(message_buffer, "should_process", AsyncMock(return_value=True)),
    ]
    with ExitStack() as pila:
        for p in [*parches, *extra]:
            pila.enter_context(p)
        await process_buffered_messages(clave)


class N8nQueContesta:
    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.eventos: list[dict] = []
        self.cabeceras: list[str] = []

    async def __call__(self, url, secreto, cuerpo, espera):
        self.eventos.append(cuerpo)
        self.cabeceras.append(secreto)
        r = self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]
        if isinstance(r, Exception):
            raise r
        if isinstance(r, httpx.Response):
            return r
        estado, cuerpo_resp = r
        return httpx.Response(estado, json=cuerpo_resp)


def n8n_que_contesta(monkeypatch, *respuestas) -> N8nQueContesta:
    from app.services import n8n_motor

    falso = N8nQueContesta(*respuestas)

    async def _publica(url):
        return True, ""

    monkeypatch.setattr(n8n_motor, "_post", falso)
    monkeypatch.setattr(n8n_motor, "validate_public_https_url", _publica)
    monkeypatch.setattr(n8n_motor, "REINTENTOS_S", (0, 0))
    return falso


async def conversacion(conv_id):
    from app.db.session import db_session
    from app.models.conversation import Conversation

    async with db_session() as db:
        return await db.get(Conversation, conv_id)


async def mensajes(conv_id) -> list:
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.message import Message

    async with db_session() as db:
        return list(
            (
                await db.execute(
                    select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at)
                )
            ).scalars().all()
        )


async def token(*scopes: str) -> tuple[dict[str, str], uuid.UUID]:
    """Un token de la API de agentes con esos ámbitos; devuelve (cabeceras, id)."""
    import secrets

    from app.api.agent_api import hash_agent_token
    from app.db.session import db_session
    from app.models.agent_token import AgentToken

    crudo = "agt_" + secrets.token_urlsafe(24)
    async with db_session() as db:
        t = AgentToken(name="n8n de prueba", token_hash=hash_agent_token(crudo), token_prefix=crudo[:12],
                       scopes=",".join(scopes))
        db.add(t)
        await db.commit()
        return {"Authorization": f"Bearer {crudo}"}, t.id


async def borrar_token(token_id) -> None:
    from sqlalchemy import delete

    from app.db.session import db_session
    from app.models.agent_token import AgentToken

    async with db_session() as db:
        await db.execute(delete(AgentToken).where(AgentToken.id == token_id))
        await db.commit()
