"""Simulador de los webhooks de n8n: herramientas de Sofía (Fase 11, SPEC-06) y
«Sofía en n8n», el flujo que hace de cerebro de un agente (SPEC-07).

Imita lo que hace un flujo de n8n con un nodo Webhook y «Header Auth»: rechaza
lo que no trae el secreto en `X-Omnicanal-Token` (403, como n8n) y contesta lo
que se le haya dicho a cada webhook.

  - `POST /webhook/{nombre}`        la llamada del CRM.
  - `PUT  /_mock/herramientas/{nombre}`  cómo contesta: `{secreto, modo,
        resultado?, segundos?, estado?}`. `modo` = `ok` | `tarda` | `falla` |
        `archivo` (devuelve una imagen servida por este mismo simulador) |
        `agente` (contesta un turno de «Sofía en n8n», ver `_turno`).
  - `GET  /_mock/llamadas`          lo que le llegó, en orden.
  - `GET  /archivos/{nombre}`       el archivo de `modo: archivo` (un JPEG).
  - `DELETE /_mock`                 vuelve al estado inicial.

Ojo: el CRM solo llama a URLs HTTPS públicas. Contra este simulador (http y
127.0.0.1) solo se puede usar desde pruebas que relajen esa guardia.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

# Una imagen JPEG mínima, con la cabecera de verdad.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200


@dataclass
class EstadoN8n:
    herramientas: dict[str, dict] = field(default_factory=dict)
    llamadas: list[dict] = field(default_factory=list)


def _turno(conf: dict, evento: dict, base: str) -> dict:
    """Lo que devuelve el «Respond to Webhook» de un flujo que hace de Sofía.

    - `respuesta`: el texto; si no se dice, repite el último mensaje del
      paciente («n8n recibió: …»), que basta para ver que el turno dio la vuelta.
    - `despues: true`: contesta `{}` («lo mando después por la API»).
    - `acciones`: se devuelven tal cual (`pasar_a_humano`, `etapa`, `ficha`…).
    - `archivo: true`: añade una imagen servida por este simulador.
    """
    if conf.get("despues"):
        return {}
    mensajes = evento.get("mensajes") or [{}]
    ultimo = mensajes[-1]
    texto = ultimo.get("texto") or ultimo.get("transcripcion") or ""
    respuesta: dict = {"respuesta": conf.get("respuesta") or f"n8n recibió: {texto}"}
    if conf.get("acciones"):
        respuesta["acciones"] = conf["acciones"]
    if conf.get("archivo"):
        respuesta["archivos"] = [{"tipo": "imagen", "url": f"{base}/archivos/turno.jpg", "nombre": "turno.jpg"}]
    return respuesta


def crear_app() -> FastAPI:
    app = FastAPI(title="Simulador de n8n")
    app.state.n8n = EstadoN8n()

    def st() -> EstadoN8n:
        return app.state.n8n

    @app.post("/webhook/{nombre}")
    async def webhook(nombre: str, request: Request, x_omnicanal_token: str | None = Header(default=None)):
        conf = st().herramientas.get(nombre)
        if conf is None:
            # Lo que contesta n8n a un webhook que no existe o no está activo.
            raise HTTPException(404, {"message": f'The requested webhook "POST {nombre}" is not registered.'})
        if x_omnicanal_token != conf.get("secreto"):
            raise HTTPException(403, "Authorization data is wrong!")
        cuerpo = await request.json()
        st().llamadas.append({"herramienta": nombre, "cuerpo": cuerpo})
        modo = conf.get("modo", "ok")
        if modo == "tarda":
            await asyncio.sleep(float(conf.get("segundos") or 30))
        if modo == "falla":
            return JSONResponse({"message": "Workflow could not be started!"}, status_code=int(conf.get("estado") or 500))
        if modo == "agente":
            return _turno(conf, cuerpo, str(request.base_url).rstrip("/"))
        respuesta: dict = {"resultado": conf.get("resultado", "ok")}
        if modo == "archivo":
            base = str(request.base_url).rstrip("/")
            respuesta["archivos"] = [
                {"tipo": "imagen", "url": f"{base}/archivos/{nombre}.jpg", "pie": conf.get("pie"), "nombre": f"{nombre}.jpg"}
            ]
        return respuesta

    @app.get("/archivos/{nombre}")
    async def archivo(nombre: str) -> Response:
        return Response(JPEG, media_type="image/jpeg")

    @app.put("/_mock/herramientas/{nombre}")
    async def configurar(nombre: str, datos: dict) -> dict:
        st().herramientas[nombre] = datos
        return {"ok": True}

    @app.get("/_mock/llamadas")
    async def llamadas() -> list[dict]:
        return st().llamadas

    @app.delete("/_mock")
    async def reiniciar() -> dict:
        app.state.n8n = EstadoN8n()
        return {"ok": True}

    @app.get("/_mock/salud")
    async def salud() -> dict:
        return {"ok": True, "proveedor": "n8n"}

    return app
