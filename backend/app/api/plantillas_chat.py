"""Plantillas de WhatsApp desde el chat: la salida cuando la ventana de 24 h
se ha cerrado.

Pasadas 24 h desde el último mensaje del cliente, WhatsApp solo deja escribirle
con una plantilla aprobada. Antes el Inbox se limitaba a avisar con un
"(próximamente)", y la única forma de mandar una plantilla a una persona era la
ficha del contacto, que va por `/admin/outbound/send`: solo admin, dependiente
del módulo de envío masivo y sin dejar el mensaje en la conversación. El
operador que tenía delante un cliente al que contestar se quedaba sin salida.

Aquí:
  - Lo puede usar cualquier operador (no el revisor: no está en su lista
    blanca de `deps.py`). Es una respuesta uno a uno, no una difusión.
  - Por eso NO mira las bajas de difusiones (`outbound_optout`): esas son de
    los envíos masivos. Los números bloqueados sí se respetan.
  - El mensaje queda en el hilo, con el texto ya rellenado, como lo haría una
    respuesta escrita a mano.
  - Solo se ofrecen las plantillas que se pueden mandar sin nada más que
    texto: aprobadas, sin fichero en la cabecera ni variables en cabecera o
    botones. Las demás siguen en Campañas, que sabe subir el fichero.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import WhatsappTemplateOut, _template_status_problem, _template_to_out
from app.api.conversations import MessageOut, _message_out, _puede_ver_conversacion
from app.api.deps import get_current_user
from app.core.events import event_bus, inbox_channel
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationCanal, ConversationStatus
from app.models.message import Message, MessageRole
from app.models.user import User
from app.providers.whatsapp import get_whatsapp_provider
from app.services.agent_guardrails import is_phone_blocked
from app.services.audit import record_audit
# `texto_rellenado` vive en el servicio desde la SPEC-18; se sigue importando de aquí.
from app.services.plantilla_saliente import EnvioFallido, mandar_plantilla, texto_rellenado  # noqa: F401

logger = get_logger(__name__)
router = APIRouter(tags=["conversations"])


def _enviable_desde_el_chat(t: WhatsappTemplateOut) -> bool:
    return (
        _template_status_problem(t.status) is None
        and t.header_format in (None, "", "text")
        and t.header_variables == 0
        and t.button_variables == 0
    )


async def _plantillas() -> list[WhatsappTemplateOut]:
    from app.providers.whatsapp.ycloud import TemplateListError, WhatsAppNotConfiguredError

    wa = get_whatsapp_provider()
    if not hasattr(wa, "list_templates"):
        return []
    try:
        return [_template_to_out(t) for t in await wa.list_templates()]
    except WhatsAppNotConfiguredError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except TemplateListError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/whatsapp/plantillas", response_model=list[WhatsappTemplateOut])
async def plantillas_para_el_chat(
    _: User = Depends(get_current_user),
) -> list[WhatsappTemplateOut]:
    return [t for t in await _plantillas() if _enviable_desde_el_chat(t)]


class EnviarPlantillaIn(BaseModel):
    template_name: str = Field(..., min_length=1, max_length=512)
    language: str = Field(..., min_length=1, max_length=20)
    # En el orden de los huecos del cuerpo ({{1}}, {{2}}… o los con nombre).
    variables: list[str] = Field(default_factory=list, max_length=30)


@router.post("/conversations/{conversation_id}/template", response_model=MessageOut)
async def enviar_plantilla(
    conversation_id: uuid.UUID,
    body: EnviarPlantillaIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MessageOut:
    row = (
        await db.execute(
            select(Conversation, Contact)
            .join(Contact, Conversation.contact_id == Contact.id)
            .where(Conversation.id == conversation_id)
        )
    ).first()
    if not row or not _puede_ver_conversacion(user, row[0]):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    conv, contact = row
    if conv.canal != ConversationCanal.whatsapp:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Las plantillas solo existen en WhatsApp."
        )
    if conv.status == ConversationStatus.cerrada:
        raise HTTPException(status.HTTP_409_CONFLICT, "La conversación está cerrada")
    destino = contact.telefono or contact.wa_user_id
    if not destino:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "El contacto no tiene número de WhatsApp."
        )
    if contact.telefono and await is_phone_blocked(contact.telefono):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Este número está bloqueado; no se le envía nada."
        )

    plantilla = next(
        (
            t
            for t in await _plantillas()
            if t.name == body.template_name and t.language == body.language
        ),
        None,
    )
    if plantilla is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esa plantilla no existe en la cuenta.")
    problema = _template_status_problem(plantilla.status)
    if problema:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problema)
    if not _enviable_desde_el_chat(plantilla):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Esta plantilla lleva un fichero o variables en la cabecera o en los botones. "
            "Envíala desde Campañas.",
        )
    if len(body.variables) != len(plantilla.variable_names) or any(
        not v.strip() for v in body.variables
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Rellena todas las variables de la plantilla."
        )

    # Quien manda una plantilla está atendiendo: el agente se aparta, igual
    # que cuando el operador escribe a mano.
    if conv.status == ConversationStatus.bot:
        conv.status = ConversationStatus.humano
    try:
        msg = await mandar_plantilla(
            db,
            conv=conv,
            destino=destino,
            plantilla=plantilla,
            variables=body.variables,
            rol=MessageRole.operator,
            extra={"sent_by": "operator"},
            wa=get_whatsapp_provider(),
        )
    except EnvioFallido as exc:
        # El motivo del proveedor va tal cual: "saldo insuficiente en YCloud"
        # le dice al operador qué hacer; un "no se pudo enviar" no.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"WhatsApp no aceptó la plantilla: {exc}") from exc
    await record_audit(
        db,
        user_id=user.id,
        action="conversation.template_sent",
        entity="conversation",
        entity_id=conv.id,
        after={"template": plantilla.name, "language": plantilla.language},
    )
    await db.commit()
    await db.refresh(msg)

    try:
        await event_bus.publish(
            inbox_channel(),
            "message.new",
            {"conversation_id": str(conv.id), "message_id": str(msg.id), "rol": "operator"},
        )
    except Exception:
        pass
    return _message_out(msg)
