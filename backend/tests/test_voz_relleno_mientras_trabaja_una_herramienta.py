"""Por teléfono, Sofía no se queda callada mientras consulta la agenda (V-3).

Consultar la disponibilidad o agendar son dos o tres segundos de herramienta.
En un chat no se nota; en una llamada, ese silencio hace que quien llama
pregunte "¿hola?" o cuelgue. Retell deja mandar un trozo de respuesta con
`content_complete=false` y encadenar después el resto con el mismo
`response_id`: eso es el relleno.

Lo que tiene que cumplirse:
  - El relleno sale ANTES de ejecutar la herramienta y como trozo incompleto.
  - Una sola vez por turno, aunque el modelo encadene varias herramientas.
  - La frase depende de la herramienta (agenda o genérica) y es configurable.
  - En Entrenamiento no se dice nada, tampoco el relleno.
  - Si mandar el relleno falla, la respuesta sale igual.
"""
from __future__ import annotations

import asyncio
import uuid

from app.providers.llm.base import LLMCompletion, LLMToolCall
from tests.test_voice_gating import (
    _cleanup,
    _FakeWebSocket,
    _patch_common,
    _silence_side_effects,
    _turn,
    _ws_env,
    pytestmark_db,
)


def _llamada(nombre: str) -> LLMToolCall:
    return LLMToolCall(id=f"call_{uuid.uuid4().hex[:6]}", name=nombre, arguments={})


class _LlmConHerramientas:
    """Pide herramientas en las primeras vueltas y luego contesta."""

    def __init__(self, tandas: list[list[LLMToolCall]]) -> None:
        self.tandas = list(tandas)

    async def complete(self, **_):
        if self.tandas:
            return LLMCompletion(content=None, tool_calls=self.tandas.pop(0))
        return LLMCompletion(content="Tengo hueco el martes a las 10.", tool_calls=[])


def _orquestador(monkeypatch, llm, orden: list[str]):
    from app.agents import orchestrator

    async def _resolver(*_a, **_k):
        return llm

    async def _herramienta(call, ctx, allowed, disponibles=None):
        orden.append(f"herramienta:{call.name}")
        return "ok"

    monkeypatch.setattr(orchestrator, "resolve_llm_provider", _resolver)
    monkeypatch.setattr(orchestrator, "_run_tool", _herramienta)
    return orchestrator


def test_el_aviso_llega_antes_de_ejecutar_la_herramienta(monkeypatch):
    orden: list[str] = []
    orq = _orquestador(
        monkeypatch, _LlmConHerramientas([[_llamada("consultar_disponibilidad")]]), orden
    )

    async def _aviso(tool_calls):
        orden.append("aviso:" + ",".join(t.name for t in tool_calls))

    texto = asyncio.run(
        orq.run_agent("prompt", [], "¿tienen hueco?", tools_enabled=[], on_tool_call=_aviso)
    )
    assert orden == ["aviso:consultar_disponibilidad", "herramienta:consultar_disponibilidad"]
    assert texto == "Tengo hueco el martes a las 10."


def test_si_el_aviso_falla_la_respuesta_sale_igual(monkeypatch):
    orden: list[str] = []
    orq = _orquestador(monkeypatch, _LlmConHerramientas([[_llamada("consultar_kb")]]), orden)

    async def _aviso_roto(_tool_calls):
        raise ConnectionError("el socket ya no está")

    texto = asyncio.run(
        orq.run_agent("prompt", [], "hola", tools_enabled=[], on_tool_call=_aviso_roto)
    )
    assert orden == ["herramienta:consultar_kb"]
    assert texto == "Tengo hueco el martes a las 10."


