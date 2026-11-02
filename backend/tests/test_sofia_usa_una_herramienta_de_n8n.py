"""De punta a punta por el orquestador: Sofía pide una herramienta de n8n y usa lo que contesta.

El modelo es de mentira (pide `buscar_pedido` y después contesta con lo que le
llegó), n8n también; lo de verdad es el camino: la herramienta sale de la base,
se ofrece al modelo, se llama con el secreto y el resultado vuelve a Sofía.
"""
from __future__ import annotations

import json

import pytest

from app.providers.llm.base import LLMCompletion, LLMToolCall
from tests._n8n import SECRETO, db_disponible, limpiar, n8n_falso, url_publica

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


class _ModeloQuePide:
    def __init__(self) -> None:
        self.herramientas_ofrecidas: list[str] = []
        self.resultado: str | None = None

    async def complete(self, *, messages, tools, **_):
        self.herramientas_ofrecidas = [t.name for t in tools]
        if self.resultado is None:
            de_herramienta = [m for m in messages if m.role == "tool"]
            if de_herramienta:
                self.resultado = de_herramienta[-1].content
                return LLMCompletion(content="Tu pedido 123 va en camino.", tool_calls=[])
            return LLMCompletion(
                content=None,
                tool_calls=[LLMToolCall(id="c1", name="pedido_e2e", arguments={"numero": "123"})],
            )
        return LLMCompletion(content="Tu pedido 123 va en camino.", tool_calls=[])


def test_la_pide_la_llama_y_contesta_con_lo_que_devolvio(monkeypatch):
    import asyncio

    from app.agents import orchestrator
    from app.db.session import db_session
    from app.models.n8n_tool import N8nTool

    url_publica(monkeypatch)
    n8n = n8n_falso(monkeypatch, (200, {"resultado": {"estado": "en camino", "llega": "mañana"}}))
    modelo = _ModeloQuePide()

    async def _resolver(*_a, **_k):
        return modelo

    monkeypatch.setattr(orchestrator, "resolve_llm_provider", _resolver)

    async def _run():
        async with db_session() as db:
            db.add(N8nTool(
                nombre="pedido_e2e", descripcion="Busca un pedido por número.",
                url="https://n8n.ejemplo.mx/webhook/pedidos", secreto=SECRETO, accion="lectura",
                parametros=[{"clave": "numero", "tipo": "texto", "descripcion": "Número", "obligatorio": True}],
            ))
            await db.commit()
        try:
            respuesta = await orchestrator.run_agent(
                system_prompt="Eres Sofía.",
                history=[],
                user_message="¿Dónde está mi pedido 123?",
                context={"canal": "web"},
                tools_enabled=None,
            )
        finally:
            await limpiar(None, "pedido_e2e")
        return respuesta

    respuesta = asyncio.run(_run())

    assert "pedido_e2e" in modelo.herramientas_ofrecidas, "a Sofía no se le ofreció la herramienta de n8n"
    assert n8n.cuerpo()["args"] == {"numero": "123"}
    assert json.loads(modelo.resultado)["resultado"] == {"estado": "en camino", "llega": "mañana"}
    assert respuesta == "Tu pedido 123 va en camino."
