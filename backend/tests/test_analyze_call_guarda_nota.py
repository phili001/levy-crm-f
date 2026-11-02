"""Al terminar una llamada, queda un resumen en la ficha del contacto (Fase 8.3).

US-20 AC3: `call_analyzed` → tarea `analyze_call` → el modelo del Agente de Voz
lee lo que se dijo y deja una nota en la ficha (lo que lee quien devuelve la
llamada) y el resumen de la conversación. Nada de cifras inventadas: si el
modelo no devuelve un JSON que encaje, no hay nota.

También el webhook: el recado de la 8.2 no se archiva cuando llega
`call_ended` (antes sí, y desaparecía de «Para hacer»), el resumen se encola
una sola vez aunque Retell reintente, y el de Retell no pisa el nuestro.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.tasks import analyze_call as tarea
from tests.test_voice_gating import _cleanup, pytestmark_db

RESPUESTA = {
    "resumen": "Ana Pérez llamó por dolor en una muela. Quedó con cita el martes a las 10.",
    "siguiente_accion": "Confirmarle la cita por WhatsApp un día antes.",
    "servicio_interes": "Valoración",
    "interes": 8,
    "urgencia": "Urgente",
    "alertas": None,
}


# ---------------------------------------------------------------------------
# Sin base de datos: lo que el modelo devuelve y cómo se escribe la nota
# ---------------------------------------------------------------------------


def test_el_json_se_acepta_aunque_venga_con_texto_alrededor():
    analisis = tarea.interpretar("Aquí va:\n" + json.dumps(RESPUESTA) + "\nListo.")
    assert analisis.interes == 8
    assert analisis.urgencia == "urgente"
    assert analisis.alertas == []


@pytest.mark.parametrize(
    "crudo",
    [
        "no sé qué decir",
        json.dumps({**RESPUESTA, "interes": 0}),
        json.dumps({**RESPUESTA, "resumen": ""}),
        json.dumps({**RESPUESTA, "urgencia": "altísima"}),
    ],
)
def test_si_el_json_no_encaja_no_se_inventa_nada(crudo):
    with pytest.raises(tarea.AnalisisFallido):
        tarea.interpretar(crudo)


def test_lo_que_no_se_sabe_sale_como_hueco_y_no_como_cifra():
    analisis = tarea.interpretar(
        json.dumps({**RESPUESTA, "interes": None, "urgencia": None, "servicio_interes": None})
    )
    nota = tarea.nota_de_la_llamada(analisis, cita=False, duracion_s=200)
    assert "• Interés: —" in nota and "• Urgencia: —" in nota
    assert "• Cita en la agenda: no" in nota
    assert "• Duración: 3 min 20 s" in nota


def test_las_alertas_del_asistente_salen_en_la_nota():
    analisis = tarea.interpretar(json.dumps({**RESPUESTA, "alertas": ["Dio un diagnóstico"]}))
    nota = tarea.nota_de_la_llamada(analisis, cita=True, duracion_s=None)
    assert "⚠️ El asistente se salió de sus reglas:" in nota
    assert "- Dio un diagnóstico" in nota
    assert "Duración" not in nota


def test_la_cita_sale_de_la_traza_y_no_de_lo_que_dijo_el_asistente():
    creada = {"tool_name": "agendar_cita", "result_preview": '{"ok": true, "event_id": "e1"}'}
    fallida = {"tool_name": "agendar_cita", "result_preview": '{"error": "Esa hora ya pasó"}'}
    consulta = {"tool_name": "consultar_disponibilidad", "result_preview": '{"ok": true}'}
    assert tarea._cita_creada([consulta, creada])
    assert not tarea._cita_creada([consulta, fallida])
    assert not tarea._cita_creada([{**creada, "error": "timeout"}])


# ---------------------------------------------------------------------------
# Con base de datos: la tarea entera
# ---------------------------------------------------------------------------


class _Modelo:
    """Proveedor falso: devuelve `crudo` y apunta lo que se le pidió."""

    def __init__(self, crudo: str | Exception):
        self.crudo = crudo
        self.pedidos: list[dict] = []

    async def complete(self, messages, **kw):
        self.pedidos.append({"messages": messages, **kw})
        if isinstance(self.crudo, Exception):
            raise self.crudo
        return SimpleNamespace(content=self.crudo)


def _entorno(monkeypatch, modelo: _Modelo, *, tope: bool = False) -> list[str]:
    """Agente de voz y modelo falsos. Devuelve la lista de avisos del registro."""
    from app.providers import llm
    from app.services import budget, runtime_config, runtime_logs

    agente = SimpleNamespace(
        id=uuid.uuid4(),
        model_name="gpt-4.1-mini",
        llm_provider_id=None,
        fallback_provider_id=None,
        fallback_model=None,
    )

    async def _runtime(_canal):
        return agente

    async def _proveedor(*_a, **_k):
        return modelo

    async def _pausa():
        return tope

    avisos: list[str] = []

    async def _log(**kw):
        avisos.append(kw.get("message", ""))

    monkeypatch.setattr(runtime_config, "get_runtime_for_channel", _runtime)
    monkeypatch.setattr(llm, "resolve_llm_provider", _proveedor)
    monkeypatch.setattr(budget, "is_budget_pause_active", _pausa)
    monkeypatch.setattr(runtime_logs, "push_runtime_log", _log)
    return avisos


async def _llamada(*, turnos: bool = True, cita: bool = False):
    """Una llamada terminada: contacto, conversación, turnos y (si se pide) la
    traza de una cita creada. Devuelve (conv_id, contact_id)."""
    from app.api.voice import _resolve_contact_and_conversation
    from app.db.session import db_session
    from app.models.agent_trace import AgentTraceEvent, TraceEventType, TraceLevel
    from app.models.conversation import Conversation
    from app.models.message import Message, MessageRole

    call_id = f"call_{uuid.uuid4().hex[:10]}"
    conv_id, contact_id = await _resolve_contact_and_conversation(
        call_id, None, "Hola, soy Ana Pérez, me duele una muela", datetime.now(UTC)
    )
    async with db_session() as db:
        if not turnos:
            for m in (await db.execute(
                Message.__table__.select().where(Message.conversation_id == conv_id)
            )).all():
                await db.execute(Message.__table__.delete().where(Message.id == m.id))
        else:
            db.add(Message(
                conversation_id=conv_id, rol=MessageRole.assistant,
                contenido="Te la aparto el martes a las diez de la mañana.",
            ))
        if cita:
            db.add(AgentTraceEvent(
                conversation_id=conv_id,
                event_type=TraceEventType.tool_invocation,
                level=TraceLevel.info,
                payload={"tool_name": "agendar_cita", "result_preview": '{"ok": true}'},
            ))
        conv = await db.get(Conversation, conv_id)
        conv.call_duration_seconds = 95
        conv.resumen = "Summary from Retell"
        await db.commit()
    return conv_id, contact_id


async def _ficha(conv_id, contact_id):
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.contact_activity import ContactActivity
    from app.models.contact_note import ContactNote
    from app.models.conversation import Conversation

    async with db_session() as db:
        notas = (
            await db.execute(select(ContactNote).where(ContactNote.contact_id == contact_id))
        ).scalars().all()
        actividad = (
            await db.execute(
                select(ContactActivity).where(ContactActivity.contact_id == contact_id)
            )
        ).scalars().all()
        conv = await db.get(Conversation, conv_id)
        return [n.texto for n in notas], [(a.tipo, a.actor_user_id, a.meta) for a in actividad], conv


@pytestmark_db
def test_al_terminar_la_llamada_queda_una_nota_en_la_ficha(monkeypatch):
    modelo = _Modelo(json.dumps(RESPUESTA))
    _entorno(monkeypatch, modelo)
    conv_id, contact_id = asyncio.run(_llamada(cita=True))
    try:
        assert asyncio.run(tarea.analizar_llamada(str(conv_id))) == "nota"
        notas, actividad, conv = asyncio.run(_ficha(conv_id, contact_id))

        assert len(notas) == 1
        assert notas[0].startswith("📞 Resumen automático de la llamada")
        assert RESPUESTA["resumen"] in notas[0]
        assert "• Cita en la agenda: sí" in notas[0]
        assert "• Duración: 1 min 35 s" in notas[0]
        assert conv.resumen == RESPUESTA["resumen"], "el nuestro sustituye al de Retell"
        assert ("note_added", None, {"origen": "llamada", "conversation_id": str(conv_id)}) in actividad

        pedido = modelo.pedidos[0]
        assert pedido["source"] == "call_analysis" and pedido["model"] == "gpt-4.1-mini"
        texto = pedido["messages"][1].content
        assert "Persona: Hola, soy Ana Pérez, me duele una muela" in texto
        assert "Asistente: Te la aparto el martes" in texto
        assert "Cita creada en la agenda durante la llamada: sí" in texto
    finally:
        asyncio.run(_cleanup([contact_id]))


@pytestmark_db
def test_si_el_modelo_devuelve_basura_no_hay_nota_y_se_avisa(monkeypatch):
    avisos = _entorno(monkeypatch, _Modelo("Lo siento, no puedo."))
    conv_id, contact_id = asyncio.run(_llamada())
    try:
        assert asyncio.run(tarea.analizar_llamada(str(conv_id))) == "fallo"
        notas, _actividad, conv = asyncio.run(_ficha(conv_id, contact_id))
        assert notas == []
        assert conv.resumen == "Summary from Retell", "sin lectura fiable queda el de reserva"
        assert any("No se pudo resumir la llamada" in a for a in avisos)
    finally:
        asyncio.run(_cleanup([contact_id]))


@pytestmark_db
def test_si_cuelga_durante_el_saludo_no_se_gasta_modelo(monkeypatch):
    modelo = _Modelo(json.dumps(RESPUESTA))
    _entorno(monkeypatch, modelo)
    conv_id, contact_id = asyncio.run(_llamada(turnos=False))
    try:
        assert asyncio.run(tarea.analizar_llamada(str(conv_id))) == "sin_turnos"
        assert modelo.pedidos == []
    finally:
        asyncio.run(_cleanup([contact_id]))


@pytestmark_db
def test_con_el_tope_de_gasto_alcanzado_no_se_resume(monkeypatch):
    modelo = _Modelo(json.dumps(RESPUESTA))
    avisos = _entorno(monkeypatch, modelo, tope=True)
    conv_id, contact_id = asyncio.run(_llamada())
    try:
        assert asyncio.run(tarea.analizar_llamada(str(conv_id))) == "tope"
        assert modelo.pedidos == []
        assert any("tope de gasto" in a for a in avisos)
    finally:
        asyncio.run(_cleanup([contact_id]))


@pytestmark_db
def test_si_el_modelo_no_responde_se_reintenta_y_al_final_se_avisa(monkeypatch):
    avisos = _entorno(monkeypatch, _Modelo(TimeoutError("sin respuesta")))
    conv_id, contact_id = asyncio.run(_llamada())
    try:
        with pytest.raises(tarea.ModeloNoResponde):
            asyncio.run(tarea.analizar_llamada(str(conv_id), reintentar=True))
        assert asyncio.run(tarea.analizar_llamada(str(conv_id))) == "fallo"
        assert any("el modelo no respondió" in a for a in avisos)
    finally:
        asyncio.run(_cleanup([contact_id]))


# ---------------------------------------------------------------------------
# El webhook de Retell
# ---------------------------------------------------------------------------


class _Peticion:
    def __init__(self, payload: dict):
        self._cuerpo = json.dumps(payload).encode()
        self.headers: dict[str, str] = {}

    async def body(self) -> bytes:
        return self._cuerpo


def _webhook(monkeypatch) -> list[str]:
    """Firma aceptada y encolado falso. Devuelve las conversaciones encoladas."""
    from app.api import voice

    class _Retell:
        async def verify_call_webhook_signature(self, _headers, _cuerpo):
            return True

    encoladas: list[str] = []
    monkeypatch.setattr(voice, "get_retell_provider", lambda: _Retell())
    monkeypatch.setattr(tarea.analyze_call, "delay", lambda conv_id: encoladas.append(conv_id))
    return encoladas


async def _evento(evento: str, call_id: str, **call) -> None:
    from app.api.voice import retell_call_webhook

    await retell_call_webhook(_Peticion({"event": evento, "call": {"call_id": call_id, **call}}))


async def _call_id(conv_id) -> str:
    from app.db.session import db_session
    from app.models.conversation import Conversation

    async with db_session() as db:
        return (await db.get(Conversation, conv_id)).session_id


@pytestmark_db
def test_el_recado_no_se_archiva_cuando_llega_el_webhook(monkeypatch):
    """La 8.2 dejaba el recado en «Para hacer» al cerrarse el WebSocket, pero
    el webhook `call_ended` llegaba después y lo archivaba igual."""
    from app.db.session import db_session
    from app.models.conversation import Conversation, ConversationStatus
    from app.services.inbox_pending import conversation_needs_action

    _webhook(monkeypatch)
    conv_id, contact_id = asyncio.run(_llamada())
    try:
        async def _derivar():
            async with db_session() as db:
                conv = await db.get(Conversation, conv_id)
                conv.status = ConversationStatus.humano
                conv.derivada_a_humano_at = datetime.now(UTC)
                await db.commit()

        async def _despues():
            async with db_session() as db:
                conv = await db.get(Conversation, conv_id)
                return conv.archived_at, await conversation_needs_action(db, conv_id)

        asyncio.run(_derivar())
        call_id = asyncio.run(_call_id(conv_id))
        asyncio.run(_evento("call_ended", call_id, duration_ms=60_000))
        archivada, pendiente = asyncio.run(_despues())
        assert archivada is None, "archivarla escondería el recado"
        assert pendiente, "tiene que seguir en «Para hacer»"
    finally:
        asyncio.run(_cleanup([contact_id]))


@pytestmark_db
def test_la_llamada_atendida_por_el_bot_si_se_archiva(monkeypatch):
    from app.db.session import db_session
    from app.models.conversation import Conversation

    _webhook(monkeypatch)
    conv_id, contact_id = asyncio.run(_llamada())
    try:
        async def _archivada():
            async with db_session() as db:
                return (await db.get(Conversation, conv_id)).archived_at

        asyncio.run(_evento("call_ended", asyncio.run(_call_id(conv_id))))
        assert asyncio.run(_archivada()) is not None
    finally:
        asyncio.run(_cleanup([contact_id]))


@pytestmark_db
def test_el_resumen_se_encola_una_vez_aunque_retell_reintente(monkeypatch):
    encoladas = _webhook(monkeypatch)
    conv_id, contact_id = asyncio.run(_llamada())
    try:
        call_id = asyncio.run(_call_id(conv_id))
        asyncio.run(_evento("call_ended", call_id))
        assert encoladas == [], "call_ended no trae el análisis: todavía no"
        for _ in range(3):
            asyncio.run(_evento("call_analyzed", call_id, call_analysis={"call_summary": "x"}))
        assert encoladas == [str(conv_id)]
    finally:
        asyncio.run(_cleanup([contact_id]))


@pytestmark_db
def test_el_resumen_de_retell_no_pisa_el_nuestro(monkeypatch):
    from app.db.session import db_session
    from app.models.conversation import Conversation

    _webhook(monkeypatch)
    conv_id, contact_id = asyncio.run(_llamada())
    try:
        async def _resumen():
            async with db_session() as db:
                return (await db.get(Conversation, conv_id)).resumen

        call_id = asyncio.run(_call_id(conv_id))
        asyncio.run(
            _evento("call_analyzed", call_id, call_analysis={"call_summary": "Otro resumen"})
        )
        assert asyncio.run(_resumen()) == "Summary from Retell", "el que ya había se queda"
    finally:
        asyncio.run(_cleanup([contact_id]))
