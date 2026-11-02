"""Dos citas no se encima nunca, ni aunque lleguen a la vez (Fase 9, US-21 AC1, A-1).

Lo impide la base de datos: la restricción de exclusión de `0060_appointments`
(`btree_gist`) rechaza la segunda cita activa que se cruce con otra en el mismo
recurso. Así, dos canales pidiendo el mismo hueco a la vez acaban en una cita y
en un "ese horario se acaba de ocupar" con horarios nuevos, sin carrera que
ganar en el código. Una cita cancelada libera su hueco, y una que empieza justo
cuando termina otra sí cabe.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.agents.tools import calendar as cal
from app.models.conversation import ConversationCanal
from tests._agenda import MARTES, a_las, citas_de, ctx, fijar_agenda, limpiar, persona
from tests.test_voice_gating import pytestmark_db


async def _cita(contact_id: uuid.UUID, inicio, minutos: int, estado: str = "agendada") -> None:
    from app.db.session import db_session
    from app.models.appointment import Appointment

    async with db_session() as db:
        db.add(
            Appointment(
                contact_id=contact_id,
                servicio="Prueba",
                starts_at=inicio,
                ends_at=inicio + timedelta(minutes=minutos),
                estado=estado,
            )
        )
        await db.commit()


@pytestmark_db
async def test_dos_canales_a_la_vez_por_el_mismo_hueco_solo_uno_se_lo_lleva(monkeypatch):
    fijar_agenda(monkeypatch)
    wa, contacto_wa = await persona(ConversationCanal.whatsapp)
    voz, contacto_voz = await persona(ConversationCanal.retell_voice)
    try:
        for conv in (wa, voz):
            await cal.consultar_disponibilidad(
                {"fecha": MARTES.isoformat(), "hora": "10:00"}, ctx(conv)
            )
        diez = a_las(MARTES, 10).isoformat()

        respuestas = await asyncio.gather(
            cal.agendar_cita({"start": diez, "servicio": "valoración"}, ctx(wa)),
            cal.agendar_cita({"start": diez, "servicio": "valoración"}, ctx(voz, voz=True)),
        )

        resultados = [json.loads(r) for r in respuestas]
        buenas = [r for r in resultados if r.get("ok")]
        malas = [r for r in resultados if not r.get("ok")]
        assert len(buenas) == 1, resultados
        assert len(malas) == 1 and malas[0]["codigo"] == "ocupado"
        assert malas[0]["slots_libres"], "quien pierde el hueco recibe horarios nuevos"
        assert diez not in [s["start"] for s in malas[0]["slots_libres"]]

        activas = [c for c in await citas_de(contacto_wa) + await citas_de(contacto_voz) if c.activa]
        assert len(activas) == 1
    finally:
        await limpiar([contacto_wa, contacto_voz])


@pytestmark_db
async def test_la_base_de_datos_rechaza_la_cita_encimada_aunque_se_salte_el_codigo():
    """Escribiendo directo en la tabla, sin pasar por la agenda: igual no entra."""
    _conv, contacto = await persona()
    try:
        await _cita(contacto, a_las(MARTES, 10), 45)
        with pytest.raises(IntegrityError, match="ex_appointments_sin_encimar"):
            await _cita(contacto, a_las(MARTES, 10, 30), 30)
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_una_cita_que_empieza_cuando_termina_otra_si_cabe():
    _conv, contacto = await persona()
    try:
        await _cita(contacto, a_las(MARTES, 10), 30)
        await _cita(contacto, a_las(MARTES, 10, 30), 30)
        assert len(await citas_de(contacto)) == 2
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_una_cita_cancelada_no_ocupa_su_hueco():
    _conv, contacto = await persona()
    try:
        await _cita(contacto, a_las(MARTES, 10), 30, estado="cancelada")
        await _cita(contacto, a_las(MARTES, 10), 30)
        assert [c.estado for c in await citas_de(contacto)] == ["cancelada", "agendada"]
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_el_operador_tampoco_puede_encimar_una_cita(monkeypatch):
    """El panel puede agendar fuera de horario o encima de un bloqueo, no encima de otra cita."""
    from app.services import agenda

    fijar_agenda(monkeypatch)
    _conv, contacto = await persona()
    try:
        await _cita(contacto, a_las(MARTES, 10), 30)
        with pytest.raises(agenda.AgendaError) as e:
            await agenda.agendar(
                contact_id=contacto,
                conversation_id=None,
                canal=None,
                servicio=agenda.Servicio("Limpieza dental", 45),
                inicio=a_las(MARTES, 9, 30),
                creada_por="operador",
            )
        assert e.value.codigo == "ocupado"
        assert len(await citas_de(contacto)) == 1
    finally:
        await limpiar([contacto])
