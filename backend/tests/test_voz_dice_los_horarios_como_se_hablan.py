"""Por teléfono, Sofía dice las horas de la agenda como se hablan (Fase 8.3).

T-8.3.1: en voz, `consultar_disponibilidad` entrega cada hueco con la etiqueta
ya dicha ("mañana a las cuatro y media de la tarde"), calculada con la zona y
la hora del negocio. El modelo no tiene reloj: con una hora ISO delante
confundía hoy con mañana y lo decía en voz alta. `start` sigue siendo la hora
exacta, con el desfase sacado de la zona, que es la que se agenda.

En los canales de texto la etiqueta no cambia ("16:30").
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.agents.tools import calendar as cal
from app.agents.tools.schedule_config import build_config

HORARIO = build_config("America/Cancun", "1,2,3,4,5", "08:00-17:00", "", "30", "0")
LUNES_18 = datetime(2026, 9, 28, 18, 0, tzinfo=HORARIO.tz)


def _hora(h: int, m: int = 0) -> datetime:
    return datetime(2026, 9, 29, h, m, tzinfo=HORARIO.tz)


@pytest.mark.parametrize(
    ("momento", "dicho"),
    [
        (_hora(16, 30), "las cuatro y media de la tarde"),
        (_hora(13), "la una de la tarde"),
        (_hora(12), "las doce del día"),
        (_hora(9, 15), "las nueve y cuarto de la mañana"),
        (_hora(10, 45), "las diez y 45 de la mañana"),
        (_hora(19), "las siete de la noche"),
    ],
)
def test_la_hora_se_dice_como_en_la_calle(momento, dicho):
    assert cal._spoken_time(momento) == dicho


def test_hoy_y_manana_son_relativos_al_dia_del_negocio():
    assert cal._spoken_label(LUNES_18.replace(hour=19), LUNES_18) == "hoy a las siete de la noche"
    assert cal._spoken_label(_hora(10), LUNES_18) == "mañana a las diez de la mañana"


def test_mas_alla_de_manana_se_dice_el_dia_y_el_mes_solo_si_cambia():
    assert cal._spoken_label(datetime(2026, 9, 30, 9, tzinfo=HORARIO.tz), LUNES_18) == (
        "el miércoles 30 a las nueve de la mañana"
    )
    assert cal._spoken_label(datetime(2026, 10, 5, 16, tzinfo=HORARIO.tz), LUNES_18) == (
        "el lunes 5 de octubre a las cuatro de la tarde"
    )


def _agenda_libre(monkeypatch) -> None:
    """Agenda sin nada ocupado, sin base de datos: aquí solo importa cómo se dicen."""
    from app.services import agenda

    async def _horario():
        return HORARIO

    async def _libre(desde, hasta, duracion_min, *, cfg, ahora):
        return agenda.huecos_del_dia(desde, duracion_min, [], cfg, ahora)

    async def _sin_catalogo():
        return []

    monkeypatch.setattr(cal, "get_schedule_config", _horario)
    monkeypatch.setattr(cal, "now_in", lambda _tz: LUNES_18)
    monkeypatch.setattr(agenda, "huecos", _libre)
    monkeypatch.setattr(agenda, "servicios", _sin_catalogo)


async def test_en_voz_los_huecos_llegan_dichos_y_con_la_hora_exacta(monkeypatch):
    _agenda_libre(monkeypatch)

    crudo = await cal.consultar_disponibilidad(
        {"fecha": "2026-09-29"}, {"channel": "retell_voice"}
    )
    primero = json.loads(crudo)["slots_libres"][0]

    assert primero["label"] == "mañana a las ocho de la mañana"
    assert primero["start"] == "2026-09-29T08:00:00-05:00", "el desfase sale de la zona de Cancún"
    assert "mañana" in crudo, "la etiqueta se lee en voz alta: la eñe sin escapar"


async def test_en_texto_la_etiqueta_sigue_siendo_la_hora(monkeypatch):
    _agenda_libre(monkeypatch)

    out = json.loads(
        await cal.consultar_disponibilidad({"fecha": "2026-09-29"}, {"channel": "whatsapp"})
    )

    assert out["slots_libres"][0]["label"] == "08:00"
