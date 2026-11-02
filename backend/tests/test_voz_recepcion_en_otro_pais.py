"""El número puede pasar la llamada a recepción aunque esté en otro país (0.7.2).

V-5 en vivo: el +52 del curso 02 tenía en Retell la lista de países de salida
en solo México. Con recepción en EE. UU., Retell rechazaba la transferencia
(«Transfer destination is not supported»): Sofía decía «te paso con recepción»
y no sonaba nada. Ahora, al conectar el número o poner recepción, el CRM añade
a esa lista el país de recepción, y solo ese, y lo enseña en las comprobaciones.

Contra el simulador de Retell. La del endpoint, además, con base de datos.
"""
from __future__ import annotations

import asyncio

import pytest

from app.providers.voice import retell_admin as ra
from tests.test_voz_alta_del_agente_en_retell import _alta, retell  # noqa: F401 — fixture
from tests.test_voz_conectar_desde_el_crm import (  # noqa: F401 — fixture
    _apartar_canales_de_voz,
    _cliente,
    _entrar,
    _salir,
    proveedores,
)
from tests.test_voz_conectar_desde_el_crm import pytestmark as con_bd

NUMERO = "+529980000001"
RECEPCION_EEUU = "+12025550143"


@pytest.mark.parametrize(
    ("telefono", "paises"),
    [
        (NUMERO, ("MX",)),
        (RECEPCION_EEUU, ("US", "CA")),
        ("+593991234567", ("EC",)),
        ("+34600111222", ("ES",)),
        ("+447911123456", ()),
    ],
)
def test_el_pais_sale_del_prefijo(telefono, paises):
    assert ra.paises_de(telefono) == paises


async def _numero_del_curso(control, paises: list[str] | None) -> None:
    """Como lo dejó el curso 02: importado y con su lista de países."""
    r = await control.post(
        "/import-phone-number",
        headers={"Authorization": "Bearer retell-key"},
        json={
            "phone_number": NUMERO,
            "termination_uri": "sofia-voz.pstn.twilio.com",
            "inbound_agents": [],
            "allowed_outbound_country_list": paises,
        },
    )
    assert r.status_code == 200, r.text


async def _paises(control) -> list[str] | None:
    return (await control.get("/_mock/numeros")).json()[NUMERO]["allowed_outbound_country_list"]


async def test_se_anade_el_pais_de_recepcion_y_solo_ese(retell):  # noqa: F811
    hacer, control = retell
    admin = hacer()
    await _numero_del_curso(control, ["MX"])

    comprobado = await ra.permitir_llamar_a(admin, NUMERO, RECEPCION_EEUU)

    assert comprobado.nombre == "recepcion_permitida" and comprobado.ok
    assert "se añadió US, CA" in comprobado.detalle
    assert await _paises(control) == ["MX", "US", "CA"]

    otra_vez = await ra.permitir_llamar_a(admin, NUMERO, RECEPCION_EEUU)
    assert otra_vez.ok and otra_vez.detalle == "MX, US, CA", "no duplica nada"


async def test_recepcion_del_mismo_pais_no_cambia_nada(retell):  # noqa: F811
    hacer, control = retell
    await _numero_del_curso(control, ["MX"])

    comprobado = await ra.permitir_llamar_a(hacer(), NUMERO, "+529981112233")

    assert comprobado.ok and comprobado.detalle == "MX"
    assert await _paises(control) == ["MX"]


async def test_sin_lista_el_numero_ya_llama_a_cualquier_pais(retell):  # noqa: F811
    hacer, control = retell
    await _numero_del_curso(control, None)

    comprobado = await ra.permitir_llamar_a(hacer(), NUMERO, RECEPCION_EEUU)

    assert comprobado.ok and "cualquier país" in comprobado.detalle
    assert await _paises(control) is None, "vacía = todos: no se le pone lista"


async def test_un_pais_que_no_se_reconoce_se_avisa_sin_tocar_la_lista(retell):  # noqa: F811
    hacer, control = retell
    await _numero_del_curso(control, ["MX"])

    comprobado = await ra.permitir_llamar_a(hacer(), NUMERO, "+447911123456")

    assert not comprobado.ok and "no sé de qué país" in comprobado.detalle
    assert await _paises(control) == ["MX"]


async def test_un_numero_que_no_esta_en_retell_se_dice(retell):  # noqa: F811
    hacer, _ = retell

    comprobado = await ra.permitir_llamar_a(hacer(), NUMERO, RECEPCION_EEUU)

    assert not comprobado.ok and "no está en la cuenta de Retell" in comprobado.detalle


@con_bd
def test_conectar_la_voz_deja_llamar_a_recepcion_en_otro_pais(proveedores):  # noqa: F811
    control, _ = proveedores

    async def _run():
        s = await _entrar()
        await _apartar_canales_de_voz(s)
        try:
            await _numero_del_curso(control, ["MX"])
            async with _cliente() as c:
                r = await c.post(
                    "/api/v1/admin/channels/retell/provision",
                    headers={"Authorization": f"Bearer {s.token}"},
                    json={
                        "api_key": "retell-key",
                        "create_agent": True,
                        "phone_number": NUMERO,
                        "transfer_number": "+1 202 555 0143",
                    },
                )
            assert r.status_code == 200, r.text
            checks = {x["name"]: x for x in r.json()["checks"]}
            assert checks["recepcion_permitida"]["ok"], checks["recepcion_permitida"]
            assert await _paises(control) == ["MX", "US", "CA"]
        finally:
            await _salir(s)

    asyncio.run(_run())
