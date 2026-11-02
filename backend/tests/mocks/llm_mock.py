"""Simulador de un proveedor de LLM compatible con OpenAI.

Se da de alta en el panel (Agentes → Proveedores) como cualquier pasarela:
`base_url = http://<host>:<puerto>/v1` y una clave cualquiera. El CRM lo trata
con el `OpenAIProvider` de siempre, así que la prueba recorre el mismo camino
que con un modelo de verdad; lo único que cambia es quién contesta.

Respuestas deterministas, decididas por el último mensaje:
  - Tras el resultado de una herramienta → un texto de cierre.
  - El cliente pide un humano ("humano", "persona", "asesor") y el agente
    tiene la herramienta `derivar_humano` → la llama.
  - Le piden una plantilla de WhatsApp («Generar con IA» de Canales →
    Plantillas) → una plantilla de recordatorio en JSON, como el modelo real.
  - La instrucción de seguimiento de «Retomar a quien no agendó» → un mensaje
    corto para retomar, o NO_ESCRIBIR si el paciente solo pidió la dirección.
  - Cualquier otra cosa → "Respuesta de prueba: <eco>".

Una clave que termina en `-invalid` recibe un 401 con la forma de OpenAI.
`PUT /_mock/fallos {"chat": 401}` hace fallar TODAS las llamadas desde ese
momento con ese código: es la "clave que deja de valer a media conversación"
de las pruebas de fallos (F-1). `{"chat": null}` lo apaga.

Nunca es un respaldo en marcha: solo contesta si alguien lo configuró a mano.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from tests.mocks._comun import clave_invalida, nuevo_id

PREFIJO_RESPUESTA = "Respuesta de prueba: "
PALABRAS_DE_HUMANO = ("humano", "persona", "asesor", "operador")
DIMENSIONES_POR_DEFECTO = 1536


@dataclass
class EstadoLlm:
    llamadas: list[dict] = field(default_factory=list)
    fallo_chat: int | None = None


class Fallos(BaseModel):
    chat: int | None = None


def _error(codigo: int, mensaje: str, tipo: str, code: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": mensaje, "type": tipo, "param": None, "code": code}},
        status_code=codigo,
    )


def _texto(contenido: object) -> str:
    """El contenido de un mensaje puede ser texto o una lista de partes."""
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        return " ".join(
            str(p.get("text") or "") for p in contenido if isinstance(p, dict)
        )
    return ""


PLANTILLA_GENERADA = {
    "nombre": "recordatorio_cita_prueba",
    "categoria": "UTILITY",
    "cabecera": "",
    "cuerpo": "Hola {{1}}, te recordamos tu cita de mañana a las {{2}}. ¿Nos confirmas?",
    "variables": [
        {"n": 1, "etiqueta": "Nombre del paciente", "ejemplo": "Ana"},
        {"n": 2, "etiqueta": "Hora de la cita", "ejemplo": "10:30"},
    ],
    "pie": "",
    "botones": [
        {"tipo": "respuesta", "texto": "Confirmo"},
        {"tipo": "respuesta", "texto": "Necesito cambiarla"},
    ],
}


def decidir(mensajes: list[dict], herramientas: list[dict]) -> dict:
    """El `message` de la respuesta: texto o llamada a herramienta."""
    ultimo = mensajes[-1] if mensajes else {}
    sistema = next(
        (_texto(m.get("content")) for m in mensajes if m.get("role") == "system"), ""
    )
    if "plantillas de WhatsApp Business" in sistema:
        return {"role": "assistant", "content": json.dumps(PLANTILLA_GENERADA, ensure_ascii=False)}
    if "[SEGUIMIENTO" in _texto(ultimo.get("content")):
        # Sin la última: es la propia instrucción, y también dice «la dirección».
        conversacion = " ".join(
            _texto(m.get("content")).lower() for m in mensajes[:-1] if m.get("role") == "user"
        )
        if "direcci" in conversacion:
            return {"role": "assistant", "content": "NO_ESCRIBIR"}
        return {
            "role": "assistant",
            "content": "Hola, ¿pudiste pensarlo? Si quieres, te busco un horario para la valoración.",
        }
    if ultimo.get("role") == "tool":
        return {"role": "assistant", "content": "Listo, ya lo dejé anotado."}
    ultimo_usuario = next(
        (_texto(m.get("content")) for m in reversed(mensajes) if m.get("role") == "user"), ""
    )
    nombres = {
        (h.get("function") or {}).get("name") for h in herramientas if isinstance(h, dict)
    }
    if "derivar_humano" in nombres and any(
        p in ultimo_usuario.lower() for p in PALABRAS_DE_HUMANO
    ):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": nuevo_id("call"),
                    "type": "function",
                    "function": {
                        "name": "derivar_humano",
                        "arguments": (
                            '{"motivo": "El cliente pide hablar con una persona",'
                            ' "mensaje_al_cliente": "Te paso con el equipo."}'
                        ),
                    },
                }
            ],
        }
    eco = " ".join(ultimo_usuario.split())[:120]
    return {"role": "assistant", "content": f"{PREFIJO_RESPUESTA}{eco}"}


def _trozos(mensaje: dict, modelo: str, entrada: int, salida: int, con_uso: bool):
    """La misma respuesta, troceada como la manda OpenAI en streaming: el texto
    de palabra en palabra, las herramientas con los argumentos partidos."""
    ident = nuevo_id("chatcmpl-mock")

    def evento(delta: dict, fin: str | None = None, uso: dict | None = None) -> str:
        chunk = {
            "id": ident, "object": "chat.completion.chunk", "created": int(time.time()),
            "model": modelo,
            "choices": [] if uso else [{"index": 0, "delta": delta, "finish_reason": fin}],
        }
        if uso:
            chunk["usage"] = uso
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    yield evento({"role": "assistant", "content": ""})
    texto = mensaje.get("content") or ""
    palabras = texto.split(" ")
    for i, palabra in enumerate(palabras):
        yield evento({"content": palabra + (" " if i < len(palabras) - 1 else "")})
    for idx, tc in enumerate(mensaje.get("tool_calls") or []):
        args = tc["function"]["arguments"]
        mitad = len(args) // 2
        yield evento({"tool_calls": [{"index": idx, "id": tc["id"], "type": "function",
                                      "function": {"name": tc["function"]["name"], "arguments": args[:mitad]}}]})
        yield evento({"tool_calls": [{"index": idx, "function": {"arguments": args[mitad:]}}]})
    yield evento({}, "tool_calls" if mensaje.get("tool_calls") else "stop")
    if con_uso:
        yield evento({}, uso={"prompt_tokens": entrada, "completion_tokens": salida,
                              "total_tokens": entrada + salida})
    yield "data: [DONE]\n\n"


def vector(texto: str, dimensiones: int) -> list[float]:
    """Embedding determinista y normalizado: el mismo texto da el mismo vector,
    así una búsqueda en la base de conocimiento encuentra lo que se indexó."""
    semilla = hashlib.sha256(texto.encode()).digest()
    crudo = [
        (semilla[i % len(semilla)] - 127.5) * (1 + (i // len(semilla)) % 7)
        for i in range(dimensiones)
    ]
    norma = math.sqrt(sum(x * x for x in crudo)) or 1.0
    return [x / norma for x in crudo]


def crear_app(estado: EstadoLlm | None = None) -> FastAPI:
    app = FastAPI(title="Simulador de LLM (compatible con OpenAI)")
    app.state.llm = estado or EstadoLlm()

    def st() -> EstadoLlm:
        return app.state.llm

    def clave(authorization: str | None) -> str:
        return (authorization or "").removeprefix("Bearer ").strip()

    @app.post("/v1/chat/completions")
    async def chat(request: Request, authorization: str | None = Header(default=None)):
        if clave_invalida(clave(authorization)):
            return _error(401, "Incorrect API key provided.", "invalid_request_error", "invalid_api_key")
        if st().fallo_chat:
            if st().fallo_chat == 401:
                return _error(401, "Incorrect API key provided.", "invalid_request_error", "invalid_api_key")
            return _error(st().fallo_chat, "Fallo simulado del proveedor.", "server_error", "mock_failure")
        cuerpo = await request.json()
        mensajes = cuerpo.get("messages") or []
        mensaje = decidir(mensajes, cuerpo.get("tools") or [])
        st().llamadas.append(
            {
                "n": len(st().llamadas) + 1,
                "model": cuerpo.get("model"),
                "ultimo": _texto((mensajes[-1] if mensajes else {}).get("content")),
                "herramientas": [
                    (h.get("function") or {}).get("name") for h in cuerpo.get("tools") or []
                ],
                "respuesta": mensaje.get("content")
                or [tc["function"]["name"] for tc in mensaje.get("tool_calls") or []],
            }
        )
        entrada = sum(len(_texto(m.get("content"))) for m in mensajes) // 4
        salida = len(mensaje.get("content") or "") // 4
        if cuerpo.get("stream"):
            return StreamingResponse(
                _trozos(mensaje, cuerpo.get("model") or "mock", entrada, salida,
                        bool((cuerpo.get("stream_options") or {}).get("include_usage"))),
                media_type="text/event-stream",
            )
        return {
            "id": nuevo_id("chatcmpl-mock"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": cuerpo.get("model") or "mock",
            "choices": [
                {
                    "index": 0,
                    "message": mensaje,
                    "finish_reason": "tool_calls" if mensaje.get("tool_calls") else "stop",
                }
            ],
            "usage": {
                "prompt_tokens": entrada,
                "completion_tokens": salida,
                "total_tokens": entrada + salida,
            },
        }

    @app.post("/v1/embeddings")
    async def embeddings(request: Request, authorization: str | None = Header(default=None)):
        if clave_invalida(clave(authorization)):
            return _error(401, "Incorrect API key provided.", "invalid_request_error", "invalid_api_key")
        cuerpo = await request.json()
        entradas = cuerpo.get("input")
        textos = entradas if isinstance(entradas, list) else [entradas or ""]
        dimensiones = int(cuerpo.get("dimensions") or DIMENSIONES_POR_DEFECTO)
        return {
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": vector(str(t), dimensiones)}
                for i, t in enumerate(textos)
            ],
            "model": cuerpo.get("model") or "mock-embedding",
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        }

    @app.post("/v1/moderations")
    async def moderacion(request: Request) -> dict:
        cuerpo = await request.json()
        entradas = cuerpo.get("input")
        n = len(entradas) if isinstance(entradas, list) else 1
        return {
            "id": nuevo_id("modr-mock"),
            "model": "mock-moderation",
            "results": [{"flagged": False, "categories": {}, "category_scores": {}}] * n,
        }

    @app.get("/v1/models")
    async def modelos() -> dict:
        return {"object": "list", "data": [{"id": "mock-1", "object": "model", "owned_by": "mock"}]}

    @app.get("/_mock/llamadas")
    async def llamadas() -> list[dict]:
        return st().llamadas

    @app.put("/_mock/fallos")
    async def fallos(datos: Fallos) -> dict:
        st().fallo_chat = datos.chat
        return {"chat": st().fallo_chat}

    @app.delete("/_mock")
    async def reiniciar() -> dict:
        app.state.llm = EstadoLlm()
        return {"ok": True}

    @app.get("/_mock/salud")
    async def salud() -> dict:
        return {"ok": True, "proveedor": "llm"}

    return app
