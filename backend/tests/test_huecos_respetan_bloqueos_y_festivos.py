"""Los huecos salen del horario menos lo que ya está ocupado (Fase 9, US-21 AC2 y AC3).

    huecos = horario − citas activas − bloqueos (− lo ocupado en Google)

Días cerrados y festivos no dan nada; un bloqueo (la comida, unas vacaciones)
quita sus horas; una cita quita su hueco y una cancelada no. Lo que se ofrece
se reparte por el día (a quien pedía "por la tarde" no le servían ocho
horarios de primera hora) o se acerca a la hora que pidió. Si el día está
lleno, se ofrecen los siguientes días con hueco.

Y un bloqueo también cuenta para la voz: durante unas vacaciones no se pasa la
llamada a una recepción vacía, se toma el recado.
"""
from __future__ import annotations

import json
from datetime import UTC, time, timedelta

from app.agents.tools import calendar as cal
from app.agents.tools.schedule_config import build_config
from app.services import agenda
from app.services.agenda import Hueco, huecos_del_dia, repartir
from tests._agenda import (
    HORARIO,
    LUNES_9,
    MARTES,
    a_las,
    bloquear,
    ctx,
    fijar_agenda,
    limpiar,
    persona,
)
from tests.test_voice_gating import pytestmark_db


def _horas(huecos: list[Hueco]) -> list[str]:
    return [h.inicio.strftime("%H:%M") for h in huecos]


# ── Sin base de datos ───────────────────────────────────────────────────────


def test_perfil_dental_ultima_cita_a_las_16_30_y_dos_horas_de_antelacion():
    """US-21 AC3: L-V de 8 a 17, citas de 30 minutos, antelación de 2 horas."""
    dental = build_config("America/Cancun", "1,2,3,4,5", "08:00-17:00", "", "30", "120")

    hoy = _horas(huecos_del_dia(LUNES_9.date(), 30, [], dental, LUNES_9))
    manana = _horas(huecos_del_dia(MARTES, 30, [], dental, LUNES_9))

    assert hoy[0] == "11:00", "a las 9:00, con dos horas de antelación"
    assert manana[0] == "08:00" and manana[-1] == "16:30"
    assert len(manana) == 18


def test_un_servicio_largo_no_se_ofrece_si_no_cabe_antes_de_cerrar():
    implante = _horas(huecos_del_dia(MARTES, 120, [], HORARIO, LUNES_9))
    assert implante[-1] == "15:00"


def test_fin_de_semana_y_festivos_no_dan_huecos():
    con_festivo = build_config("America/Cancun", "1,2,3,4,5", "08:00-17:00", "2026-09-29", "30", "0")
    sabado = LUNES_9.date() + timedelta(days=5)
    assert huecos_del_dia(sabado, 30, [], HORARIO, LUNES_9) == []
    assert huecos_del_dia(MARTES, 30, [], con_festivo, LUNES_9) == []


def test_lo_ocupado_se_descuenta_aunque_llegue_en_otra_zona():
    """Una cita de 10:00 a 11:00 en Cancún, guardada en UTC."""
    ocupado = [(a_las(MARTES, 10).astimezone(UTC), a_las(MARTES, 11).astimezone(UTC))]
    horas = _horas(huecos_del_dia(MARTES, 30, ocupado, HORARIO, LUNES_9))
    assert "09:30" in horas and "11:00" in horas
    assert "10:00" not in horas and "10:30" not in horas


def test_los_horarios_se_reparten_por_el_dia():
    del_dia = huecos_del_dia(MARTES, 30, [], HORARIO, LUNES_9)
    elegidos = _horas(repartir(del_dia, 8))
    assert len(elegidos) == 8
    assert elegidos[0] == "08:00" and elegidos[-1] == "16:30", "de punta a punta, no solo la mañana"


def test_con_hora_preferida_se_ofrecen_las_mas_cercanas():
    del_dia = huecos_del_dia(MARTES, 30, [], HORARIO, LUNES_9)
    assert _horas(repartir(del_dia, 3, time(16, 0))) == ["15:30", "16:00", "16:30"]


