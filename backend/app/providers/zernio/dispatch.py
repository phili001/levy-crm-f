"""Lo que manda el webhook de Zernio, traducido a lo que entiende el CRM.

Un solo endpoint recibe Instagram, Messenger y (si se enciende) WhatsApp; el
reparto es por `account.platform`. Todo aquí es puro (sin red ni BD) para
poder probarlo contra los eventos reales de `tests/fixtures/zernio/`.

Tres cosas que los eventos reales enseñaron y la doc no deja claras:

- En los ECOS (`message.sent`) el `sender` es la propia cuenta del negocio: el
  cliente está en `conversation.participantId`. Tomarlo del `sender` crearía
  una ficha para la clínica misma.
- `sentVia` dice quién escribió: `null` = la app de Instagram/Facebook (o algo
  anterior a 2026-08), `human` = una persona en el inbox de Zernio, `api` =
  nosotros. Solo los dos primeros son una persona contestando; lo nuestro ya
  está guardado y se descarta.
- Otro bot conectado a la cuenta (ManyChat) llega también con `sentVia: null`,
  indistinguible de una persona. Por eso en el alta se exige desconectar otras
  apps; aquí no hay forma de saberlo.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Literal

from app.providers.whatsapp.base import WA_USER_PREFIX, IncomingMessage, normalize_phone

# Plataforma de Zernio → canal del CRM. Messenger llega como `facebook`.
CANAL_POR_PLATAFORMA = {
    "instagram": "instagram_dm",
    "facebook": "messenger",
    "whatsapp": "whatsapp",
}

# Prefijo del identificador del cliente, el mismo criterio que usa
# `store_incoming` para saber de qué canal viene. WhatsApp va por teléfono.
_PREFIJO = {"instagram": "ig:", "facebook": "fb:"}

# Quién escribió un eco: estos son una PERSONA contestando fuera del CRM.
_SENT_VIA_PERSONA = (None, "human")

_TIPO_ADJUNTO = {
    "image": "image",
    "video": "video",
    "file": "document",
    "sticker": "sticker",
    "share": "image",
}

TipoEvento = Literal["entrante", "eco", "propio", "cuenta", "ignorado"]


@dataclass
class ZernioEvent:
    tipo: TipoEvento
    event_id: str
    evento: str
    plataforma: str = ""
    account_id: str = ""
    profile_id: str = ""
    incoming: IncomingMessage | None = None
    # Eco: {provider_message_id, customer_id, text, sent_by, tomar_control}
    eco: dict = field(default_factory=dict)
    # Cuenta: "connected" | "disconnected"
    estado_cuenta: str = ""
    motivo: str = ""

    @property
    def canal(self) -> str | None:
        return CANAL_POR_PLATAFORMA.get(self.plataforma)


def verify_signature(headers: dict[str, str], body: bytes, secret: str) -> bool:
    """HMAC-SHA256 en hex del cuerpo CRUDO, comparado en tiempo constante.

    `headers` con las claves en minúsculas. Zernio antes se llamaba Late y
    manda la misma firma también como `x-late-signature`.
    """
    if not secret:
        return False
    firma = (headers.get("x-zernio-signature") or headers.get("x-late-signature") or "").strip()
    if not firma:
        return False
    esperada = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, firma.lower())


def identificador_cliente(plataforma: str, id_plataforma: str, *, wa: dict | None = None) -> str:
    """`ig:<igsid>`, `fb:<psid>` o, en WhatsApp, el teléfono en E.164 (o
    `wa:<bsuid>` cuando Meta ya no manda el número)."""
    if plataforma == "whatsapp":
        wa = wa or {}
        telefono = str(wa.get("phoneNumber") or "").strip()
        if telefono:
            return normalize_phone(telefono if telefono.startswith("+") else f"+{telefono}")
        bsuid = str(wa.get("businessScopedUserId") or "").strip()
        if bsuid:
            return f"{WA_USER_PREFIX}{bsuid}"
        crudo = str(id_plataforma or "").strip()
        return normalize_phone(f"+{crudo}") if crudo.isdigit() else crudo
    return f"{_PREFIJO.get(plataforma, '')}{id_plataforma}"


def _fichas_adjuntos(adjuntos: list) -> list[dict]:
    """Lo que hay que guardar para volver a pedir cada adjunto más tarde: su
    posición. La URL caduca; `messageId` + índice no."""
    fichas = []
    for i, a in enumerate(adjuntos or []):
        if isinstance(a, dict):
            fichas.append(
                {"index": i, "type": a.get("type") or "", "originalType": a.get("originalType")}
            )
    return fichas


def _objeto(valor) -> dict:
    """Zernio no siempre manda objetos: en `webhook.test`, `message` es un texto."""
    return valor if isinstance(valor, dict) else {}


def _entrante(payload: dict, ev: ZernioEvent) -> ZernioEvent:
    msg = _objeto(payload.get("message"))
    conv = payload.get("conversation") or {}
    sender = msg.get("sender") or {}
    plataforma = ev.plataforma
    customer_id = identificador_cliente(plataforma, str(sender.get("id") or ""), wa=sender)
    platform_mid = str(msg.get("platformMessageId") or msg.get("id") or "")
    if not customer_id or not platform_mid:
        ev.tipo, ev.motivo = "ignorado", "sin_cliente_o_id"
        return ev

    adjuntos = [a for a in (msg.get("attachments") or []) if isinstance(a, dict)]
    fichas = _fichas_adjuntos(adjuntos)
    texto = msg.get("text")
    message_type = "text"
    audio_url = media_url = media_kind = None
    audio_index = media_index = None
    for i, a in enumerate(adjuntos):
        tipo = str(a.get("type") or "")
        if tipo == "audio" and audio_index is None:
            audio_url = a.get("url") or None
            audio_index = i
            message_type = "audio"
        elif tipo in _TIPO_ADJUNTO and media_index is None:
            media_url = a.get("url") or None
            media_kind = _TIPO_ADJUNTO[tipo]
            media_index = i
            if message_type == "text":
                message_type = "document" if media_kind == "document" else media_kind
    if message_type == "text" and not texto and adjuntos:
        message_type = "other"

    username = str(sender.get("username") or "").strip()
    nombre = str(sender.get("name") or "").strip()
    handle = f"@{username}" if plataforma == "instagram" and username else None
    metadata = payload.get("metadata") or {}
    zernio = {
        "conversation_id": str(msg.get("conversationId") or conv.get("id") or ""),
        "message_id": str(msg.get("id") or ""),
        "platform_message_id": platform_mid,
        "account_id": ev.account_id,
        "platform": plataforma,
        "sent_at": msg.get("sentAt"),
        "attachments": fichas,
        # Posición del adjunto y del audio que se bajan: con ella se pide una
        # URL nueva cuando la del evento ha caducado.
        "media_index": media_index,
        "audio_index": audio_index,
    }
    if isinstance(metadata, dict) and metadata.get("postbackPayload"):
        zernio["postback"] = str(metadata.get("postbackPayload"))

    ev.incoming = IncomingMessage(
        provider_message_id=platform_mid,
        from_phone=customer_id,
        to_phone="",
        message_type=message_type,  # type: ignore[arg-type]
        text=texto,
        audio_url=audio_url,
        media_kind=media_kind,  # type: ignore[arg-type]
        media_url=media_url,
        # En IG el `name` que manda Zernio suele ser el @usuario; si coincide,
        # no lo usamos como nombre real.
        customer_name=(nombre if nombre and nombre != username else None) or handle or None,
        customer_handle=handle,
        from_user_id=(str(sender.get("businessScopedUserId") or "") or None)
        if plataforma == "whatsapp"
        else None,
        raw={"zernio": zernio},
    )
    return ev


def _eco(payload: dict, ev: ZernioEvent) -> ZernioEvent:
    msg = _objeto(payload.get("message"))
    conv = payload.get("conversation") or {}
    sent_via = msg.get("sentVia")
    if sent_via == "api":
        # Lo mandó este CRM: ya está guardado con su `mid`.
        ev.tipo, ev.motivo = "propio", "sent_via_api"
        return ev
    participante = str(conv.get("participantId") or "")
    platform_mid = str(msg.get("platformMessageId") or msg.get("id") or "")
    customer_id = identificador_cliente(ev.plataforma, participante)
    if not participante or not platform_mid:
        ev.tipo, ev.motivo = "ignorado", "eco_sin_cliente_o_id"
        return ev
    persona = sent_via in _SENT_VIA_PERSONA
    if sent_via == "human":
        sent_by = "zernio_inbox"
    elif persona:
        sent_by = {"instagram": "instagram_app", "facebook": "messenger_app"}.get(
            ev.plataforma, "whatsapp_business_app"
        )
    else:
        sent_by = f"zernio_{sent_via}"
    ev.eco = {
        "provider_message_id": platform_mid,
        "customer_id": customer_id,
        "text": msg.get("text"),
        "sent_by": sent_by,
        # Una persona contestó desde fuera: Sofía se aparta. Lo automático de
        # Zernio (difusiones, secuencias) se refleja pero no le quita el sitio.
        "tomar_control": persona,
    }
    return ev


def parse_event(payload: dict, *, event_id: str = "") -> ZernioEvent:
    """Un evento del webhook → `ZernioEvent`. Nunca lanza con un dict."""
    account = _objeto(payload.get("account"))
    ev = ZernioEvent(
        tipo="ignorado",
        event_id=str(payload.get("id") or event_id or ""),
        evento=str(payload.get("event") or ""),
        plataforma=str(account.get("platform") or _objeto(payload.get("message")).get("platform") or ""),
        account_id=str(account.get("accountId") or account.get("id") or ""),
        profile_id=str(account.get("profileId") or ""),
    )
    if ev.evento == "message.received":
        ev.tipo = "entrante"
        return _entrante(payload, ev)
    if ev.evento == "message.sent":
        ev.tipo = "eco"
        return _eco(payload, ev)
    if ev.evento in ("account.connected", "account.disconnected"):
        ev.tipo = "cuenta"
        ev.estado_cuenta = ev.evento.split(".", 1)[1]
        return ev
    ev.motivo = "evento_no_usado"
    return ev


def route(ev: ZernioEvent) -> str | None:
    """Canal del CRM al que va el evento, o None si la plataforma no se usa."""
    return ev.canal
