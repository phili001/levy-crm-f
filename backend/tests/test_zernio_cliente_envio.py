"""El cliente de Zernio envía una sola vez y guarda el id real (sin red).

Contra un transporte simulado de httpx, con las respuestas que documenta
Zernio. Si esto falla:
  - sin `Idempotency-Key`, o con otra llave en el reintento, un 429 manda el
    mismo mensaje dos veces al paciente;
  - leer el id de otro sitio que `data.messageId` (el bug de vocero) deja al
    CRM sin reconocer su propio eco, y lo guardaría dos veces;
  - reintentar un 500 de envío puede duplicar el mensaje: Zernio pudo
    haberlo entregado;
  - un rechazo por ventana tiene que llegar como tal, para que el panel lo
    explique en vez de decir "error".
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.providers.zernio.client import (
    ZernioAllowanceExceeded,
    ZernioAuthError,
    ZernioClient,
    ZernioError,
    ZernioWindowClosed,
)


def _cliente(manejador) -> tuple[ZernioClient, list[httpx.Request]]:
    vistas: list[httpx.Request] = []

    def _h(req: httpx.Request) -> httpx.Response:
        vistas.append(req)
        return manejador(req, len(vistas))

    return ZernioClient("sk_prueba", transport=httpx.MockTransport(_h)), vistas


def _ok(message_id="aWdf_mid_real"):
    return httpx.Response(
        200, json={"success": True, "data": {"messageId": message_id, "conversationId": "c1"}}
    )


def _enviar(zc, **kw):
    return asyncio.run(
        zc.send_message("conv1", "acc1", "Hola", idempotency_key="llave-1", **kw)
    )


def test_el_envio_lleva_llave_y_devuelve_el_mid_real():
    zc, vistas = _cliente(lambda req, n: _ok())
    envio = _enviar(zc)
    assert envio.message_id == "aWdf_mid_real"
    req = vistas[0]
    assert req.url.path == "/api/v1/inbox/conversations/conv1/messages"
    assert req.headers["idempotency-key"] == "llave-1"
    assert req.headers["authorization"] == "Bearer sk_prueba"
    assert json.loads(req.content) == {"accountId": "acc1", "message": "Hola"}


def test_fuera_de_24h_va_con_la_etiqueta_de_agente_humano():
    zc, vistas = _cliente(lambda req, n: _ok())
    _enviar(zc, messaging_type="MESSAGE_TAG", message_tag="HUMAN_AGENT")
    cuerpo = json.loads(vistas[0].content)
    assert cuerpo["messagingType"] == "MESSAGE_TAG"
    assert cuerpo["messageTag"] == "HUMAN_AGENT"


def test_un_429_se_reintenta_con_la_misma_llave(monkeypatch):
    async def _sin_espera(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", _sin_espera)
    zc, vistas = _cliente(
        lambda req, n: httpx.Response(429, headers={"Retry-After": "1"}) if n == 1 else _ok()
    )
    assert _enviar(zc).message_id == "aWdf_mid_real"
    assert len(vistas) == 2
    assert vistas[0].headers["idempotency-key"] == vistas[1].headers["idempotency-key"]


def test_un_500_al_enviar_no_se_reintenta():
    zc, vistas = _cliente(lambda req, n: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(ZernioError) as e:
        _enviar(zc)
    assert e.value.status == 500
    assert len(vistas) == 1


def test_la_ventana_cerrada_llega_como_tal():
    zc, _ = _cliente(
        lambda req, n: httpx.Response(
            403, json={"error": "outside window", "code": "platform_api_error"}
        )
    )
    with pytest.raises(ZernioWindowClosed):
        _enviar(zc)


def test_llave_mala_y_cupo_agotado_se_distinguen():
    zc, _ = _cliente(lambda req, n: httpx.Response(401, json={"error": "invalid key"}))
    with pytest.raises(ZernioAuthError):
        _enviar(zc)
    zc, _ = _cliente(
        lambda req, n: httpx.Response(403, json={"code": "message_allowance_exceeded"})
    )
    with pytest.raises(ZernioAllowanceExceeded):
        _enviar(zc)


def test_sin_message_id_no_se_da_por_enviado():
    zc, _ = _cliente(lambda req, n: httpx.Response(200, json={"success": True, "data": {}}))
    with pytest.raises(ZernioError):
        _enviar(zc)


def test_resolver_un_adjunto_pide_json_con_la_cuenta():
    zc, vistas = _cliente(
        lambda req, n: httpx.Response(200, json={"status": "success", "url": "https://x.fbcdn.net/a"})
    )
    url = asyncio.run(zc.resolve_attachment("conv1", "m_mid", 2, "acc1"))
    assert url == "https://x.fbcdn.net/a"
    req = vistas[0]
    assert req.url.path == "/api/v1/inbox/conversations/conv1/messages/m_mid/attachments/2"
    assert req.url.params["accountId"] == "acc1"
    assert req.url.params["format"] == "json"


def test_la_paginacion_devuelve_el_cursor_solo_si_hay_mas():
    zc, _ = _cliente(
        lambda req, n: httpx.Response(
            200,
            json={
                "data": [{"id": "t1"}],
                "pagination": {"hasMore": n == 1, "nextCursor": "cur2"},
            },
        )
    )
    convs, cursor = asyncio.run(zc.list_conversations("acc1"))
    assert [c["id"] for c in convs] == ["t1"] and cursor == "cur2"
    _, cursor = asyncio.run(zc.list_conversations("acc1", cursor))
    assert cursor is None


def test_el_troceo_respeta_el_tope_en_bytes():
    from app.services.zernio import trocear

    texto = "Primera frase. " * 80 + "\n\n" + "😀" * 400
    trozos = trocear(texto, 1000)
    assert all(len(t.encode()) <= 1000 for t in trozos)
    assert "".join(trozos).replace(" ", "").replace("\n", "") == texto.replace(" ", "").replace(
        "\n", ""
    )


def test_la_descarga_rechaza_hosts_fuera_de_la_lista():
    from app.providers.zernio.media import DescargaBloqueada, descargar

    with pytest.raises(DescargaBloqueada):
        asyncio.run(descargar("https://evil.example.com/a.jpg", max_bytes=10))
    with pytest.raises(DescargaBloqueada):
        asyncio.run(descargar("http://169.254.169.254/latest", max_bytes=10))


def test_la_descarga_corta_una_redireccion_fuera_de_meta():
    from app.providers.zernio.media import DescargaBloqueada, descargar

    def _h(req: httpx.Request) -> httpx.Response:
        if req.url.host == "scontent.fbcdn.net":
            return httpx.Response(302, headers={"Location": "https://evil.example.com/x"})
        return httpx.Response(200, content=b"secreto")

    with pytest.raises(DescargaBloqueada):
        asyncio.run(
            descargar(
                "https://scontent.fbcdn.net/a.jpg",
                max_bytes=100,
                transport=httpx.MockTransport(_h),
            )
        )


def test_el_historial_espera_lo_que_pide_zernio_tras_un_429(monkeypatch):
    """En vivo Zernio pidió 55 s («Please retry after 55 seconds», sin
    cabecera) y la importación, que solo aguantaba 10, se cortó a medias."""
    esperas: list[float] = []

    async def _dormir(s):
        esperas.append(s)

    monkeypatch.setattr(asyncio, "sleep", _dormir)

    def _h(req, n):
        if n == 1:
            return httpx.Response(429, json={"error": "Rate limit exceeded. Please retry after 55 seconds."})
        return httpx.Response(200, json={"data": [], "pagination": {"hasMore": False}})

    vistas: list[httpx.Request] = []

    def _t(req):
        vistas.append(req)
        return _h(req, len(vistas))

    zc = ZernioClient(
        "sk", transport=httpx.MockTransport(_t), max_espera_429=90, intentos_429=4
    )
    convs, _ = asyncio.run(zc.list_conversations("acc1"))
    assert convs == [] and 55.0 in esperas and len(vistas) == 2

    # El envío normal (con alguien esperando) no aguanta tanto: falla y avisa.
    vistas.clear()
    rapido = ZernioClient("sk", transport=httpx.MockTransport(_t))
    with pytest.raises(ZernioError):
        asyncio.run(rapido.list_conversations("acc1"))
