"""Mandar una plantilla de WhatsApp y dejarla en el hilo (SPEC-18).

Antes había dos caminos y ninguno servía para una tarea del beat: el del chat
(`api/plantillas_chat.py`) exige un operador conectado, y el de las difusiones
(`tasks/outbound_send.py`) anota «[Campaña] Plantilla «x» con: …» en vez del
texto que leyó el paciente. Los recordatorios necesitan las dos cosas que
faltaban: salir sin nadie delante y quedar en la conversación tal como los
recibió el paciente, para que Sofía sepa qué le preguntaron cuando conteste
«Confirmo».

Aquí no se hace commit: lo hace quien llama, junto con lo suyo (la marca de la
cita, la actividad de la ficha), para que todo quede o no quede a la vez.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationCanal, ConversationStatus
from app.models.message import Message, MessageRole

if TYPE_CHECKING:
    from app.api.admin import WhatsappTemplateOut

logger = get_logger(__name__)


class EnvioFallido(RuntimeError):
    """WhatsApp no aceptó la plantilla. El mensaje es el motivo del proveedor."""


def texto_rellenado(t: WhatsappTemplateOut, variables: list[str]) -> str:
    """El texto tal como lo recibe el cliente, para guardarlo en el hilo.

    Con formato posicional, `{{n}}` es `variables[n-1]`; con nombre, el valor va
    en el orden de `variable_names`. Cabecera de texto y pie van también: son
    parte de lo que el cliente lee.
    """
    valores = dict(zip(t.variable_names, variables))

    def poner(m: re.Match) -> str:
        return valores.get(m.group(1), m.group(0))

    cuerpo = re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", poner, t.body or "")
    partes = [p for p in (t.header_text, cuerpo, t.footer) if p]
    return "\n\n".join(partes)


async def plantilla_de_la_cuenta(nombre: str, idioma: str) -> WhatsappTemplateOut | None:
    """La plantilla de la cuenta con ese nombre e idioma, o None.

    En vivo contra el proveedor, como el listado del panel: si Meta la pausó
    esta mañana, se sabe ahora y no con la copia de ayer. Lanza lo que lance el
    proveedor (sin credenciales, clave mala): quien llama decide si reintenta.
    """
    from app.api.admin import _template_to_out
    from app.providers.whatsapp import get_whatsapp_provider

    for t in await get_whatsapp_provider().list_templates():  # type: ignore[attr-defined]
        salida = _template_to_out(t)
        if salida.name == nombre and salida.language == idioma:
            return salida
    return None


async def conversacion_whatsapp(db: AsyncSession, contact: Contact, destino: str) -> Conversation:
    """La conversación de WhatsApp abierta del contacto, o una nueva.

    Es la que encuentra el mensaje entrante cuando el paciente contesta, así
    que la respuesta cae en el mismo hilo que el aviso.
    """
    conv = (
        await db.execute(
            select(Conversation)
            .where(
                Conversation.contact_id == contact.id,
                Conversation.canal == ConversationCanal.whatsapp,
                Conversation.ended_at.is_(None),
            )
            .order_by(Conversation.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if conv is None:
        conv = Conversation(
            contact_id=contact.id,
            canal=ConversationCanal.whatsapp,
            session_id=destino,
            status=ConversationStatus.bot,
        )
        db.add(conv)
        await db.flush()
    return conv


async def mandar_plantilla(
    db: AsyncSession,
    *,
    conv: Conversation,
    destino: str,
    plantilla: WhatsappTemplateOut,
    variables: list[str],
    rol: MessageRole,
    extra: dict,
    idempotency_key: str | None = None,
    wa=None,
) -> Message:
    """Manda la plantilla y añade al hilo el mensaje con el texto rellenado.

    Lanza `EnvioFallido` con el motivo del proveedor si no sale. Si sale, el
    mensaje queda en la sesión (sin commit) con su id de WhatsApp, para que los
    ✓✓ casen con él. `wa`: el proveedor, si quien llama ya tiene uno.
    """
    from app.providers.whatsapp import get_whatsapp_provider
    from app.services.channel_sender import PROVIDER_NOOP_ID, contar_saliente_whatsapp
    from app.services.delivery_status import marcar_enviado

    try:
        ext_id = await (wa or get_whatsapp_provider()).send_template(
            destino,
            plantilla.name,
            plantilla.language,
            variables,
            param_format=plantilla.param_format,
            variable_names=plantilla.variable_names,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001 — el motivo del proveedor es lo que sirve
        logger.warning(
            "plantilla_saliente.fallo", conversation_id=str(conv.id), error=str(exc)[:300]
        )
        raise EnvioFallido(str(exc)) from exc
    if not ext_id or ext_id == PROVIDER_NOOP_ID:
        raise EnvioFallido("El proveedor de WhatsApp no envió la plantilla: revisa sus credenciales.")
    await contar_saliente_whatsapp(conv.id)

    msg = Message(
        conversation_id=conv.id,
        rol=rol,
        contenido=texto_rellenado(plantilla, variables),
        extra=marcar_enviado(
            {**extra, "template": {"name": plantilla.name, "language": plantilla.language}},
            ext_id,
            conv.canal,
        ),
    )
    db.add(msg)
    conv.last_message_at = datetime.now(timezone.utc)
    return msg
