"""Simulador de la API REST de Retell (voz).

Imita lo que usan `app/providers/voice/retell.py`, `retell_admin.py` y
`scripts/medir_v3.py`: validar una llamada (`GET /v2/get-call/{id}`), listar
llamadas (`POST /v3/list-calls`), borrar una (`DELETE /v2/delete-call/{id}`),
crear, cambiar y publicar agentes con sus versiones, y comprar, importar y
atar números. El CRM lo usa si `RETELL_BASE_URL` apunta aquí.

Los agentes siguen el modelo de versiones de Retell (spec 2026-09-14): uno
nuevo es un borrador v0; publicar deja la versión en solo lectura; para
cambiarla hay que sacar un borrador (`create-agent-version`) y publicarlo
(`publish-agent-version`). El `POST /publish-agent/{id}` antiguo NO está: el
CRM no debe volver a depender de él.

Además emite el webhook de ciclo de vida (`call_ended`, `call_analyzed`)
firmado como Retell: `X-Retell-Signature: v=<ms>,d=<hex>` con la API key del
canal como secreto. No hay otro secreto en Retell, así que el simulador firma
con la última clave con la que le habló el CRM (o la que mande la prueba).

Lo que NO simula: el WebSocket del Custom LLM. Ahí Retell es quien se conecta
al CRM, y para probarlo basta un cliente WebSocket en la propia prueba; la
latencia real (V-3) solo se mide con llamadas de verdad.

Rutas de control:
  - `POST /_mock/calls`     crea una llamada (en curso) para que get-call la vea.
  - `POST /_mock/webhook`   manda `call_ended` o `call_analyzed` firmado.
  - `POST /_mock/agentes`   da de alta un agente "hecho a mano en la consola".
  - `GET  /_mock/agentes`   cada agente: su última versión y si está publicada.
  - `GET  /_mock/numeros`   los números de la cuenta y a qué agente van.
  - `DELETE /_mock`         vuelve al estado inicial.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

from tests.mocks._comun import (
    clave_invalida,
    cuerpo_json,
    entorno,
    entregar,
    firma_retell,
    nuevo_id,
)

# Los idiomas de español que acepta Retell. `es-MX` no existe (es la trampa
# de siempre): el de México es `es-419`.
_IDIOMAS_ES = {"es-ES", "es-419"}


@dataclass
class EstadoRetell:
    llamadas: dict[str, dict] = field(default_factory=dict)
    # Cada agente es su lista de versiones, de la v0 en adelante.
    agentes: dict[str, list[dict]] = field(default_factory=dict)
    numeros: dict[str, dict] = field(default_factory=dict)
    borradas: list[str] = field(default_factory=list)
    ultima_clave: str = ""


class NuevaLlamada(BaseModel):
    call_id: str | None = None
    agent_id: str = "agent_mock"
    call_status: str = "ongoing"
    from_number: str = "+529981112233"
    to_number: str = "+529980000000"
    # Latencia de cada turno (ms), como la da Retell en `latency.e2e.values`.
    # Con ella la llamada nace terminada, lista para medir (V-3).
    e2e_ms: list[float] | None = None


class Webhook(BaseModel):
    call_id: str
    event: str = "call_analyzed"
    duration_ms: int = 95_000
    combined_cost: float | None = 12.5  # centavos de dólar, como Retell
    call_summary: str = "El paciente pidió cita para limpieza dental."
    url: str | None = None
    api_key: str | None = None
    firma_mala: bool = False


class AgenteAMano(BaseModel):
    """Un agente creado a mano en la consola de Retell (el de V-3, por ejemplo)."""

    agent_id: str = "agent_mock"
    publicado: bool = True
    voice_id: str = "retell-Andrea"
    language: str = "es-419"


def _error(status: int, mensaje: str) -> HTTPException:
    return HTTPException(status, {"status": "error", "message": mensaje})


def crear_app(estado: EstadoRetell | None = None) -> FastAPI:
    app = FastAPI(title="Simulador de Retell")
    app.state.retell = estado or EstadoRetell()

    def st() -> EstadoRetell:
        return app.state.retell

    def autenticar(authorization: str | None) -> None:
        clave = (authorization or "").removeprefix("Bearer ").strip()
        if clave_invalida(clave):
            raise _error(401, "Unauthorized")
        st().ultima_clave = clave

    def versiones_de(agent_id: str) -> list[dict]:
        versiones = st().agentes.get(agent_id)
        if not versiones:
            raise _error(404, "Agent not found")
        return versiones

    def resolver(agent_id: str, version: str | None) -> dict:
        versiones = versiones_de(agent_id)
        if version in (None, "", "latest"):
            return versiones[-1]
        if version == "latest_published":
            publicadas = [v for v in versiones if v["is_published"]]
            if not publicadas:
                raise _error(404, "Agent has no published version")
            return publicadas[-1]
        try:
            numero = int(version)
        except ValueError:
            raise _error(400, f"Unknown version reference: {version}") from None
        for v in versiones:
            if v["version"] == numero:
                return v
        raise _error(404, "Agent version not found")

    def comprobar_agentes(inbound: list | None) -> None:
        for atado in inbound or []:
            if atado.get("agent_id") not in st().agentes:
                raise _error(400, f"Agent {atado.get('agent_id')} not found")

    def alta_de_version(agent_id: str, datos: dict, *, version: int, base: int | None) -> dict:
        return {
            **datos,
            "agent_id": agent_id,
            "version": version,
            "base_version": base,
            "is_published": False,
            "last_modification_timestamp": int(time.time() * 1000),
        }

    @app.get("/v2/get-call/{call_id}")
    async def get_call(call_id: str, authorization: str | None = Header(default=None)) -> dict:
        autenticar(authorization)
        llamada = st().llamadas.get(call_id)
        if llamada is None:
            raise _error(404, "Call not found")
        return llamada

    @app.post("/v3/list-calls")
    async def list_calls(authorization: str | None = Header(default=None)) -> dict:
        autenticar(authorization)
        # Más recientes primero, como el orden por defecto de Retell.
        items = sorted(
            st().llamadas.values(), key=lambda c: c.get("start_timestamp") or 0, reverse=True
        )
        return {"items": items, "has_more": False}

    # ------------------------------------------------------------- agentes
    @app.post("/create-agent")
    async def create_agent(request: Request, authorization: str | None = Header(default=None)) -> dict:
        autenticar(authorization)
        datos = await request.json()
        if not datos.get("response_engine") or not datos.get("voice_id"):
            raise _error(422, "response_engine and voice_id are required")
        idioma = datos.get("language")
        if idioma and str(idioma).startswith("es") and idioma not in _IDIOMAS_ES:
            raise _error(400, f"Invalid language: {idioma}")
        agent_id = nuevo_id("agent")
        st().agentes[agent_id] = [alta_de_version(agent_id, datos, version=0, base=None)]
        return st().agentes[agent_id][0]

    @app.get("/get-agent/{agent_id}")
    async def get_agent(
        agent_id: str,
        version: str | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict:
        autenticar(authorization)
        return resolver(agent_id, version)

    @app.get("/list-agent-versions/{agent_id}")
    async def list_agent_versions(
        agent_id: str, authorization: str | None = Header(default=None)
    ) -> dict:
        autenticar(authorization)
        items = [
            {
                "version": v["version"],
                "is_published": v["is_published"],
                "base_version": v["base_version"],
                "last_modification_timestamp": v["last_modification_timestamp"],
            }
            for v in reversed(versiones_de(agent_id))
        ]
        return {"items": items, "has_more": False}

    @app.post("/create-agent-version/{agent_id}")
    async def create_agent_version(
        agent_id: str, request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        autenticar(authorization)
        base = resolver(agent_id, str((await request.json()).get("base_version")))
        versiones = versiones_de(agent_id)
        datos = {
            k: v
            for k, v in base.items()
            if k not in ("version", "base_version", "is_published", "last_modification_timestamp")
        }
        nueva = alta_de_version(
            agent_id, datos, version=versiones[-1]["version"] + 1, base=base["version"]
        )
        versiones.append(nueva)
        return nueva

    @app.patch("/update-agent/{agent_id}")
    async def update_agent(
        agent_id: str,
        request: Request,
        version: str | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict:
        autenticar(authorization)
        objetivo = resolver(agent_id, version)
        if objetivo["is_published"]:
            raise _error(400, "A published version is read-only: create a draft from it first")
        objetivo.update(await request.json())
        objetivo["last_modification_timestamp"] = int(time.time() * 1000)
        return objetivo

    @app.post("/publish-agent-version/{agent_id}")
    async def publish_agent_version(
        agent_id: str, request: Request, authorization: str | None = Header(default=None)
    ) -> Response:
        autenticar(authorization)
        objetivo = resolver(agent_id, str((await request.json()).get("version")))
        if objetivo["is_published"]:
            raise _error(400, "This version is already published")
        objetivo["is_published"] = True
        return Response(status_code=200)

    # ------------------------------------------------------------- números
    @app.post("/create-phone-number")
    async def create_phone_number(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        autenticar(authorization)
        datos = await request.json()
        if datos.get("country_code", "US") not in ("US", "CA"):
            raise _error(400, "Only US and CA numbers can be purchased")
        comprobar_agentes(datos.get("inbound_agents"))
        lada = int(datos.get("area_code") or 415)
        numero = f"+1{lada}555{len(st().numeros) + 1000:04d}"
        st().numeros[numero] = {
            "phone_number": numero,
            "phone_number_type": "retell-twilio",
            "area_code": lada,
            "inbound_agents": datos.get("inbound_agents") or [],
            "nickname": datos.get("nickname"),
        }
        return st().numeros[numero]

    @app.post("/import-phone-number")
    async def import_phone_number(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        autenticar(authorization)
        datos = await request.json()
        numero = datos.get("phone_number")
        if not numero or not datos.get("termination_uri"):
            raise _error(422, "phone_number and termination_uri are required")
        if numero in st().numeros:
            raise _error(400, "Phone number already exists")
        comprobar_agentes(datos.get("inbound_agents"))
        st().numeros[numero] = {
            "phone_number": numero,
            "phone_number_type": "custom",
            "inbound_agents": datos.get("inbound_agents") or [],
            "nickname": datos.get("nickname"),
            "sip_outbound_trunk_config": {"termination_uri": datos["termination_uri"]},
            "allowed_outbound_country_list": datos.get("allowed_outbound_country_list"),
        }
        return st().numeros[numero]

    @app.get("/get-phone-number/{phone_number}")
    async def get_phone_number(
        phone_number: str, authorization: str | None = Header(default=None)
    ) -> dict:
        autenticar(authorization)
        if phone_number not in st().numeros:
            raise _error(404, "Phone number not found")
        return st().numeros[phone_number]

    @app.patch("/update-phone-number/{phone_number}")
    async def update_phone_number(
        phone_number: str, request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        autenticar(authorization)
        if phone_number not in st().numeros:
            raise _error(404, "Phone number not found")
        datos = await request.json()
        numero = st().numeros[phone_number]
        if "inbound_agents" in datos:
            comprobar_agentes(datos["inbound_agents"])
            numero["inbound_agents"] = datos["inbound_agents"] or []
        if datos.get("termination_uri"):
            numero["sip_outbound_trunk_config"] = {"termination_uri": datos["termination_uri"]}
        if "allowed_outbound_country_list" in datos:
            numero["allowed_outbound_country_list"] = datos["allowed_outbound_country_list"]
        return numero

    @app.delete("/v2/delete-call/{call_id}", response_class=Response)
    async def delete_call(call_id: str, authorization: str | None = Header(default=None)):
        autenticar(authorization)
        if st().llamadas.pop(call_id, None) is None:
            raise _error(404, "Call not found")
        st().borradas.append(call_id)
        return Response(status_code=204)

    # --------------------------------------------------------------- control
    @app.post("/_mock/calls")
    async def crear_llamada(datos: NuevaLlamada) -> dict:
        call_id = datos.call_id or nuevo_id("call_mock")
        st().llamadas[call_id] = {
            "call_id": call_id,
            "call_type": "phone_call",
            "agent_id": datos.agent_id,
            "call_status": datos.call_status,
            "from_number": datos.from_number,
            "to_number": datos.to_number,
            "direction": "inbound",
            "start_timestamp": int(time.time() * 1000),
        }
        if datos.e2e_ms is not None:
            valores = list(datos.e2e_ms)
            st().llamadas[call_id].update(
                call_status="ended",
                latency={"e2e": {"values": valores, "num": len(valores)}},
            )
        return st().llamadas[call_id]

    @app.post("/_mock/webhook")
    async def webhook(datos: Webhook) -> dict:
        url = datos.url or entorno("RETELL_MOCK_WEBHOOK_URL")
        clave = datos.api_key or st().ultima_clave or entorno("RETELL_MOCK_API_KEY")
        if not url or not clave:
            raise HTTPException(
                400,
                "Falta a dónde mandarlo o con qué firmarlo: pasa `url` y `api_key`, "
                "o define RETELL_MOCK_WEBHOOK_URL y RETELL_MOCK_API_KEY.",
            )
        llamada = dict(st().llamadas.get(datos.call_id) or {"call_id": datos.call_id})
        inicio = llamada.get("start_timestamp") or int(time.time() * 1000) - datos.duration_ms
        llamada.update(
            {
                "call_status": "ended",
                "end_timestamp": inicio + datos.duration_ms,
                "duration_ms": datos.duration_ms,
                "disconnection_reason": "user_hangup",
                "recording_url": f"https://mock.retellai.com/recordings/{datos.call_id}.wav",
                "transcript": "Agent: Hola, ¿en qué te ayudo?\nUser: Quiero una cita.",
            }
        )
        if datos.combined_cost is not None:
            llamada["call_cost"] = {"combined_cost": datos.combined_cost}
        if datos.event == "call_analyzed":
            llamada["call_analysis"] = {
                "call_summary": datos.call_summary,
                "user_sentiment": "Positive",
                "call_successful": True,
            }
        if datos.call_id in st().llamadas:
            st().llamadas[datos.call_id] = llamada
        cuerpo = cuerpo_json({"event": datos.event, "call": llamada})
        firma = firma_retell("clave-equivocada" if datos.firma_mala else clave, cuerpo)
        return await entregar(url, cuerpo, {"X-Retell-Signature": firma})

    @app.post("/_mock/agentes")
    async def agente_a_mano(datos: AgenteAMano) -> dict:
        version = alta_de_version(
            datos.agent_id,
            {
                "response_engine": {"type": "custom-llm", "llm_websocket_url": "wss://a-mano"},
                "voice_id": datos.voice_id,
                "language": datos.language,
            },
            version=0,
            base=None,
        )
        version["is_published"] = datos.publicado
        st().agentes[datos.agent_id] = [version]
        return version

    @app.get("/_mock/agentes")
    async def agentes() -> dict:
        return {
            agent_id: {
                **versiones[-1],
                "publicado": versiones[-1]["is_published"],
                "versiones": len(versiones),
            }
            for agent_id, versiones in st().agentes.items()
        }

    @app.get("/_mock/numeros")
    async def numeros() -> dict:
        return st().numeros

    @app.delete("/_mock")
    async def reiniciar() -> dict:
        app.state.retell = EstadoRetell()
        return {"ok": True}

    @app.get("/_mock/salud")
    async def salud() -> dict:
        return {"ok": True, "proveedor": "retell"}

    return app
