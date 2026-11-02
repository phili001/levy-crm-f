"""El bloque común de Ajustes se antepone al prompt de todos los agentes.

Si esto falla, Sofía texto y Sofía voz pueden contar cosas distintas del mismo
negocio: cada una solo sabría lo que diga su propio prompt.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import runtime_config as rc


@pytest.fixture(autouse=True)
def _sin_fecha(monkeypatch):
    """La fecha del día va al final del prompt y cambia en cada turno; aquí se
    prueba el ORDEN de los bloques fijos, así que se deja fuera. Tiene sus
    propias pruebas en test_el_agente_sabe_que_dia_es_hoy.py."""

    async def vacio():
        return ""

    monkeypatch.setattr(rc, "render_date_block", vacio)


def test_sin_bloque_el_prompt_efectivo_es_guard_mas_agente(monkeypatch):
    async def sin_bloque(key, default, db=None):
        return default

    async def sin_reglas():
        return ""

    monkeypatch.setattr("app.services.app_settings.get_app_setting", sin_bloque)
    monkeypatch.setattr("app.services.learned_rules.render_learned_rules_block", sin_reglas)
    prompt = asyncio.run(rc.assemble_system_prompt("Eres Sofía."))
    assert prompt == rc.SECURITY_GUARD + "Eres Sofía."


def test_con_bloque_va_entre_la_seguridad_y_el_agente(monkeypatch):
    async def con_bloque(key, default, db=None):
        assert key == rc.SETTING_PROMPT_BLOQUE_COMUN
        return "Clínica Sonrisa Perfecta, Cancún. Limpiezas y ortodoncia."

    async def sin_reglas():
        return ""

    monkeypatch.setattr("app.services.app_settings.get_app_setting", con_bloque)
    monkeypatch.setattr("app.services.learned_rules.render_learned_rules_block", sin_reglas)
    prompt = asyncio.run(rc.assemble_system_prompt("Eres Sofía."))
    assert prompt.startswith(rc.SECURITY_GUARD)
    cuerpo = prompt[len(rc.SECURITY_GUARD):]
    assert cuerpo.startswith("[SOBRE EL NEGOCIO")
    assert "Clínica Sonrisa Perfecta" in cuerpo
    assert cuerpo.endswith("Eres Sofía.")


def test_si_falla_la_lectura_del_bloque_el_prompt_sigue_intacto(monkeypatch):
    async def explota(key, default, db=None):
        raise RuntimeError("bd caída")

    async def sin_reglas():
        return ""

    monkeypatch.setattr("app.services.app_settings.get_app_setting", explota)
    monkeypatch.setattr("app.services.learned_rules.render_learned_rules_block", sin_reglas)
    prompt = asyncio.run(rc.assemble_system_prompt("Eres Sofía."))
    assert prompt == rc.SECURITY_GUARD + "Eres Sofía."


@pytest.mark.parametrize("vacio", ["", "   ", "\n"])
def test_un_bloque_vacio_no_deja_cabecera_huerfana(monkeypatch, vacio):
    async def bloque(key, default, db=None):
        return vacio

    monkeypatch.setattr("app.services.app_settings.get_app_setting", bloque)
    assert asyncio.run(rc.render_common_block()) == ""
