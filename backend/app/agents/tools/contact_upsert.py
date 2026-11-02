"""Tool: actualizar el contacto del usuario que está hablando.

SEGURIDAD: el teléfono SIEMPRE es el del webhook (ctx.telefono). Ignoramos
cualquier valor de `args["telefono"]` que el LLM pudiera intentar pasar
inducido por un usuario malicioso ("mi número real es +34999..."). Esto evita
que un atacante en WhatsApp pueda pisar/leakear datos de OTRO contacto.

`telefono_declarado` (Fase 10.1) es otra cosa: el teléfono que la persona dice
en un canal que no lo da (Instagram, Messenger, chat web, correo). Se guarda
aparte y NUNCA toca `telefono`: solo sirve para proponerle al equipo unir dos
fichas (`services/identity_linking.py`), y la fusión la decide una persona.
"""
import json
from typing import Any

from sqlalchemy import select

from app.agents.tools.registry import Tool, register_tool
from app.core.logging import get_logger
from app.db.session import db_session
from app.models.contact import Contact, ContactEstado, ContactOrigen
from app.providers.llm.base import LLMToolSchema
from app.services import identity_linking
from app.services.security_alerts import notify_security

logger = get_logger(__name__)


async def crear_actualizar_contacto(args: dict[str, Any], ctx: dict[str, Any]) -> str:
    # El teléfono se toma SIEMPRE del contexto del webhook, NUNCA de los args.
    telefono = (ctx.get("telefono") or "").strip()
    if not telefono:
        return json.dumps({"error": "No se ha podido identificar el contacto"})

    # Si el LLM intentó pasar otro teléfono, lo registramos como intento.
    if args.get("telefono") and args.get("telefono").strip() != telefono:
        attempted = args.get("telefono").strip()
        logger.warning(
            "agent.tool.telefono_override_blocked",
            ctx_phone="<PHONE>",
            arg_phone="<PHONE>",
        )
        await notify_security(
            kind="phone_override",
            title="Intento de suplantación de teléfono vía agente",
            details={
                "real_phone": telefono,
                "intento_phone": attempted,
            },
            throttle_key=telefono,
        )

    update_fields: dict[str, Any] = {}
    for k in ("nombre", "email", "servicio_interes"):
        v = args.get(k)
        if v and isinstance(v, str):
            update_fields[k] = v.strip()[:200]  # límite duro para no llenar BD
    if email := update_fields.get("email"):
        update_fields["email"] = email.lower()

    estado_in = args.get("estado")
    if estado_in:
        try:
            update_fields["estado"] = ContactEstado(estado_in)
        except ValueError:
            pass

    # Si el agente captura identidad real (nombre o email), deja de ser un
    # visitante anónimo y pasa a ser un lead de verdad. Voz e Instagram crean
    # el contacto con in_crm=False para no llenar el CRM de gente sin datos;
    # en cuanto dejan nombre/email, se promueve para que aparezca en Contactos.
    captured_identity = bool(update_fields.get("nombre") or update_fields.get("email"))

    # Import local: la lista canónica de prefijos vive en la capa de API.
    from app.api.contacts import is_channel_identifier

    declarado_in = str(args.get("telefono_declarado") or "").strip()
    declarado = identity_linking.telefono_declarado_e164(declarado_in) if declarado_in else None

    async with db_session() as db:
        existing = (
            await db.execute(select(Contact).where(Contact.telefono == telefono))
        ).scalar_one_or_none()
        if existing:
            for k, v in update_fields.items():
                setattr(existing, k, v)
            if captured_identity and not existing.in_crm:
                existing.in_crm = True
            aviso_telefono = None
            nuevo_declarado = False
            if declarado_in:
                if not is_channel_identifier(existing.telefono):
                    # WhatsApp con número y las llamadas ya traen el teléfono de
                    # verdad: uno "declarado" aquí sería un segundo número.
                    aviso_telefono = "no hace falta: este canal ya trae su teléfono"
                elif declarado is None:
                    aviso_telefono = "no parece un teléfono completo; pídeselo con lada"
                elif existing.telefono_declarado != declarado:
                    existing.telefono_declarado = declarado
                    nuevo_declarado = True
            await db.commit()
            await db.refresh(existing)
            if nuevo_declarado or update_fields.get("email"):
                await identity_linking.proponer_para(
                    existing.id,
                    telefono_declarado=nuevo_declarado,
                    email=bool(update_fields.get("email")),
                )
            respuesta = {"updated": True, "id": str(existing.id), "nombre": existing.nombre}
            if aviso_telefono:
                respuesta["telefono_declarado"] = aviso_telefono
            return json.dumps(respuesta, ensure_ascii=False)
        # crear
        contact = Contact(
            telefono=telefono,
            origen=ContactOrigen.whatsapp,
            estado=ContactEstado.contacto,
            **update_fields,
        )
        db.add(contact)
        await db.commit()
        await db.refresh(contact)
        return json.dumps({"created": True, "id": str(contact.id), "nombre": contact.nombre})


register_tool(
    Tool(
        schema=LLMToolSchema(
            name="crear_actualizar_contacto",
            description=(
                "Actualiza los datos del contacto que está hablando (su teléfono "
                "se toma automáticamente de la conversación, NO se acepta como "
                "parámetro). Úsalo para registrar nombre, email, servicio de "
                "interés o estado del lead. Si por Instagram, Messenger, el chat "
                "web o el correo te da su teléfono, guárdalo en "
                "`telefono_declarado`. No informes al usuario de acciones "
                "internas del CRM."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "nombre": {"type": "string"},
                    "email": {"type": "string"},
                    "servicio_interes": {"type": "string"},
                    "telefono_declarado": {
                        "type": "string",
                        "description": (
                            "El teléfono que la persona te dio en ESTA conversación, "
                            "tal como lo dijo. Solo en Instagram, Messenger, chat web "
                            "o correo: WhatsApp y las llamadas ya traen el suyo. No "
                            "cambia quién es: el equipo decide si es la misma persona "
                            "que otra ficha."
                        ),
                    },
                    "estado": {
                        "type": "string",
                        "enum": [
                            "contacto",
                            "solicitud_presupuesto",
                            "seguimiento",
                            "cliente",
                            "perdido",
                            "no_cualifica",
                        ],
                    },
                },
            },
        ),
        handler=crear_actualizar_contacto,
    )
)
