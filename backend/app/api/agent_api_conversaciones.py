"""La API de agentes para n8n cuando hace de cerebro de un agente (Fase 11, SPEC-07).

Con «Sofía en n8n», el flujo puede contestar en la respuesta directa del
webhook o más tarde por aquí: leer el hilo, mandar un mensaje (texto y/o
archivos), pasar la conversación a una persona y actualizar la ficha.

Ámbitos (`models/agent_token.py`), los únicos que tocan contenido:
  - `conversations:read`  → `GET  /conversations/{id}/messages`
  - `conversations:reply` → `POST /conversations/{id}/messages`, `POST …/handoff`
  - `contacts:write`      → `PATCH /contacts/{id}`

Reglas que no se saltan:
  - Si una persona tomó la conversación (no está en `bot`), n8n no contesta en
    ella: 409. El inbox es de quien la atiende.
  - Con la ventana de 24 h cerrada no sale texto libre: 409 (hay que usar una
    plantilla desde el panel).
  - En Entrenamiento lo que manda n8n queda como sugerencia.
  - Por correo no se contesta por aquí: el borrador de correo sale de la
    respuesta directa, que es la que conoce el hilo.
  - Una `idempotency_key` repetida no manda dos veces.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent_api import _audit_token, require_scope
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.agent_token import AgentToken

logger = get_logger(__name__)

router = APIRouter(prefix="/agent-api", tags=["agent_api"])

_ROL = {"user": "paciente", "assistant": "sofia", "operator": "operador"}
_IDEM_TTL_S = 24 * 3600


class MensajeIn(BaseModel):
    texto: str | None = Field(None, max_length=8000)
    archivos: list[dict[str, Any]] = Field(default_factory=list)
    idempotency_key: str | None = Field(None, max_length=100)


class HandoffIn(BaseModel):
    motivo: str = Field("n8n lo pasa a una persona", max_length=200)
    mensaje_al_cliente: str | None = Field(None, max_length=1000)


class FichaIn(BaseModel):
    nombre: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=320)
    etapa: str | None = Field(None, max_length=40)
    etiquetas_agregar: list[str] = Field(default_factory=list, max_length=10)


async def _la_conversacion(db: AsyncSession, conversation_id: uuid.UUID):
    from app.models.conversation import Conversation

    conv = await db.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversación no encontrada")
    return conv


@router.get("/conversations/{conversation_id}/messages")
async def leer_mensajes(
    conversation_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("conversations:read")),
) -> dict:
    """El hilo, del más viejo al más nuevo. Los archivos con un enlace firmado
    de 1 h; las sugerencias sin enviar, marcadas (el paciente no las vio)."""
    from app.models.message import Message, MessageRole
    from app.services.media_publica import url_publica

    conv = await _la_conversacion(db, conversation_id)
    filas = (
        await db.execute(
            select(Message)
            .where(Message.conversation_id == conv.id, Message.rol != MessageRole.system)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    salida = []
    for m in reversed(filas):
        extra = m.extra or {}
        item: dict[str, Any] = {
            "id": str(m.id),
            "rol": _ROL.get(m.rol.value, m.rol.value),
            "texto": m.contenido or m.audio_transcript or "",
            "tipo": m.media_type or ("audio" if m.audio_url else "texto"),
            "fecha": m.created_at.isoformat() if m.created_at else None,
        }
        if m.media_url and m.media_url.startswith("/uploads/"):
            item["archivo_url"] = url_publica(m.media_url)
        if extra.get("is_draft") and not extra.get("draft_sent"):
            item["sugerencia_sin_enviar"] = True
        salida.append(item)
    await _audit_token(db, token, "agent_api.conversation.read", "conversation", conv.id, {"n": len(salida)})
    return {"conversation_id": str(conv.id), "canal": conv.canal.value, "estado": conv.status.value,
            "mensajes": salida}


async def _ya_se_mando(clave: str | None) -> bool:
    """Marca la clave y dice si ya estaba (Redis; sin Redis, no se bloquea)."""
    if not clave:
        return False
    try:
        from app.core.redis import get_redis

        nueva = await get_redis().set(f"agent_api:idem:{clave}", "1", ex=_IDEM_TTL_S, nx=True)
        return not nueva
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_api.idem.error", error=str(e))
        return False


@router.post("/conversations/{conversation_id}/messages")
async def mandar_mensaje(
    conversation_id: uuid.UUID,
    body: MensajeIn,
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("conversations:reply")),
) -> dict:
    from app.core.channel_capabilities import formatear_para_canal
    from app.models.conversation import ConversationCanal, ConversationStatus
    from app.models.message import Message, MessageRole
    from app.services.agent_pause import is_channel_training
    from app.services.archivos_salientes import mandar_archivos_de_n8n
    from app.services.channel_sender import MessagingWindowClosed, SendNotConfirmed
    from app.services.conversation import _split_response, deliver_response_parts

    texto = (body.texto or "").strip()
    if not texto and not body.archivos:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Manda `texto`, `archivos` o los dos.")
    conv = await _la_conversacion(db, conversation_id)
    if conv.status != ConversationStatus.bot:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"La conversación está en «{conv.status.value}»: la atiende una persona o está cerrada. "
            "n8n no contesta en ella.",
        )
    if conv.canal == ConversationCanal.email:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Por correo se contesta en la respuesta directa del webhook (es la que arma el borrador en el hilo).",
        )
    if await _ya_se_mando(body.idempotency_key):
        return {"repetido": True}

    from app.services.n8n_motor import ids_de_sofia, marcar_nuevos_de_n8n

    entrenamiento = await is_channel_training(conv.canal.value)
    # Lo que aparezca de Sofía a partir de aquí lo mandó n8n.
    antes = await ids_de_sofia(conv.id)
    enviados = 0
    sugerencia = False
    if texto:
        texto = formatear_para_canal(texto, conv.canal)
        if entrenamiento:
            db.add(Message(
                conversation_id=conv.id, rol=MessageRole.assistant, contenido=texto,
                extra={"is_draft": True, "draft_sent": False, "training": True, "motor": "n8n"},
            ))
            conv.last_message_at = datetime.now(timezone.utc)
            await db.commit()
            sugerencia = True
        else:
            try:
                enviados = await deliver_response_parts(db, conv, _split_response(texto, 3))
            except MessagingWindowClosed as e:
                raise HTTPException(status.HTTP_409_CONFLICT, f"Ventana cerrada, usa una plantilla: {e}") from e
            except SendNotConfirmed as e:
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"El canal no confirmó el envío: {e}") from e
            if enviados == 0:
                raise HTTPException(status.HTTP_409_CONFLICT, "Ventana cerrada, usa una plantilla desde el panel.")

    archivos = []
    if body.archivos:
        archivos = await mandar_archivos_de_n8n(
            body.archivos,
            {"conversation_id": str(conv.id), "canal": conv.canal.value, "is_training": entrenamiento},
            "api n8n",
        )
    await marcar_nuevos_de_n8n(conv.id, antes)
    await _audit_token(db, token, "agent_api.conversation.reply", "conversation", conv.id,
                       {"partes": enviados, "archivos": len(archivos), "sugerencia": sugerencia})
    return {"enviados": enviados, "sugerencia": sugerencia, "archivos": archivos}


@router.post("/conversations/{conversation_id}/handoff")
async def pasar_a_una_persona(
    conversation_id: uuid.UUID,
    body: HandoffIn,
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("conversations:reply")),
) -> dict:
    """El mismo camino que `derivar_humano`: mensaje puente al paciente, la
    conversación a `humano` y aviso a la operadora."""
    from app.agents.tools.human_handoff import _derivar_a_humano

    conv = await _la_conversacion(db, conversation_id)
    resultado = await _derivar_a_humano(conv.id, body.motivo, {"mensaje_al_cliente": body.mensaje_al_cliente})
    await _audit_token(db, token, "agent_api.conversation.handoff", "conversation", conv.id,
                       {"motivo": body.motivo[:80]})
    return resultado


@router.patch("/contacts/{contact_id}")
async def actualizar_ficha(
    contact_id: uuid.UUID,
    body: FichaIn,
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("contacts:write")),
) -> dict:
    """Nombre, correo, etapa y etiquetas. Nunca el teléfono: es la llave de la ficha."""
    from app.models.contact import Contact
    from app.services.n8n_motor import aplicar_cambios_de_ficha

    if await db.get(Contact, contact_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ficha no encontrada")
    cambios = await aplicar_cambios_de_ficha(contact_id, body.model_dump())
    await _audit_token(db, token, "agent_api.contact.update", "contact", contact_id, {"cambios": cambios})
    return {"cambios": cambios}
