"""Sofía ve una herramienta de n8n solo si está activa y el agente la tiene permitida.

Si esto falla, o una herramienta apagada en el panel sigue llamando a n8n, o un
agente con su lista de herramientas cerrada (el de voz, por ejemplo) puede usar
una que nadie le dio.
"""
from __future__ import annotations

import pytest

from tests._n8n import admin, cliente, db_disponible, limpiar

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


def test_activa_y_permitida_si_apagada_o_fuera_de_la_lista_no():
    import asyncio

    from app.agents.orchestrator import _herramientas_disponibles, _resolve_allowed_tools
    from app.db.session import db_session
    from app.models.n8n_tool import N8nTool

    async def _run():
        cab, admin_id = await admin()
        try:
            async with db_session() as db:
                db.add_all([
                    N8nTool(nombre="pedido_activo", descripcion="Busca un pedido.", url="https://n8n.ejemplo.mx/a",
                            secreto="s1", accion="lectura", parametros=[]),
                    N8nTool(nombre="pedido_apagado", descripcion="Apagada.", url="https://n8n.ejemplo.mx/b",
                            secreto="s2", accion="lectura", parametros=[], activa=False),
                ])
                await db.commit()

            disponibles = await _herramientas_disponibles()
            assert "pedido_activo" in disponibles
            assert "pedido_apagado" not in disponibles, "una herramienta apagada sigue ofreciéndose"
            assert "consultar_kb" in disponibles, "las del CRM tienen que seguir ahí"

            assert "pedido_activo" in _resolve_allowed_tools(None, disponibles)
            assert _resolve_allowed_tools([], disponibles) == set()
            assert "pedido_activo" not in _resolve_allowed_tools(["consultar_kb"], disponibles), (
                "un agente con su lista cerrada recibe una herramienta que nadie le dio"
            )
            assert "pedido_activo" in _resolve_allowed_tools(["consultar_kb", "pedido_activo"], disponibles)

            # En Agentes → herramientas sale con su origen, para activarla.
            async with cliente() as c:
                r = await c.get("/api/v1/admin/tools", headers=cab)
            assert r.status_code == 200, r.text
            por_nombre = {t["name"]: t for t in r.json()}
            assert por_nombre["pedido_activo"]["origen"] == "n8n"
            assert por_nombre["consultar_kb"]["origen"] == "crm"
            assert "pedido_apagado" not in por_nombre
        finally:
            await limpiar(admin_id, "pedido_activo", "pedido_apagado")

    asyncio.run(_run())


async def test_si_no_se_pueden_leer_sofia_sigue_con_las_suyas(monkeypatch):
    from app.agents import orchestrator
    from app.services import n8n_tools

    async def _caida():
        raise RuntimeError("BD caída")

    monkeypatch.setattr(n8n_tools, "herramientas_activas", _caida)
    disponibles = await orchestrator._herramientas_disponibles()
    assert "consultar_kb" in disponibles and "derivar_humano" in disponibles
