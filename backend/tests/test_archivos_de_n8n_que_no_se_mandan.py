"""Los archivos de n8n que no deben llegar al paciente, y lo que se le dice a Sofía.

Un archivo que no sale nunca tumba la respuesta: Sofía recibe «no enviado» con
el motivo y lo dice con naturalidad. Lo que no sale:
- HTML (o SVG, o un script) disfrazado de imagen;
- una «imagen» que en realidad es un PDF;
- un documento por Instagram, que no los admite;
- cualquier archivo en una llamada de voz;
- el cuarto y siguientes de una misma llamada;
- uno que está en una URL interna.
"""
from __future__ import annotations

import json

import pytest

from tests._n8n import (
    archivo,
    borrar_conversacion,
    conversacion,
    db_disponible,
    herramienta,
    mensajes_de,
    n8n_falso,
    url_publica,
)

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")

_URL = "https://archivos.ejemplo.mx/x"


def _llamar(monkeypatch, canal: str, archivos: list, *descargas, ctx_extra: dict | None = None):
    import asyncio

    from app.core import events
    from app.services.n8n_tools import llamar_herramienta

    url_publica(monkeypatch)
    n8n_falso(monkeypatch, (200, {"archivos": archivos}), *descargas)

    async def _publicar(*_a):
        return None

    monkeypatch.setattr(events.event_bus, "publish", _publicar)

    async def _run():
        conv_id, ficha_id = await conversacion(canal)
        try:
            salida = json.loads(await llamar_herramienta(
                herramienta(nombre="archivos_prueba"), {},
                {"conversation_id": str(conv_id), "canal": canal, **(ctx_extra or {})},
            ))
            con_archivo = [m for m in await mensajes_de(conv_id) if m.media_type]
        finally:
            await borrar_conversacion(conv_id, ficha_id)
        return salida["archivos"], con_archivo

    return asyncio.run(_run())


def test_html_disfrazado_de_imagen(monkeypatch):
    estados, hilo = _llamar(monkeypatch, "web", [{"tipo": "imagen", "url": _URL}],
                            archivo(b"<html><script>alert(1)</script></html>", "image/jpeg"))
    assert estados[0]["estado"] == "no enviado" and "no es un archivo" in estados[0]["motivo"]
    assert hilo == []


def test_una_imagen_que_es_un_pdf(monkeypatch):
    estados, _ = _llamar(monkeypatch, "web", [{"tipo": "imagen", "url": _URL}],
                         archivo(b"%PDF-1.4 " + b"0" * 100, "application/pdf"))
    assert estados[0]["estado"] == "no enviado" and "documento" in estados[0]["motivo"]


def test_un_documento_por_instagram(monkeypatch):
    estados, _ = _llamar(monkeypatch, "instagram_dm", [{"tipo": "documento", "url": _URL}],
                         archivo(b"%PDF-1.4 " + b"0" * 100, "application/pdf"))
    assert estados[0]["estado"] == "no enviado" and "no admite" in estados[0]["motivo"]


def test_en_una_llamada_de_voz(monkeypatch):
    estados, hilo = _llamar(monkeypatch, "web", [{"tipo": "imagen", "url": _URL}], archivo(),
                            ctx_extra={"channel": "retell_voice"})
    assert estados[0]["estado"] == "no enviado" and "llamada" in estados[0]["motivo"]
    assert hilo == []


def test_como_mucho_tres_por_llamada(monkeypatch):
    estados, hilo = _llamar(monkeypatch, "web", [{"tipo": "imagen", "url": _URL}] * 4, archivo())
    assert [e["estado"] for e in estados] == ["enviado", "enviado", "enviado", "no enviado"]
    assert len(hilo) == 3


def test_una_url_interna(monkeypatch):
    estados, _ = _llamar(monkeypatch, "web", [{"tipo": "imagen", "url": "https://127.0.0.1/foto.jpg"}], archivo())
    assert estados[0]["estado"] == "no enviado" and "URL rechazada" in estados[0]["motivo"]
