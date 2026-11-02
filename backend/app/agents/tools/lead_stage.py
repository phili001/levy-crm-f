"""Tool: mover a la persona de etapa del embudo (Fase 9, US-22 AC4).

Etapas con los nombres de la clínica: contacto → cotización → seguimiento →
paciente, o perdido / no califica. En la base siguen siendo los valores de
siempre (`solicitud_presupuesto`, `cliente`, `no_cualifica`): cambia el texto,
no el enum, así que no hace falta migración.

Como las demás herramientas de la ficha, solo toca a la persona de la
conversación, y el cambio queda en la actividad de la ficha como hecho por el
sistema (sin autor).
"""
import json
from typing import Any

from sqlalchemy import select

from app.agents.tools.calendar import _conversacion
from app.agents.tools.registry import Tool, register_tool
from app.core.logging import get_logger
from app.db.session import db_session
from app.models.contact import Contact, ContactEstado
from app.providers.llm.base import LLMToolSchema
from app.services.agenda import normalizar
from app.services.contact_activity import record_activity

logger = get_logger(__name__)

ETAPAS: dict[str, ContactEstado] = {
    "contacto": ContactEstado.contacto,
    "cotizacion": ContactEstado.solicitud_presupuesto,
    "seguimiento": ContactEstado.seguimiento,
    "paciente": ContactEstado.cliente,
    "perdido": ContactEstado.perdido,
    "no_califica": ContactEstado.no_cualifica,
}
_NOMBRE = {estado: nombre for nombre, estado in ETAPAS.items()}


def etapa_de(texto: Any) -> ContactEstado | None:
    """"Cotización", "no califica" o el valor de la base ("cliente") → estado."""
    clave = normalizar(str(texto or "")).replace(" ", "_")
    if clave in ETAPAS:
        return ETAPAS[clave]
    try:
        return ContactEstado(clave)
    except ValueError:
        return None


async def actualizar_estado_lead(args: dict[str, Any], ctx: dict[str, Any]) -> str:
    etapa = etapa_de(args.get("etapa"))
    if etapa is None:
        return json.dumps(
            {"error": "Etapa desconocida. Usa una de: " + ", ".join(ETAPAS) + "."},
            ensure_ascii=False,
        )
    conv = await _conversacion(ctx)
    if conv is None:
        return json.dumps({"error": "Solo puedo cambiar la etapa de la persona de esta conversación."})
    _conv_id, contact_id, _canal = conv

    async with db_session() as db:
        contact = (
            await db.execute(select(Contact).where(Contact.id == contact_id))
        ).scalar_one_or_none()
        if contact is None:
            return json.dumps({"error": "No encuentro la ficha de esta persona."})
        antes = contact.estado
        if antes == etapa:
            return json.dumps({"ok": True, "etapa": _NOMBRE[etapa], "sin_cambios": True})
        contact.estado = etapa
        await record_activity(
            db, contact.id, "status_changed", meta={"from": antes.value, "to": etapa.value}
        )
        await db.commit()
    logger.info("lead_stage.cambiada", contact_id=str(contact_id), a=etapa.value)
    return json.dumps({"ok": True, "etapa": _NOMBRE[etapa], "antes": _NOMBRE.get(antes)})


register_tool(
    Tool(
        schema=LLMToolSchema(
            name="actualizar_estado_lead",
            description=(
                "Cambia la etapa de la persona de esta conversación en el embudo: "
                "contacto (acaba de llegar), cotizacion (pidió precio), seguimiento "
                "(hay que volver a escribirle), paciente (ya agendó o vino), perdido o "
                "no_califica. Es interno: no se lo cuentes a la persona."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "etapa": {"type": "string", "enum": list(ETAPAS)},
                },
                "required": ["etapa"],
            },
        ),
        handler=actualizar_estado_lead,
    )
)
