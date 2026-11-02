"""Mover una cita es todo o nada (Fase 9, US-22 AC2).

`reagendar_cita` cancela la cita vieja y crea la nueva en UNA transacción: la
nueva queda `reagendada` y la vieja `cancelada`, que suelta su hueco. Si el
hueco nuevo no entra (otra cita llegó antes), el rollback deja la vieja tal
cual: nadie se queda sin cita por intentar moverla. Y solo se mueve una cita
de la persona de la conversación, nunca la de otro contacto.
"""
from __future__ import annotations

import json

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
    """Consulta y agenda a esa hora, como lo haría Sofía."""
    await cal.consultar_disponibilidad({"fecha": MARTES.isoformat(), "hora": f"{hora:02d}:00"}, ctx(conv))
    respuesta = json.loads(
        await cal.agendar_cita(
            {"start": a_las(MARTES, hora).isoformat(), "servicio": "valoración"}, ctx(conv)
        )
    )
    assert respuesta["ok"] is True, respuesta
    return respuesta


async def _ofrecer(conv, hora: int) -> None:
    await cal.consultar_disponibilidad({"fecha": MARTES.isoformat(), "hora": f"{hora:02d}:00"}, ctx(conv))


@pytestmark_db
async def test_mover_la_cita_la_cambia_de_hueco_y_suelta_el_viejo(monkeypatch):
    fijar_agenda(monkeypatch)
    conv, contacto = await persona()
    try:
        await _agendada(conv, 10)
        await _ofrecer(conv, 12)

        respuesta = json.loads(
            await cal.reagendar_cita({"start": a_las(MARTES, 12).isoformat()}, ctx(conv))
        )

        assert respuesta["ok"] is True
        assert respuesta["antes"] == "mañana a las 10:00"
        assert respuesta["label"] == "mañana a las 12:00"
        vieja, nueva = await citas_de(contacto)
        assert (vieja.estado, nueva.estado) == ("cancelada", "reagendada")
        assert nueva.servicio == vieja.servicio == "Cita de valoración"
        assert nueva.conversation_id == conv
        inicios = [h.inicio for h in await agenda.huecos(MARTES, MARTES, 30, cfg=HORARIO, ahora=LUNES_9)]
        assert a_las(MARTES, 10) in inicios, "el hueco viejo vuelve a estar libre"
        assert a_las(MARTES, 12) not in inicios
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_si_el_hueco_nuevo_se_ocupa_la_cita_vieja_sigue_en_pie(monkeypatch):
    """La prueba de la transacción: sin la validación previa, la nueva cita
    choca en la base de datos y el rollback tiene que dejar la vieja activa."""
    fijar_agenda(monkeypatch)
    conv_a, contacto_a = await persona()
    conv_b, contacto_b = await persona()
    try:
        await _agendada(conv_a, 10)
        await _agendada(conv_b, 11)

        async def _sin_validar(*_a, **_k):
            return None

        monkeypatch.setattr(agenda, "_validar_para_agente", _sin_validar)
        respuesta = json.loads(
            await cal.reagendar_cita({"start": a_las(MARTES, 11).isoformat()}, ctx(conv_a))
        )

        assert respuesta["codigo"] == "ocupado"
        assert "se acaba de ocupar" in respuesta["error"]
        [cita_a] = await citas_de(contacto_a)
        assert cita_a.estado == "agendada" and cita_a.starts_at == a_las(MARTES, 10)
        assert [c.estado for c in await citas_de(contacto_b)] == ["agendada"]
    finally:
        await limpiar([contacto_a, contacto_b])


@pytestmark_db
async def test_mover_a_un_horario_no_ofrecido_no_toca_nada(monkeypatch):
    fijar_agenda(monkeypatch)
    conv, contacto = await persona()
    try:
        await _agendada(conv, 10)
        respuesta = json.loads(
            await cal.reagendar_cita({"start": a_las(MARTES, 16).isoformat()}, ctx(conv))
        )
        assert respuesta["codigo"] == "no_ofrecido"
        assert respuesta["slots_libres"], "vuelve con horarios para ofrecer"
        assert [c.estado for c in await citas_de(contacto)] == ["agendada"]
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_no_se_puede_mover_la_cita_de_otra_persona(monkeypatch):
    fijar_agenda(monkeypatch)
    conv_a, contacto_a = await persona()
    conv_b, contacto_b = await persona()
    try:
        cita_a = await _agendada(conv_a, 10)
        await _ofrecer(conv_b, 12)
        respuesta = json.loads(
            await cal.reagendar_cita(
                {"start": a_las(MARTES, 12).isoformat(), "cita_id": cita_a["cita_id"]}, ctx(conv_b)
            )
        )
        assert "no tiene ninguna cita" in respuesta["error"]
        assert [c.estado for c in await citas_de(contacto_a)] == ["agendada"]
    finally:
        await limpiar([contacto_a, contacto_b])
