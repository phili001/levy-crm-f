"""Después de unir fichas, cada canal sigue encontrando a su paciente (Fase 10.1).

Al unir la ficha de Instagram a la de WhatsApp, la llave pasa a ser el
teléfono y el `ig:…` se guarda en `identidad_canal`. Todo lo que buscaba a la
persona por su identificador de canal tiene que mirar ahí también; si no, el
siguiente DM abre otra ficha (la duplicada que se acababa de deshacer), el chat
web deja de reconocer al visitante y la respuesta por Instagram se va a un
«+52…» que Instagram no conoce.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests._fichas import borrar, conversacion_y_ficha, entra, ficha, leer, llave_ig, telefono_mx
from tests.test_voice_gating import pytestmark_db


async def _une(destino, origen) -> None:
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.contact import Contact
    from app.services.contact_merge import fusionar_contactos

    async with db_session() as db:
        d = (await db.execute(select(Contact).where(Contact.id == destino))).scalar_one()
        o = (await db.execute(select(Contact).where(Contact.id == origen))).scalar_one()
        await fusionar_contactos(db, d, o)
        await db.commit()


@pytestmark_db
@pytest.mark.asyncio
async def test_el_siguiente_dm_de_instagram_cae_en_la_ficha_unida():
    from sqlalchemy import func, select

    from app.db.session import db_session
    from app.models.contact import Contact

    telefono = telefono_mx()
    llave = llave_ig()
    _conv_wa, wa = await conversacion_y_ficha(await entra(telefono, "hola por WhatsApp"))
    conv_ig, ig = await conversacion_y_ficha(await entra(llave, "hola por Instagram"))
    try:
        await _une(wa, ig)

        conv_despues, ficha_despues = await conversacion_y_ficha(
            await entra(llave, "soy yo otra vez")
        )
        assert ficha_despues == wa, "el DM abrió otra ficha en vez de usar la unida"
        assert conv_despues == conv_ig, "el DM no siguió en su conversación de Instagram"
        async with db_session() as db:
            duplicadas = (
                await db.execute(
                    select(func.count()).select_from(Contact).where(Contact.telefono == llave)
                )
            ).scalar_one()
        assert duplicadas == 0
    finally:
        await borrar(wa, ig)


@pytestmark_db
@pytest.mark.asyncio
async def test_el_chat_web_reconoce_al_visitante_tras_unir_su_ficha():
    from app.api.webchat import _find_contact
    from app.db.session import db_session

    visitante = "v" + telefono_mx()[1:]
    web = await ficha(f"web:{visitante}", "web")
    wa = await ficha(telefono_mx(), "whatsapp")
    try:
        await _une(wa, web)
        async with db_session() as db:
            encontrada = await _find_contact(db, visitante)
        assert encontrada is not None and encontrada.id == wa
        assert (await leer(wa)).identidad_canal == f"web:{visitante}"
    finally:
        await borrar(wa, web)


def test_instagram_contesta_a_su_cuenta_aunque_la_llave_sea_el_telefono():
    from app.services.channel_sender import _destino_instagram

    ficha_unida = SimpleNamespace(telefono="+529981234567", identidad_canal="ig:222")
    assert _destino_instagram(SimpleNamespace(session_id="ig:111"), ficha_unida) == "ig:111"
    assert _destino_instagram(SimpleNamespace(session_id=None), ficha_unida) == "ig:222"
    solo_ig = SimpleNamespace(telefono="ig:333", identidad_canal=None)
    assert _destino_instagram(SimpleNamespace(session_id=None), solo_ig) == "ig:333"


@pytest.mark.parametrize(
    "numero, llave",
    [
        ("+5219981234567", "+529981234567"),
        ("+529981234567", "+529981234567"),
        ("5219981234567", "+529981234567"),
        (None, "voice:call_1"),
        ("", "voice:call_1"),
    ],
)
def test_la_llamada_se_guarda_con_la_misma_regla_que_whatsapp(numero, llave):
    from app.api.voice import _llave_de_quien_llama

    assert _llave_de_quien_llama(numero, "call_1") == llave


def test_messenger_es_un_identificador_de_canal_y_no_un_telefono():
    from app.api.contacts import is_channel_identifier, normalize_phone

    assert is_channel_identifier("fb:123456")
    assert normalize_phone("fb:123456") == "fb:123456"
