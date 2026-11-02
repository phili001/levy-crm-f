"""En horario, Sofía pasa la llamada a recepción (Fase 8.2, US-20 AC1).

Cuando quien llama pide hablar con una persona, `derivar_humano` decide: en
horario y con teléfono de recepción en el canal, deja el número en el contexto
de la llamada y el turno se lo manda a Retell como `transfer_number`; Retell
pasa la llamada en cuanto Sofía termina de decir su frase. La conversación NO
pasa a `humano` (la atiende una persona en directo: no hay nada que devolver).

La mayoría corre sin base de datos; las del turno completo, con ella.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from app.agents.tools import human_handoff, schedule_config
from app.agents.tools.schedule_config import build_config
from tests.test_voice_gating import (
    _cleanup,
    _FakeWebSocket,
    _patch_common,
    _runtime,
    _turn,
    _ws_env,
    pytestmark_db,
)

RECEPCION = "+529981112233"
# Lunes a viernes de 8 a 14 y de 16 a 20, en Cancún, con Navidad cerrada.
HORARIO = build_config(
    "America/Cancun", "1,2,3,4,5", "08:00-14:00,16:00-20:00", "2026-12-25", "30", "0"
)
MARTES_10 = datetime(2026, 9, 29, 10, 0, tzinfo=HORARIO.tz)


def test_se_atiende_en_los_tramos_de_los_dias_laborables():
    assert HORARIO.abierto(MARTES_10)
    assert not HORARIO.abierto(MARTES_10.replace(hour=15)), "entre los dos tramos"
    assert not HORARIO.abierto(MARTES_10.replace(hour=20)), "a la hora de cerrar ya no"
    assert not HORARIO.abierto(datetime(2026, 10, 3, 10, 0, tzinfo=HORARIO.tz)), "sábado"
    assert not HORARIO.abierto(datetime(2026, 12, 25, 10, 0, tzinfo=HORARIO.tz)), "festivo"


def test_la_hora_se_lleva_a_la_zona_del_negocio():
    # 15:00 UTC son las 10:00 en Cancún: abierto, aunque en UTC "sean las 3".
    assert HORARIO.abierto(datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc))
    # 03:00 UTC del martes son las 22:00 del lunes en Cancún: cerrado.
    assert not HORARIO.abierto(datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc))


class _RedisNx:
    def __init__(self) -> None:
        self.claves: set[str] = set()

    async def set(self, clave, _valor, ex=None, nx=False):
        if nx and clave in self.claves:
            return None
        self.claves.add(clave)
        return True


def _entorno(monkeypatch, *, recepcion=RECEPCION, ahora=MARTES_10):
    """La herramienta sin BD: canal, horario, reloj y Redis de mentira."""
    from app.services import runtime_config

    async def _canal(_tipo):
        return {"transfer_number": recepcion} if recepcion else {}

    async def _horario():
        return HORARIO

    async def _nada(*_a, **_k):
        return None

    derivaciones: list[str] = []

    async def _derivar(conversation_id, motivo, _args):
        derivaciones.append(motivo)
        return {"derivado": True}

    redis = _RedisNx()
    monkeypatch.setattr(runtime_config, "get_channel_config", _canal)
    monkeypatch.setattr(schedule_config, "get_schedule_config", _horario)
    monkeypatch.setattr(schedule_config, "now_in", lambda _tz: ahora)
    monkeypatch.setattr(human_handoff, "get_redis", lambda: redis)
    monkeypatch.setattr(human_handoff, "publish_agent_step", _nada)
    monkeypatch.setattr(human_handoff, "push_runtime_log", _nada)
    monkeypatch.setattr(human_handoff, "_derivar_a_humano", _derivar)
    return derivaciones


def _derivar_en_llamada(voz: dict, conv_id: str | None = None) -> dict:
    ctx = {"conversation_id": conv_id or str(uuid.uuid4()), "channel": "retell_voice", "voice": voz}
    return json.loads(
        asyncio.run(human_handoff.derivar_humano({"motivo": "quiere hablar con recepción"}, ctx))
    )


def test_en_horario_la_herramienta_pasa_la_llamada_sin_derivar(monkeypatch):
    derivaciones = _entorno(monkeypatch)
    voz: dict = {"transfer_number": None}
    assert _derivar_en_llamada(voz) == {"accion": "transferir"}
    assert voz["transfer_number"] == RECEPCION
    assert derivaciones == [], "la atiende una persona en directo: no queda en «Para hacer»"


def test_si_la_transferencia_no_entro_la_segunda_vez_se_toma_el_recado(monkeypatch):
    """Una llamada de prueba desde el navegador no se puede transferir: sin este
    freno, Sofía repetiría «te paso con recepción» en bucle."""
    derivaciones = _entorno(monkeypatch)
    conv = str(uuid.uuid4())
    assert _derivar_en_llamada({"transfer_number": None}, conv)["accion"] == "transferir"
    otra: dict = {"transfer_number": None}
    respuesta = _derivar_en_llamada(otra, conv)
    assert respuesta["accion"] == "recado"
    assert "ya se intentó" in respuesta["por_que"]
    assert otra["transfer_number"] is None
    assert len(derivaciones) == 1


def test_fuera_de_la_llamada_no_se_intenta_transferir(monkeypatch):
    """Sin el contexto de la llamada (por ejemplo, un turno de texto marcado mal)
    la herramienta no puede pasar nada: recado."""
    _entorno(monkeypatch)
    ctx = {"conversation_id": str(uuid.uuid4()), "channel": "retell_voice"}
    respuesta = json.loads(
        asyncio.run(human_handoff.derivar_humano({"motivo": "persona"}, ctx))
    )
    assert respuesta["accion"] == "recado"


def test_con_derivar_humano_las_instrucciones_explican_las_dos_salidas():
    from app.api.voice import VOICE_END_CALL_INSTRUCTIONS, instrucciones_de_voz

    texto = instrucciones_de_voz(["consultar_kb", "derivar_humano"])
    assert texto != VOICE_END_CALL_INSTRUCTIONS
    assert '"transferir"' in texto and '"recado"' in texto
    assert "no puedes pasar la llamada" not in texto.lower()
    assert instrucciones_de_voz(None) == texto, "sin configurar = todas las herramientas"


def test_antes_de_pasar_la_llamada_sofia_pregunta_para_que_es():
    """V-5 en vivo: Sofía pasaba la llamada en cuanto oía «con recepción», sin
    preguntar el motivo. El ejemplo de la herramienta ('usuario pide hablar con
    persona') le decía que eso ya era un motivo."""
    from app.agents.tools.registry import ALL_TOOLS
    from app.api.voice import instrucciones_de_voz

    texto = instrucciones_de_voz(["derivar_humano"])
    assert "no es un motivo" in texto and "espera su" in texto
    motivo = ALL_TOOLS["derivar_humano"].schema.parameters["properties"]["motivo"]
    assert "hablar con persona" not in motivo["description"]


def test_el_websocket_manda_el_numero_para_que_retell_pase_la_llamada(monkeypatch):
    from app.api import voice

    _ws_env(monkeypatch, [])

    async def _turno(call_id, text, transcript, **kw):
        await kw["emisor"].abrir()
        return voice.TurnResult("Te paso con recepción, un momento.", transfer_number=RECEPCION)

    monkeypatch.setattr(voice, "_run_retell_turn", _turno)
    ws = _FakeWebSocket(
        [
            {
                "interaction_type": "response_required",
                "response_id": 4,
                "transcript": [{"role": "user", "content": "¿me pasas con recepción?"}],
            }
        ]
    )
    asyncio.run(voice.retell_llm_ws(ws, "call_transferencia"))
    final = [m for m in ws.sent if m.get("response_id") == 4 and m.get("content_complete")]
    assert final and final[-1]["transfer_number"] == RECEPCION
    assert final[-1]["end_call"] is False, "colgar ganaría a transferir en Retell"


def test_un_turno_normal_no_manda_transfer_number(monkeypatch):
    from app.api import voice

    _ws_env(monkeypatch, [])
    ws = _FakeWebSocket(
        [
            {
                "interaction_type": "response_required",
                "response_id": 5,
                "transcript": [{"role": "user", "content": "hola"}],
            }
        ]
    )
    asyncio.run(voice.retell_llm_ws(ws, "call_normal"))
    final = [m for m in ws.sent if m.get("response_id") == 5 and m.get("content_complete")]
    assert final and "transfer_number" not in final[-1]


@pytestmark_db
def test_el_turno_convierte_la_decision_de_la_herramienta_en_transferencia(monkeypatch):
    """De punta a punta en el turno: la herramienta deja el número en el
    contexto y el turno lo devuelve, aunque el modelo pusiera además la marca
    de colgar. La respuesta queda guardada con a dónde se pasó."""
    from sqlalchemy import select

    from app.api import voice
    from app.db.session import db_session
    from app.models.conversation import Conversation, ConversationStatus
    from app.models.message import Message, MessageRole

    _patch_common(monkeypatch, agent=_runtime(tools_enabled=["derivar_humano"]))
    prompts: list[str] = []

    async def _agente(**kwargs):
        prompts.append(kwargs["system_prompt"])
        kwargs["context"]["voice"]["transfer_number"] = RECEPCION
        return f"Te paso con recepción, un momento. {voice.END_CALL_MARKER}"

    monkeypatch.setattr(voice, "run_agent", _agente)
    call_id = f"call_{uuid.uuid4().hex[:10]}"
    result = _turn(call_id, "¿me comunicas con recepción?")
    try:
        assert result.transfer_number == RECEPCION
        assert result.end_call is False
        assert voice.END_CALL_MARKER not in result.text
        assert '"transferir"' in prompts[0], "el prompt explica cómo pasar la llamada"

        async def _leer():
            async with db_session() as db:
                conv = (
                    await db.execute(select(Conversation).where(Conversation.session_id == call_id))
                ).scalar_one()
                ultimo = (
                    await db.execute(
                        select(Message)
                        .where(Message.conversation_id == conv.id, Message.rol == MessageRole.assistant)
                    )
                ).scalar_one()
                return conv, ultimo

        conv, mensaje = asyncio.run(_leer())
        assert conv.status == ConversationStatus.bot, "no pasa a humano: la atiende recepción"
        assert mensaje.extra["transferida_a"] == RECEPCION
    finally:
        asyncio.run(_cleanup([], [f"voice:{call_id}"]))


@pytestmark_db
def test_en_entrenamiento_no_se_pasa_la_llamada(monkeypatch):
    from app.api import voice

    _patch_common(monkeypatch, training=True, agent=_runtime(tools_enabled=["derivar_humano"]))

    async def _agente(**kwargs):
        kwargs["context"]["voice"]["transfer_number"] = RECEPCION
        return "Te paso con recepción."

    monkeypatch.setattr(voice, "run_agent", _agente)
    call_id = f"call_{uuid.uuid4().hex[:10]}"
    result = _turn(call_id, "¿me pasas con alguien?")
    try:
        assert result.transfer_number is None, "en Entrenamiento el bot no le habla a nadie"
        assert result.end_call is True
    finally:
        asyncio.run(_cleanup([], [f"voice:{call_id}"]))
