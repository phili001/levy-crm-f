"""El servidor MCP vive dentro de la API, en /mcp (v1.2.0).

Antes era un contenedor aparte que Coolify nunca desplegaba, así que la tarjeta
«Conectar por MCP» del panel apuntaba a una URL muerta. Ahora lo sirve `app` y
llega con una actualización normal. Lo que se comprueba aquí:

  - Se llega por /mcp y por /mcp/ (sin redirección) y salen las 17 herramientas.
  - Un `Host` con el dominio de la instalación no se rechaza (la protección
    contra «DNS rebinding» del paquete devolvía 421).
  - Sin token ninguna herramienta llama a nada; con un identificador que no es
    UUID, tampoco.
  - Cada herramienta pasa por la agent-api con el token de quien llama: con el
    ámbito contesta, sin él da el 403 de siempre, y lo que escribe queda
    auditado con el nombre del token.

Las pruebas que entran en la app necesitan Postgres y Redis (el límite por IP
de la API abre Redis en cada petición); sin ellos se saltan.
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tests._n8n import db_disponible

DOMINIO = "crm.ejemplo.mx"
CABECERAS_MCP = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
INICIO = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "prueba", "version": "1"},
    },
}
LAS_17 = {
    "monitor_overview",
    "get_llm_costs",
    "get_channels_status",
    "get_health",
    "list_interactions",
    "get_interaction_metadata",
    "list_kb_documents",
    "get_kb_document",
    "search_kb",
    "create_kb_document",
    "update_kb_document",
    "list_agents",
    "update_agent_prompt",
    "get_conversation_messages",
    "send_conversation_message",
    "handoff_conversation",
    "update_contact",
}

con_infra = pytest.mark.skipif(
    not db_disponible(), reason="Sin Postgres/Redis (se ejecuta en CI y en local con la infra)"
)


# ---------------------------------------------------------------------------
# Ayudas
# ---------------------------------------------------------------------------


def _ctx(cabeceras: dict[str, str] | None = None):
    """Lo mínimo del Context de FastMCP que lee una herramienta: la petición."""
    peticion = SimpleNamespace(headers=httpx.Headers(cabeceras or {}))
    return SimpleNamespace(request_context=SimpleNamespace(request=peticion))


class _SinLlamadas:
    """Sustituye al cliente de la agent-api y apunta si alguien lo pidió."""

    def __init__(self) -> None:
        self.veces = 0

    def __call__(self):
        self.veces += 1
        raise AssertionError("El MCP no debía llamar a la agent-api")


def _al_mismo_proceso(monkeypatch) -> list[httpx.Request]:
    """La llamada interna a la agent-api entra en la app por ASGI, sin red."""
    from app import mcp_server
    from app.main import app

    vistas: list[httpx.Request] = []

    async def _apunta(request: httpx.Request) -> None:
        vistas.append(request)

    def _cliente():
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000/api/v1/agent-api",
            event_hooks={"request": [_apunta]},
        )

    monkeypatch.setattr(mcp_server, "_cliente", _cliente)
    return vistas


@asynccontextmanager
async def _sesion(token: str | None = None):
    """Una sesión del cliente MCP oficial contra la app, por ASGI."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    from app.main import app
    from app.mcp_server import mcp_en_marcha

    def _fabrica(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            headers=headers,
            timeout=timeout,
            auth=auth,
        )

    cabeceras = {"Authorization": f"Bearer {token}"} if token else None
    async with (
        mcp_en_marcha(),
        streamablehttp_client(
            f"http://{DOMINIO}/mcp", headers=cabeceras, httpx_client_factory=_fabrica
        ) as (lectura, escritura, _),
        ClientSession(lectura, escritura) as sesion,
    ):
        await sesion.initialize()
        yield sesion


def _json(resultado) -> dict:
    return json.loads(resultado.content[0].text)


# ---------------------------------------------------------------------------
# Sin infraestructura: la lógica de las herramientas
# ---------------------------------------------------------------------------


