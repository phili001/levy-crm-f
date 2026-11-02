"""Agenda: nada en el pasado, horario configurable y días cerrados.

Tres fallos que cubrían estos tests:
  - `agendar_cita` solo validaba que el fin fuera posterior al inicio, así que
    "apúntame el 3 de enero" creaba el evento en el AÑO PASADO.
  - `_slots_for_day` generaba el día entero sin descartar lo ya pasado: a las
    17:00 seguía ofreciendo las 9:30 de esa misma mañana.
  - el horario estaba fijo en código (Europe/Madrid, 9-18) sin comprobar fin de
    semana ni festivos: ofrecía sábado y domingo igual que un martes.

Desde la Fase 9 los huecos los calcula la agenda propia
(`services/agenda.huecos_del_dia`) y ya no hace falta Google para ofrecerlos.
"""
import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.agents.tools.calendar import agendar_cita
from app.agents.tools.schedule_config import build_config
from app.services.agenda import Servicio, huecos_del_dia, resolver_servicio
from tests.test_voice_gating import pytestmark_db

MADRID = ZoneInfo("Europe/Madrid")


def _cfg(**kw):
    base = dict(
        tz_name="Europe/Madrid",
        work_days="1,2,3,4,5",
        work_hours="09:00-18:00",
        holidays="",
        slot_step="30",
        min_notice="0",
    )
    base.update(kw)
    return build_config(**base)


# ── Configuración de horario ────────────────────────────────────────────────


def test_defaults_son_los_de_siempre():
    """Al desplegar, ninguna instalación cambia de horario."""
    cfg = _cfg()
    assert cfg.tz_name == "Europe/Madrid"
    assert cfg.work_hours == [(time(9, 0), time(18, 0))]
    assert cfg.work_days == {1, 2, 3, 4, 5}
    assert cfg.slot_step_min == 30


def test_horario_partido_y_otra_zona():
    """Un comercio con jornada partida que además abre sábados."""
    cfg = _cfg(
        tz_name="America/Mexico_City",
        work_hours="09:00-14:00,16:00-20:00",
        work_days="1,2,3,4,5,6",
    )
    assert cfg.tz_name == "America/Mexico_City"
    assert cfg.work_hours == [(time(9, 0), time(14, 0)), (time(16, 0), time(20, 0))]
    assert 6 in cfg.work_days


def test_zona_horaria_invalida_cae_al_default_sin_romper():
    cfg = _cfg(tz_name="Marte/Olympus")
    assert cfg.tz_name == "America/Cancun"


def test_fin_de_semana_y_festivos_estan_cerrados():
    cfg = _cfg(holidays="2026-12-25")
    sabado = datetime(2026, 8, 15, tzinfo=MADRID).date()   # sábado
    domingo = datetime(2026, 8, 16, tzinfo=MADRID).date()  # domingo
    martes = datetime(2026, 8, 11, tzinfo=MADRID).date()
    navidad = datetime(2026, 12, 25, tzinfo=MADRID).date()

    assert cfg.is_working_day(martes)
    assert not cfg.is_working_day(sabado)
    assert not cfg.is_working_day(domingo)
    assert not cfg.is_working_day(navidad)
    assert "sábado" in cfg.closed_reason(sabado)
    assert "festivo" in cfg.closed_reason(navidad)
    assert cfg.closed_reason(martes) is None


def test_quien_abre_sabados_puede_configurarlo():
    cfg = _cfg(work_days="1,2,3,4,5,6")
    assert cfg.is_working_day(datetime(2026, 8, 15, tzinfo=MADRID).date())


# ── Huecos del día ──────────────────────────────────────────────────────────


def _horas(dia: datetime, cfg, ahora: datetime, ocupados=()) -> list[str]:
    return [h.inicio.strftime("%H:%M") for h in huecos_del_dia(dia.date(), 30, list(ocupados), cfg, ahora)]


def test_no_se_ofrecen_horas_que_ya_han_pasado():
    """A las 17:00 no se puede ofrecer las 9:30 de esa misma mañana."""
    cfg = _cfg()
    dia = datetime(2026, 8, 11, tzinfo=MADRID)  # martes
    ahora = dia.replace(hour=17, minute=0)
    huecos = huecos_del_dia(dia.date(), 30, [], cfg, ahora)

    assert huecos, "debería quedar algún hueco de 17:00 a 18:00"
    assert all(h.inicio >= ahora for h in huecos)
    assert all(h >= "17:00" for h in _horas(dia, cfg, ahora))


