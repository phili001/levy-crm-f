"""Responder por SMTP a los hilos de un buzón IMAP.

Es la otra mitad de `imap_ingest.py`. Lo que tiene que cumplir una respuesta
para que el cliente la vea donde toca:

  - En el MISMO hilo de su correo: `In-Reply-To` = el `Message-ID` que
    contestamos y `References` = la cadena anterior + ese id. La raíz de
    `References` es la que `imap_ingest` usa para agrupar, así que la siguiente
    respuesta del cliente cae en la misma conversación del CRM.
  - Con `Message-ID` propio (del dominio del buzón): es el id que guarda el
    CRM como `provider_message_id`.
  - Copiada en Enviados. Gmail lo hace solo con lo que sale por su SMTP; la
    mayoría de hostings no, y el cliente vería su buzón sin la respuesta.

Separado de `providers/email/smtp_client.py` a propósito: aquel manda el correo
transaccional del panel (recuperar contraseña) con las credenciales globales;
este habla en nombre del buzón de un canal.
"""
from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import db_session
from app.models.channel import Channel, ChannelType
from app.models.conversation import Conversation
from app.providers.email import imap_client
from app.providers.email.imap_client import ConfigImap, Servidor, dominio_de, traducir_error
from app.providers.gmail.client import _ensure_re_subject
from app.services.channel_secrets import channel_config

logger = get_logger(__name__)

TIMEOUT_SEGUNDOS = 30


class EnvioSmtpError(Exception):
    """El envío no salió; el mensaje se puede enseñar tal cual."""


async def canal_imap() -> Channel | None:
    """El canal de correo por IMAP (el más antiguo si hubiera varios)."""
    async with db_session() as db:
        canales = (
            await db.execute(
                select(Channel)
                .where(Channel.type == ChannelType.email, Channel.enabled.is_(True))
                .order_by(Channel.created_at.asc())
            )
        ).scalars().all()
    return next((c for c in canales if (c.config or {}).get("transport") == "imap"), None)


def es_hilo_imap(conv: Conversation) -> bool:
    return (conv.gmail_thread_id or "").startswith("imap:")


def en_no_responder(remitente: str, lista: list[str] | None) -> bool:
    """¿El remitente está en la lista de no-responder del buzón?

    Cada entrada es una dirección (`facturas@proveedor.com`) o un dominio
    (`@proveedor.com` o `proveedor.com`).
    """
    correo = (remitente or "").strip().lower()
    if not correo:
        return False
    dominio = dominio_de(correo)
    for entrada in lista or []:
        e = str(entrada).strip().lower()
        if not e:
            continue
        if e == correo or e.lstrip("@") == dominio:
            return True
    return False


async def _nombre_remitente() -> str:
    """El nombre que ve el paciente junto a la dirección.

    Sale de Apariencia, que es donde la clínica escribe su nombre. Antes salía
    de `APP_NAME`, que en la instalación en un clic vale «CRM Omnicanal»: los
    correos de Sofía llegaban firmados por el producto, no por la clínica. Sin
    nombre en Apariencia (o si no se puede leer), el de la instalación.
    """
    from app.db.session import db_session
    from app.services import apariencia

    try:
        async with db_session() as db:
            nombre = (await apariencia.leer(db)).nombre
    except Exception as exc:  # noqa: BLE001
        logger.warning("correo_smtp.nombre_apariencia_ilegible", error=type(exc).__name__)
        nombre = None
    return nombre or settings.APP_NAME or ""


def construir_respuesta(
    *,
    desde: str,
    para: str,
    asunto: str,
    cuerpo: str,
    in_reply_to: str | None,
    references: str | None,
    nombre: str | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    nombre = settings.APP_NAME if nombre is None else nombre
    msg["From"] = formataddr((nombre, desde)) if nombre else desde
    msg["To"] = para
    msg["Subject"] = _ensure_re_subject(asunto)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=dominio_de(desde) or None)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(cuerpo)
    return msg