def test_varios_dias_reparten_el_cupo():
    dos_dias = huecos_del_dia(MARTES, 30, [], HORARIO, LUNES_9) + huecos_del_dia(
        MARTES + timedelta(days=1), 30, [], HORARIO, LUNES_9
    )
    elegidos = repartir(dos_dias, 6)
    assert [h.inicio.date() for h in elegidos].count(MARTES) == 3


# ── Con base de datos ───────────────────────────────────────────────────────


@pytestmark_db
async def test_un_bloqueo_quita_sus_horas():
    bloqueo = await bloquear(a_las(MARTES, 13), a_las(MARTES, 14), "comida")
    try:
        horas = _horas(await agenda.huecos(MARTES, MARTES, 30, cfg=HORARIO, ahora=LUNES_9))
        assert "12:30" in horas and "14:00" in horas
        assert "13:00" not in horas and "13:30" not in horas
        # Una limpieza de 45 minutos a las 12:30 se comería el principio de la comida.
        limpieza = _horas(await agenda.huecos(MARTES, MARTES, 45, cfg=HORARIO, ahora=LUNES_9))
        assert "12:00" in limpieza and "12:30" not in limpieza
    finally:
        await limpiar([], [bloqueo])


@pytestmark_db
async def test_dia_bloqueado_ofrece_los_siguientes_dias(monkeypatch):
    fijar_agenda(monkeypatch)
    conv, contacto = await persona()
    bloqueo = await bloquear(a_las(MARTES, 0), a_las(MARTES + timedelta(days=1), 0), "vacaciones")
    try:
        oferta = json.loads(
            await cal.consultar_disponibilidad({"fecha": MARTES.isoformat()}, ctx(conv))
        )
        assert oferta["slots_libres"] == []
        assert oferta["motivo"] == "Ese día ya no quedan horarios libres."
        dias = {s["start"][:10] for s in oferta["otros_dias"]}
        assert dias == {"2026-09-30", "2026-10-01"}, "los dos siguientes días con hueco"
        assert oferta["otros_dias"][0]["label"].startswith("el miércoles 30 a las ")

        # Lo que viene en `otros_dias` también se puede agendar.
        cita = json.loads(
            await cal.agendar_cita(
                {"start": oferta["otros_dias"][0]["start"], "servicio": "valoración"}, ctx(conv)
            )
        )
        assert cita["ok"] is True
    finally:
        await limpiar([contacto], [bloqueo])


@pytestmark_db
async def test_dia_cerrado_dice_por_que_y_ofrece_los_siguientes(monkeypatch):
    fijar_agenda(monkeypatch)
    conv, contacto = await persona()
    try:
        sabado = (LUNES_9.date() + timedelta(days=5)).isoformat()
        oferta = json.loads(await cal.consultar_disponibilidad({"fecha": sabado}, ctx(conv)))
        assert oferta["cerrado"] is True and "sábado" in oferta["motivo"]
        assert {s["start"][:10] for s in oferta["otros_dias"]} == {"2026-10-05", "2026-10-06"}
    finally:
        await limpiar([contacto])


@pytestmark_db
def test_durante_un_bloqueo_la_voz_toma_el_recado(monkeypatch):
    """En horario pero con la agenda bloqueada (vacaciones): no se pasa la llamada."""
    import asyncio
    import uuid

    from tests.test_voice_gating import _cleanup
    from tests.test_voz_toma_recado_fuera_de_horario import (
        MARTES_10,
        _derivar,
        _entorno,
        _llamada,
    )

    _entorno(monkeypatch, recepcion="+529981112233", ahora=MARTES_10)
    bloqueo = asyncio.run(bloquear(MARTES_10 - timedelta(hours=2), MARTES_10 + timedelta(hours=6)))
    conv_id, contact_id = asyncio.run(_llamada(f"call_{uuid.uuid4().hex[:10]}"))
    try:
        voz: dict = {"transfer_number": None}
        respuesta = _derivar(conv_id, voz)
        assert respuesta["accion"] == "recado"
        assert "bloqueada" in respuesta["por_que"]
        assert voz["transfer_number"] is None
    finally:
        asyncio.run(_cleanup([contact_id]))
        asyncio.run(limpiar([], [bloqueo]))
