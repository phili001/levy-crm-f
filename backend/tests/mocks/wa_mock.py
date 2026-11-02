"""Simulador de YCloud (WhatsApp).

Imita las rutas de `https://api.ycloud.com/v2` que usa
`app/providers/whatsapp/ycloud.py`: envío (`sendDirectly`), subida de
adjuntos, plantillas, números y endpoints de webhook. Para usarlo, el CRM
apunta `YCLOUD_BASE_URL` a `http://<host>:<puerto>/v2`.

Por qué no se portó el `wa-mock` de vocero: aquel imita la Graph API de Meta y
este CRM habla con YCloud, que tiene otras rutas, otra forma de respuesta y
otra firma. Un simulador con la forma equivocada solo prueba que el simulador
está bien.

Rutas de control (no existen en YCloud):
  - `POST /_mock/inbound`   llega un mensaje del cliente → webhook firmado.
  - `POST /_mock/status`    cambia el estado de un mensaje enviado (✓✓, fallo).
  - `GET  /_mock/outbox`    lo que el CRM mandó, en orden.
  - `GET  /_mock/subidas`   los archivos que el CRM subió como media.
  - `PUT  /_mock/fallos`    que el envío falle con un código (500, 429…).
  - `PUT  /_mock/plantillas/{nombre}`  Meta revisa una plantilla creada por el
                            CRM: la aprueba o la rechaza con su motivo.
  - `PUT  /_mock/eventos-ignorados`  eventos que YCloud «se queda callado» al
                            crear (o también al actualizar) un endpoint, como
                            pasó con los ecos en la cuenta del curso.
  - `DELETE /_mock`         vuelve al estado inicial.

A qué URL y con qué secreto se firma el webhook: si el CRM registró su endpoint
por la API (`POST /webhookEndpoints`, como hace el alta en un servidor
público), se usan ese y el secreto que se le devolvió, igual que haría YCloud.
Si no (desarrollo en local: el alta no registra URLs de `localhost`), se usan
`WA_MOCK_WEBHOOK_URL` y `WA_MOCK_WEBHOOK_SECRET`, o los que mande la prueba.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from tests.mocks._comun import (
    clave_invalida,
    cuerpo_json,
    entorno,
    entregar,
    firma_ycloud,
    nuevo_id,
)

NUMERO_POR_DEFECTO = "+529980000000"
WABA_POR_DEFECTO = "mockwaba1"

# La que usa el recordatorio de citas (WA-4). Aprobada, para que el envío de
# plantilla desde el chat tenga algo que mandar sin configurar nada.
PLANTILLAS_INICIALES = [
    {
        "wabaId": WABA_POR_DEFECTO,
        "name": "recordatorio_cita",
        "language": "es_MX",
        "category": "UTILITY",
        "status": "APPROVED",
        "components": [
            {
                "type": "BODY",
                "text": "Hola {{1}}, te recordamos tu cita el {{2}}. Responde SÍ para confirmar.",
                "example": {"body_text": [["Ana", "lunes 10:00"]]},
            }
        ],
    }
]


def _ahora_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class EstadoWa:
    numero: str = NUMERO_POR_DEFECTO
    outbox: list[dict] = field(default_factory=list)
    plantillas: list[dict] = field(default_factory=lambda: [dict(p) for p in PLANTILLAS_INICIALES])
    endpoints: list[dict] = field(default_factory=list)
    # Idempotency-Key → id devuelto: el reintento con la misma clave no manda
    # dos veces, como hace YCloud.
    idempotencia: dict[str, dict] = field(default_factory=dict)
    fallo_envio: int | None = None
    # Lo que el CRM subió como media (para mandarlo después por su id).
    subidas: list[dict] = field(default_factory=list)
    # Eventos que el endpoint no activa aunque se pidan, y si solo pasa al crear.
    eventos_ignorados: list[str] = field(default_factory=list)
    ignorar_solo_al_crear: bool = False


class Entrante(BaseModel):
    """Un mensaje del cliente. `from_` es el teléfono; sin él, `from_user_id`
    simula a alguien que Meta solo identifica por BSUID."""

    from_: str | None = Field(default=None, alias="from")
    from_user_id: str | None = None
    to: str | None = None
    name: str | None = None
    type: str = "text"
    text: str | None = "Hola"
    # Con `type="button"`, el texto del botón que tocó (respuesta rápida).
    boton: str | None = None
    wamid: str | None = None
    event_id: str | None = None
    # Anuncio Click to WhatsApp: si llega, el mensaje trae `referral`.
    ctwa_clid: str | None = None
    ad_headline: str | None = None
    # Destino y secreto explícitos (tienen prioridad sobre lo registrado).
    url: str | None = None
    secret: str | None = None
    # Para probar el rechazo: firma con un secreto que no es el bueno.
    firma_mala: bool = False

    model_config = {"populate_by_name": True, "extra": "forbid"}


class CambioEstado(BaseModel):
    wamid: str
    status: str = "delivered"
    error_code: str | None = None
    error_message: str | None = None
    url: str | None = None
    secret: str | None = None


class Fallos(BaseModel):
    envio: int | None = None


class Revision(BaseModel):
    status: str = "APPROVED"
    reason: str | None = None
    language: str | None = None


def crear_app(estado: EstadoWa | None = None) -> FastAPI:
    app = FastAPI(title="Simulador de YCloud")
    app.state.wa = estado or EstadoWa(numero=entorno("WA_MOCK_NUMERO", NUMERO_POR_DEFECTO))

    def st() -> EstadoWa:
        return app.state.wa

    def autenticar(clave: str | None) -> None:
        if clave_invalida(clave):
            # Forma del error de YCloud: el CRM enseña `message` al operador.
            raise HTTPException(401, {"code": "UNAUTHORIZED", "message": "Invalid API key"})

    # ---------------------------------------------------------------- envío
    @app.post("/v2/whatsapp/messages/sendDirectly")
    async def enviar(
        request: Request,
        x_api_key: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ) -> dict:
        autenticar(x_api_key)
        if st().fallo_envio:
            raise HTTPException(
                st().fallo_envio, {"code": "MOCK_FAILURE", "message": "Fallo simulado al enviar"}
            )
        cuerpo = await request.json()
        if idempotency_key and idempotency_key in st().idempotencia:
            return st().idempotencia[idempotency_key]
        if bool(cuerpo.get("to")) == bool(cuerpo.get("recipient")):
            # YCloud exige exactamente uno de los dos (ver `_recipient_fields`).
            raise HTTPException(400, {"message": "Exactly one of 'to' or 'recipient' is required"})
        tipo = cuerpo.get("type")
        if tipo in ("image", "video", "audio", "document", "sticker"):
            # Un media por id tiene que haberse subido antes a este mismo número.
            media_id = (cuerpo.get(tipo) or {}).get("id")
            if media_id and media_id not in {s["id"] for s in st().subidas}:
                raise HTTPException(400, {"message": f"Media {media_id} not found"})
        respuesta = {
            "id": nuevo_id("ycmock.out"),
            "wamid": nuevo_id("wamid.mock.out"),
            "status": "accepted",
            "from": cuerpo.get("from"),
            "to": cuerpo.get("to"),
            "type": cuerpo.get("type"),
            "createTime": _ahora_iso(),
        }
        st().outbox.append(
            {
                "n": len(st().outbox) + 1,
                "id": respuesta["id"],
                "wamid": respuesta["wamid"],
                "to": cuerpo.get("to"),
                "recipient": cuerpo.get("recipient"),
                "type": cuerpo.get("type"),
                "texto": (cuerpo.get("text") or {}).get("body"),
                "plantilla": (cuerpo.get("template") or {}).get("name"),
                "idempotency_key": idempotency_key,
                "cuerpo": cuerpo,
                "at": _ahora_iso(),
            }
        )
        if idempotency_key:
            st().idempotencia[idempotency_key] = respuesta
        return respuesta

    # La ruta real lleva el número del negocio (API v2 de YCloud). Con otro
    # número, o sin él, YCloud no encuentra la ruta: 404, como el de verdad.
    @app.post("/v2/whatsapp/media/{numero}/upload")
    async def subir(
        numero: str, request: Request, x_api_key: str | None = Header(default=None)
    ) -> dict:
        autenticar(x_api_key)
        if numero != st().numero:
            raise HTTPException(404, {"error": {"message": f"Phone number {numero} not found"}})
        formulario = await request.form()
        archivo = formulario.get("file")
        if archivo is None or not hasattr(archivo, "read"):
            raise HTTPException(400, {"error": {"message": "Missing file"}})
        contenido = await archivo.read()
        media_id = nuevo_id("mockmedia")
        st().subidas.append({"id": media_id, "numero": numero, "nombre": archivo.filename,
                             "tipo": archivo.content_type, "bytes": len(contenido)})
        return {"id": media_id}

    # ----------------------------------------------------- plantillas y número
    @app.get("/v2/whatsapp/templates")
    async def plantillas(x_api_key: str | None = Header(default=None)) -> dict:
        autenticar(x_api_key)
        return {"items": st().plantillas, "total": len(st().plantillas), "offset": 0, "limit": 100}

    @app.post("/v2/whatsapp/templates")
    async def crear_plantilla(request: Request, x_api_key: str | None = Header(default=None)) -> dict:
        """Como YCloud: la deja en revisión (`PENDING`) y la devuelve."""
        autenticar(x_api_key)
        cuerpo = await request.json()
        faltan = [k for k in ("wabaId", "name", "language", "category", "components") if not cuerpo.get(k)]
        if faltan:
            raise HTTPException(400, {"message": f"Missing required field(s): {', '.join(faltan)}"})
        if cuerpo["wabaId"] != WABA_POR_DEFECTO:
            raise HTTPException(404, {"message": f"WABA {cuerpo['wabaId']} not found"})
        if any(
            p["name"] == cuerpo["name"] and p["language"] == cuerpo["language"]
            for p in st().plantillas
        ):
            # Meta lo dice en `error_user_msg`, que es el que el CRM enseña.
            raise HTTPException(
                400,
                {
                    "error": {
                        "message": "Invalid parameter",
                        "error_user_msg": "Ya existe contenido en este idioma para esta plantilla.",
                    }
                },
            )
        nueva = {**cuerpo, "status": "PENDING", "createTime": _ahora_iso()}
        st().plantillas.append(nueva)
        return nueva

    @app.get("/v2/whatsapp/phoneNumbers")
    async def numeros(x_api_key: str | None = Header(default=None)) -> dict:
        autenticar(x_api_key)
        return {
            "items": [
                {
                    "id": f"{WABA_POR_DEFECTO}_{st().numero.lstrip('+')}",
                    "phoneNumber": st().numero,
                    "wabaId": WABA_POR_DEFECTO,
                    "verifiedName": "Clínica de pruebas",
                    "qualityRating": "GREEN",
                    "status": "CONNECTED",
                }
            ],
            "total": 1,
        }

    # -------------------------------------------------- endpoints de webhook
    @app.get("/v2/webhookEndpoints")
    async def listar_endpoints(x_api_key: str | None = Header(default=None)) -> dict:
        autenticar(x_api_key)
        # El secreto NO sale en el listado, igual que en YCloud.
        return {"items": [{k: v for k, v in e.items() if k != "secret"} for e in st().endpoints]}

    @app.post("/v2/webhookEndpoints")
    async def crear_endpoint(request: Request, x_api_key: str | None = Header(default=None)) -> dict:
        autenticar(x_api_key)
        cuerpo = await request.json()
        pedidos = cuerpo.get("enabledEvents") or []
        endpoint = {
            "id": nuevo_id("whe"),
            "url": cuerpo.get("url"),
            "enabledEvents": [e for e in pedidos if e not in st().eventos_ignorados],
            "status": cuerpo.get("status") or "enabled",
            "secret": "whsec_" + secrets.token_hex(16),
        }
        st().endpoints.append(endpoint)
        # El secreto solo viaja en la respuesta de creación.
        return endpoint

    @app.patch("/v2/webhookEndpoints/{endpoint_id}")
    async def actualizar_endpoint(
        endpoint_id: str, request: Request, x_api_key: str | None = Header(default=None)
    ) -> dict:
        autenticar(x_api_key)
        endpoint = _endpoint(endpoint_id)
        cambios = {k: v for k, v in (await request.json()).items() if k != "secret"}
        if "enabledEvents" in cambios and not st().ignorar_solo_al_crear:
            cambios["enabledEvents"] = [
                e for e in cambios["enabledEvents"] if e not in st().eventos_ignorados
            ]
        endpoint.update(cambios)
        return {k: v for k, v in endpoint.items() if k != "secret"}

    @app.post("/v2/webhookEndpoints/{endpoint_id}/rotateSecret")
    async def rotar(endpoint_id: str, x_api_key: str | None = Header(default=None)) -> dict:
        autenticar(x_api_key)
        endpoint = _endpoint(endpoint_id)
        endpoint["secret"] = "whsec_" + secrets.token_hex(16)
        return {"id": endpoint_id, "secret": endpoint["secret"]}

    def _endpoint(endpoint_id: str) -> dict:
        for e in st().endpoints:
            if e["id"] == endpoint_id:
                return e
        raise HTTPException(404, {"message": "Webhook endpoint not found"})

    # --------------------------------------------------------------- control
    def destino(url: str | None, secreto: str | None) -> tuple[str, str]:
        registrado = st().endpoints[-1] if st().endpoints else {}
        url = url or registrado.get("url") or entorno("WA_MOCK_WEBHOOK_URL")
        secreto = secreto or registrado.get("secret") or entorno("WA_MOCK_WEBHOOK_SECRET")
        if not url or not secreto:
            raise HTTPException(
                400,
                "No sé a dónde mandar el webhook: registra el endpoint por la API, "
                "define WA_MOCK_WEBHOOK_URL y WA_MOCK_WEBHOOK_SECRET o pásalos en la petición.",
            )
        return url, secreto

    async def firmar_y_entregar(evento: dict, url: str, secreto: str, firma_mala: bool) -> dict:
        cuerpo = cuerpo_json(evento)
        firma = firma_ycloud("secreto-equivocado" if firma_mala else secreto, cuerpo)
        return await entregar(url, cuerpo, {"YCloud-Signature": firma})

    @app.post("/_mock/inbound")
    async def entrante(datos: Entrante) -> dict:
        url, secreto = destino(datos.url, datos.secret)
        wamid = datos.wamid or nuevo_id("wamid.mock.in")
        mensaje: dict = {
            "id": nuevo_id("ycmock.in"),
            "wamid": wamid,
            "wabaId": WABA_POR_DEFECTO,
            "to": datos.to or st().numero,
            "sendTime": _ahora_iso(),
            "type": datos.type,
        }
        if datos.from_:
            mensaje["from"] = datos.from_
        if datos.from_user_id:
            mensaje["fromUserId"] = datos.from_user_id
        if datos.name:
            # Sin nombre pedido no se inventa uno: Meta tampoco lo manda siempre.
            mensaje["customerProfile"] = {"name": datos.name}
        if datos.type == "text":
            mensaje["text"] = {"body": datos.text or ""}
        if datos.type == "button":
            # Forma de Meta (YCloud la reenvía igual): texto y payload del botón.
            mensaje["button"] = {"text": datos.boton or "", "payload": datos.boton or ""}
        if datos.ctwa_clid or datos.ad_headline:
            mensaje["referral"] = {
                "source_type": "ad",
                "source_id": "1200000000000",
                "source_url": "https://fb.me/anuncio-de-prueba",
                "headline": datos.ad_headline or "Anuncio de prueba",
                "body": "Escríbenos por WhatsApp",
                **({"ctwa_clid": datos.ctwa_clid} if datos.ctwa_clid else {}),
            }
        evento = {
            "id": datos.event_id or nuevo_id("evt"),
            "type": "whatsapp.inbound_message.received",
            "apiVersion": "v2",
            "createTime": _ahora_iso(),
            "whatsappInboundMessage": mensaje,
        }
        resultado = await firmar_y_entregar(evento, url, secreto, datos.firma_mala)
        return {**resultado, "wamid": wamid, "event_id": evento["id"]}

    @app.post("/_mock/status")
    async def estado_mensaje(datos: CambioEstado) -> dict:
        url, secreto = destino(datos.url, datos.secret)
        enviado = next(
            (m for m in st().outbox if datos.wamid in (m["wamid"], m["id"])), None
        )
        mensaje = {
            "id": enviado["id"] if enviado else "",
            "wamid": enviado["wamid"] if enviado else datos.wamid,
            "status": datos.status,
            "to": (enviado or {}).get("to") or "",
            "updateTime": _ahora_iso(),
        }
        if datos.error_code:
            mensaje["errorCode"] = datos.error_code
            mensaje["errorMessage"] = datos.error_message or ""
        evento = {
            "id": nuevo_id("evt"),
            "type": "whatsapp.message.updated",
            "apiVersion": "v2",
            "createTime": _ahora_iso(),
            "whatsappMessage": mensaje,
        }
        return await firmar_y_entregar(evento, url, secreto, False)

    @app.get("/_mock/subidas")
    async def subidas() -> list[dict]:
        return st().subidas

    @app.get("/_mock/outbox")
    async def outbox() -> list[dict]:
        return st().outbox

    @app.put("/_mock/fallos")
    async def fallos(datos: Fallos) -> dict:
        st().fallo_envio = datos.envio
        return {"envio": st().fallo_envio}

    @app.put("/_mock/plantillas/{nombre}")
    async def revisar_plantilla(nombre: str, datos: Revision) -> dict:
        """Lo que hace Meta al revisar: aprobarla o rechazarla con su motivo."""
        for p in st().plantillas:
            if p["name"] == nombre and (datos.language is None or p["language"] == datos.language):
                p["status"] = datos.status
                if datos.reason:
                    p["reason"] = datos.reason
                else:
                    p.pop("reason", None)
                return p
        raise HTTPException(404, {"message": f"Template {nombre} not found"})

    @app.put("/_mock/eventos-ignorados")
    async def eventos_ignorados(datos: dict) -> dict:
        st().eventos_ignorados = list(datos.get("eventos") or [])
        st().ignorar_solo_al_crear = bool(datos.get("solo_al_crear"))
        return {"eventos": st().eventos_ignorados, "solo_al_crear": st().ignorar_solo_al_crear}

    @app.delete("/_mock")
    async def reiniciar() -> dict:
        app.state.wa = EstadoWa(numero=st().numero)
        return {"ok": True}

    @app.get("/_mock/salud")
    async def salud() -> dict:
        return {"ok": True, "proveedor": "ycloud", "ts": int(time.time())}

    return app
