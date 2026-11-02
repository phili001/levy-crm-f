"""El teléfono que alguien DICE por Instagram no cambia quién es (Fase 10.1, US-24 AC2).

Instagram, Messenger, el chat web y el correo no dan el teléfono de quien
escribe. Cuando la persona lo dice, Sofía lo guarda con
`crear_actualizar_contacto(telefono_declarado=…)`, APARTE de la llave
(`telefono`, que es lo que empareja sus mensajes con su ficha). Si lo pusiera
en la llave, un desconocido que escribe «mi número es el +52…» se quedaría con
la ficha de otro paciente: su historial, sus citas y sus notas.

Lo que se comprueba aquí:
  - el número dicho se guarda en E.164 con la regla MX (10 dígitos = México),
    para que coincida con el que guardan WhatsApp y las llamadas;
  - lo que no es un teléfono no se guarda;
  - por Instagram se guarda aparte y la llave no se toca;
  - por WhatsApp o por teléfono no se guarda un «segundo número»;
  - intentar cambiar la llave con `telefono` sigue bloqueado y avisa.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from tests._fichas import borrar, ficha, leer, llave_ig, telefono_mx
from tests.test_voice_gating import pytestmark_db


@pytest.mark.parametrize(
    "dicho, esperado",
    [
        ("998 123 4567", "+529981234567"),
        ("(998) 123-4567", "+529981234567"),
        ("+52 998 123 4567", "+529981234567"),
        ("+52 1 998 123 4567", "+529981234567"),
        ("044 998 123 4567", "+529981234567"),
        ("0052 998 123 4567", "+529981234567"),
        ("+1 (555) 123-4567", "+15551234567"),
    ],
)
def test_el_numero_dicho_se_guarda_en_formato_internacional(dicho, esperado):
    from app.services.identity_linking import telefono_declarado_e164

    assert telefono_declarado_e164(dicho) == esperado


@pytest.mark.parametrize("dicho", ["12345", "mañana te lo paso", "ig:12345", "", None])
def test_lo_que_no_es_un_telefono_no_se_guarda(dicho):
    from app.services.identity_linking import telefono_declarado_e164

    assert telefono_declarado_e164(dicho) is None


@pytestmark_db
@pytest.mark.asyncio
async def test_por_instagram_el_telefono_dicho_se_guarda_aparte():
    from app.agents.tools.contact_upsert import crear_actualizar_contacto

    llave = llave_ig()
    ig = await ficha(llave, "instagram")
    try:
        respuesta = json.loads(
            await crear_actualizar_contacto(
                {"nombre": "Juan Pérez", "telefono_declarado": "998 555 0101"},
                {"telefono": llave},
            )
        )
        assert respuesta["updated"] is True
        f = await leer(ig)
        assert f.telefono == llave, "el teléfono dicho pisó la llave de Instagram"
        assert f.telefono_declarado == "+529985550101"
        assert f.nombre == "Juan Pérez"
    finally:
        await borrar(ig)


@pytestmark_db
@pytest.mark.asyncio
async def test_por_whatsapp_no_se_guarda_un_segundo_numero():
    """WhatsApp con número y las llamadas ya traen el teléfono de verdad."""
    from app.agents.tools.contact_upsert import crear_actualizar_contacto

    telefono = telefono_mx()
    wa = await ficha(telefono, "whatsapp")
    try:
        respuesta = json.loads(
            await crear_actualizar_contacto(
                {"telefono_declarado": "998 555 0102"}, {"telefono": telefono}
            )
        )
        assert "ya trae su teléfono" in respuesta["telefono_declarado"]
        f = await leer(wa)
        assert f.telefono == telefono
        assert f.telefono_declarado is None
    finally:
        await borrar(wa)


@pytestmark_db
@pytest.mark.asyncio
async def test_un_numero_a_medias_no_se_guarda_y_sofia_lo_sabe():
    from app.agents.tools.contact_upsert import crear_actualizar_contacto

    llave = llave_ig()
    ig = await ficha(llave, "instagram")
    try:
        respuesta = json.loads(
            await crear_actualizar_contacto({"telefono_declarado": "998 12"}, {"telefono": llave})
        )
        assert "lada" in respuesta["telefono_declarado"]
        assert (await leer(ig)).telefono_declarado is None
    finally:
        await borrar(ig)


@pytestmark_db
@pytest.mark.asyncio
async def test_ni_con_el_parametro_telefono_se_cambia_la_llave(monkeypatch):
    """El bloqueo de siempre sigue en pie: `telefono` no es un parámetro."""
    from app.agents.tools import contact_upsert

    aviso = AsyncMock()
    monkeypatch.setattr(contact_upsert, "notify_security", aviso)
    llave = llave_ig()
    ig = await ficha(llave, "instagram")
    try:
        await contact_upsert.crear_actualizar_contacto(
            {"telefono": telefono_mx(), "nombre": "Alguien"}, {"telefono": llave}
        )
        assert (await leer(ig)).telefono == llave
        assert aviso.await_count == 1
    finally:
        await borrar(ig)
