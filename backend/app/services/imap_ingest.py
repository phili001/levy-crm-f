"""Ingesta de correo por IMAP (buzón con contraseña de aplicación).

Calcado de `gmail_ingest.py`, con lo que IMAP obliga a cambiar:

  - El ancla es `(UIDVALIDITY, último UID)` en `channels.config`. Si el
    servidor cambia UIDVALIDITY, los UID dejan de valer y se re-sincroniza por
    fecha (ver `imap_client.traer_nuevos`).
  - El id de cada correo en el CRM es su `Message-ID` (`imap:<message-id>`), no
    el UID: así una re-sincronización no duplica nada, porque `store_incoming`
    ya descarta un `provider_message_id` repetido.
  - El HILO no lo da el servidor: se saca de las cabeceras. La raíz es el
    primer id de `References`; si no hay, `In-Reply-To`; si tampoco, el propio
    `Message-ID`. Las respuestas del CRM llevan esa cadena, así que la contestación
    del cliente cae en la misma conversación.
  - Los adjuntos se guardan en disco al llegar (IMAP no tiene un "pídeme el
    adjunto luego" como la API de Gmail) y el panel los sirve desde ahí.

Un correo que falla al procesarse NO se pierde: el ancla se queda justo antes
de él y se reintenta en la pasada siguiente, hasta `MAX_INTENTOS`. Pasado eso
se salta con un aviso visible en Registros, igual que en Gmail.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.session import db_session
from app.models.channel import Channel, ChannelType
from app.providers.email import imap_client
from app.providers.email.imap_client import ConfigImap, ImapError, Servidor
from app.providers.gmail.client import _strip_html, sanitize_email_html_document
from app.providers.whatsapp.base import IncomingMessage
from app.services.channel_secrets import channel_config
from app.services.conversation import store_incoming
from app.services.media import save_local
from app.services.runtime_logs import push_runtime_log

logger = get_logger(__name__)

TRANSPORTE_IMAP = "imap"
MAX_INTENTOS = 5
# Un adjunto enorme no puede llenar el volumen: por encima se anuncia en el
# texto pero no se guarda.
MAX_BYTES_ADJUNTO = 25 * 1024 * 1024

_LOCK = "imap:ingest:{channel_id}"
_LOCK_TTL = 110  # menor que el intervalo del beat (120 s)
_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""

_RE_ID = re.compile(r"<[^<>]+>")


# ---------------------------------------------------------------- parseo MIME


def _ids(cabecera: str | None) -> list[str]:
    return _RE_ID.findall(cabecera or "")


def hilo_de(message_id: str | None, in_reply_to: str | None, references: str | None) -> str:
    """La raíz del hilo, estable para todos los correos de la conversación."""
    raiz = (_ids(references) or _ids(in_reply_to) or _ids(message_id) or [""])[0]
    if not raiz:
        return ""
    # `gmail_thread_id` es String(255); un id larguísimo se resume.
    valor = f"imap:{raiz}"
    return valor if len(valor) <= 255 else "imap:" + hashlib.sha256(raiz.encode()).hexdigest()


def _es_adjunto(parte: EmailMessage) -> bool:
    if parte.is_multipart():
        return False
    disposicion = (parte.get_content_disposition() or "").lower()
    if disposicion == "attachment":
        return True
    # Imágenes incrustadas con Content-ID (logos de firma): no son adjuntos
    # del cliente, igual que en Gmail.
    if disposicion == "inline" and parte.get("Content-ID"):
        return False
    return bool(parte.get_filename()) and parte.get_content_maintype() != "text"


def parsear_correo(crudo: bytes) -> dict:
    """El correo crudo (RFC 822) en el mismo formato que produce Gmail, para
    que `store_incoming` y el resto del CRM no distingan de dónde vino."""
    msg = BytesParser(policy=policy.default).parsebytes(crudo)
    assert isinstance(msg, EmailMessage)
    from_name, from_addr = parseaddr(str(msg.get("From", "")))
    destinatarios = getaddresses([str(msg.get("To", ""))])
    _, reply_to = parseaddr(str(msg.get("Reply-To", "")))

    texto = ""
    html = ""
    cuerpo_texto = msg.get_body(preferencelist=("plain",))
    if cuerpo_texto is not None:
        texto = cuerpo_texto.get_content()
    cuerpo_html = msg.get_body(preferencelist=("html",))
    if cuerpo_html is not None:
        html = cuerpo_html.get_content()
    if not texto and html:
        texto = _strip_html(html)

    adjuntos: list[dict] = []
    for parte in msg.walk():
        if not _es_adjunto(parte):
            continue
        nombre = parte.get_filename() or "adjunto"
        datos = parte.get_payload(decode=True) or b""
        ficha = {
            "filename": nombre,
            "mime_type": parte.get_content_type(),
            "size": len(datos),
        }
        if datos and len(datos) <= MAX_BYTES_ADJUNTO:
            archivo, _ = save_local(datos, nombre)
            ficha.update({"attachment_id": archivo, "local": True})
        adjuntos.append(ficha)
    if adjuntos:
        marcas = "\n".join(f"[adjunto: {a['filename']}]" for a in adjuntos)
        texto = (texto.strip() + ("\n\n" if texto.strip() else "") + marcas).strip()

    message_id = str(msg.get("Message-ID", "")).strip() or None
    in_reply_to = str(msg.get("In-Reply-To", "")).strip() or None
    references = str(msg.get("References", "")).strip() or None
    return {
        "message_id": message_id,
        "threadId": hilo_de(message_id, in_reply_to, references),
        "from_name": from_name or None,
        "from_addr": (from_addr or "").lower(),
        "to_addr": (destinatarios[0][1] if destinatarios else "").lower(),
        "reply_to_addr": (reply_to or "").lower() or None,
        "subject": str(msg.get("Subject", "")) or None,
        "rfc822_message_id": message_id,
        "in_reply_to": in_reply_to,
        "references": references,
        "list_unsubscribe": str(msg.get("List-Unsubscribe", "")) or None,
        "list_id": str(msg.get("List-Id", "")) or None,
        "precedence": str(msg.get("Precedence", "")) or None,
        "auto_submitted": str(msg.get("Auto-Submitted", "")) or None,
        "body": texto.strip(),
        "html": sanitize_email_html_document(html),
        "attachments": adjuntos,
    }


def a_incoming(p: dict, crudo: bytes) -> IncomingMessage:
    # Sin Message-ID (raro, pero pasa con algunos sistemas) el id sale del
    # contenido: el mismo correo releído da el mismo id y no se duplica.
    ident = p["message_id"] or "sin-id-" + hashlib.sha256(crudo).hexdigest()[:32]
    return IncomingMessage(
        provider_message_id=f"imap:{ident}"[:255],
        from_phone=f"email:{p['from_addr']}",
        to_phone=f"email:{p['to_addr']}",
        message_type="text",
        text=p["body"],
        customer_name=p["from_name"],
        raw={
            "threadId": p["threadId"],
            "subject": p["subject"],
            "rfc822_message_id": p["rfc822_message_id"],
            "in_reply_to": p["in_reply_to"],
            "references": p["references"],
            "attachments": p["attachments"],
            "inline_attachments": [],
            "reply_to_addr": p["reply_to_addr"] or p["from_addr"] or None,
            "html": p["html"],
            "email_headers": {
                "list_unsubscribe": p["list_unsubscribe"],
                "list_id": p["list_id"],
                "precedence": p["precedence"],
                "auto_submitted": p["auto_submitted"],
            },
        },
    )


# ---------------------------------------------------------------- canal


def es_canal_imap(ch: Channel) -> bool:
    return (ch.config or {}).get("transport") == TRANSPORTE_IMAP


def config_imap(ch: Channel) -> ConfigImap:
    cfg = channel_config(ch)
    return ConfigImap(
        usuario=str(cfg.get("usuario") or ""),
        password=str(cfg.get("imap_password") or ""),
        servidor=Servidor(
            str(cfg.get("imap_host") or ""),
            int(cfg.get("imap_port") or 993),
            str(cfg.get("imap_seguridad") or "ssl"),
        ),
        carpeta=str(cfg.get("carpeta") or "INBOX"),
    )


async def _canales_imap() -> list[Channel]:
    async with db_session() as db:
        filas = (
            await db.execute(
                select(Channel)
                .where(Channel.type == ChannelType.email, Channel.enabled.is_(True))
                .order_by(Channel.created_at.asc())
            )
        ).scalars().all()
    return [c for c in filas if es_canal_imap(c)]


async def _guardar_estado(channel_id: uuid.UUID, **cambios) -> None:
    async with db_session() as db:
        ch = (await db.execute(select(Channel).where(Channel.id == channel_id))).scalar_one_or_none()
        if not ch:
            return
        cfg = dict(ch.config or {})
        cfg.update(cambios)
        ch.config = cfg
        await db.commit()


# ---------------------------------------------------------------- sincronizar


async def _procesar(correo: imap_client.Correo, cuenta: str) -> str | None:
    """Guarda un correo y encola a Sofía. Devuelve None si fue bien, o el error."""
    from app.tasks.process_message import process_message as task_process

    try:
        p = parsear_correo(correo.crudo)
        # Lo que manda la propia cuenta (copias, reenvíos a uno mismo) no es de
        # un cliente: no se contesta.
        if cuenta and p["from_addr"] == cuenta.lower():
            return None
        msg_id = await store_incoming(a_incoming(p, correo.crudo))
        if msg_id is not None:
            task_process.delay(str(msg_id))
        return None
    except Exception as exc:  # noqa: BLE001 — un correo raro no para la pasada
        logger.error("imap.correo.fallo", uid=correo.uid, error=str(exc)[:300])
        return str(exc)[:300]


async def sincronizar_canal(ch: Channel) -> dict:
    cfg_canal = dict(ch.config or {})
    cfg = config_imap(ch)
    if not cfg.usuario or not cfg.password or not cfg.servidor.host:
        return {"status": "sin_configurar", "stored": 0}

    try:
        pasada = await asyncio.to_thread(
            imap_client.traer_nuevos,
            cfg,
            cfg_canal.get("imap_uidvalidity"),
            int(cfg_canal.get("imap_last_uid") or 0),
        )
    except ImapError as exc:
        # Contraseña revocada, servidor caído…: la tarjeta del canal lo enseña
        # (`imap_error`) y el resto de canales sigue a lo suyo.
        await _guardar_estado(ch.id, imap_error=str(exc))
        await push_runtime_log(
            level="error",
            event="imap.sync.error",
            message=f"No entran correos del buzón {cfg.usuario}: {exc}",
            channel="email",
        )
        return {"status": "error", "stored": 0}

    fallos = dict(cfg_canal.get("imap_fallos") or {}) if not pasada.resincronizado else {}
    guardados = 0
    ancla = pasada.ultimo_uid
    for correo in pasada.correos:
        error = await _procesar(correo, cfg.usuario)
        if error is None:
            fallos.pop(str(correo.uid), None)
            guardados += 1
            continue
        intentos = int(fallos.get(str(correo.uid), 0)) + 1
        if intentos >= MAX_INTENTOS:
            fallos.pop(str(correo.uid), None)
            await push_runtime_log(
                level="error",
                event="imap.retry.exhausted",
                message=(
                    f"Un correo del buzón {cfg.usuario} no ha podido procesarse tras "
                    f"{intentos} intentos y se salta. Búscalo a mano en el buzón."
                ),
                channel="email",
                error=error,
            )
            continue
        fallos[str(correo.uid)] = intentos
        # El ancla se queda justo antes del primer correo que falló: la
        # pasada siguiente lo vuelve a traer (los ya guardados no se duplican).
        ancla = min(ancla, correo.uid - 1)

    await _guardar_estado(
        ch.id,
        imap_uidvalidity=pasada.uidvalidity,
        imap_last_uid=ancla,
        imap_fallos=fallos,
        imap_error=None,
    )
    return {
        "status": "resync" if pasada.resincronizado else "incremental",
        "stored": guardados,
        "found": len(pasada.correos),
        "fallos": len(fallos),
    }


async def sincronizar_buzones() -> dict:
    """Una pasada por cada buzón IMAP activo, cada uno con su lock."""
    canales = await _canales_imap()
    if not canales:
        return {"status": "no_channel", "stored": 0}
    redis = get_redis()
    resumen: dict = {"status": "ok", "stored": 0, "buzones": len(canales)}
    for ch in canales:
        clave = _LOCK.format(channel_id=ch.id)
        token: str | None = uuid.uuid4().hex
        try:
            if not await redis.set(clave, token, nx=True, ex=_LOCK_TTL):
                logger.info("imap.sync.locked", channel_id=str(ch.id))
                continue
        except Exception as exc:  # noqa: BLE001 — sin Redis se sigue, sin lock
            logger.warning("imap.sync.lock_failed", error=str(exc))
            token = None
        try:
            r = await sincronizar_canal(ch)
            resumen["stored"] += r.get("stored", 0)
        finally:
            if token:
                try:
                    await redis.eval(_RELEASE_LOCK_LUA, 1, clave, token)
                except Exception as exc:  # noqa: BLE001 — el TTL lo suelta
                    logger.warning("imap.sync.unlock_failed", error=str(exc))
    return resumen