def test_el_relleno_va_incompleto_una_vez_y_luego_la_respuesta_cierra_el_turno(monkeypatch):
    from app.api import voice

    turnos: list[str] = []
    _ws_env(monkeypatch, turnos)

    async def _turno_con_herramientas(call_id, text, transcript, **kw):
        # Como el turno real: la moderación da el visto bueno y el emisor se
        # abre. Luego el modelo encadena dos tandas de herramientas.
        await kw["emisor"].abrir()
        await kw["on_tool_call"]([_llamada("consultar_disponibilidad")])
        await kw["on_tool_call"]([_llamada("agendar_cita")])
        return voice.TurnResult("Listo, te agendé el martes a las 10.")

    monkeypatch.setattr(voice, "_run_retell_turn", _turno_con_herramientas)
    ws = _FakeWebSocket(
        [
            {
                "interaction_type": "response_required",
                "response_id": 7,
                "transcript": [{"role": "user", "content": "quiero cita el martes"}],
            }
        ]
    )
    asyncio.run(voice.retell_llm_ws(ws, "call_relleno"))

    del_turno = [m for m in ws.sent if m.get("response_id") == 7]
    assert [m["content_complete"] for m in del_turno] == [False, True], del_turno
    relleno, final = del_turno
    assert relleno["content"].strip() == voice.VOICE_TEXTS["filler_agenda"]
    assert relleno["end_call"] is False
    assert final["content"] == "Listo, te agendé el martes a las 10."


def test_la_frase_depende_de_la_herramienta_y_se_puede_cambiar(monkeypatch):
    from app.api import voice

    _silence_side_effects(monkeypatch)
    assert asyncio.run(voice._frase_de_relleno([_llamada("agendar_cita")])) == (
        voice.VOICE_TEXTS["filler_agenda"]
    )
    assert asyncio.run(voice._frase_de_relleno([_llamada("consultar_kb")])) == (
        voice.VOICE_TEXTS["filler_default"]
    )

    async def _config(_canal):
        return {"filler_default": "Un momentito, por favor."}

    monkeypatch.setattr(voice, "get_channel_config", _config)
    assert asyncio.run(voice._frase_de_relleno([_llamada("buscar_contacto")])) == (
        "Un momentito, por favor."
    )


def _captura_run_agent(monkeypatch, training: bool) -> list:
    from app.api import voice

    _patch_common(monkeypatch, training=training)
    recibidos: list = []

    async def _run_agent(**kwargs):
        recibidos.append(kwargs)
        # El agente pide una herramienta: el aviso de voz decide si se dice algo.
        await kwargs["on_tool_call"]([_llamada("consultar_disponibilidad")])
        return "respuesta del agente"

    monkeypatch.setattr(voice, "run_agent", _run_agent)
    return recibidos


async def _contacto_de(call_id: str):
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.conversation import Conversation

    async with db_session() as db:
        conv = (
            await db.execute(select(Conversation).where(Conversation.session_id == call_id))
        ).scalar_one()
        return conv.contact_id


@pytestmark_db
def test_en_entrenamiento_no_se_dice_ni_el_relleno(monkeypatch):
    from app.api import voice

    recibidos = _captura_run_agent(monkeypatch, training=True)
    call_id = f"call_{uuid.uuid4().hex[:10]}"
    avisos: list = []

    async def _aviso(tool_calls):
        avisos.append(tool_calls)

    enviados: list = []

    async def _send(payload):
        enviados.append(payload)

    _turn(call_id, "Hola", on_tool_call=_aviso, emisor=voice._Emisor(_send, 1))
    assert avisos == [], "en Entrenamiento quien llama no debe oír el relleno"
    assert recibidos[0]["on_text_delta"] is None, "ni la respuesta en streaming"
    assert enviados == []
    asyncio.run(_cleanup([asyncio.run(_contacto_de(call_id))]))


@pytestmark_db
def test_en_una_llamada_normal_el_relleno_y_el_streaming_llegan_al_agente(monkeypatch):
    from app.api import voice

    recibidos = _captura_run_agent(monkeypatch, training=False)
    call_id = f"call_{uuid.uuid4().hex[:10]}"
    avisos: list = []

    async def _aviso(tool_calls):
        avisos.append(tool_calls)

    emisor = voice._Emisor(lambda _p: asyncio.sleep(0), 1)
    _turn(call_id, "Hola", on_tool_call=_aviso, emisor=emisor)
    assert len(avisos) == 1
    assert recibidos[0]["on_text_delta"] == emisor.texto_del_modelo
    asyncio.run(_cleanup([asyncio.run(_contacto_de(call_id))]))
