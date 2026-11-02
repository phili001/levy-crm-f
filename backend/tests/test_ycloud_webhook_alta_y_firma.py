"""El alta del webhook por API y la ventana de la firma.

Antes, el secreto de firma se copiaba a mano de la consola de YCloud al panel:
tres pasos con el secreto en el portapapeles, y un carácter de menos dejaba los
mensajes entrando sin validar, con el síntoma muy lejos de la causa.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

import httpx
import pytest

from app.providers.whatsapp.ycloud import YCloudProvider

SECRETO = "whsec_de_prueba_1234567890"
URL = "https://crm.ejemplo.mx/api/v1/webhooks/ycloud"


def _firma(cuerpo: bytes, ts: int, secreto: str = SECRETO) -> str:
    mac = hmac.new(secreto.encode(), f"{ts}.".encode() + cuerpo, hashlib.sha256)
    return f"t={ts},s={mac.hexdigest()}"


def _proveedor(monkeypatch, respuestas: dict) -> YCloudProvider:
    """Un proveedor con credenciales de mentira y la API de YCloud simulada."""
    p = YCloudProvider()

    async def _api_key():
        return "ycl_de_prueba"

    async def _secreto():
        return SECRETO

    monkeypatch.setattr(p, "_api_key", _api_key)
    monkeypatch.setattr(p, "_webhook_secret", _secreto)

    def _handler(request: httpx.Request) -> httpx.Response:
        # La base de YCloud ya trae /v2; las claves de abajo se escriben sin él.
        ruta = request.url.path.removeprefix("/v2")
        clave = f"{request.method} {ruta}"
        cuerpo, estado = respuestas.get(clave, ({}, 404))
        return httpx.Response(estado, json=cuerpo)

    transporte = httpx.MockTransport(_handler)
    original = httpx.AsyncClient

    def _cliente(*args, **kwargs):
        kwargs["transport"] = transporte
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _cliente)
    return p


def test_el_alta_crea_el_endpoint_y_devuelve_el_secreto(monkeypatch):
    p = _proveedor(monkeypatch, {
        "GET /webhookEndpoints": ({"items": []}, 200),
        "POST /webhookEndpoints": ({"id": "wh_1", "secret": SECRETO}, 200),
    })
    r = asyncio.run(p.create_or_update_webhook_endpoint(URL))
    assert r["secret"] == SECRETO
    assert r["reused"] is False


def test_instalar_dos_veces_no_crea_dos_endpoints(monkeypatch):
    """Dos endpoints con la misma URL = cada mensaje entrando dos veces."""
    p = _proveedor(monkeypatch, {
        "GET /webhookEndpoints": ({"items": [{"id": "wh_1", "url": URL}]}, 200),
        "PATCH /webhookEndpoints/wh_1": ({"id": "wh_1"}, 200),
    })
    r = asyncio.run(p.create_or_update_webhook_endpoint(URL))
    assert r["reused"] is True
    # YCloud no devuelve el secreto al actualizar: quien llama conserva el suyo.
    assert not r.get("secret")


def test_la_barra_final_no_engaña_al_alta(monkeypatch):
    p = _proveedor(monkeypatch, {
        "GET /webhookEndpoints": ({"items": [{"id": "wh_1", "url": URL + "/"}]}, 200),
        "PATCH /webhookEndpoints/wh_1": ({"id": "wh_1"}, 200),
    })
    assert asyncio.run(p.create_or_update_webhook_endpoint(URL))["reused"] is True


def test_rotar_devuelve_el_secreto_nuevo(monkeypatch):
    p = _proveedor(monkeypatch, {
        "POST /webhookEndpoints/wh_1/rotateSecret": ({"secret": "whsec_nuevo"}, 200),
    })
    assert asyncio.run(p.rotate_webhook_secret("wh_1")) == "whsec_nuevo"


def test_una_firma_de_hace_una_hora_se_rechaza(monkeypatch):
    """Una petición legítima capturada no puede reenviarse mañana."""
    p = _proveedor(monkeypatch, {})
    cuerpo = json.dumps({"type": "whatsapp.inbound_message.received"}).encode()
    vieja = int(time.time()) - 3600
    cabeceras = {"ycloud-signature": _firma(cuerpo, vieja)}
    assert asyncio.run(p.verify_webhook_signature(cabeceras, cuerpo)) is False


def test_una_firma_recien_hecha_se_acepta(monkeypatch):
    p = _proveedor(monkeypatch, {})
    cuerpo = json.dumps({"type": "whatsapp.inbound_message.received"}).encode()
    cabeceras = {"ycloud-signature": _firma(cuerpo, int(time.time()))}
    assert asyncio.run(p.verify_webhook_signature(cabeceras, cuerpo)) is True


def test_una_marca_de_tiempo_ilegible_se_rechaza(monkeypatch):
    p = _proveedor(monkeypatch, {})
    cuerpo = b"{}"
    cabeceras = {"ycloud-signature": "t=ayer,s=deadbeef"}
    assert asyncio.run(p.verify_webhook_signature(cabeceras, cuerpo)) is False


def test_el_reloj_del_servidor_adelantado_no_pasa(monkeypatch):
    """±5 min en los dos sentidos: un reloj mal puesto se nota igual."""
    p = _proveedor(monkeypatch, {})
    cuerpo = b"{}"
    futura = int(time.time()) + 3600
    assert asyncio.run(p.verify_webhook_signature({"ycloud-signature": _firma(cuerpo, futura)}, cuerpo)) is False
