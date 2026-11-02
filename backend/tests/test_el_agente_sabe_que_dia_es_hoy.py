"""El agente sabe qué día y qué hora es en el negocio.

Antes no lo sabía: ante "¿tienen hueco mañana en la tarde?" el modelo se
inventaba la fecha y la agenda contestaba "esa fecha ya pasó". Salió en la
primera prueba en vivo de la voz (2026-09-23), pero afectaba a todos los
canales: el prompt de texto tampoco llevaba la fecha.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services import runtime_config as rc


def _config_de(tz: str):
    from app.agents.tools.schedule_config import ScheduleConfig

    return ScheduleConfig(tz=ZoneInfo(tz), tz_name=tz, work_days={1, 2, 3, 4, 5}, work_hours=[])


def _fijar(monkeypatch, cuando: datetime, tz: str = "America/Cancun") -> None:
    from app.agents.tools import schedule_config

    async def config():
        return _config_de(tz)

    monkeypatch.setattr(schedule_config, "get_schedule_config", config)
    monkeypatch.setattr(schedule_config, "now_in", lambda _tz: cuando)


def test_el_prompt_dice_el_dia_la_hora_y_la_zona(monkeypatch):
    _fijar(monkeypatch, datetime(2026, 9, 24, 10, 15, tzinfo=ZoneInfo("America/Cancun")))
    bloque = asyncio.run(rc.render_date_block())
    assert "Hoy es jueves 24 de septiembre de 2026" in bloque
    assert "son las 10:15" in bloque
    assert "America/Cancun" in bloque
    assert "AAAA-MM-DD" in bloque, "las herramientas esperan la fecha ya calculada"


def test_la_fecha_va_al_final_para_no_romper_la_cache_del_prompt(monkeypatch):
    _fijar(monkeypatch, datetime(2026, 12, 31, 23, 59, tzinfo=ZoneInfo("America/Cancun")))

    async def sin_bloque(key, default, db=None):
        return default

    async def sin_reglas():
        return ""

    monkeypatch.setattr("app.services.app_settings.get_app_setting", sin_bloque)
    monkeypatch.setattr("app.services.learned_rules.render_learned_rules_block", sin_reglas)
    prompt = asyncio.run(rc.assemble_system_prompt("Eres Sofía."))
    fijo, _, fecha = prompt.partition("\n\n[FECHA Y HORA ACTUAL]")
    assert fijo == rc.SECURITY_GUARD + "Eres Sofía.", "lo que no cambia va delante, intacto"
    assert "jueves 31 de diciembre de 2026" in fecha


def test_si_no_se_puede_leer_la_hora_el_turno_sigue(monkeypatch):
    async def explota():
        raise RuntimeError("bd caída")

    async def sin_bloque(key, default, db=None):
        return default

    async def sin_reglas():
        return ""

    monkeypatch.setattr(rc, "render_date_block", explota)
    monkeypatch.setattr("app.services.app_settings.get_app_setting", sin_bloque)
    monkeypatch.setattr("app.services.learned_rules.render_learned_rules_block", sin_reglas)
    assert asyncio.run(rc.assemble_system_prompt("Eres Sofía.")) == rc.SECURITY_GUARD + "Eres Sofía."