def _enviar_sync(servidor: Servidor, usuario: str, password: str, msg: EmailMessage) -> None:
    contexto = ssl.create_default_context()
    if servidor.seguridad == "ssl":
        with smtplib.SMTP_SSL(servidor.host, servidor.port, timeout=TIMEOUT_SEGUNDOS, context=contexto) as s:
            s.login(usuario, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(servidor.host, servidor.port, timeout=TIMEOUT_SEGUNDOS) as s:
            s.ehlo()
            s.starttls(context=contexto)
            s.ehlo()
            s.login(usuario, password)
            s.send_message(msg)


def probar_smtp(servidor: Servidor, usuario: str, password: str) -> None:
    """Entra en el SMTP sin enviar nada. Lanza EnvioSmtpError legible."""
    try:
        contexto = ssl.create_default_context()
        if servidor.seguridad == "ssl":
            with smtplib.SMTP_SSL(servidor.host, servidor.port, timeout=TIMEOUT_SEGUNDOS, context=contexto) as s:
                s.login(usuario, password)
        else:
            with smtplib.SMTP(servidor.host, servidor.port, timeout=TIMEOUT_SEGUNDOS) as s:
                s.ehlo()
                s.starttls(context=contexto)
                s.ehlo()
                s.login(usuario, password)
    except Exception as exc:  # noqa: BLE001
        raise EnvioSmtpError(traducir_error(exc, "SMTP")) from exc


async def _copiar_en_enviados(ch: Channel, cfg: dict, msg: EmailMessage) -> None:
    """Best-effort: la respuesta ya salió; no poder copiarla no es un fallo de
    envío, pero se avisa para que no se note solo cuando el cliente pregunte."""
    smtp_host = str(cfg.get("smtp_host") or "")
    if smtp_host.endswith("gmail.com"):
        return  # Gmail guarda en Enviados lo que sale por su SMTP.
    imap = ConfigImap(
        usuario=str(cfg.get("usuario") or ""),
        password=str(cfg.get("imap_password") or ""),
        servidor=Servidor(
            str(cfg.get("imap_host") or ""),
            int(cfg.get("imap_port") or 993),
            str(cfg.get("imap_seguridad") or "ssl"),
        ),
    )
    try:
        carpeta = cfg.get("carpeta_enviados") or await asyncio.to_thread(
            imap_client.detectar_enviados, imap
        )
        if not carpeta:
            logger.warning("smtp.enviados.sin_carpeta", canal=str(ch.id))
            return
        await asyncio.to_thread(imap_client.guardar_en_enviados, imap, carpeta, msg.as_bytes())
    except Exception as exc:  # noqa: BLE001
        from app.services.runtime_logs import push_runtime_log

        await push_runtime_log(
            level="warn",
            event="smtp.enviados.fallo",
            message=(
                "La respuesta salió, pero no se pudo copiar en la carpeta de "
                f"Enviados del buzón: {str(exc)[:160]}"
            ),
            channel="email",
        )


async def enviar_respuesta(
    ch: Channel,
    *,
    para: str,
    asunto: str,
    cuerpo: str,
    in_reply_to: str | None,
    references: str | None,
) -> str:
    """Envía la respuesta y devuelve su `Message-ID`. Lanza EnvioSmtpError."""
    cfg = channel_config(ch)
    usuario = str(cfg.get("usuario") or "")
    password = str(cfg.get("smtp_password") or cfg.get("imap_password") or "")
    servidor = Servidor(
        str(cfg.get("smtp_host") or ""),
        int(cfg.get("smtp_port") or 587),
        str(cfg.get("smtp_seguridad") or "starttls"),
    )
    if not (usuario and password and servidor.host):
        raise EnvioSmtpError("El buzón no tiene configurado el envío (SMTP).")
    msg = construir_respuesta(
        desde=usuario,
        para=para,
        asunto=asunto,
        cuerpo=cuerpo,
        in_reply_to=in_reply_to,
        references=references,
        nombre=await _nombre_remitente(),
    )
    try:
        await asyncio.to_thread(_enviar_sync, servidor, usuario, password, msg)
    except Exception as exc:  # noqa: BLE001
        raise EnvioSmtpError(traducir_error(exc, "SMTP")) from exc
    await _copiar_en_enviados(ch, cfg, msg)
    return str(msg["Message-ID"])