async def test_sin_token_ninguna_herramienta_llama_a_la_agent_api(monkeypatch):
    from app import mcp_server

    vigia = _SinLlamadas()
    monkeypatch.setattr(mcp_server, "_cliente", vigia)
    un_id = str(uuid.uuid4())
    llamadas = [
        mcp_server.monitor_overview(_ctx()),
        mcp_server.get_llm_costs(_ctx()),
        mcp_server.get_channels_status(_ctx()),
        mcp_server.get_health(_ctx()),
        mcp_server.list_interactions(_ctx()),
        mcp_server.get_interaction_metadata(un_id, _ctx()),
        mcp_server.list_kb_documents(_ctx()),
        mcp_server.get_kb_document(un_id, _ctx()),
        mcp_server.search_kb("horario", _ctx()),
        mcp_server.create_kb_document("Nota", "Texto", _ctx()),
        mcp_server.update_kb_document(un_id, "Texto", _ctx()),
        mcp_server.list_agents(_ctx()),
        mcp_server.update_agent_prompt(un_id, "Eres amable.", _ctx()),
        mcp_server.get_conversation_messages(un_id, _ctx()),
        mcp_server.send_conversation_message(un_id, _ctx(), texto="Hola"),
        mcp_server.handoff_conversation(un_id, _ctx()),
        mcp_server.update_contact(un_id, _ctx(), nombre="Ana"),
    ]
    assert len(llamadas) == len(LAS_17)
    for llamada in llamadas:
        respuesta = json.loads(await llamada)
        assert respuesta["error"] is True
        assert "Authorization: Bearer" in respuesta["detail"]
        assert "status" not in respuesta
    # Una cabecera que no es Bearer (o un Bearer vacío) cuenta como sin token.
    for cabecera in ("Basic dXN1YXJpbzpjbGF2ZQ==", "Bearer ", "agt_suelto"):
        respuesta = json.loads(await mcp_server.get_health(_ctx({"Authorization": cabecera})))
        assert respuesta["error"] is True
    assert vigia.veces == 0


async def test_un_identificador_que_no_es_uuid_no_sale_hacia_otra_ruta_de_la_api(monkeypatch):
    from app import mcp_server

    vigia = _SinLlamadas()
    monkeypatch.setattr(mcp_server, "_cliente", vigia)
    con_token = _ctx({"Authorization": "Bearer agt_cualquiera"})
    for llamada in (
        mcp_server.get_kb_document("../../admin/users", con_token),
        mcp_server.update_agent_prompt("../../../health", "x", con_token),
        mcp_server.update_contact("1 OR 1=1", con_token, nombre="x"),
    ):
        respuesta = json.loads(await llamada)
        assert respuesta["error"] is True and respuesta["status"] == 422
    assert vigia.veces == 0


async def test_el_error_de_red_no_ensena_la_url_interna(monkeypatch):
    from app import mcp_server

    def _caido():
        def _rechaza(request):
            raise httpx.ConnectError("[Errno 111] Connection refused 127.0.0.1:8000")

        return httpx.AsyncClient(
            transport=httpx.MockTransport(_rechaza), base_url="http://127.0.0.1:8000/api/v1"
        )

    monkeypatch.setattr(mcp_server, "_cliente", _caido)
    respuesta = json.loads(
        await mcp_server.get_health(_ctx({"Authorization": "Bearer agt_cualquiera"}))
    )
    assert respuesta["error"] is True
    assert "127.0.0.1" not in respuesta["detail"]


# ---------------------------------------------------------------------------
# Contra la app: el transporte
# ---------------------------------------------------------------------------


@con_infra
async def test_el_cliente_mcp_ve_las_17_herramientas_en_mcp():
    async with _sesion("agt_da_igual_para_listar") as sesion:
        herramientas = await sesion.list_tools()
    assert {h.name for h in herramientas.tools} == LAS_17


@con_infra
async def test_mcp_con_y_sin_barra_final_contesta_sin_redirigir_y_con_el_dominio_real():
    from app.main import app
    from app.mcp_server import mcp_en_marcha

    async with (
        mcp_en_marcha(),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=f"https://{DOMINIO}"
        ) as cliente,
    ):
        for ruta in ("/mcp", "/mcp/"):
            r = await cliente.post(
                ruta,
                headers={
                    **CABECERAS_MCP,
                    "Host": DOMINIO,
                    "Origin": f"https://{DOMINIO}",
                    "X-Forwarded-Proto": "https",
                },
                json=INICIO,
            )
            # Ni 307 (redirección de la barra) ni 421 (Host no permitido).
            assert r.status_code == 200, (ruta, r.status_code, r.text)
            assert r.json()["result"]["serverInfo"]["name"] == "crm"

        # El GET (stream SSE del servidor) no se abre: 405, como prevé el
        # protocolo, en vez de una conexión colgada.
        r = await cliente.get("/mcp", headers={"Accept": "text/event-stream"})
        assert r.status_code == 405

        # Un formulario de otra web no cuela: el POST tiene que ser JSON.
        r = await cliente.post(
            "/mcp",
            headers={"Accept": CABECERAS_MCP["Accept"], "Content-Type": "text/plain"},
            content=json.dumps(INICIO),
        )
        assert r.status_code == 400


@con_infra
async def test_un_cuerpo_de_mas_de_4_mb_se_corta_antes_de_leerlo():
    from app.main import app
    from app.mcp_server import mcp_en_marcha

    enorme = json.dumps({**INICIO, "relleno": "x" * (5 * 1024 * 1024)})
    async with (
        mcp_en_marcha(),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=f"https://{DOMINIO}"
        ) as cliente,
    ):
        r = await cliente.post("/mcp", headers=CABECERAS_MCP, content=enorme)
    assert r.status_code == 413


