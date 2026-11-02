"""Orchestrator del agente con tool use.

Bucle: LLM → tool_calls → ejecutar tools → LLM (con results) → respuesta final.
Máximo N iteraciones para evitar loops infinitos.
"""
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.agents.tools import ALL_TOOLS
from app.agents.tools.registry import Tool
from app.core.logging import get_logger
from app.core.trace_context import (
    reset_agent_id,
    reset_conversation_id,
    set_agent_id,
    set_conversation_id,
)
from app.providers.llm import get_llm_provider, resolve_llm_provider
from app.providers.llm.base import LLMMessage, LLMToolCall
from app.services.flow_events import publish_agent_step
from app.services.trace_logger import (
    TraceLevel,
    log_router_decision,
    log_tool_invocation,
)

logger = get_logger(__name__)

MAX_ITERATIONS = 5


async def run_agent(
    system_prompt: str,
    history: list[LLMMessage],
    user_message: str,
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    context: dict[str, Any] | None = None,
    tools_enabled: list[str] | None = None,
    llm_provider_id: "uuid.UUID | None" = None,
    fallback_provider_id: "uuid.UUID | None" = None,
    fallback_model: "str | None" = None,
    on_tool_call: "Callable[[list[LLMToolCall]], Awaitable[None]] | None" = None,
    on_text_delta: "Callable[[str], Awaitable[None]] | None" = None,
    on_tool_start: "Callable[[str], Awaitable[None]] | None" = None,
) -> str:
    """Devuelve la respuesta final del agente (texto).

    `on_tool_call`: se llama ANTES de ejecutar cada tanda de herramientas, con
    las llamadas que pidió el modelo. La usa la voz para decir una frase de
    relleno ("déjame revisar la agenda") mientras la herramienta trabaja: en una
    llamada, dos segundos de silencio suenan a que se cortó. Un fallo del
    aviso se registra y se sigue; nunca tumba la respuesta.

    `on_text_delta`: recibe el texto según lo genera el modelo (streaming). La
    voz lo usa para que Retell empiece a hablar con las primeras palabras. El
    valor devuelto sigue siendo la respuesta entera.

    `on_tool_start`: aviso con el nombre de la herramienta en cuanto el modelo
    empieza a pedirla (solo con streaming), antes que `on_tool_call`.

    `tools_enabled`: lista blanca de nombres de tool que el agente puede usar.
    None = SIN CONFIGURAR → todas las tools registradas (legacy). Lista vacía =
    NINGUNA (un agente que solo conversa). Son dos cosas distintas: ver
    `_resolve_allowed_tools`.

    Si se pasa una lista, SOLO se ofrecen al LLM esas tools y `_run_tool` rechaza
    cualquier llamada a una tool fuera de la lista (defensa en profundidad:
    aunque el modelo alucine un nombre, no se ejecuta). Esto es lo que hace que
    el agente de voz no pueda derivar a humano: `derivar_humano` no entra en su
    lista.
    """
    llm = await resolve_llm_provider(llm_provider_id, fallback_provider_id, fallback_model)
    disponibles = await _herramientas_disponibles()
    allowed = _resolve_allowed_tools(tools_enabled, disponibles)
    tool_schemas = [t.schema for name, t in disponibles.items() if name in allowed]

    messages: list[LLMMessage] = [LLMMessage(role="system", content=system_prompt)]
    messages.extend(history)
    messages.append(LLMMessage(role="user", content=user_message))

    ctx = context or {}
    # Un id por turno: dos llamadas iguales a una herramienta de n8n en el mismo
    # turno llevan la misma `llamada_id` y n8n puede no repetirla.
    ctx.setdefault("turno_id", str(uuid.uuid4()))
    # Setea el conversation_id para que los módulos profundos (LLM client,
    # tools, KB) puedan asociar sus eventos de trace sin recibir el id por
    # parámetro. Se resetea al salir.
    conv_id = ctx.get("conversation_id")
    token = set_conversation_id(conv_id) if conv_id else None
    # Atribución del consumo: el id del Agente que atiende (si lo hay) para que
    # el openai_client lo registre en cada llamada. Se resetea al salir.
    agent_id = ctx.get("agent_id")
    agent_token = set_agent_id(agent_id) if agent_id else None

    try:
        for iteration in range(MAX_ITERATIONS):
            # Solo se pasan si se usan: un proveedor de pruebas (o uno viejo) que
            # no los conozca sigue funcionando igual que antes.
            avisos: dict[str, Any] = {}
            if on_text_delta is not None:
                avisos["on_delta"] = on_text_delta
            if on_tool_start is not None:
                avisos["on_tool_start"] = on_tool_start
            completion = await llm.complete(
                messages=messages,
                tools=tool_schemas,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                **avisos,
            )

            if completion.tool_calls:
                messages.append(
                    LLMMessage(role="assistant", content=completion.content, tool_calls=completion.tool_calls)
                )
                if on_tool_call is not None:
                    try:
                        await on_tool_call(completion.tool_calls)
                    except Exception as e:  # noqa: BLE001 — el aviso es accesorio
                        logger.warning("agent.on_tool_call_failed", error=str(e))
                for tc in completion.tool_calls:
                    tool_result = await _run_tool(tc, ctx, allowed, disponibles)
                    messages.append(
                        LLMMessage(role="tool", tool_call_id=tc.id, content=tool_result, name=tc.name)
                    )
                continue

            return (completion.content or "").strip()

        logger.warning("agent.max_iterations_reached")
        await log_router_decision(
            decision="max_iterations_reached",
            reason=f"Agente excedió {MAX_ITERATIONS} iteraciones sin respuesta final",
            level=TraceLevel.warn,
        )
        return "Necesito un momento para procesar esto. Si es urgente, dímelo y aviso al equipo."
    finally:
        if token is not None:
            reset_conversation_id(token)
        if agent_token is not None:
            reset_agent_id(agent_token)