def test_dia_futuro_ofrece_la_jornada_entera():
    cfg = _cfg()
    dia = datetime(2026, 9, 15, tzinfo=MADRID)
    ahora = datetime(2026, 8, 11, 17, 0, tzinfo=MADRID)
    horas = _horas(dia, cfg, ahora)
    assert horas[0] == "09:00"
    assert horas[-1] == "17:30"


def test_la_antelacion_minima_se_respeta():
    cfg = _cfg(min_notice="120")  # 2 horas
    dia = datetime(2026, 8, 11, tzinfo=MADRID)
    ahora = dia.replace(hour=10, minute=0)
    assert _horas(dia, cfg, ahora)[0] == "12:00"


def test_los_ocupados_se_descuentan():
    cfg = _cfg()
    dia = datetime(2026, 9, 15, tzinfo=MADRID)
    ahora = datetime(2026, 8, 11, tzinfo=MADRID)
    ocupado = [(dia.replace(hour=10), dia.replace(hour=11))]
    labels = _horas(dia, cfg, ahora, ocupado)
    assert "09:00" in labels
    assert "10:00" not in labels
    assert "10:30" not in labels
    assert "11:00" in labels


def test_horario_partido_no_ofrece_la_hora_de_comer():
    cfg = _cfg(work_hours="09:00-14:00,16:00-20:00")
    dia = datetime(2026, 9, 15, tzinfo=MADRID)
    ahora = datetime(2026, 8, 11, tzinfo=MADRID)
    labels = _horas(dia, cfg, ahora)
    assert "13:30" in labels
    assert "14:30" not in labels
    assert "15:30" not in labels
    assert "16:00" in labels


def test_el_servicio_dice_cuanto_dura_la_cita():
    """El fin ya no lo manda el modelo: sale de la duración del servicio."""
    catalogo = [Servicio("Limpieza dental", 45), Servicio("Implante dental", 120)]
    assert resolver_servicio("una limpieza", catalogo).duracion_min == 45
    assert resolver_servicio("Implante", catalogo).duracion_min == 120
    # Lo que no está en el catálogo se guarda con su nombre y 30 minutos.
    assert resolver_servicio("revisión dental", catalogo) == Servicio("revisión dental", 30)


# ── agendar_cita ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_se_puede_agendar_en_el_pasado():
    """El caso literal de la auditoría: "apúntame el 3 de enero"."""
    ayer = datetime.now(MADRID) - timedelta(days=1)
    out = json.loads(await agendar_cita({"start": ayer.isoformat(), "servicio": "Corte"}, {}))
    assert "error" in out
    assert "pasado" in out["error"]


@pytest.mark.asyncio
async def test_no_se_agenda_un_dia_cerrado(monkeypatch):
    """Con el horario por defecto (L-V), un sábado se rechaza con motivo."""
    from app.agents.tools import calendar as cal

    async def fake_cfg():
        return _cfg()

    monkeypatch.setattr(cal, "get_schedule_config", fake_cfg)

    # Próximo sábado a las 11:00.
    ahora = datetime.now(MADRID)
    dias = (5 - ahora.weekday()) % 7 or 7
    sabado = (ahora + timedelta(days=dias)).replace(
        hour=11, minute=0, second=0, microsecond=0
    )
    out = json.loads(await cal.agendar_cita({"start": sabado.isoformat(), "servicio": "Corte"}, {}))
    assert "error" in out
    assert "sábado" in out["error"]


@pytestmark_db
async def test_sin_google_la_agenda_propia_ofrece_huecos(monkeypatch):
    """Antes, sin Google conectado no había agenda: "no propongas horas". Ahora
    los huecos salen de la agenda del CRM y Google es solo una copia (US-21)."""
    from app.agents.tools import calendar as cal
    from tests._agenda import MARTES, fijar_agenda

    fijar_agenda(monkeypatch)  # la copia en Google, apagada
    out = json.loads(await cal.consultar_disponibilidad({"fecha": MARTES.isoformat()}, {}))
    assert "error" not in out
    assert out["slots_libres"] and out["total_libres"] > 0


@pytest.mark.asyncio
async def test_consultar_disponibilidad_de_una_fecha_pasada(monkeypatch):
    from app.agents.tools import calendar as cal

    async def fake_cfg():
        return _cfg()

    monkeypatch.setattr(cal, "get_schedule_config", fake_cfg)
    ayer = (datetime.now(MADRID) - timedelta(days=1)).strftime("%Y-%m-%d")
    out = json.loads(await cal.consultar_disponibilidad({"fecha": ayer}, {}))
    assert "error" in out
    assert "pasado" in out["error"]
