"""n8n contesta más tarde por la API y la respuesta queda en el chat del inbox.

Con «Sofía en n8n», un flujo lento contesta 200 sin `respuesta` y manda el
mensaje después por `POST /agent-api/conversations/{id}/messages`. Sale por el
canal como una respuesta de Sofía, queda marcado como de n8n, y una
`idempotency_key` repetida no lo manda dos veces. n8n también puede leer el
hilo, pasar la conversación a una persona y actualizar la ficha.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests._motor_n8n import borrar_token, conversacion, mensajes, token, whatsapp_entrante
from tests._n8n import cliente, db_disponible

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


@pytest.mark.asyncio
async def test_contesta_lee_el_hilo_y_no_repite():
    cab, token_id = await token("conversations:read", "conversations:reply")
    enviar = AsyncMock(return_value="wamid.API")
    try:
        conv, _msg, _clave = await whatsapp_entrante("¿Me confirman la cita?")
        async with cliente() as c:
            with patch("app.services.channel_sender.send_text_to_conversation", enviar):
                cuerpo = {"texto": "Confirmada: mañana a las 10.", "idempotency_key": f"evt-{conv.id}"}
                r = await c.post(f"/api/v1/agent-api/conversations/{conv.id}/messages", headers=cab, json=cuerpo)
                assert r.status_code == 200, r.text
                assert r.json()["enviados"] == 1
                r = await c.post(f"/api/v1/agent-api/conversations/{conv.id}/messages", headers=cab, json=cuerpo)
                assert r.json() == {"repetido": True}
            assert enviar.await_count == 1, "la misma idempotency_key salió dos veces"

            r = await c.get(f"/api/v1/agent-api/conversations/{conv.id}/messages", headers=cab)
            assert r.status_code == 200
            hilo = r.json()["mensajes"]
            assert [m["rol"] for m in hilo] == ["paciente", "sofia"]
            assert hilo[-1]["texto"] == "Confirmada: mañana a las 10."

        de_sofia = [m for m in await mensajes(conv.id) if m.rol.value == "assistant"]
        assert (de_sofia[-1].extra or {}).get("motor") == "n8n"
    finally:
        await borrar_token(token_id)


@pytest.mark.asyncio
async def test_pasar_a_una_persona_y_actualizar_la_ficha():
    from app.db.session import db_session
    from app.models.contact import Contact

    cab, token_id = await token("conversations:reply", "contacts:write")
    try:
        with patch("app.services.channel_sender.send_text_to_conversation", AsyncMock(return_value="wamid.P")):
            conv, _msg, _clave = await whatsapp_entrante()
            async with cliente() as c:
                r = await c.patch(f"/api/v1/agent-api/contacts/{conv.contact_id}", headers=cab,
                                  json={"nombre": "Ana López", "email": "ana@ejemplo.mx", "etapa": "paciente",
                                        "etiquetas_agregar": ["vino-de-n8n"]})
                assert r.status_code == 200, r.text
                assert set(r.json()["cambios"]) >= {"nombre", "email", "etapa", "etiqueta:vino-de-n8n"}

                r = await c.post(f"/api/v1/agent-api/conversations/{conv.id}/handoff", headers=cab,
                                 json={"motivo": "pide hablar con la doctora"})
                assert r.status_code == 200, r.text
        assert (await conversacion(conv.id)).status.value == "humano"
        async with db_session() as db:
            ficha = await db.get(Contact, conv.contact_id)
        assert ficha.nombre == "Ana López" and ficha.estado.value == "cliente"
        assert ficha.telefono.startswith("+52998"), "la API tocó el teléfono, que es la llave"
    finally:
        await borrar_token(token_id)


@pytest.mark.asyncio
async def test_en_entrenamiento_queda_como_sugerencia():
    cab, token_id = await token("conversations:reply")
    enviar = AsyncMock(return_value="wamid.X")
    try:
        conv, _msg, _clave = await whatsapp_entrante()
        async with cliente() as c:
            with patch("app.services.channel_sender.send_text_to_conversation", enviar), \
                    patch("app.services.agent_pause.is_channel_training", AsyncMock(return_value=True)):
                r = await c.post(f"/api/v1/agent-api/conversations/{conv.id}/messages", headers=cab,
                                 json={"texto": "Te propongo el martes."})
        assert r.status_code == 200 and r.json()["sugerencia"] is True, r.text
        enviar.assert_not_awaited()
        borrador = [m for m in await mensajes(conv.id) if (m.extra or {}).get("is_draft")]
        assert borrador and (borrador[0].extra or {}).get("motor") == "n8n"
    finally:
        await borrar_token(token_id)
