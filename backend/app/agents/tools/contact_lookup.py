"""Tool: consultar los datos del contacto del usuario que está hablando.

SEGURIDAD: solo devuelve el contacto cuyo teléfono coincide con el del
webhook (ctx.telefono). Antes la tool aceptaba `telefono` o `email` arbitrarios
en args, lo que permitía a un usuario WhatsApp pedirle al agente que buscara
"el contacto con email jefe@empresa.com" y filtrar PII de cualquier contacto.

Desde la Fase 9 devuelve también sus próximas citas (con el `cita_id` que
piden reagendar_cita y cancelar_cita) y sus últimas notas, para que Sofía no
pregunte lo que el equipo ya sabe. Las notas son internas: la descripción de
la herramienta le dice al modelo que no se las lea a la persona.

Por teléfono no salen ni las notas ni el correo: el número de quien llama se
puede falsificar, y quien llamara con el de un paciente las oiría (dry-run 2,
SEC-12). El nombre y las citas sí, que son lo que Sofía necesita para atender.
"""
import json
from typing import Any

from sqlalchemy import select

from app.agents.tools.calendar import _cita_json, _es_voz
from app.agents.tools.registry import Tool, register_tool
from app.agents.tools.schedule_config import get_schedule_config, now_in
from app.core.logging import get_logger
from app.db.session import db_session
from app.models.contact import Contact
from app.models.contact_note import ContactNote
from app.providers.llm.base import LLMToolSchema
from app.services import agenda

logger = get_logger(__name__)

_MAX_NOTAS = 3
_MAX_LARGO_NOTA = 300


async def buscar_contacto(_args: dict[str, Any], ctx: dict[str, Any]) -> str:
    telefono = (ctx.get("telefono") or "").strip()
    if not telefono:
        return json.dumps({"found": False, "error": "Contacto no identificado"})

    voz = _es_voz(ctx)
    async with db_session() as db:
        contact = (
            await db.execute(select(Contact).where(Contact.telefono == telefono))
        ).scalar_one_or_none()
        if not contact:
            return json.dumps({"found": False})
        notas = [] if voz else (
            await db.execute(
                select(ContactNote.texto)
                .where(ContactNote.contact_id == contact.id)
                .order_by(ContactNote.created_at.desc())
                .limit(_MAX_NOTAS)
            )
        ).scalars().all()

    cfg = await get_schedule_config()
    ahora = now_in(cfg.tz)
    citas = await agenda.citas_proximas(contact.id, ahora=ahora)
    return json.dumps(
        {
            "found": True,
            "id": str(contact.id),
            "nombre": contact.nombre,
            # Devolvemos los campos que el agente necesita para conversar.
            # NUNCA devolvemos teléfono/email de OTROS contactos.
            "email": None if voz else contact.email,
            "estado": contact.estado.value,
            "servicio_interes": contact.servicio_interes,
            "citas_proximas": [_cita_json(c, cfg, ahora, voz=voz) for c in citas],
            "notas_internas_recientes": [
                (texto or "")[:_MAX_LARGO_NOTA] for texto in notas if texto
            ],
        },
        ensure_ascii=False,
    )


register_tool(
    Tool(
        schema=LLMToolSchema(
            name="buscar_contacto",
            description=(
                "Recupera los datos del contacto que está hablando (su teléfono "
                "se toma automáticamente de la conversación, no se acepta como "
                "parámetro): estado del lead, sus próximas citas (la primera es la "
                "siguiente) y las últimas notas del equipo. Las notas son internas: "
                "úsalas como contexto, no se las leas ni las cites a la persona. "
                "NO puede usarse para consultar otros contactos del CRM."
            ),
            parameters={"type": "object", "properties": {}},
        ),
        handler=buscar_contacto,
    )
)
