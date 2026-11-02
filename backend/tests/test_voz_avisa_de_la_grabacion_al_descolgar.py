"""Al descolgar, Sofía avisa de que la llamada se graba (Fase 8.3, LFPDPPP).

T-8.3.3: el perfil `dental-mx` trae un saludo con el nombre de la clínica, la
divulgación del bot y el aviso de grabación, y lo guarda en
`app_settings["voice.greeting"]`. Nadie lo leía: la llamada saludaba con el
genérico, que no avisaba de nada. Ahora el orden es canal → perfil → genérico,
y el genérico también avisa.
"""
from __future__ import annotations

import inspect

import pytest

from app.api import voice

PERFIL = "Clínica Sonrisa Perfecta, te atiende Sofía. Esta llamada se graba."


def _instalacion(monkeypatch, *, canal: dict | None, perfil: str) -> None:
    async def _canal(_tipo):
        return canal

    async def _ajuste(clave, default, db=None):
        assert clave == "voice.greeting"
        return perfil or default

    monkeypatch.setattr(voice, "get_channel_config", _canal)
    monkeypatch.setattr(voice, "get_app_setting", _ajuste)


async def test_sin_saludo_en_el_canal_se_dice_el_del_perfil(monkeypatch):
    _instalacion(monkeypatch, canal={"api_key": "x"}, perfil=PERFIL)
    assert await voice._voice_greeting() == PERFIL


async def test_el_saludo_escrito_en_el_canal_manda(monkeypatch):
    _instalacion(monkeypatch, canal={"greeting": " Hola, soy Ana. "}, perfil=PERFIL)
    assert await voice._voice_greeting() == "Hola, soy Ana."


@pytest.mark.parametrize("canal", [None, {"greeting": "   "}])
async def test_sin_perfil_el_generico_tambien_avisa(monkeypatch, canal):
    _instalacion(monkeypatch, canal=canal, perfil="")
    saludo = await voice._voice_greeting()
    assert "se graba" in saludo and "asistente virtual" in saludo


def test_los_saludos_de_fabrica_avisan_de_la_grabacion():
    from app.scripts.profiles.dental_mx import SALUDO_VOZ
    from app.scripts.seed import VOICE_DEFAULT_GREETING

    for saludo in (voice.VOICE_TEXTS["greeting"], VOICE_DEFAULT_GREETING, SALUDO_VOZ):
        assert "se graba" in saludo, saludo
        assert "asistente virtual" in saludo, saludo


def test_el_seed_no_tapa_el_saludo_del_perfil_con_el_generico():
    """El seed escribe un saludo en el canal que no tiene. Con el genérico
    fijo, el del perfil no llegaba a sonar nunca en esa instalación."""
    from app.scripts import seed

    fuente = inspect.getsource(seed.seed_voice_agent)
    assert "SETTING_VOICE_GREETING" in fuente and "or VOICE_DEFAULT_GREETING" in fuente
