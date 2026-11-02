"""Retomar a quien preguntó y no agendó (SPEC-18, tramo 3).

La persona preguntó (un precio, un horario, un tratamiento), Sofía le contestó y
no volvió a escribir. Pasadas `horas` (20 por defecto) y antes de que se cierre
la ventana de 24 h, Sofía le escribe UNA vez para retomar. Dentro de la ventana
el mensaje es texto normal: no hace falta plantilla y en WhatsApp no se cobra
aparte.

Quién es candidato lo decide SQL, barato: conversación de WhatsApp, Instagram o
Messenger que sigue con Sofía, sin cita futura, cuya ficha no es ya paciente
(ni perdido ni «no cualifica») y sin seguimiento previo. Si de verdad vale la
pena escribirle lo decide Sofía leyendo la conversación: no hay un clasificador
de intención, y uno de palabras clave le escribiría a quien solo pidió la
dirección. Si decide que no, contesta `NO_ESCRIBIR` y no sale nada.

La conversación se apunta (`seguimiento_enviado_at`) ANTES de preguntarle al
modelo y en su propia transacción: así no se le pregunta dos veces por la misma
(el beat pasa cada 10 minutos) y no se bloquea la conversación mientras el
modelo piensa. Justo antes de mandar se comprueba que el paciente no haya
escrito entretanto.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, func, select, text

from app.core.logging import get_logger
from app.db.session import db_session
from app.models.appointment import ESTADOS_ACTIVOS, Appointment
from app.models.contact import Contact, ContactEstado
from app.models.conversation import Conversation, ConversationCanal, ConversationStatus
from app.models.message import Message, MessageRole

logger = get_logger(__name__)

# El gasto de estas llamadas sale aparte en Consumo.
FUENTE_DEL_GASTO = "seguimiento"
# Hasta dónde se mira: pasadas 23 h la ventana de 24 h está a punto de cerrar y
# el mensaje podría no llegar (o, en Instagram, pedir una etiqueta que Meta
# prohíbe para mensajes automáticos).
VENTANA_HORAS = 23
CANALES = (ConversationCanal.whatsapp, ConversationCanal.instagram_dm, ConversationCanal.messenger)
YA_NO_SE_RETOMA = (ContactEstado.cliente, ContactEstado.perdido, ContactEstado.no_cualifica)
NO_ESCRIBIR = "NO_ESCRIBIR"
MAX_CARACTERES = 500
HISTORIAL = 20

INSTRUCCION = """[SEGUIMIENTO — instrucción interna del sistema. El paciente NO la ve; no la menciones.]
La persona dejó de contestar hace más de {horas} horas y no tiene ninguna cita. Decide si vale la pena escribirle UNA sola vez para retomar la conversación.

Escribe solo si preguntó por un servicio, un precio, una cita o un horario y la conversación quedó a medias.
NO escribas si solo pidió un dato (la dirección, el horario) y ya lo tuvo, si dijo que no le interesa, si se despidió o dio las gracias cerrando, si no es un paciente (proveedor, spam, publicidad) o si la conversación ya pasó a una persona del equipo.

