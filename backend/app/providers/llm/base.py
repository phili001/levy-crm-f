"""Interfaz abstracta para LLM con tool use.

El agente trabaja en términos de esta interfaz, no del SDK concreto.
"""
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal


class LLMNotConfiguredError(RuntimeError):
    """No hay credenciales para llamar al modelo.

    Es un FALLO, no una respuesta. Antes el cliente devolvía como contenido el
    texto "(LLM no configurado. Configura las claves en el panel admin.)", el
    respaldo lo propagaba y el orquestador lo entregaba tal cual: un mensaje
    interno del panel llegaba por WhatsApp al cliente final del negocio. Y pasa
    el día 1 de toda instalación, mientras se están configurando las claves.

    Como excepción entra por el mismo camino que cualquier caída del modelo:
    `services/conversation.py` la captura y deriva la conversación a una
    persona, sin reintentar y sin contestar nada raro.
    """


@dataclass
class LLMToolSchema:
    """Descripción de una herramienta que el modelo puede invocar."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass
class LLMToolCall:
    """Una invocación de tool por parte del modelo."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMMessage:
    """Mensaje en el contexto. Compatible con todos los roles habituales."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    tool_call_id: str | None = None  # solo para role=tool
    name: str | None = None


@dataclass
class LLMCompletion:
    """Salida del modelo: texto + posibles tool_calls."""

    content: str | None
    tool_calls: list[LLMToolCall]
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


# Recibe cada trozo de texto de la respuesta según lo genera el modelo. Lo usa
# la voz para que Retell empiece a hablar con las primeras palabras en vez de
# esperar la respuesta entera (V-3: esa espera era más de la mitad de la
# latencia de cada turno).
OnDelta = Callable[[str], Awaitable[None]]
# Recibe el NOMBRE de una herramienta en cuanto el modelo empieza a pedirla,
# antes de que termine de escribir sus argumentos. Lo usa la voz para decir el
# relleno ("déjame revisar la agenda") unos cientos de milisegundos antes.
OnToolStart = Callable[[str], Awaitable[None]]


class LLMProvider(ABC):
    async def is_configured(self) -> bool:
        """¿Tiene este proveedor lo necesario para poder llamar al modelo?

        Lo usa el failover para no intentar el respaldo cuando no hay respaldo
        que valga (y propagar el error del primario, que es lo que deriva la
        conversación a una persona). Por defecto True: un proveedor que no
        distinga el caso siempre puede intentarlo — el error real saldrá al
        llamar. Cada cliente concreto lo afina según lo que le haga falta
        (clave, y en las pasarelas también base_url y modelo).
        """
        return True

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[LLMToolSchema] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        source: str = "agent",
        on_delta: OnDelta | None = None,
        on_tool_start: OnToolStart | None = None,
    ) -> LLMCompletion:
        """Respuesta completa del modelo.

        `on_delta`: si se pasa, cada trozo de texto se entrega según llega
        (streaming). Un cliente que no sepa hacer streaming lo cumple igual
        llamándolo UNA vez con el texto entero al terminar: más lento, pero
        nunca se pierde nada. El valor devuelto es el mismo en los dos casos.

        `on_tool_start`: aviso temprano con el nombre de cada herramienta que el
        modelo empieza a pedir. Solo lo da quien hace streaming; es un adelanto,
        nunca sustituye a `tool_calls` de la respuesta.
        """
        ...
