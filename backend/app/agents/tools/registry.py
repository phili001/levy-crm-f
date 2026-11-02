"""Registro de tools disponibles para el agente.

Cada tool registra:
- schema (nombre, descripción, params JSON Schema) → para el LLM
- handler (función async que recibe args dict y context dict) → ejecución
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.providers.llm.base import LLMToolSchema

ToolHandler = Callable[[dict[str, Any], dict[str, Any]], Awaitable[Any]]


@dataclass
class Tool:
    schema: LLMToolSchema
    handler: ToolHandler
    # Cómo se apuntan los argumentos en la traza. Las herramientas de n8n tapan
    # los datos que el administrador marcó como sensibles; las del CRM, tal cual.
    redactar: Callable[[dict[str, Any]], dict[str, Any]] | None = None


ALL_TOOLS: dict[str, Tool] = {}


def register_tool(tool: Tool) -> None:
    ALL_TOOLS[tool.schema.name] = tool
