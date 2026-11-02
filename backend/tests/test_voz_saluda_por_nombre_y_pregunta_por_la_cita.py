"""Al descolgar, Sofía saluda por su nombre a quien ya conoce y le pregunta por su cita.

Salió en el dry-run 2 (C-1, en la viva): el saludo era siempre el mismo, aunque
llamara un paciente con ficha y cita. La 10.1 daba por hecho que «Hola, Juan,
¿es por tu cita?» salía solo con `buscar_contacto`, pero el saludo se dice antes
de que el modelo haga nada. El aviso de grabación no se toca.
"""
from __future__ import annotations

import pytest

from app.api import voice
from app.providers.voice import retell
from tests._agenda import MARTES, fijar_agenda, limpiar, persona
from tests.test_buscar_contacto_trae_sus_citas_y_notas import _agendar
from tests.test_voice_gating import pytestmark_db

BASE = (
    "Clínica Sonrisa Perfecta, te atiende Sofía, asistente virtual. Esta llamada se "
    "graba para mejorar la atención. ¿En qué te puedo ayudar?"
)


def _saludo_base(monkeypatch) -> None:
    async def _canal(_tipo):
        return None

    async def _ajuste(clave, default, db=None):
        return BASE

    monkeypatch.setattr(voice, "get_channel_config", _canal)
    monkeypatch.setattr(voice, "get_app_setting", _ajuste)


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        ("Carlos Dominguez", "Carlos"),
        ("maría josé", "María"),
        ("+529980000007", None),
        ("@juanp", None),
        ("ig:12345", None),
        ("", None),
        (None, None),
    ],
)
def test_solo_se_dice_en_voz_alta_un_nombre_de_verdad(nombre, esperado):
    assert voice._nombre_de_pila(nombre) == esperado


def test_con_cita_la_pregunta_final_pasa_a_ser_la_de_la_cita():
    saludo = voice._con_nombre(BASE, "Carlos", "mañana a las cuatro de la tarde")
    assert saludo.startswith("Hola, Carlos. Clínica Sonrisa Perfecta")
    assert "Esta llamada se graba" in saludo
    assert saludo.endswith("¿Llamas por tu cita de mañana a las cuatro de la tarde?")
    assert "¿En qué te puedo ayudar?" not in saludo


def test_una_cita_de_otro_dia_se_dice_con_del():
    saludo = voice._con_nombre(BASE, "Carlos", "el miércoles 30 a las ocho de la mañana")
    assert saludo.endswith("¿Llamas por tu cita del miércoles 30 a las ocho de la mañana?")


def test_sin_cita_se_queda_la_pregunta_de_siempre():
    assert voice._con_nombre(BASE, "Carlos", None) == "Hola, Carlos. " + BASE


def test_un_saludo_que_ya_empieza_con_hola_no_repite_el_hola():
    saludo = voice._con_nombre(
        "Hola, gracias por llamar a la clínica. Esta llamada se graba.", "Ana", None
    )
    assert saludo == "Hola, Ana, gracias por llamar a la clínica. Esta llamada se graba."


async def test_sin_el_numero_de_quien_llama_el_saludo_de_siempre(monkeypatch):
    _saludo_base(monkeypatch)
    assert await voice._saludo_de_la_llamada("call_sin_numero_apuntado") == BASE


@pytestmark_db
async def test_a_quien_ya_conocemos_lo_saluda_por_su_nombre_y_le_pregunta_por_su_cita(
    monkeypatch,
):
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.contact import Contact

    _saludo_base(monkeypatch)
    fijar_agenda(monkeypatch)
    conv, contacto = await persona(nombre="Carlos Dominguez")
    try:
        await _agendar(conv, MARTES, 10)
        async with db_session() as db:
            telefono = (
                await db.execute(select(Contact.telefono).where(Contact.id == contacto))
            ).scalar_one()
        # Retell manda el móvil mexicano con el 1 de antes: la ficha lo guarda sin él.
        retell._recordar_numero("call_c1_conocido", "+521" + telefono.removeprefix("+52"))

        saludo = await voice._saludo_de_la_llamada("call_c1_conocido")

        assert saludo.startswith("Hola, Carlos. Clínica Sonrisa Perfecta")
        assert "Esta llamada se graba" in saludo
        assert "¿Llamas por tu cita de mañana" in saludo
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_sin_cita_lo_saluda_por_su_nombre_y_pregunta_lo_de_siempre(monkeypatch):
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.contact import Contact

    _saludo_base(monkeypatch)
    fijar_agenda(monkeypatch)
    _conv, contacto = await persona(nombre="Ana López")
    try:
        async with db_session() as db:
            telefono = (
                await db.execute(select(Contact.telefono).where(Contact.id == contacto))
            ).scalar_one()
        retell._recordar_numero("call_c1_sin_cita", telefono)
        assert await voice._saludo_de_la_llamada("call_c1_sin_cita") == "Hola, Ana. " + BASE
    finally:
        await limpiar([contacto])
