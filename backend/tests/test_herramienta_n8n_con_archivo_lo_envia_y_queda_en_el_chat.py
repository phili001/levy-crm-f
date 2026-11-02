"""Un archivo que devuelve una herramienta de n8n le llega al paciente y queda en el inbox.

El flujo de n8n contesta `archivos: [{tipo, url}]`; el CRM lo baja, lo guarda,
lo manda por el canal de la conversación y lo deja en el hilo como mensaje de
Sofía. A Sofía le llega qué pasó, sin los enlaces (para que no los pegue).
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

_FOTO = {"tipo": "imagen", "url": "https://archivos.ejemplo.mx/antes-despues.jpg", "pie": "Antes y después",
         "nombre": "antes-despues.jpg"}


def test_por_el_chat_web_se_publica_y_queda_en_el_hilo(monkeypatch):
    import asyncio

    from app.core import events
    from app.services.n8n_tools import llamar_herramienta

    url_publica(monkeypatch)
    n8n = n8n_falso(monkeypatch, (200, {"resultado": "Te mando la foto.", "archivos": [_FOTO]}), archivo())
    publicados = []

    async def _publicar(canal, tipo, datos):
        publicados.append((canal, tipo, datos))

    monkeypatch.setattr(events.event_bus, "publish", _publicar)

    async def _run():
        conv_id, ficha_id = await conversacion("web")
        try:
            salida = json.loads(await llamar_herramienta(
                herramienta(nombre="foto_tratamiento"), {}, {"conversation_id": str(conv_id), "canal": "web"}
            ))
            hilo = await mensajes_de(conv_id)
        finally:
            await borrar_conversacion(conv_id, ficha_id)
        return salida, hilo

    salida, hilo = asyncio.run(_run())

    assert [p.url.host for p in n8n.peticiones] == ["n8n.ejemplo.mx", "archivos.ejemplo.mx"]
    assert salida["archivos"] == [{"tipo": "imagen", "nombre": "antes-despues.jpg", "estado": "enviado"}]
    assert "archivos.ejemplo.mx" not in json.dumps(salida), "a Sofía le llegó el enlace del archivo"
    assert salida["resultado"] == "Te mando la foto."

    al_widget = [d for _c, t, d in publicados if t == "message.out"]
    assert al_widget and al_widget[0]["media"]["tipo"] == "image"
    assert "/api/v1/media/p/" in al_widget[0]["media"]["url"], "el widget no recibe un enlace firmado"
    assert al_widget[0]["text"] == "Antes y después"

    con_archivo = [m for m in hilo if m.media_type]
    assert len(con_archivo) == 1, "el archivo no quedó en el hilo del inbox"
    m = con_archivo[0]
    assert m.rol.value == "assistant" and m.media_url.startswith("/uploads/")
    assert m.contenido == "Antes y después" and (m.extra or {}).get("origen") == "n8n"


def test_por_whatsapp_se_sube_al_proveedor_y_se_manda(monkeypatch):
    import asyncio

    from app.providers.whatsapp import ycloud
    from app.services.n8n_tools import llamar_herramienta

    url_publica(monkeypatch)
    n8n_falso(monkeypatch, (200, {"archivos": [_FOTO]}), archivo())
    llamadas = {}

    class _WhatsApp:
        async def upload_media(self, contenido, mime, nombre):
            llamadas["subida"] = (len(contenido), mime, nombre)
            return "media_123"

        async def send_media(self, telefono, media_id, kind, caption=None, filename=None):
            llamadas["envio"] = (telefono, media_id, kind, caption)
            return "wamid.ABC"

    monkeypatch.setattr(ycloud, "get_whatsapp_provider", lambda: _WhatsApp())

    async def _run():
        conv_id, ficha_id = await conversacion("whatsapp")
        try:
            salida = json.loads(await llamar_herramienta(
                herramienta(nombre="foto_tratamiento"), {}, {"conversation_id": str(conv_id), "canal": "whatsapp"}
            ))
            hilo = await mensajes_de(conv_id)
        finally:
            await borrar_conversacion(conv_id, ficha_id)
        return salida, hilo

    salida, hilo = asyncio.run(_run())

    assert salida["archivos"][0]["estado"] == "enviado", salida
    assert llamadas["subida"][1] == "image/jpeg"
    assert llamadas["envio"][1:] == ("media_123", "image", "Antes y después")
    m = [x for x in hilo if x.media_type][0]
    assert (m.extra or {}).get("provider_message_id") == "wamid.ABC"


def test_por_messenger_va_por_zernio_con_enlace_firmado(monkeypatch):
    import asyncio

    from app.services import zernio
    from app.services.n8n_tools import llamar_herramienta

    url_publica(monkeypatch)
    n8n_falso(monkeypatch, (200, {"archivos": [_FOTO]}), archivo())
    enviado = {}

    class _Zernio:
        async def send_message(self, conversation_id, account_id, text, **kw):
            enviado.update(kw, conversation_id=conversation_id, text=text)

            class _Envio:
                message_id = "mid.1"
                warnings: list = []

            return _Envio()

    async def _destino(conv):
        return ("zconv_1", "acc_1")

    async def _cliente():
        return _Zernio()

    async def _si(conv):
        return True

    monkeypatch.setattr(zernio, "destino_de_conversacion", _destino)
    monkeypatch.setattr(zernio, "cliente", _cliente)
    monkeypatch.setattr(zernio, "conversacion_va_por_zernio", _si)

    async def _run():
        conv_id, ficha_id = await conversacion("messenger")
        try:
            return json.loads(await llamar_herramienta(
                herramienta(nombre="foto_tratamiento"), {}, {"conversation_id": str(conv_id), "canal": "messenger"}
            ))
        finally:
            await borrar_conversacion(conv_id, ficha_id)

    salida = asyncio.run(_run())
    assert salida["archivos"][0]["estado"] == "enviado", salida
    assert enviado["attachment_type"] == "image"
    assert "/api/v1/media/p/" in enviado["attachment_url"], "Zernio no recibe un enlace que pueda bajar"
    assert enviado["text"] == "Antes y después"
