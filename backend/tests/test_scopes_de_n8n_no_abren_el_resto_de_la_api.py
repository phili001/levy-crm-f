"""Los ámbitos de n8n solo abren lo suyo, y los de siempre no abren lo de n8n.

`conversations:read`, `conversations:reply` y `contacts:write` son los únicos
que tocan contenido. Un token de métricas o de KB no puede leer un hilo ni
contestar; uno de n8n que solo lee no puede contestar ni tocar la ficha, y no
abre las métricas ni los prompts.
"""
from __future__ import annotations

import uuid

import pytest

from tests._motor_n8n import borrar_token, token
from tests._n8n import cliente, db_disponible

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


@pytest.mark.asyncio
async def test_cada_ambito_abre_solo_lo_suyo():
    conv_id, contacto_id = uuid.uuid4(), uuid.uuid4()
    de_siempre, id1 = await token("monitor:read", "kb:read", "prompts:write")
    solo_leer, id2 = await token("conversations:read")
    try:
        async with cliente() as c:
            for cab in (de_siempre,):
                assert (await c.get(f"/api/v1/agent-api/conversations/{conv_id}/messages", headers=cab)).status_code == 403
                assert (await c.post(f"/api/v1/agent-api/conversations/{conv_id}/messages", headers=cab,
                                     json={"texto": "x"})).status_code == 403
                assert (await c.patch(f"/api/v1/agent-api/contacts/{contacto_id}", headers=cab,
                                      json={"nombre": "x"})).status_code == 403

            assert (await c.get(f"/api/v1/agent-api/conversations/{conv_id}/messages",
                                headers=solo_leer)).status_code == 404  # pasa el ámbito; no existe
            assert (await c.post(f"/api/v1/agent-api/conversations/{conv_id}/messages", headers=solo_leer,
                                 json={"texto": "x"})).status_code == 403
            assert (await c.post(f"/api/v1/agent-api/conversations/{conv_id}/handoff", headers=solo_leer,
                                 json={})).status_code == 403
            assert (await c.get("/api/v1/agent-api/monitor/overview", headers=solo_leer)).status_code == 403
            assert (await c.get(f"/api/v1/agent-api/conversations/{conv_id}/messages")).status_code == 401
    finally:
        await borrar_token(id1)
        await borrar_token(id2)
