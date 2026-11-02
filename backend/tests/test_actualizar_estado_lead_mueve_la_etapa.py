"""Sofía mueve a la persona de etapa con los nombres de la clínica (Fase 9, US-22 AC4).

contacto → cotización → seguimiento → paciente, o perdido / no califica. En la
base siguen siendo los valores de siempre (`solicitud_presupuesto`, `cliente`,
`no_cualifica`): cambia el texto, no el enum. Solo se toca a la persona de la
conversación, y el cambio queda en la actividad de la ficha.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.agents.tools import lead_stage
from app.models.contact import ContactEstado
from tests._agenda import ctx, limpiar, persona
from tests.test_voice_gating import pytestmark_db


@pytest.mark.parametrize(
    ("dicho", "estado"),
    [
        ("contacto", ContactEstado.contacto),
        ("Cotización", ContactEstado.solicitud_presupuesto),
        ("seguimiento", ContactEstado.seguimiento),
        ("paciente", ContactEstado.cliente),
        ("perdido", ContactEstado.perdido),
        ("No califica", ContactEstado.no_cualifica),
        ("cliente", ContactEstado.cliente),
        ("inventada", None),
    ],
)
def test_las_etapas_de_la_clinica_son_los_estados_de_siempre(dicho, estado):
    assert lead_stage.etapa_de(dicho) == estado


@pytestmark_db
async def test_sofia_mueve_la_etapa_y_queda_en_la_actividad():
    from app.db.session import db_session
    from app.models.contact import Contact
    from app.models.contact_activity import ContactActivity

    conv, contacto = await persona()
    try:
        out = json.loads(await lead_stage.actualizar_estado_lead({"etapa": "cotizacion"}, ctx(conv)))
        assert out == {"ok": True, "etapa": "cotizacion", "antes": "contacto"}

        out = json.loads(await lead_stage.actualizar_estado_lead({"etapa": "paciente"}, ctx(conv)))
        assert out["antes"] == "cotizacion"
        again = json.loads(await lead_stage.actualizar_estado_lead({"etapa": "paciente"}, ctx(conv)))
        assert again["sin_cambios"] is True

        async with db_session() as db:
            estado = (
                await db.execute(select(Contact.estado).where(Contact.id == contacto))
            ).scalar_one()
            cambios = (
                await db.execute(
                    select(ContactActivity.meta)
                    .where(
                        ContactActivity.contact_id == contacto,
                        ContactActivity.tipo == "status_changed",
                    )
                    .order_by(ContactActivity.created_at)
                )
            ).scalars().all()
        assert estado == ContactEstado.cliente
        assert [(m["from"], m["to"]) for m in cambios] == [
            ("contacto", "solicitud_presupuesto"),
            ("solicitud_presupuesto", "cliente"),
        ]
    finally:
        await limpiar([contacto])


async def test_una_etapa_desconocida_se_rechaza_con_las_validas():
    out = json.loads(await lead_stage.actualizar_estado_lead({"etapa": "vip"}, {}))
    assert "cotizacion" in out["error"] and "no_califica" in out["error"]