async def _herramientas_disponibles() -> dict[str, Tool]:
    """Las del código más las de n8n que estén activas (Fase 11).

    Las de n8n se leen en cada turno: un cambio en Conexiones → n8n vale desde
    el mensaje siguiente. Si no se pueden leer, Sofía sigue con las suyas: una
    herramienta extra caída no puede dejar a nadie sin respuesta. Una de n8n con
    el nombre de una del CRM no entra (el panel ya no deja guardarla así).
    """
    disponibles: dict[str, Tool] = dict(ALL_TOOLS)
    try:
        from app.services.n8n_tools import como_herramienta, herramientas_activas

        for nombre, fila in (await herramientas_activas()).items():
            if nombre in disponibles:
                logger.warning("n8n.tool.nombre_reservado", herramienta=nombre)
                continue
            disponibles[nombre] = como_herramienta(fila)
    except Exception as e:  # noqa: BLE001
        logger.warning("n8n.tools.no_se_pudieron_leer", error=type(e).__name__)
    return disponibles


def _resolve_allowed_tools(
    tools_enabled: list[str] | None, disponibles: dict[str, Tool] | None = None
) -> set[str]:
    """Lista blanca efectiva de nombres de tool para esta ejecución.

    Distingue DOS casos que antes se confundían:

      - `None` = SIN CONFIGURAR (agente legacy, campo nunca tocado) → todas las
        tools registradas.
      - `[]` = NINGUNA, elegido a propósito → conjunto vacío.

    Antes el check era `if not tools_enabled`, y una lista vacía es falsy: quien
    desmarcaba todas las casillas en el panel para tener un agente que solo
    conversara acababa con uno que agendaba citas en Google y derivaba
    conversaciones. Exactamente lo contrario de lo que pidió.

    Una lista se intersecta con las tools que existen de verdad (un nombre que
    ya no exista simplemente se ignora, no rompe).
    """
    disponibles = ALL_TOOLS if disponibles is None else disponibles
    if tools_enabled is None:
        return set(disponibles.keys())
    return {name for name in tools_enabled if name in disponibles}


async def _run_tool(
    call: LLMToolCall,
    ctx: dict[str, Any],
    allowed: set[str],
    disponibles: dict[str, Tool] | None = None,
) -> str:
    # Defensa en profundidad: aunque el schema solo ofreció las tools
    # permitidas, si el modelo alucina/invoca una fuera de la lista blanca, no
    # la ejecutamos. Crítico para garantizar que el agente de voz no derive a
    # humano por mucho que el LLM intente llamar a `derivar_humano`.
    if call.name not in allowed:
        await log_tool_invocation(
            tool_name=call.name,
            args=call.arguments,
            error=f"Tool no permitida para este agente: {call.name}",
        )
        return json.dumps({"error": f"Tool no disponible: {call.name}"})

    tool = (ALL_TOOLS if disponibles is None else disponibles).get(call.name)
    if not tool:
        await log_tool_invocation(
            tool_name=call.name,
            args=call.arguments,
            error=f"Tool desconocida: {call.name}",
        )
        return json.dumps({"error": f"Tool desconocida: {call.name}"})

    # Los datos marcados como sensibles en una herramienta de n8n no van a la traza.
    args_traza = tool.redactar(call.arguments) if tool.redactar else call.arguments
    started = time.perf_counter()
    # Flujo en vivo: ilumina el nodo de la tool que se está ejecutando.
    await publish_agent_step(
        "tool",
        conversation_id=ctx.get("conversation_id"),
        agent_id=ctx.get("agent_id"),
        tool=call.name,
    )
    try:
        result = await tool.handler(call.arguments, ctx)
        result_str = result if isinstance(result, str) else json.dumps(result)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await log_tool_invocation(
            tool_name=call.name,
            args=args_traza,
            result_preview=result_str,
            latency_ms=elapsed_ms,
        )
        return result_str
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.error("tool.error", tool=call.name, error=str(e))
        await log_tool_invocation(
            tool_name=call.name,
            args=args_traza,
            error=str(e),
            latency_ms=elapsed_ms,
        )
        return json.dumps({"error": str(e)})
