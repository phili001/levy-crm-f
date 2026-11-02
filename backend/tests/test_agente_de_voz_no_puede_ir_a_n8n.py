"""El motor n8n se configura desde el panel, y solo para agentes de texto.

Las llamadas van por un WebSocket en tiempo real con Retell: un flujo de n8n no
contesta a esa velocidad. Un agente de voz con motor n8n se rechaza (422), y la
base de datos tampoco lo deja (CHECK de la migración 0064). Uno de texto sí: el
CRM genera su secreto, lo enmascara en el listado y «Probar» llama al flujo con
`prueba: true` y con `api.base`, como un turno real.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from tests._n8n import admin, cliente, db_disponible, limpiar

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


def _agente(kind: str, **campos) -> dict:
    datos = {"name": f"Prueba motor {uuid.uuid4().hex[:6]}", "kind": kind, "prompt_system": "Eres Sofía.",
             "is_active": False}
    datos.update(campos)
    return datos


def test_voz_no_texto_si_con_secreto_y_prueba(monkeypatch):
    import asyncio

    from sqlalchemy import delete

    from app.db.session import db_session
    from app.models.agent import Agent
    from app.services import n8n_motor

    async def _publica(url):
        return (url.startswith("https://"), "La URL debe empezar por https://")

    monkeypatch.setattr("app.core.url_guard.validate_public_https_url", _publica)
    monkeypatch.setattr(n8n_motor, "validate_public_https_url", _publica)
    recibidos = []

    async def _post(url, secreto, cuerpo, espera):
        recibidos.append((secreto, cuerpo))
        return httpx.Response(200, json={"respuesta": "Hola, soy Sofía desde n8n"})

    monkeypatch.setattr(n8n_motor, "_post", _post)

    async def _run():
        cab, admin_id = await admin()
        creados = []
        try:
            async with cliente() as c:
                r = await c.post("/api/v1/admin/agents", headers=cab,
                                 json=_agente("voice", motor="n8n", n8n_url="https://n8n.ejemplo.mx/webhook/sofia"))
                assert r.status_code == 422 and "voz" in r.text, r.text

                r = await c.post("/api/v1/admin/agents", headers=cab,
                                 json=_agente("text", motor="n8n", n8n_url="http://n8n.ejemplo.mx/webhook/sofia"))
                assert r.status_code == 422 and "URL del webhook de n8n" in r.text

                r = await c.post("/api/v1/admin/agents", headers=cab,
                                 json=_agente("text", motor="n8n", n8n_url="https://n8n.ejemplo.mx/webhook/sofia"))
                assert r.status_code == 200, r.text
                agente = r.json()
                creados.append(uuid.UUID(agente["id"]))
                assert agente["motor"] == "n8n" and agente["n8n_secreto_enmascarado"].startswith("•")

                r = await c.get(f"/api/v1/admin/agents/{agente['id']}/n8n/secreto", headers=cab)
                secreto = r.json()["secreto"]
                assert len(secreto) >= 32 and secreto[-4:] == agente["n8n_secreto_enmascarado"][-4:]

                r = await c.post(f"/api/v1/admin/agents/{agente['id']}/n8n/probar", headers=cab,
                                 json={"texto": "¿Abren el sábado?"})
                assert r.status_code == 200 and r.json()["ok"], r.text
                assert recibidos[-1][0] == secreto and recibidos[-1][1]["prueba"] is True
                assert recibidos[-1][1]["mensajes"][0]["texto"] == "¿Abren el sábado?"
                # Como un turno real: sin `api.base`, la herramienta que busca en el
                # conocimiento no tiene a dónde llamar y n8n corta el flujo.
                assert recibidos[-1][1]["api"]["base"].endswith("/api/v1/agent-api")

                r = await c.post(f"/api/v1/admin/agents/{agente['id']}/n8n/regenerar-secreto", headers=cab)
                assert r.status_code == 200
                r = await c.get(f"/api/v1/admin/agents/{agente['id']}/n8n/secreto", headers=cab)
                assert r.json()["secreto"] != secreto

                # Volver al CRM conserva la URL (para regresar a n8n sin reescribirla).
                cuerpo = _agente("text", motor="crm", name=agente["name"])
                r = await c.patch(f"/api/v1/admin/agents/{agente['id']}", headers=cab, json=cuerpo)
                assert r.json()["motor"] == "crm" and r.json()["n8n_url"]
        finally:
            async with db_session() as db:
                for aid in creados:
                    await db.execute(delete(Agent).where(Agent.id == aid))
                await db.commit()
            await limpiar(admin_id)

    asyncio.run(_run())


def test_la_base_tampoco_deja_un_agente_de_voz_en_n8n():
    import asyncio

    from sqlalchemy.exc import IntegrityError

    from app.db.session import db_session
    from app.models.agent import Agent

    async def _run():
        async with db_session() as db:
            db.add(Agent(name="voz-n8n", kind="voice", prompt_system="x", motor="n8n", is_active=False))
            with pytest.raises(IntegrityError):
                await db.commit()

    asyncio.run(_run())
