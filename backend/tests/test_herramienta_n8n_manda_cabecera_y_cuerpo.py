"""Lo que el CRM le manda a n8n y lo que le llega a Sofía de vuelta.

Si esto falla, o el flujo de n8n no puede comprobar que la llamada viene del
CRM (falta el secreto en `X-Omnicanal-Token`), o le llegan datos que la
herramienta no pidió, o Sofía recibe lo de n8n como si fueran órdenes.
"""
from __future__ import annotations

import json

import httpx

from tests._n8n import SECRETO, herramienta, n8n_falso, url_publica


async def test_lleva_el_secreto_y_solo_los_datos_que_pide_la_herramienta(monkeypatch):
    from app.services.n8n_tools import llamar_herramienta

    url_publica(monkeypatch)
    n8n = n8n_falso(monkeypatch, (200, {"resultado": "El pedido 123 va en camino."}))

    salida = await llamar_herramienta(
        herramienta(),
        {"numero": "123", "otra_cosa": "no la pidió nadie"},
        {"turno_id": "t1", "canal": "whatsapp", "agent_id": "a1"},
    )

    peticion = n8n.peticiones[0]
    assert peticion.method == "POST"
    assert peticion.headers["X-Omnicanal-Token"] == SECRETO
    cuerpo = n8n.cuerpo()
    assert cuerpo["evento"] == "herramienta"
    assert cuerpo["herramienta"] == "buscar_pedido"
    assert cuerpo["args"] == {"numero": "123"}, "le llegó a n8n un dato que la herramienta no declara"
    assert cuerpo["prueba"] is False
    assert len(cuerpo["llamada_id"]) == 64
    assert cuerpo["conversacion"]["canal"] == "whatsapp"
    # Sin `datos_contacto`, ni nombre ni teléfono del paciente.
    assert "telefono" not in json.dumps(cuerpo["contacto"])

    vuelta = json.loads(salida)
    assert vuelta["resultado"] == "El pedido 123 va en camino."
    assert "instrucciones" in vuelta["_nota_seguridad"], "Sofía recibe lo de n8n sin aviso de que son datos"


async def test_la_misma_llamada_en_el_mismo_turno_lleva_el_mismo_id(monkeypatch):
    from app.services.n8n_tools import llamar_herramienta

    url_publica(monkeypatch)
    n8n = n8n_falso(monkeypatch, (200, {"resultado": "ok"}))
    ctx = {"turno_id": "t1"}
    await llamar_herramienta(herramienta(), {"numero": "1"}, ctx)
    await llamar_herramienta(herramienta(), {"numero": "1"}, ctx)
    await llamar_herramienta(herramienta(), {"numero": "2"}, ctx)
    ids = [n8n.cuerpo(i)["llamada_id"] for i in range(3)]
    assert ids[0] == ids[1] != ids[2]


async def test_texto_suelto_y_respuestas_enormes(monkeypatch):
    from app.services import n8n_tools

    url_publica(monkeypatch)
    n8n_falso(monkeypatch, (200, "Pedido entregado ayer"))
    vuelta = json.loads(await n8n_tools.llamar_herramienta(herramienta(), {"numero": "1"}, {}))
    assert vuelta["resultado"] == "Pedido entregado ayer"

    n8n_falso(monkeypatch, (200, {"resultado": "x" * 50_000}))
    vuelta = json.loads(await n8n_tools.llamar_herramienta(herramienta(), {"numero": "1"}, {}))
    assert len(vuelta["resultado"]) < 9_000 and vuelta["resultado"].endswith("[…respuesta recortada]")


async def test_lectura_se_reintenta_una_vez_y_escritura_nunca(monkeypatch):
    from app.services import n8n_tools

    url_publica(monkeypatch)
    n8n = n8n_falso(monkeypatch, (500, "caído"), (200, {"resultado": "ya"}))
    hecho = await n8n_tools.llamar_a_n8n(herramienta(accion="lectura"), {"numero": "1"}, {})
    assert hecho["ok"] and hecho["intentos"] == 2 and len(n8n.peticiones) == 2

    n8n = n8n_falso(monkeypatch, (500, "caído"), (200, {"resultado": "ya"}))
    hecho = await n8n_tools.llamar_a_n8n(herramienta(accion="escritura"), {"numero": "1"}, {})
    assert not hecho["ok"] and len(n8n.peticiones) == 1, "una de escritura se repitió: podría duplicar un pedido"


async def test_no_sigue_redirecciones_ni_espera_de_mas_en_una_llamada(monkeypatch):
    from app.services import n8n_tools

    url_publica(monkeypatch)
    n8n = n8n_falso(monkeypatch, (302, ""))
    hecho = await n8n_tools.llamar_a_n8n(herramienta(), {"numero": "1"}, {})
    assert not hecho["ok"] and "redirección" in hecho["error"] and len(n8n.peticiones) == 1

    esperas = []
    original = n8n_tools._post

    async def _post(url, secreto, cuerpo, espera):
        esperas.append(espera)
        return await original(url, secreto, cuerpo, espera)

    monkeypatch.setattr(n8n_tools, "_post", _post)
    n8n_falso(monkeypatch, (200, {"resultado": "ok"}))
    await n8n_tools.llamar_a_n8n(herramienta(espera_s=15), {"numero": "1"}, {"channel": "retell_voice"})
    assert esperas == [n8n_tools.ESPERA_MAXIMA_VOZ_S], "en una llamada de voz se esperan 15 s en silencio"


async def test_si_n8n_no_contesta_sofia_no_inventa(monkeypatch):
    from app.services import n8n_tools

    url_publica(monkeypatch)
    n8n_falso(monkeypatch, httpx.ConnectTimeout("sin respuesta"))
    vuelta = json.loads(await n8n_tools.llamar_herramienta(herramienta(accion="lectura"), {"numero": "1"}, {}))
    assert "error" in vuelta and "no tienes ese dato" in vuelta["instruccion"]
