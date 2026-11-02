"""Sofía solo agenda horarios que le ofreció a esa persona (Fase 9, US-22 AC1).

`consultar_disponibilidad` apunta en `offered_slots` lo que le enseña al
modelo, y `agendar_cita` solo acepta un `start` ofrecido a ESA conversación en
los últimos 30 minutos, comparado por instante exacto. Si no, rechaza sin crear
nada y vuelve con horarios reales de ese día para ofrecer: el modelo no puede
confirmar una hora que se inventó.

La cita queda ligada a la persona y a la conversación (antes solo se escribía
en Google) y dura lo que dura su servicio.
"""
from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import update

from app.agents.tools import calendar as cal
from app.models.conversation import ConversationCanal
from tests._agenda import MARTES, a_las, citas_de, ctx, fijar_agenda, limpiar, persona
from tests.test_voice_gating import pytestmark_db


async def _consultar(conv, hora: str | None = None) -> dict:
    args = {"fecha": MARTES.isoformat()}
    if hora:
        args["hora"] = hora
    return json.loads(await cal.consultar_disponibilidad(args, ctx(conv)))


async def _agendar(conv, start: str, servicio: str = "valoración") -> dict:
    return json.loads(await cal.agendar_cita({"start": start, "servicio": servicio}, ctx(conv)))


@pytestmark_db
async def test_sin_consultar_antes_no_se_agenda_y_vuelve_con_horarios(monkeypatch):
    fijar_agenda(monkeypatch)
    conv, contacto = await persona()
    try:
        respuesta = await _agendar(conv, a_las(MARTES, 11).isoformat())

        assert respuesta["codigo"] == "no_ofrecido"
        assert "No confirmes" in respuesta["error"]
        assert await citas_de(contacto) == []
        ofrecidos = [s["start"] for s in respuesta["slots_libres"]]
        assert a_las(MARTES, 11).isoformat() in ofrecidos, "se ofrecen las horas más cercanas a la pedida"

        # Lo que vino en el rechazo ya cuenta como ofrecido.
        assert (await _agendar(conv, ofrecidos[0]))["ok"] is True
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_lo_ofrecido_hace_mas_de_30_minutos_ya_no_vale(monkeypatch):
    from app.db.session import db_session
    from app.models.offered_slot import OfferedSlot

    fijar_agenda(monkeypatch)
    conv, contacto = await persona()
    try:
        start = (await _consultar(conv, "12:00"))["slots_libres"][0]["start"]
        async with db_session() as db:
            await db.execute(
                update(OfferedSlot)
                .where(OfferedSlot.conversation_id == conv)
                .values(offered_at=OfferedSlot.offered_at - timedelta(minutes=31))
            )
            await db.commit()

        assert (await _agendar(conv, start))["codigo"] == "no_ofrecido"
        assert await citas_de(contacto) == []
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_lo_ofrecido_a_otra_conversacion_no_vale(monkeypatch):
    fijar_agenda(monkeypatch)
    conv_a, contacto_a = await persona()
    conv_b, contacto_b = await persona(ConversationCanal.instagram_dm)
    try:
        start = (await _consultar(conv_a, "15:00"))["slots_libres"][0]["start"]
        assert (await _agendar(conv_b, start))["codigo"] == "no_ofrecido"
        assert await citas_de(contacto_b) == []
    finally:
        await limpiar([contacto_a, contacto_b])


@pytestmark_db
async def test_el_mismo_instante_con_otro_desfase_si_vale(monkeypatch):
    """Se compara el instante, no el texto: las 10 de Cancún son las 15 UTC."""
    fijar_agenda(monkeypatch)
    conv, contacto = await persona()
    try:
        await _consultar(conv, "10:00")
        assert (await _agendar(conv, "2026-09-29T15:00:00+00:00"))["ok"] is True
        # Un minuto de diferencia ya no es lo ofrecido.
        conv2, contacto2 = await persona()
        await _consultar(conv2, "11:00")
        assert (await _agendar(conv2, "2026-09-29T11:01:00-05:00"))["codigo"] == "no_ofrecido"
        await limpiar([contacto2])
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_la_cita_queda_ligada_a_la_persona_y_dura_lo_que_su_servicio(monkeypatch):
    fijar_agenda(monkeypatch)
    conv, contacto = await persona(ConversationCanal.messenger)
    try:
        oferta = json.loads(
            await cal.consultar_disponibilidad(
                {"fecha": MARTES.isoformat(), "servicio": "limpieza", "hora": "12:00"}, ctx(conv)
            )
        )
        assert (oferta["servicio"], oferta["duracion_min"]) == ("Limpieza dental", 45)
        start = next(s["start"] for s in oferta["slots_libres"] if s["label"] == "12:00")

        respuesta = await _agendar(conv, start, servicio="una limpieza")

        assert respuesta["ok"] is True and respuesta["servicio"] == "Limpieza dental"
        assert respuesta["label"] == "mañana a las 12:00"
        [cita] = await citas_de(contacto)
        assert cita.conversation_id == conv
        assert cita.canal == ConversationCanal.messenger
        assert cita.creada_por == "agente" and cita.estado == "agendada"
        assert cita.ends_at - cita.starts_at == timedelta(minutes=45)
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_sin_conversacion_no_se_agenda(monkeypatch):
    fijar_agenda(monkeypatch)
    respuesta = json.loads(
        await cal.agendar_cita({"start": a_las(MARTES, 10).isoformat(), "servicio": "valoración"}, {})
    )
    assert "conversación" in respuesta["error"]
