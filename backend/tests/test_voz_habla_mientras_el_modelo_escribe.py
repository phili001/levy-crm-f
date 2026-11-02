"""Por teléfono, Sofía empieza a hablar con las primeras palabras del modelo.

V-3 (2026-09-24) no pasó: el CRM esperaba la respuesta ENTERA del modelo
antes de mandarle nada a Retell, y hacía la moderación en serie antes de
llamarlo. Ahora el texto sale en streaming y la moderación corre en paralelo.
Lo que no puede romperse al hacerlo:

  - Nada del modelo se dice antes del visto bueno de la moderación.
  - La marca de fin de llamada no se dice nunca, aunque llegue partida.
  - El relleno sale en su sitio y la respuesta final no repite lo ya dicho.
  - El streaming real de OpenAI (texto y herramientas troceadas) da la misma
    respuesta que sin streaming, con sus tokens contados.
  - Si el modelo ya habló y falla, no se repite la respuesta con el respaldo.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.api.voice import END_CALL_MARKER, _Emisor
from tests.mocks import llm_mock
from tests.test_simuladores_proveedores import _Servidor


class _Retell:
    """Lo que le llega a Retell en un turno."""

    def __init__(self) -> None:
        self.enviados: list[dict] = []

    async def send(self, payload: dict) -> None:
        self.enviados.append(payload)

    @property
    def dicho(self) -> str:
        return "".join(m["content"] for m in self.enviados)


def _hablar(emisor: _Emisor, trozos: list[str]) -> None:
    async def correr():
        for t in trozos:
            await emisor.texto_del_modelo(t)

    asyncio.run(correr())


def test_nada_sale_antes_de_la_moderacion_y_luego_sale_en_orden():
    retell = _Retell()
    emisor = _Emisor(retell.send, 3)
    _hablar(emisor, ["La limpieza ", "anda alrededor ", "de 800 pesos."])
    assert retell.enviados == [], "habló antes del visto bueno de la moderación"
    asyncio.run(emisor.abrir())
    assert retell.dicho == "La limpieza anda alrededor de 800 pesos."
    assert all(m["content_complete"] is False and m["response_id"] == 3 for m in retell.enviados)
    assert emisor.resto("La limpieza anda alrededor de 800 pesos.") == ""


def test_la_marca_de_fin_de_llamada_no_se_dice_aunque_llegue_partida():
    retell = _Retell()
    emisor = _Emisor(retell.send, 1)
    asyncio.run(emisor.abrir())
    mitad = len(END_CALL_MARKER) // 2
    _hablar(
        emisor,
        ["Gracias por llamar, ", "que te vaya bien.\n", END_CALL_MARKER[:mitad], END_CALL_MARKER[mitad:]],
    )
    assert END_CALL_MARKER[:3].lower() not in retell.dicho.lower(), retell.dicho
    assert retell.dicho == "Gracias por llamar, que te vaya bien.\n"
    # La respuesta final ya viene sin la marca: no queda nada por decir.
    assert emisor.resto("Gracias por llamar, que te vaya bien.") == ""


def test_un_corchete_normal_no_se_queda_retenido():
    retell = _Retell()
    emisor = _Emisor(retell.send, 1)
    asyncio.run(emisor.abrir())
    _hablar(emisor, ["El precio [aproximado] es de 500 pesos."])
    assert retell.dicho == "El precio [aproximado] es de 500 pesos."


def test_el_relleno_sale_en_su_sitio_y_la_final_no_repite():
    retell = _Retell()
    emisor = _Emisor(retell.send, 5)

    async def turno():
        await emisor.abrir()
        await emisor.texto_del_modelo("Claro.")  # texto antes de la herramienta
        await emisor.decir("Déjame revisar la agenda. ")  # relleno
        await emisor.texto_del_modelo("Tengo el martes ")
        await emisor.texto_del_modelo("a las diez.")

    asyncio.run(turno())
    assert retell.dicho == "Claro.Déjame revisar la agenda. Tengo el martes a las diez."
    # Lo que se guarda es la respuesta de la última vuelta; ya salió entera.
    assert emisor.resto("Tengo el martes a las diez.") == ""


def test_si_la_respuesta_final_es_otra_cosa_no_se_mezcla():
    """Un error a media respuesta: lo ya dicho no se repite; el mensaje de
    error solo se dice si no había salido nada."""
    retell = _Retell()
    emisor = _Emisor(retell.send, 1)
    asyncio.run(emisor.abrir())
    assert emisor.resto("Disculpa, he tenido un problema.") == "Disculpa, he tenido un problema."
    _hablar(emisor, ["Te cuento que "])
    assert emisor.resto("Disculpa, he tenido un problema.") == ""


# ---------------------------------------------------------------- streaming real
@pytest.fixture
def llm(monkeypatch):
    from app.providers.llm import openai_client

    monkeypatch.setattr(openai_client, "record_llm_call", AsyncMock())
    with _Servidor(llm_mock.crear_app()) as servidor:
        proveedor = openai_client.OpenAIProvider(
            explicit_api_key="mock-key",
            explicit_base_url=f"{servidor.url}/v1",
            default_model="mock-1",
            label="simulador",
        )
        yield proveedor, servidor, openai_client


def _mensajes(texto: str):
    from app.providers.llm.base import LLMMessage

    return [LLMMessage(role="system", content="Eres Sofía."), LLMMessage(role="user", content=texto)]


async def test_el_streaming_da_la_misma_respuesta_que_sin_streaming(llm):
    proveedor, _, modulo = llm
    trozos: list[str] = []

    async def recibir(t: str) -> None:
        trozos.append(t)

    entera = await proveedor.complete(_mensajes("¿Cuánto cuesta una limpieza?"))
    en_trozos = await proveedor.complete(_mensajes("¿Cuánto cuesta una limpieza?"), on_delta=recibir)
    assert len(trozos) > 1, "llegó de una vez: no hubo streaming"
    assert "".join(trozos) == en_trozos.content == entera.content
    uso = modulo.record_llm_call.await_args.kwargs
    assert uso["has_usage"] and uso["prompt_tokens"] > 0, "el tope de gasto dejaría de contar"


async def test_las_herramientas_llegan_enteras_aunque_vengan_troceadas(llm):
    from app.providers.llm.base import LLMToolSchema

    proveedor, _, _ = llm
    herramienta = LLMToolSchema(
        name="derivar_humano", description="Pasa con una persona",
        parameters={"type": "object", "properties": {"motivo": {"type": "string"}}},
    )
    trozos: list[str] = []

    async def recibir(t: str) -> None:
        trozos.append(t)

    r = await proveedor.complete(
        _mensajes("Quiero hablar con un humano"), tools=[herramienta], on_delta=recibir
    )
    [llamada] = r.tool_calls
    assert llamada.name == "derivar_humano"
    assert llamada.arguments["motivo"] == "El cliente pide hablar con una persona"
    assert r.finish_reason == "tool_calls"


async def test_si_el_modelo_ya_hablo_y_falla_no_se_repite_con_el_respaldo():
    from app.providers.llm.base import LLMCompletion, LLMProvider
    from app.providers.llm.openai_client import FallbackLLMProvider

    class _HablaYSeCae(LLMProvider):
        async def complete(self, *a, on_delta=None, **k):
            await on_delta("Te cuento que ")
            raise ConnectionError("se cortó el stream")

    class _Respaldo(LLMProvider):
        llamado = False

        async def complete(self, *a, on_delta=None, **k):
            _Respaldo.llamado = True
            return LLMCompletion(content="otra respuesta", tool_calls=[])

    dicho: list[str] = []

    async def recibir(t: str) -> None:
        dicho.append(t)

    with pytest.raises(ConnectionError):
        await FallbackLLMProvider(_HablaYSeCae(), _Respaldo()).complete(
            _mensajes("hola"), on_delta=recibir
        )
    assert _Respaldo.llamado is False, "repetiría la respuesta con otra voz"
    assert dicho == ["Te cuento que "]


async def test_si_falla_antes_de_hablar_si_entra_el_respaldo():
    from app.providers.llm.base import LLMCompletion, LLMProvider
    from app.providers.llm.openai_client import FallbackLLMProvider

    class _SeCae(LLMProvider):
        async def complete(self, *a, on_delta=None, **k):
            raise ConnectionError("429")

    class _Respaldo(LLMProvider):
        async def complete(self, *a, on_delta=None, **k):
            await on_delta("Hola, ")
            await on_delta("te ayudo.")
            return LLMCompletion(content="Hola, te ayudo.", tool_calls=[])

    dicho: list[str] = []

    async def recibir(t: str) -> None:
        dicho.append(t)

    fb = FallbackLLMProvider(_SeCae(), _Respaldo())
    fb.secondary.is_configured = AsyncMock(return_value=True)
    r = await fb.complete(_mensajes("hola"), on_delta=recibir)
    assert r.content == "Hola, te ayudo." and "".join(dicho) == "Hola, te ayudo."


async def test_el_nombre_de_la_herramienta_avisa_antes_de_tener_los_argumentos(llm):
    """El relleno sale con el primer trozo de la herramienta, no al final."""
    from app.providers.llm.base import LLMToolSchema

    proveedor, _, _ = llm
    herramienta = LLMToolSchema(
        name="derivar_humano", description="Pasa con una persona",
        parameters={"type": "object", "properties": {"motivo": {"type": "string"}}},
    )
    orden: list[str] = []

    async def empieza(nombre: str) -> None:
        orden.append(f"empieza:{nombre}")

    r = await proveedor.complete(
        _mensajes("Quiero hablar con un humano"), tools=[herramienta], on_tool_start=empieza
    )
    orden.append("respuesta completa")
    assert orden == ["empieza:derivar_humano", "respuesta completa"]
    assert r.tool_calls[0].arguments["motivo"], "el aviso no puede comerse los argumentos"


def test_con_los_dos_avisos_el_relleno_suena_una_sola_vez(monkeypatch):
    from app.api import voice
    from tests.test_voice_gating import _FakeWebSocket, _ws_env

    _ws_env(monkeypatch, [])

    async def _turno(call_id, text, transcript, **kw):
        await kw["emisor"].abrir()
        await kw["on_tool_start"]("consultar_disponibilidad")  # llega el nombre
        await kw["on_tool_call"]([type("T", (), {"name": "consultar_disponibilidad"})()])
        return voice.TurnResult("Tengo el martes a las diez.")

    monkeypatch.setattr(voice, "_run_retell_turn", _turno)
    ws = _FakeWebSocket(
        [{"interaction_type": "response_required", "response_id": 4,
          "transcript": [{"role": "user", "content": "¿hay hueco?"}]}]
    )
    asyncio.run(voice.retell_llm_ws(ws, "call_temprano"))
    del_turno = [m for m in ws.sent if m.get("response_id") == 4]
    assert [m["content"].strip() for m in del_turno] == [
        voice.VOICE_TEXTS["filler_agenda"], "Tengo el martes a las diez."
    ]
