"""Simulador de la API de Zernio (Instagram y Messenger).

Imita lo que usa `app/providers/zernio/client.py`, con las formas de la doc
oficial y de los eventos reales de `tests/fixtures/zernio/`:

  - `GET  /api/v1/accounts`                   cuentas conectadas (valida la llave)
  - `GET  /api/v1/accounts/health`
  - `POST /api/v1/webhooks/settings`          registra el aviso de mensajes
  - `DELETE /api/v1/webhooks/settings?id=`
  - `POST /api/v1/webhooks/test`              manda un evento de prueba firmado
  - `POST /api/v1/inbox/conversations/{id}/messages`  envío; devuelve
    `data.messageId` y respeta `Idempotency-Key` (misma llave y mismo cuerpo →
    la respuesta original con `Idempotent-Replayed: true`; otro cuerpo → 422)
  - `GET  /api/v1/inbox/conversations` y `…/{id}/messages` (historial)
  - `GET  …/messages/{mid}/attachments/{i}?format=json`

Una clave que acaba en `-invalid` → 401. El CRM lo usa si `ZERNIO_BASE_URL`
apunta aquí (`http://<host>:9105/api/v1`).

Los eventos salen firmados como Zernio: `X-Zernio-Signature` = HMAC-SHA256
hex del cuerpo crudo con el secreto con el que el CRM registró el webhook (o
`ZERNIO_MOCK_WEBHOOK_SECRET`), y también con las cabeceras heredadas `X-Late-*`.

Rutas de control:
  - `POST /_mock/entrante`  llega un DM (instagram) o un mensaje a la página (facebook).
  - `POST /_mock/eco`       la clínica contesta desde la app (`message.sent`, `sentVia: null`).
  - `POST /_mock/fallo`     los próximos envíos fallan con ese código (F-1: Zernio caído).
  - `GET  /_mock/enviados`  lo que el CRM mandó.
  - `GET  /_mock/webhook`   lo que el CRM registró (url, eventos; el secreto no).
  - `DELETE /_mock`         vuelve al estado inicial.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tests.mocks._comun import clave_invalida, cuerpo_json, entorno, entregar, nuevo_id

CUENTA_IG = "mock_acc_ig"
CUENTA_FB = "mock_acc_fb"
PERFIL = "mock_profile"


def firma_zernio(secreto: str, cuerpo: bytes) -> str:
    return hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class EstadoZernio:
    webhooks: dict[str, dict] = field(default_factory=dict)
    enviados: list[dict] = field(default_factory=list)
    idempotencia: dict[str, tuple[dict, dict]] = field(default_factory=dict)
    # Conversaciones de Zernio: id → {platform, account_id, participant_id, mensajes}
    conversaciones: dict[str, dict] = field(default_factory=dict)
    fallo: dict | None = None
    cuentas: list[dict] = field(
        default_factory=lambda: [
            {"_id": CUENTA_IG, "platform": "instagram", "username": "clinica.mock",
             "displayName": "Clínica Mock", "profileId": PERFIL, "needsReconnection": False},
            {"_id": CUENTA_FB, "platform": "facebook", "username": "Clínica Mock",
             "displayName": "Clínica Mock", "profileId": PERFIL, "needsReconnection": False},
        ]
    )


class Entrante(BaseModel):
    platform: str = "instagram"  # instagram | facebook
    participant_id: str | None = None
    text: str = "Hola, ¿tienen cita esta semana?"
    nombre: str = "Paciente Mock"
    url: str | None = None
    secreto: str | None = None
    firma_mala: bool = False


class Eco(BaseModel):
    platform: str = "instagram"
    participant_id: str
    text: str = "Le contesto yo desde el celular"
    sent_via: str | None = None
    url: str | None = None
    secreto: str | None = None


class Fallo(BaseModel):
    status: int = 500
    code: str = ""
    # Cuántos envíos fallan (0 = hasta que se quite con DELETE /_mock).
    veces: int = 0


def crear_app(estado: EstadoZernio | None = None) -> FastAPI:
    app = FastAPI(title="Simulador de Zernio")
    app.state.zernio = estado or EstadoZernio()

    def st() -> EstadoZernio:
        return app.state.zernio

    def autenticar(authorization: str | None) -> None:
        if clave_invalida((authorization or "").removeprefix("Bearer ").strip()):
            raise HTTPException(401, {"error": "Invalid API key"})

    def cuenta(plataforma: str) -> dict:
        return next(c for c in st().cuentas if c["platform"] == plataforma)

    def destino(url: str | None, secreto: str | None) -> tuple[str, str]:
        registrado = next(iter(st().webhooks.values()), {})
        url = url or registrado.get("url") or entorno("ZERNIO_MOCK_WEBHOOK_URL")
        secreto = secreto or registrado.get("secret") or entorno("ZERNIO_MOCK_WEBHOOK_SECRET")
        if not url or not secreto:
            raise HTTPException(
                400,
                "No hay webhook registrado: conecta Zernio desde el CRM, o define "
                "ZERNIO_MOCK_WEBHOOK_URL y ZERNIO_MOCK_WEBHOOK_SECRET.",
            )
        return url, secreto

    async def emitir(evento: dict, url: str, secreto: str, *, firma_mala: bool = False) -> dict:
        cuerpo = cuerpo_json(evento)
        firma = firma_zernio("otro-secreto" if firma_mala else secreto, cuerpo)
        cabeceras = {
            "X-Zernio-Event": evento["event"],
            "X-Zernio-Event-Id": evento["id"],
            "X-Zernio-Signature": firma,
            "X-Late-Event": evento["event"],
            "X-Late-Event-Id": evento["id"],
            "X-Late-Signature": firma,
            "User-Agent": "Zernio-Webhooks/1.0",
        }
        return await entregar(url, cuerpo, cabeceras) | {"evento": evento}

    def bloque_cuenta(plataforma: str) -> dict:
        c = cuenta(plataforma)
        return {
            "id": c["_id"], "accountId": c["_id"], "profileId": c["profileId"],
            "platform": plataforma, "username": c["username"], "displayName": c["displayName"],
        }

    def conversacion(plataforma: str, participante: str, nombre: str = "") -> tuple[str, dict]:
        for cid, c in st().conversaciones.items():
            if c["platform"] == plataforma and c["participant_id"] == participante:
                return cid, c
        cid = uuid.uuid4().hex[:24]
        st().conversaciones[cid] = {
            "platform": plataforma, "account_id": cuenta(plataforma)["_id"],
            "participant_id": participante, "participant_name": nombre, "mensajes": [],
        }
        return cid, st().conversaciones[cid]

    # ---------- API ----------

    @app.get("/api/v1/accounts")
    async def accounts(authorization: str | None = Header(default=None)) -> dict:
        autenticar(authorization)
        return {"accounts": st().cuentas, "hasAnalyticsAccess": False}

    @app.get("/api/v1/accounts/health")
    async def health(authorization: str | None = Header(default=None)) -> dict:
        autenticar(authorization)
        return {"accounts": [{"accountId": c["_id"], "healthy": True} for c in st().cuentas]}

    @app.post("/api/v1/webhooks/settings")
    async def registrar(request: Request, authorization: str | None = Header(default=None)) -> dict:
        autenticar(authorization)
        datos = await request.json()
        if not datos.get("url") or not datos.get("events"):
            raise HTTPException(400, {"error": "url and events are required"})
        wid = uuid.uuid4().hex[:24]
        st().webhooks[wid] = {**datos, "_id": wid, "isActive": True}
        return {"success": True, "webhook": {k: v for k, v in st().webhooks[wid].items() if k != "secret"}}

    @app.delete("/api/v1/webhooks/settings")
    async def borrar(id: str = "", authorization: str | None = Header(default=None)) -> dict:
        autenticar(authorization)
        if st().webhooks.pop(id, None) is None:
            raise HTTPException(404, {"error": "Webhook not found"})
        return {"success": True}

    @app.post("/api/v1/webhooks/test")
    async def probar(request: Request, authorization: str | None = Header(default=None)) -> dict:
        autenticar(authorization)
        wid = (await request.json()).get("webhookId")
        registrado = st().webhooks.get(wid)
        if registrado is None:
            raise HTTPException(404, {"error": "Webhook not found"})
        evento = {"id": str(uuid.uuid4()), "event": "webhook.test", "timestamp": _ahora()}
        await emitir(evento, registrado["url"], registrado["secret"])
        return {"success": True}

    @app.post("/api/v1/inbox/conversations/{cid}/messages")
    async def enviar(
        cid: str,
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ):
        autenticar(authorization)
        cuerpo = await request.json()
        if idempotency_key and idempotency_key in st().idempotencia:
            previo_cuerpo, previa = st().idempotencia[idempotency_key]
            if previo_cuerpo != cuerpo:
                return JSONResponse({"error": "Idempotency key reused with a different body"}, 422)
            return JSONResponse(previa, headers={"Idempotent-Replayed": "true"})
        if st().fallo is not None:
            fallo = st().fallo
            if fallo.get("veces"):
                fallo["veces"] -= 1
                if fallo["veces"] <= 0:
                    st().fallo = None
            return JSONResponse({"error": "mock failure", "code": fallo.get("code") or ""}, fallo["status"])
        conv = st().conversaciones.get(cid)
        if conv is None:
            raise HTTPException(404, {"error": "Conversation not found"})
        if cuerpo.get("accountId") != conv["account_id"]:
            raise HTTPException(400, {"error": "accountId does not match the conversation"})
        mid = nuevo_id("m_mid" if conv["platform"] == "facebook" else "aWdf_mid")
        conv["mensajes"].append(
            {"id": mid, "direction": "outgoing", "message": cuerpo.get("message"), "sentAt": _ahora()}
        )
        st().enviados.append({"conversation_id": cid, "message_id": mid, **cuerpo,
                              "idempotency_key": idempotency_key})
        respuesta = {"success": True, "data": {"messageId": mid, "conversationId": cid}}
        if idempotency_key:
            st().idempotencia[idempotency_key] = (cuerpo, respuesta)
        return respuesta

    @app.get("/api/v1/inbox/conversations")
    async def listar(accountId: str = "", authorization: str | None = Header(default=None)) -> dict:
        autenticar(authorization)
        datos = [
            {"id": cid, "platform": c["platform"], "accountId": c["account_id"],
             "participantId": c["participant_id"], "participantName": c["participant_name"],
             "status": "active"}
            for cid, c in st().conversaciones.items()
            if not accountId or c["account_id"] == accountId
        ]
        return {"data": datos, "pagination": {"hasMore": False, "nextCursor": None}}

    @app.get("/api/v1/inbox/conversations/{cid}/messages")
    async def mensajes(cid: str, authorization: str | None = Header(default=None)) -> dict:
        autenticar(authorization)
        conv = st().conversaciones.get(cid)
        if conv is None:
            raise HTTPException(404, {"error": "Conversation not found"})
        return {"status": "success", "messages": conv["mensajes"],
                "pagination": {"hasMore": False, "nextCursor": None}}

    @app.get("/api/v1/inbox/conversations/{cid}/messages/{mid}/attachments/{indice}")
    async def adjunto(
        cid: str, mid: str, indice: int, accountId: str = "",
        authorization: str | None = Header(default=None),
    ) -> dict:
        autenticar(authorization)
        if not accountId:
            raise HTTPException(400, {"error": "missing_required_field"})
        return {"status": "success", "url": f"https://scontent.fbcdn.net/mock/{mid}/{indice}",
                "refreshed": True}

    # ---------- Control ----------

    @app.get("/_mock/salud")
    async def salud() -> dict:
        return {"ok": True}

    @app.post("/_mock/entrante")
    async def entrante(e: Entrante) -> dict:
        participante = e.participant_id or str(int(time.time() * 1000))
        cid, conv = conversacion(e.platform, participante, e.nombre)
        mid = nuevo_id("m_mid" if e.platform == "facebook" else "aWdf_mid")
        conv["mensajes"].append(
            {"id": mid, "direction": "incoming", "message": e.text, "sentAt": _ahora()}
        )
        evento = {
            "id": str(uuid.uuid4()),
            "event": "message.received",
            "message": {
                "id": uuid.uuid4().hex[:24], "conversationId": cid, "platform": e.platform,
                "platformMessageId": mid, "direction": "incoming", "text": e.text,
                "attachments": [],
                "sender": {"id": participante, "name": e.nombre,
                           **({"username": e.nombre.lower().replace(" ", ".")} if e.platform == "instagram" else {})},
                "sentAt": _ahora(), "isRead": False, "sentVia": None,
            },
            "conversation": {"id": cid, "platformConversationId": participante,
                             "participantId": participante, "participantName": e.nombre,
                             "status": "active"},
            "account": bloque_cuenta(e.platform),
            "timestamp": _ahora(),
        }
        url, secreto = destino(e.url, e.secreto)
        return await emitir(evento, url, secreto, firma_mala=e.firma_mala)

    @app.post("/_mock/eco")
    async def eco(e: Eco) -> dict:
        cid, conv = conversacion(e.platform, e.participant_id)
        mid = nuevo_id("m_mid" if e.platform == "facebook" else "aWdf_mid")
        conv["mensajes"].append(
            {"id": mid, "direction": "outgoing", "message": e.text, "sentAt": _ahora()}
        )
        c = cuenta(e.platform)
        evento = {
            "id": str(uuid.uuid4()),
            "event": "message.sent",
            "message": {
                "id": uuid.uuid4().hex[:24], "conversationId": cid, "platform": e.platform,
                "platformMessageId": mid, "direction": "outgoing", "text": e.text,
                "attachments": [],
                # En el eco, el remitente es la propia cuenta (así llega de verdad).
                "sender": {"id": c["_id"], "name": c["displayName"], "username": c["username"]},
                "sentAt": _ahora(), "isRead": True, "sentVia": e.sent_via,
            },
            "conversation": {"id": cid, "platformConversationId": e.participant_id,
                             "participantId": e.participant_id, "status": "active"},
            "account": bloque_cuenta(e.platform),
            "timestamp": _ahora(),
        }
        url, secreto = destino(e.url, e.secreto)
        return await emitir(evento, url, secreto)

    @app.post("/_mock/fallo")
    async def fallar(f: Fallo) -> dict:
        st().fallo = f.model_dump()
        return {"ok": True}

    @app.get("/_mock/enviados")
    async def enviados() -> list[dict]:
        return st().enviados

    @app.get("/_mock/webhook")
    async def webhook() -> list[dict]:
        return [{k: v for k, v in w.items() if k != "secret"} for w in st().webhooks.values()]

    @app.delete("/_mock")
    async def reiniciar() -> dict:
        app.state.zernio = EstadoZernio()
        return {"ok": True}

    return app