Si no vale la pena, contesta exactamente: NO_ESCRIBIR
Si vale la pena, escribe UN mensaje corto (dos frases como mucho), cálido y sin presionar, que retome lo que preguntó y termine con una pregunta abierta (por ejemplo, si quiere que le busques un horario). No inventes precios, promociones ni horarios que no salgan en la conversación. Sin saludo largo y sin firmar."""


async def candidatas(horas: int, ahora: datetime) -> list[uuid.UUID]:
    """Conversaciones donde el paciente lleva entre `horas` y 23 h callado."""
    desde = ahora - timedelta(hours=VENTANA_HORAS)
    hasta = ahora - timedelta(hours=horas)
    ultimo_del_paciente = (
        select(Message.conversation_id, func.max(Message.created_at).label("cuando"))
        .join(Conversation, Conversation.id == Message.conversation_id)
        # `last_message_at` está indexada: deja el agregado en las
        # conversaciones de las últimas 24 h, no en toda la historia.
        .where(Message.rol == MessageRole.user, Conversation.last_message_at >= desde)
        .group_by(Message.conversation_id)
        .subquery()
    )
    cita_futura = exists().where(
        Appointment.contact_id == Conversation.contact_id,
        Appointment.estado.in_(ESTADOS_ACTIVOS),
        Appointment.starts_at > ahora,
    )
    q = (
        select(Conversation.id)
        .join(ultimo_del_paciente, ultimo_del_paciente.c.conversation_id == Conversation.id)
        .join(Contact, Contact.id == Conversation.contact_id)
        .where(
            and_(
                Conversation.canal.in_(CANALES),
                Conversation.status == ConversationStatus.bot,
                Conversation.ended_at.is_(None),
                Conversation.archived_at.is_(None),
                Conversation.quarantined_at.is_(None),
                Conversation.seguimiento_enviado_at.is_(None),
                ultimo_del_paciente.c.cuando >= desde,
                ultimo_del_paciente.c.cuando <= hasta,
                Contact.estado.notin_(YA_NO_SE_RETOMA),
                ~cita_futura,
            )
        )
        .order_by(ultimo_del_paciente.c.cuando)
        .limit(50)
    )
    async with db_session() as db:
        return [f[0] for f in await db.execute(q)]


def _cuenta(m: Message) -> bool:
    """Si el paciente vio este mensaje. Un borrador sin enviar (correo, modo
    Entrenamiento) no cuenta: el paciente nunca lo recibió."""
    extra = m.extra or {}
    return m.rol != MessageRole.system and not (extra.get("is_draft") and not extra.get("draft_sent"))


async def _historial(conv_id: uuid.UUID) -> list[Message]:
    async with db_session() as db:
        filas = (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conv_id)
                .order_by(Message.created_at.desc())
                .limit(HISTORIAL)
            )
        ).scalars().all()
    return [m for m in reversed(filas) if _cuenta(m) and (m.contenido or m.audio_transcript)]


def decision(respuesta: str) -> str | None:
    """El mensaje que escribió Sofía, o None si decidió no escribir."""
    texto = (respuesta or "").strip().strip('"').strip()
    if not texto or NO_ESCRIBIR in texto.upper():
        return None
    return texto[:MAX_CARACTERES]


async def _preguntar_a_sofia(agente, mensajes: list[Message], horas: int) -> str:
    from app.providers.llm import resolve_llm_provider
    from app.providers.llm.base import LLMMessage

    roles = {MessageRole.user: "user", MessageRole.assistant: "assistant", MessageRole.operator: "assistant"}
    historia = [
        LLMMessage(role=roles[m.rol], content=m.contenido or m.audio_transcript or "")
        for m in mensajes
        if m.rol in roles
    ]
    llm = await resolve_llm_provider(
        agente.llm_provider_id, agente.fallback_provider_id, agente.fallback_model
    )
    respuesta = await llm.complete(
        messages=[
            LLMMessage(role="system", content=agente.prompt_system or ""),
            *historia,
            LLMMessage(role="user", content=INSTRUCCION.format(horas=horas)),
        ],
        model=agente.model_name,
        temperature=0.3,
        max_tokens=300,
        source=FUENTE_DEL_GASTO,
    )
    return respuesta.content or ""


async def _apuntar(conv_id: uuid.UUID) -> bool:
    """Marca la conversación como retomada. False si otro ya la tomó."""
    async with db_session() as db:
        tomada = (
            await db.execute(
                text(
                    "UPDATE conversations SET seguimiento_enviado_at = now() "
                    "WHERE id = :id AND seguimiento_enviado_at IS NULL RETURNING id"
                ),
                {"id": conv_id},
            )
        ).first()
        await db.commit()
    return tomada is not None


async def _dejar_dicho(contact_id: uuid.UUID, tipo: str, meta: dict) -> None:
    from app.services.contact_activity import record_activity

    async with db_session() as db:
        await record_activity(db, contact_id, tipo, meta=meta)
        await db.commit()


async def retomar_una(conv_id: uuid.UUID, horas: int) -> str:
    """Una conversación: «enviado», «omitido» o «pendiente» (se mira luego)."""
    from app.services.channel_sender import send_text_to_conversation
    from app.services.delivery_status import marcar_enviado
    from app.services.runtime_config import get_runtime_for_channel
    from app.services.seguimientos import _baja_o_bloqueo, _freno, whatsapp_de

    async with db_session() as db:
        conv = await db.get(Conversation, conv_id)
        contacto = await db.get(Contact, conv.contact_id) if conv else None
        if conv is None or contacto is None:
            return "omitido"
        canal = conv.canal.value
        if await _freno(canal):
            return "pendiente"
        motivo = None
        if conv.canal == ConversationCanal.whatsapp:
            destino = whatsapp_de(contacto)
            motivo = await _baja_o_bloqueo(db, destino) if destino else "no tiene número de WhatsApp"

    mensajes = await _historial(conv_id)
    # Solo si lo último que vio el paciente es de Sofía: si lo último es suyo,
    # quien le debe una respuesta es el CRM, no un seguimiento.
    if not mensajes or mensajes[-1].rol != MessageRole.assistant or not any(
        m.rol == MessageRole.user for m in mensajes
    ):
        return "pendiente"
    ultimo_visto = mensajes[-1].id
    agente = await get_runtime_for_channel(canal)
    if agente is not None and getattr(agente, "motor", "crm") == "n8n":
        motivo = motivo or "el agente de este canal piensa en n8n"
    if agente is None:
        motivo = motivo or "no hay un agente con modelo para este canal"

    if not await _apuntar(conv_id):
        return "omitido"
    if motivo:
        await _dejar_dicho(contacto.id, "seguimiento_omitido", {"conversation_id": str(conv_id), "motivo": motivo})
        return "omitido"

    try:
        texto = decision(await _preguntar_a_sofia(agente, mensajes, horas))
    except Exception as exc:  # noqa: BLE001 — sin modelo, no se insiste: era un solo intento
        logger.warning("retomar.modelo_fallo", conversation_id=str(conv_id), error=str(exc)[:300])
        await _dejar_dicho(
            contacto.id, "seguimiento_omitido",
            {"conversation_id": str(conv_id), "motivo": "el modelo no contestó"},
        )
        return "omitido"
    if texto is None:
        await _dejar_dicho(
            contacto.id, "seguimiento_omitido",
            {"conversation_id": str(conv_id), "motivo": "Sofía decidió que no hacía falta"},
        )
        return "omitido"

    # El paciente pudo escribir mientras el modelo pensaba: entonces ya hay
    # conversación y un «¿sigues interesado?» llegaría a destiempo.
    if (await _historial(conv_id))[-1].id != ultimo_visto:
        await _dejar_dicho(
            contacto.id, "seguimiento_omitido",
            {"conversation_id": str(conv_id), "motivo": "el paciente escribió justo antes"},
        )
        return "omitido"

    async with db_session() as db:
        conv = await db.get(Conversation, conv_id)
        try:
            ext_id = await send_text_to_conversation(conv, texto)
        except Exception as exc:  # noqa: BLE001 — ventana cerrada, proveedor caído…
            logger.warning("retomar.envio_fallo", conversation_id=str(conv_id), error=str(exc)[:300])
            await _dejar_dicho(
                contacto.id, "seguimiento_omitido",
                {"conversation_id": str(conv_id), "motivo": f"no salió: {exc}"[:300]},
            )
            return "omitido"
        msg = Message(
            conversation_id=conv.id,
            rol=MessageRole.assistant,
            contenido=texto,
            extra=marcar_enviado({"kind": "seguimiento", "sent_by": "sistema"}, ext_id, conv.canal),
        )
        db.add(msg)
        conv.last_message_at = datetime.now(timezone.utc)
        from app.services.contact_activity import record_activity

        await record_activity(
            db, contacto.id, "seguimiento_enviado", meta={"conversation_id": str(conv_id), "canal": canal}
        )
        await db.commit()
        msg_id = msg.id

    try:
        from app.core.events import event_bus, inbox_channel

        await event_bus.publish(
            inbox_channel(),
            "message.new",
            {"conversation_id": str(conv_id), "message_id": str(msg_id), "rol": "assistant"},
        )
    except Exception:  # noqa: BLE001 — el panel se entera al recargar
        pass
    return "enviado"
