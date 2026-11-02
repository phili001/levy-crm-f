"""En Entrenamiento, el archivo de una herramienta de n8n queda en la sugerencia.

Entrenamiento es «Sofía sugiere, no envía». Un archivo que saliera directo al
paciente se saltaría la revisión. Queda como sugerencia con el archivo, y sale
cuando una persona le da Enviar, aunque no lleve texto.
"""
from __future__ import annotations

import json

import pytest

from tests._n8n import (
    admin,
    archivo,
    borrar_conversacion,
    cliente,
    conversacion,
    db_disponible,
    herramienta,
    limpiar,
    mensajes_de,
    n8n_falso,
    url_publica,
)

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


def test_queda_en_la_sugerencia_y_sale_al_enviarla(monkeypatch):
    import asyncio

    from app.core import events
    from app.services.n8n_tools import llamar_herramienta

    url_publica(monkeypatch)
    n8n_falso(monkeypatch, (200, {"archivos": [{"tipo": "imagen", "url": "https://archivos.ejemplo.mx/f.jpg"}]}),
              archivo())
    publicados = []

    async def _publicar(canal, tipo, datos):
        publicados.append((tipo, datos))

    monkeypatch.setattr(events.event_bus, "publish", _publicar)

    async def _run():
        conv_id, ficha_id = await conversacion("web")
        cab, admin_id = await admin()
        try:
            salida = json.loads(await llamar_herramienta(
                herramienta(nombre="foto_tratamiento"), {},
                {"conversation_id": str(conv_id), "canal": "web", "is_training": True},
            ))
            assert salida["archivos"][0]["estado"] == "en la sugerencia", salida
            assert not [d for t, d in publicados if t == "message.out"], "en Entrenamiento salió al paciente"

            sugerencia = [m for m in await mensajes_de(conv_id) if m.media_type][0]
            assert (sugerencia.extra or {}).get("is_draft") is True
            assert (sugerencia.extra or {}).get("training") is True

            async with cliente() as c:
                r = await c.post(
                    f"/api/v1/conversations/{conv_id}/drafts/{sugerencia.id}/send",
                    headers=cab,
                    json={"text": ""},
                )
            assert r.status_code == 200, r.text
            al_widget = [d for t, d in publicados if t == "message.out"]
            assert al_widget and al_widget[0]["media"]["tipo"] == "image", "al darle Enviar no salió el archivo"

            enviada = [m for m in await mensajes_de(conv_id) if m.id == sugerencia.id][0]
            assert (enviada.extra or {}).get("draft_sent") is True
        finally:
            await borrar_conversacion(conv_id, ficha_id)
            await limpiar(admin_id)

    asyncio.run(_run())
