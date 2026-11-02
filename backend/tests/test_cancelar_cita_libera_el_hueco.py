"""Sofía cancela la cita de quien se lo pide, y el hueco vuelve a estar libre (Fase 9, US-22 AC2).

La cita cancelada no se borra: queda en la ficha con su estado, para la
historia. Sin `cita_id` se cancela la próxima; con uno ajeno, nada. Y la
cancelación queda en la actividad de la ficha.
"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.agents.tools import calendar as cal
from app.services import agenda
from tests._agenda import (
    HORARIO,
    LUNES_9,
    MARTES,
    a_las,
    citas_de,
    ctx,
    fijar_agenda,
    limpiar,
    persona,
)
from tests.test_voice_gating import pytestmark_db


async def _agendada(conv, hora: int) -> dict:
    await cal.consultar_disponibilidad({"fecha": MARTES.isoformat(), "hora": f"{hora:02d}:00"}, ctx(conv))
    respuesta = json.loads(
        await cal.agendar_cita({"start": a_las(MARTES, hora).isoformat(), "servicio": "valoración"}, ctx(conv))
    )
    assert respuesta["ok"] is True, respuesta
    return respuesta


@pytestmark_db
async def test_cancelar_suelta_el_hueco_y_la_cita_queda_en_la_ficha(monkeypatch):
    from app.db.session import db_session
    from app.models.contact_activity import ContactActivity

    fijar_agenda(monkeypatch)
    conv, contacto = await persona()
    try:
        await _agendada(conv, 10)

        respuesta = json.loads(await cal.cancelar_cita({}, ctx(conv)))

        assert respuesta["ok"] is True
        assert respuesta["cancelada"]["label"] == "mañana a las 10:00"
        [cita] = await citas_de(contacto)
        assert cita.estado == "cancelada"
        libres = await agenda.huecos(MARTES, MARTES, 30, cfg=HORARIO, ahora=LUNES_9)
        assert a_las(MARTES, 10) in [h.inicio for h in libres]
        async with db_session() as db:
            tipos = (
                await db.execute(
                    select(ContactActivity.tipo).where(ContactActivity.contact_id == contacto)
                )
            ).scalars().all()
        assert {"appointment_booked", "appointment_cancelled"} <= set(tipos)
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_sin_cita_proxima_no_hay_nada_que_cancelar(monkeypatch):
    fijar_agenda(monkeypatch)
    conv, contacto = await persona()
    try:
        respuesta = json.loads(await cal.cancelar_cita({}, ctx(conv)))
        assert "no tiene ninguna cita" in respuesta["error"]
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_no_se_puede_cancelar_la_cita_de_otra_persona(monkeypatch):
    fijar_agenda(monkeypatch)
    conv_a, contacto_a = await persona()
    conv_b, contacto_b = await persona()
    try:
        cita_a = await _agendada(conv_a, 10)
        respuesta = json.loads(await cal.cancelar_cita({"cita_id": cita_a["cita_id"]}, ctx(conv_b)))
        assert "no tiene ninguna cita" in respuesta["error"]
        assert [c.estado for c in await citas_de(contacto_a)] == ["agendada"]
    finally:
        await limpiar([contacto_a, contacto_b])
