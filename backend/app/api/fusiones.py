"""Propuestas «¿Es el mismo paciente?» (Fase 10.1).

Las deja `services/identity_linking.py`; aquí el panel las lee y las descarta.
La fusión en sí sigue siendo `POST /contacts/{id}/merge`, ahora con
`suggestion_id` (`api/contacts.py`): una sola puerta para una operación
irreversible sobre datos de pacientes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.appointment import ESTADOS_ACTIVOS, Appointment
from app.models.contact import Contact
from app.models.merge_suggestion import ESTADO_DESCARTADA, ESTADO_PENDIENTE, MergeSuggestion
from app.models.user import User
from app.schemas.common import OkResponse
from app.services import identity_linking
from app.services.audit import record_audit
from app.services.contact_merge import FusionImposible, claves_tras_fusion

router = APIRouter(prefix="/contacts", tags=["contacts"])

# De qué canal es cada ficha, para el texto del aviso. Primero la llave (lo que
# de verdad identifica a la ficha); si es un teléfono, el origen.
_CANAL_POR_PREFIJO = (
    ("ig:", "Instagram"),
    ("fb:", "Messenger"),
    ("web:", "chat web"),
    ("email:", "correo"),
    ("voice:", "llamada"),
    ("wa:", "WhatsApp"),
)
_CANAL_POR_ORIGEN = {
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "messenger": "Messenger",
    "web": "chat web",
    "email": "correo",
}


class FichaDePropuesta(BaseModel):
    id: uuid.UUID
    nombre: str | None
    telefono: str
    social_handle: str | None
    canal: str


class PropuestaFusionOut(BaseModel):
    id: uuid.UUID
    motivo: str
    # El teléfono o el correo que coincidió.
    dato: str
    # `absorbe` desaparece al fusionar; `queda` se queda con todo.
    absorbe: FichaDePropuesta
    queda: FichaDePropuesta
    # La llave con la que queda la ficha unida (ver `claves_tras_fusion`).
    telefono_final: str
    # Citas por venir de las dos fichas: la unida las conserva todas.
    citas_activas: int
    # Por qué no se puede unir ahora, si no se puede (el panel desactiva el botón).
    bloqueo: str | None
    created_at: str


def _canal(contact: Contact) -> str:
    for prefijo, nombre in _CANAL_POR_PREFIJO:
        if contact.telefono.startswith(prefijo):
            return nombre
    return _CANAL_POR_ORIGEN.get(contact.origen.value, "teléfono")


def _ficha(contact: Contact) -> FichaDePropuesta:
    return FichaDePropuesta(
        id=contact.id,
        nombre=contact.nombre,
        telefono=contact.telefono,
        social_handle=contact.social_handle,
        canal=_canal(contact),
    )


@router.get("/{contact_id}/merge-suggestions", response_model=list[PropuestaFusionOut])
async def list_merge_suggestions(
    contact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[PropuestaFusionOut]:
    """Propuestas pendientes en las que está esta ficha, de cualquiera de los dos
    lados: el aviso sale tanto en la conversación donde dio el teléfono como en
    la ficha que ya lo tenía."""
    propuestas = (
        await db.execute(
            select(MergeSuggestion)
            .where(
                or_(
                    MergeSuggestion.contact_id == contact_id,
                    MergeSuggestion.candidate_id == contact_id,
                ),
                MergeSuggestion.estado == ESTADO_PENDIENTE,
            )
            .order_by(MergeSuggestion.created_at)
        )
    ).scalars().all()
    if not propuestas:
        return []
    ids = {p.contact_id for p in propuestas} | {p.candidate_id for p in propuestas}
    fichas = {
        c.id: c
        for c in (await db.execute(select(Contact).where(Contact.id.in_(ids)))).scalars().all()
    }
    ahora = datetime.now(timezone.utc)
    salida: list[PropuestaFusionOut] = []
    for p in propuestas:
        absorbe, queda = fichas.get(p.contact_id), fichas.get(p.candidate_id)
        if absorbe is None or queda is None:
            continue
        # Se recalcula al leerla: desde que se propuso, alguna de las dos pudo
        # unirse con otra ficha y ya no caber.
        try:
            telefono_final, _identidad = claves_tras_fusion(queda, absorbe)
            bloqueo = None
        except FusionImposible as e:
            telefono_final, bloqueo = queda.telefono, str(e)
        citas = (
            await db.execute(
                select(func.count())
                .select_from(Appointment)
                .where(
                    Appointment.contact_id.in_([absorbe.id, queda.id]),
                    Appointment.estado.in_(ESTADOS_ACTIVOS),
                    Appointment.starts_at >= ahora,
                )
            )
        ).scalar_one()
        salida.append(
            PropuestaFusionOut(
                id=p.id,
                motivo=p.motivo,
                dato=p.dato,
                absorbe=_ficha(absorbe),
                queda=_ficha(queda),
                telefono_final=telefono_final,
                citas_activas=int(citas),
                bloqueo=bloqueo,
                created_at=p.created_at.isoformat() if p.created_at else "",
            )
        )
    return salida


@router.post("/merge-suggestions/{suggestion_id}/dismiss", response_model=OkResponse)
async def dismiss_merge_suggestion(
    suggestion_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> OkResponse:
    """«No es el mismo paciente»: la propuesta se descarta y ese par no se
    vuelve a proponer.

    SOLO admin, igual que la fusión: descartar también decide sobre quién es
    quién, y no se vuelve a preguntar.
    """
    propuesta = (
        await db.execute(select(MergeSuggestion).where(MergeSuggestion.id == suggestion_id))
    ).scalar_one_or_none()
    if propuesta is None or propuesta.estado != ESTADO_PENDIENTE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "La propuesta ya no está pendiente")
    propuesta.estado = ESTADO_DESCARTADA
    propuesta.resuelta_at = datetime.now(timezone.utc)
    await record_audit(
        db,
        user_id=current_user.id,
        action="contacts.merge_suggestion_dismissed",
        entity="contact",
        entity_id=propuesta.contact_id,
        after={"candidate_id": str(propuesta.candidate_id), "motivo": propuesta.motivo},
    )
    await db.commit()
    await identity_linking.avisar_panel()
    return OkResponse()
