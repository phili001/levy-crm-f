"""Un hueco `{{PENDIENTE}}` sin rellenar no llega al modelo, así que no llega al paciente.

Salió en el dry-run 2 (B-1, crm2): con el teléfono de la clínica sin rellenar,
Sofía contestó «el teléfono de la clínica es {{PENDIENTE: teléfono con lada, ej.
998 123 4567}}». El checklist del Inicio avisa de los huecos, pero nada impedía
abrir los canales antes de rellenarlos.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import runtime_config as rc


@pytest.fixture(autouse=True)
def _sin_fecha_ni_reglas(monkeypatch):
    async def vacio():
        return ""

    monkeypatch.setattr(rc, "render_date_block", vacio)
    monkeypatch.setattr("app.services.learned_rules.render_learned_rules_block", vacio)


def test_un_hueco_sin_rellenar_no_llega_al_modelo(monkeypatch):
    async def con_huecos(key, default, db=None):
        return (
            "Teléfono de la clínica: {{PENDIENTE: teléfono con lada, ej. 998 123 4567}}\n"
            "Dirección: {{ PENDIENTE: calle y número,\ncolonia, Cancún}}\n"
        )

    monkeypatch.setattr("app.services.app_settings.get_app_setting", con_huecos)
    prompt = asyncio.run(rc.assemble_system_prompt("Eres Sofía. Horario: {{PENDIENTE: días}}"))

    assert "{{" not in prompt and "PENDIENTE" not in prompt
    assert prompt.count("dato aún sin configurar") == 3
    assert "Teléfono de la clínica: (dato aún sin configurar" in prompt
    assert "Eres Sofía." in prompt


def test_sin_huecos_el_prompt_no_cambia(monkeypatch):
    async def sin_huecos(key, default, db=None):
        return "Teléfono de la clínica: 998 000 0006\n"

    monkeypatch.setattr("app.services.app_settings.get_app_setting", sin_huecos)
    prompt = asyncio.run(rc.assemble_system_prompt("Eres Sofía."))
    assert "998 000 0006" in prompt and "sin configurar" not in prompt