@con_infra
async def test_sin_arrancar_el_gestor_mcp_contesta_503_y_no_un_500():
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"https://{DOMINIO}"
    ) as cliente:
        r = await cliente.post("/mcp", headers=CABECERAS_MCP, json=INICIO)
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Contra la app: las herramientas pasan por la agent-api
# ---------------------------------------------------------------------------


async def _fake_embed_status(textos):
    from app.providers.embeddings import EmbeddingResult

    return EmbeddingResult(vectors=[[0.01] * 1536 for _ in textos], ok=True)


async def _admin_con_tokens(*tokens: tuple[str, list[str]]):
    """Un administrador y sus tokens de agente; devuelve (user_id, [creados])."""
    import app.api.admin as admin
    from app.db.session import db_session
    from app.models.user import User

    async with db_session() as db:
        u = User(
            email=f"mcp-{uuid.uuid4().hex[:8]}@ejemplo.com",
            password_hash="x",
            nombre="Admin MCP",
            role="admin",
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        creados = [
            await admin.create_agent_token(
                admin.AgentTokenCreateIn(name=nombre, scopes=ambitos), db=db, me=u
            )
            for nombre, ambitos in tokens
        ]
    return u.id, creados


async def _limpiar(user_id, tokens, doc_ids) -> None:
    from sqlalchemy import delete

    from app.db.session import db_session
    from app.models.agent_token import AgentToken
    from app.models.audit_log import AuditLog
    from app.models.document import Document
    from app.models.user import User

    async with db_session() as db:
        for doc_id in doc_ids:
            await db.execute(delete(AuditLog).where(AuditLog.entity_id == doc_id))
            await db.execute(delete(Document).where(Document.id == doc_id))
        await db.execute(delete(AgentToken).where(AgentToken.id.in_([t.id for t in tokens])))
        await db.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


@con_infra
async def test_con_kb_read_las_herramientas_contestan_y_sin_el_ambito_dan_403(monkeypatch):
    vistas = _al_mismo_proceso(monkeypatch)
    user_id, (lector, monitor) = await _admin_con_tokens(
        ("Lector MCP", ["kb:read"]), ("Monitor MCP", ["monitor:read"])
    )
    try:
        sin_vectores = AsyncMock(return_value=[[0.0] * 1536])
        with patch("app.providers.embeddings.embed_texts", new=sin_vectores):
            async with _sesion(lector.token) as sesion:
                lista = _json(await sesion.call_tool("list_kb_documents", {}))
                assert "error" not in lista, lista
                busqueda = _json(
                    await sesion.call_tool("search_kb", {"query": "horario", "top_k": 3})
                )
                assert "results" in busqueda, busqueda

                # Sin monitor:read, el 403 de la agent-api llega tal cual.
                negado = _json(await sesion.call_tool("get_health", {}))
                assert negado["error"] is True and negado["status"] == 403
                assert "monitor:read" in negado["detail"]

        async with _sesion(monitor.token) as sesion:
            salud = _json(await sesion.call_tool("get_health", {}))
            assert "error" not in salud and "db" in salud, salud
            negado = _json(await sesion.call_tool("list_kb_documents", {}))
            assert negado["error"] is True and negado["status"] == 403

        async with _sesion("agt_inventado_que_no_existe") as sesion:
            falso = _json(await sesion.call_tool("list_kb_documents", {}))
            assert falso["error"] is True and falso["status"] == 401

        # Cada llamada interna llevó el token de quien llamaba, y solo a la
        # agent-api.
        assert vistas
        for r in vistas:
            assert r.url.path.startswith("/api/v1/agent-api/")
            assert r.headers["authorization"].startswith("Bearer ")
    finally:
        await _limpiar(user_id, [lector, monitor], [])


@con_infra
async def test_lo_que_escribe_el_mcp_queda_auditado_con_el_nombre_del_token(monkeypatch):
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.audit_log import AuditLog

    _al_mismo_proceso(monkeypatch)
    user_id, (escritor,) = await _admin_con_tokens(("Claude de la clínica", ["kb:write"]))
    doc_ids: list[uuid.UUID] = []
    try:
        with patch(
            "app.services.kb_indexer.embed_texts_with_status",
            new=AsyncMock(side_effect=_fake_embed_status),
        ):
            async with _sesion(escritor.token) as sesion:
                creado = _json(
                    await sesion.call_tool(
                        "create_kb_document",
                        {"titulo": "Horario MCP", "contenido": "Abrimos de 9 a 20 h."},
                    )
                )
        assert "error" not in creado, creado
        doc_ids.append(uuid.UUID(creado["document_id"]))

        async with db_session() as db:
            filas = (
                await db.execute(select(AuditLog).where(AuditLog.entity_id == doc_ids[0]))
            ).scalars().all()
        assert [f.action for f in filas] == ["kb.note.created"]
        assert filas[0].after["agent_token"] == "Claude de la clínica"
        assert filas[0].after["agent_token_id"] == str(escritor.id)
    finally:
        await _limpiar(user_id, [escritor], doc_ids)
