"""`buscar_contacto` le da a Sofía las próximas citas y las últimas notas (Fase 9, US-22 AC3).

Antes devolvía solo id, nombre, email, estado y servicio: Sofía no sabía que
la persona ya tenía cita ni lo que el equipo había anotado. Ahora trae sus
próximas citas (la primera es la siguiente, con el `cita_id` que piden
reagendar_cita y cancelar_cita) y las tres últimas notas, de la más nueva a
la más vieja. Las canceladas no salen.
"""
from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select

from app.agents.tools import calendar as cal
from app.agents.tools import contact_lookup
from tests._agenda import MARTES, a_las, ctx, fijar_agenda, limpiar, persona
from tests.test_voice_gating import pytestmark_db


async def _agendar(conv, dia, hora: int) -> dict:
    await cal.consultar_disponibilidad({"fecha": dia.isoformat(), "hora": f"{hora:02d}:00"}, ctx(conv))
    return json.loads(
        await cal.agendar_cita({"start": a_las(dia, hora).isoformat(), "servicio": "valoración"}, ctx(conv))
    )


@pytestmark_db
async def test_trae_las_proximas_citas_y_las_ultimas_notas(monkeypatch):
    from app.db.session import db_session
    from app.models.contact import Contact
    from app.models.contact_note import ContactNote

    fijar_agenda(monkeypatch)
    conv, contacto = await persona()
    try:
        miercoles = MARTES + timedelta(days=1)
        await _agendar(conv, MARTES, 10)
        await _agendar(conv, miercoles, 12)
        cancelada = await _agendar(conv, MARTES, 16)
        await cal.cancelar_cita({"cita_id": cancelada["cita_id"]}, ctx(conv))

        async with db_session() as db:
            telefono = (
                await db.execute(select(Contact.telefono).where(Contact.id == contacto))
            ).scalar_one()
            for i in range(1, 5):
                db.add(
                    ContactNote(
                        contact_id=contacto,
                        texto=f"nota {i}",
                        created_at=a_las(MARTES, 8) - timedelta(days=10 - i),
                    )
                )
            await db.commit()

        out = json.loads(
            await contact_lookup.buscar_contacto({}, {"telefono": telefono, "conversation_id": str(conv)})
        )

        assert [c["label"] for c in out["citas_proximas"]] == [
            "mañana a las 10:00",
            "el miércoles 30 a las 12:00",
        ]
        assert out["citas_proximas"][0]["cita_id"] and out["citas_proximas"][0]["servicio"]
        assert out["notas_internas_recientes"] == ["nota 4", "nota 3", "nota 2"]
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_sin_citas_ni_notas_las_listas_salen_vacias(monkeypatch):
    from app.db.session import db_session
    from app.models.contact import Contact

    fijar_agenda(monkeypatch)
    conv, contacto = await persona()
    try:
        async with db_session() as db:
            telefono = (
                await db.execute(select(Contact.telefono).where(Contact.id == contacto))
            ).scalar_one()
        out = json.loads(await contact_lookup.buscar_contacto({}, {"telefono": telefono}))
        assert out["found"] is True
        assert out["citas_proximas"] == [] and out["notas_internas_recientes"] == []
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_por_telefono_no_salen_las_notas_ni_el_correo(monkeypatch):
    """Salió en el dry-run 2 (SEC-12): el número de quien llama se puede
    falsificar, y quien llamara con el de un paciente oía sus notas internas."""
    from sqlalchemy import update

    from app.db.session import db_session
    from app.models.contact import Contact
    from app.models.contact_note import ContactNote

    fijar_agenda(monkeypatch)
    conv, contacto = await persona()
    try:
        await _agendar(conv, MARTES, 10)
        async with db_session() as db:
            await db.execute(
                update(Contact).where(Contact.id == contacto).values(email="paciente@ejemplo.mx")
            )
            db.add(ContactNote(contact_id=contacto, texto="alérgico a la penicilina"))
            await db.commit()
            telefono = (
                await db.execute(select(Contact.telefono).where(Contact.id == contacto))
            ).scalar_one()

        por_texto = json.loads(await contact_lookup.buscar_contacto({}, {"telefono": telefono}))
        por_voz = json.loads(
            await contact_lookup.buscar_contacto(
                {}, {"telefono": telefono, **ctx(conv, voz=True)}
            )
        )

        assert por_texto["notas_internas_recientes"] == ["alérgico a la penicilina"]
        assert por_texto["email"] == "paciente@ejemplo.mx"
        assert por_voz["notas_internas_recientes"] == [] and por_voz["email"] is None
        assert por_voz["nombre"] == por_texto["nombre"]
        assert len(por_voz["citas_proximas"]) == 1
    finally:
        await limpiar([contacto])
