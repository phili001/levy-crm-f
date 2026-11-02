"""Al unir fichas, la llave es el teléfono de verdad y nada se pierde (Fase 10.1, US-24 AC3).

Antes, fusionar conservaba siempre el `telefono` de la ficha que se queda. Si
el operador fusionaba desde la ficha de Instagram (la de la URL), la ficha
unida se quedaba con `ig:…` como llave y el +52… de WhatsApp desaparecía: los
siguientes WhatsApp de esa persona abrían otra ficha.

Lo que se comprueba aquí:
  - la llave es el teléfono de verdad, venga de la ficha que venga, y la cuenta
    del canal pasa a `identidad_canal`;
  - las citas pasan a la ficha unida;
  - la fusión que sale de una propuesta la resuelve, y no acepta una propuesta
    de otro par;
  - si quedarían dos cuentas de canal que guardar, la fusión se niega (409) y
    no toca nada.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from tests._fichas import (
    borrar,
    cita_para,
    dueno_de_la_cita,
    ficha,
    leer,
    llave_ig,
    propuestas_de,
    telefono_mx,
)
from tests.test_voice_gating import pytestmark_db


def _f(telefono, identidad=None):
    return SimpleNamespace(telefono=telefono, identidad_canal=identidad)


@pytest.mark.parametrize(
    "destino, origen, esperado",
    [
        (_f("ig:1"), _f("+529981234567"), ("+529981234567", "ig:1")),
        (_f("+529981234567"), _f("ig:1"), ("+529981234567", "ig:1")),
        # Las dos con teléfono: manda el del destino, como siempre.
        (_f("+529981234567"), _f("+529987654321"), ("+529981234567", None)),
        # El `wa:` no se guarda: el BSUID ya vive en `wa_user_id`.
        (_f("wa:MX.123"), _f("+529981234567"), ("+529981234567", None)),
        # Sin teléfono en ninguna: una llave y la otra cuenta en la identidad.
        (_f("ig:1"), _f("fb:2"), ("ig:1", "fb:2")),
        # La misma cuenta por los dos lados no cuenta dos veces.
        (_f("+529981234567", "ig:1"), _f("ig:1"), ("+529981234567", "ig:1")),
        # Una llamada con número oculto no deja nada que guardar.
        (_f("+529981234567"), _f("voice:call_1"), ("+529981234567", None)),
    ],
)
def test_la_llave_es_el_telefono_venga_de_la_ficha_que_venga(destino, origen, esperado):
    from app.services.contact_merge import claves_tras_fusion

    assert claves_tras_fusion(destino, origen) == esperado


def test_dos_cuentas_de_canal_que_guardar_no_caben():
    from app.services.contact_merge import FusionImposible, claves_tras_fusion

    with pytest.raises(FusionImposible) as e:
        claves_tras_fusion(_f("+529981234567", "ig:1"), _f("fb:2"))
    assert "Instagram y Messenger" in str(e.value)


@pytest.fixture
async def http():
    from app.main import app
    from tests.test_agenda_desde_el_panel import _borrar_usuarios, _usuarios

    admin, _op, _rev, usuarios = await _usuarios()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, admin
    await _borrar_usuarios(usuarios)


@pytestmark_db
@pytest.mark.asyncio
async def test_fusionar_desde_instagram_conserva_el_telefono_y_las_citas(http):
    c, admin = http
    telefono = telefono_mx()
    llave = llave_ig()
    ig = await ficha(llave, "instagram")
    wa = await ficha(telefono, "whatsapp", nombre="Juan Pérez")
    cita = await cita_para(wa)
    try:
        r = await c.post(f"/api/v1/contacts/{ig}/merge", json={"source_id": str(wa)}, headers=admin)
        assert r.status_code == 200, r.text
        unida = await leer(ig)
        assert unida.telefono == telefono, "la ficha unida perdió el teléfono de WhatsApp"
        assert unida.identidad_canal == llave
        assert unida.nombre == "Juan Pérez"
        assert await leer(wa) is None
        assert await dueno_de_la_cita(cita) == ig
    finally:
        await borrar(ig, wa)


@pytestmark_db
@pytest.mark.asyncio
async def test_la_fusion_desde_una_propuesta_la_resuelve(http):
    from app.agents.tools.contact_upsert import crear_actualizar_contacto

    c, admin = http
    telefono = telefono_mx()
    llave = llave_ig()
    wa = await ficha(telefono, "whatsapp")
    ig = await ficha(llave, "instagram")
    otra = await ficha(telefono_mx(), "whatsapp")
    try:
        await crear_actualizar_contacto({"telefono_declarado": telefono}, {"telefono": llave})
        [propuesta] = await propuestas_de(ig)

        ajena = await c.post(
            f"/api/v1/contacts/{otra}/merge",
            json={"source_id": str(ig), "suggestion_id": str(propuesta.id)},
            headers=admin,
        )
        assert ajena.status_code == 422, "aceptó una propuesta de otro par"

        r = await c.post(
            f"/api/v1/contacts/{wa}/merge",
            json={"source_id": str(ig), "suggestion_id": str(propuesta.id)},
            headers=admin,
        )
        assert r.status_code == 200, r.text
        assert r.json()["identidad_canal"] == llave
        assert r.json()["telefono_declarado"] is None, "el teléfono dicho ya es la llave"
        assert await propuestas_de(wa) == []
        assert await leer(ig) is None
    finally:
        await borrar(ig, wa, otra)


@pytestmark_db
@pytest.mark.asyncio
async def test_si_se_perderia_una_cuenta_la_fusion_se_niega_sin_tocar_nada(http):
    c, admin = http
    telefono = telefono_mx()
    wa = await ficha(telefono, "whatsapp", identidad_canal=llave_ig())
    fb = await ficha(f"fb:{telefono[1:]}", "messenger")
    try:
        r = await c.post(f"/api/v1/contacts/{wa}/merge", json={"source_id": str(fb)}, headers=admin)
        assert r.status_code == 409
        assert "Messenger" in r.json()["detail"]
        assert await leer(fb) is not None, "borró la ficha aunque la fusión se negó"
        assert (await leer(wa)).telefono == telefono
    finally:
        await borrar(wa, fb)
