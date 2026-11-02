"""Archivos que Sofía le manda al paciente (Fase 11, SPEC-06).

Hasta ahora Sofía solo mandaba texto, y adjuntar solo lo podía hacer el
operador, y solo por WhatsApp. Una herramienta de n8n puede devolver
`archivos: [{tipo, url, pie?, nombre?}]` y aquí se hace el resto:

1. **Bajarlo** (`descargar`): solo HTTPS a hosts públicos, sin redirecciones,
   con tope de tamaño por tipo y comprobando que el contenido es lo que dice
   (nada de HTML, SVG ni ejecutables disfrazados de imagen).
2. **Guardarlo** en el volumen, como los adjuntos del operador: el inbox lo
   enseña aunque n8n borre su copia.
3. **Mandarlo** por el canal (`enviar_archivo`), con la misma ventana de 24 h
   que el texto:
   - WhatsApp por YCloud o Meta: se sube al proveedor y se manda por su id.
   - WhatsApp, Instagram y Messenger por Zernio: Zernio lo baja de un enlace
     público firmado del CRM.
   - Chat de la web: el widget lo pinta desde ese mismo enlace.
   - Correo, voz e Instagram por la API oficial: no; se dice por qué.
4. En **Entrenamiento** no sale nada: queda como sugerencia con el archivo, y
   sale cuando alguien le da Enviar (`conversations.send_draft`).
"""
from __future__ import annotations

import mimetypes
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.channel_capabilities import capacidades
from app.core.logging import get_logger
from app.core.url_guard import validate_public_https_url
from app.models.conversation import Conversation, ConversationCanal
from app.services.media import MediaValidationError, categorize_mime, max_bytes_for, save_local

logger = get_logger(__name__)

MAX_ARCHIVOS_POR_LLAMADA = 3
# Lo que se baja de n8n, como mucho, aunque WhatsApp acepte documentos más
# grandes: se lee en memoria y no tiene sentido mandar 100 MB por un chat.
MAX_BYTES_DESDE_N8N = 16 * 1024 * 1024
TIPOS = {"imagen": "image", "audio": "audio", "video": "video", "documento": "document"}
NOMBRE_DEL_TIPO = {v: k for k, v in TIPOS.items()}
_ZERNIO_TIPO = {"image": "image", "audio": "audio", "video": "video", "document": "file"}
_TIMEOUT_DESCARGA_S = 20.0


class ArchivoRechazado(ValueError):
    """No se puede mandar; el mensaje se le dice a Sofía (y al panel)."""


@dataclass
class ArchivoGuardado:
    kind: str           # image | audio | video | document
    mime: str
    size: int
    nombre: str         # el que verá el paciente (documentos)
    ruta: str           # `/uploads/<archivo>`, lo que va a `messages.media_url`
    contenido: bytes


def _parece_peligroso(datos: bytes) -> bool:
    """HTML, SVG, XML o scripts no son una imagen ni un PDF, digan lo que digan."""
    inicio = datos[:512].lstrip().lower()
    return (
        inicio.startswith(b"<")
        or inicio.startswith(b"mz")  # ejecutable de Windows
        or inicio.startswith(b"#!")
        or b"<script" in inicio
    )


async def descargar(url: str, tipo: str, nombre: str | None = None) -> ArchivoGuardado:
    kind = TIPOS.get((tipo or "").strip().lower())
    if kind is None:
        raise ArchivoRechazado(f"tipo «{tipo}» desconocido (imagen, audio, video o documento)")
    ok, motivo = await validate_public_https_url(url or "")
    if not ok:
        raise ArchivoRechazado(f"URL rechazada: {motivo}")
    tope = min(max_bytes_for(kind), MAX_BYTES_DESDE_N8N)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_DESCARGA_S, follow_redirects=False) as client:
            async with client.stream("GET", url) as r:
                if r.status_code != 200:
                    raise ArchivoRechazado(f"no se pudo bajar (HTTP {r.status_code})")
                partes: list[bytes] = []
                leidos = 0
                async for trozo in r.aiter_bytes():
                    leidos += len(trozo)
                    if leidos > tope:
                        raise ArchivoRechazado(f"pesa más de {tope // (1024 * 1024)} MB")
                    partes.append(trozo)
                mime = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    except httpx.HTTPError as exc:
        raise ArchivoRechazado(f"no se pudo bajar ({type(exc).__name__})") from exc
    datos = b"".join(partes)
    if not datos:
        raise ArchivoRechazado("está vacío")
    if _parece_peligroso(datos):
        raise ArchivoRechazado("el contenido no es un archivo que se pueda mandar")
    if not mime or mime == "application/octet-stream":
        mime = mimetypes.guess_type(nombre or url)[0] or ""
    try:
        real = categorize_mime(mime)
    except MediaValidationError as exc:
        raise ArchivoRechazado(f"formato no admitido ({mime or 'desconocido'})") from exc
    if real == "sticker":
        real = "image"
    if real != kind:
        raise ArchivoRechazado(f"se anunció como {tipo} y es {NOMBRE_DEL_TIPO.get(real, real)}")
    ext = mimetypes.guess_extension(mime) or ""
    limpio = (nombre or "").strip()[:120] or f"{NOMBRE_DEL_TIPO[kind]}{ext}"
    _archivo, ruta = save_local(datos, limpio if "." in limpio else f"{limpio}{ext}")
    return ArchivoGuardado(kind=kind, mime=mime, size=len(datos), nombre=limpio, ruta=ruta, contenido=datos)


def desde_mensaje(msg) -> ArchivoGuardado:
    """El archivo de un mensaje ya guardado (una sugerencia de Entrenamiento que
    alguien acaba de darle Enviar)."""
    from app.services.media import resolve_local_path

    archivo = (msg.media_url or "").rsplit("/", 1)[-1]
    ruta = resolve_local_path(archivo) if archivo else None
    if ruta is None:
        raise ArchivoRechazado("el archivo ya no está en el servidor")
    with open(ruta, "rb") as f:
        contenido = f.read()
    return ArchivoGuardado(
        kind=msg.media_type or "document",
        mime=msg.media_mime or "application/octet-stream",
        size=len(contenido),
        nombre=msg.media_filename or archivo,
        ruta=msg.media_url,
        contenido=contenido,
    )


def canal_admite(canal: ConversationCanal | str, kind: str) -> bool:
    return kind in capacidades(canal).archivos_salientes


async def _telefono(conv: Conversation) -> str:
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.contact import Contact

    async with db_session() as db:
        return (
            await db.execute(select(Contact.telefono).where(Contact.id == conv.contact_id))
        ).scalar_one()


async def _por_zernio(conv: Conversation, guardado: ArchivoGuardado, pie: str | None, *, canal: str) -> str:
    from app.providers.zernio import ZernioError, ZernioWindowClosed
    from app.services import zernio
    from app.services.channel_sender import MessagingWindowClosed, SendNotConfirmed
    from app.services.media_publica import url_publica

    destino = await zernio.destino_de_conversacion(conv)
    zc = await zernio.cliente()
    if destino is None or zc is None:
        raise SendNotConfirmed(f"Esta conversación de {canal} no tiene por dónde mandar el archivo")
    conversation_id, account_id = destino
    try:
        envio = await zc.send_message(
            conversation_id,
            account_id,
            pie or "",
            idempotency_key=str(uuid.uuid4()),
            attachment_url=url_publica(guardado.ruta),
            attachment_type=_ZERNIO_TIPO[guardado.kind],
        )
    except ZernioWindowClosed as e:
        raise MessagingWindowClosed(f"{canal} no deja enviar: la ventana de mensajería está cerrada.") from e
    except ZernioError as e:
        raise SendNotConfirmed(f"Zernio no confirmó el archivo por {canal}: {e}") from e
    return envio.message_id


async def enviar_archivo(conv: Conversation, guardado: ArchivoGuardado, *, pie: str | None = None) -> str:
    """Lo manda por el canal de la conversación y devuelve el id del proveedor.

    Lanza `ArchivoRechazado` si el canal no admite ese tipo, y las mismas que el
    texto (`MessagingWindowClosed`, `SendNotConfirmed`) si la ventana está
    cerrada o el proveedor no confirma.
    """
    from app.services.channel_sender import (
        WA_SESSION_WINDOW_HOURS,
        MessagingWindowClosed,
        _require_confirmed,
        _ventana_meta,
        contar_saliente_whatsapp,
        last_inbound_age_hours,
    )
    from app.services.zernio import conversacion_va_por_zernio

    if not canal_admite(conv.canal, guardado.kind):
        raise ArchivoRechazado(
            f"{conv.canal.value} no admite {NOMBRE_DEL_TIPO.get(guardado.kind, guardado.kind)}"
        )

    if conv.canal == ConversationCanal.whatsapp:
        horas = await last_inbound_age_hours(conv)
        if horas is not None and horas > WA_SESSION_WINDOW_HOURS:
            raise MessagingWindowClosed(
                "Han pasado más de 24 h desde el último mensaje del cliente: WhatsApp solo "
                "deja mandar una plantilla aprobada."
            )
        if await conversacion_va_por_zernio(conv):
            enviado = await _por_zernio(conv, guardado, pie, canal="WhatsApp")
        else:
            from app.providers.whatsapp.ycloud import get_whatsapp_provider

            wa = get_whatsapp_provider()
            media_id = await wa.upload_media(guardado.contenido, guardado.mime, guardado.nombre)
            enviado = _require_confirmed(
                await wa.send_media(
                    await _telefono(conv),
                    media_id,
                    guardado.kind,  # type: ignore[arg-type]
                    caption=pie,
                    filename=guardado.nombre if guardado.kind == "document" else None,
                ),
                "whatsapp",
            )
        await contar_saliente_whatsapp(conv.id)
        return enviado

    if conv.canal in (ConversationCanal.instagram_dm, ConversationCanal.messenger):
        nombre = "Instagram" if conv.canal == ConversationCanal.instagram_dm else "Messenger"
        await _ventana_meta(conv, nombre)
        if not await conversacion_va_por_zernio(conv):
            raise ArchivoRechazado("por la API oficial de Instagram el CRM no manda archivos")
        return await _por_zernio(conv, guardado, pie, canal=nombre)

    if conv.canal == ConversationCanal.web:
        from app.core.events import event_bus
        from app.services.channel_sender import webchat_channel

        ext_id = str(uuid.uuid4())
        await event_bus.publish(
            webchat_channel(conv.id),
            "message.out",
            {
                "conversation_id": str(conv.id),
                "message_id": ext_id,
                "text": pie or "",
                "media": media_para_el_widget(guardado.ruta, guardado.kind, guardado.nombre),
            },
        )
        return ext_id

    raise ArchivoRechazado(f"{conv.canal.value} no admite archivos")


def media_para_el_widget(ruta: str, kind: str, nombre: str | None) -> dict:
    """Lo que necesita `widget.js` para pintar el archivo. `archivo` (el nombre
    guardado) es estable, y con él se evita pintarlo dos veces."""
    from app.services.media_publica import url_publica

    return {
        "tipo": kind,
        "url": url_publica(ruta),
        "nombre": nombre or "",
        "archivo": ruta.rsplit("/", 1)[-1],
    }


async def registrar(
    conv: Conversation,
    guardado: ArchivoGuardado,
    *,
    pie: str | None,
    extra: dict[str, Any],
) -> uuid.UUID:
    """El archivo en el hilo, como mensaje de Sofía (o sugerencia, según `extra`)."""
    from app.core.events import event_bus, inbox_channel
    from app.db.session import db_session
    from app.models.message import Message, MessageRole

    async with db_session() as db:
        msg = Message(
            conversation_id=conv.id,
            rol=MessageRole.assistant,
            contenido=pie,
            media_type=guardado.kind,
            media_url=guardado.ruta,
            media_mime=guardado.mime,
            media_size=guardado.size,
            media_filename=guardado.nombre,
            extra=extra,
        )
        db.add(msg)
        conv_db = await db.get(Conversation, conv.id)
        if conv_db is not None:
            conv_db.last_message_at = datetime.now(timezone.utc)
        await db.commit()
        msg_id = msg.id
    try:
        await event_bus.publish(
            inbox_channel(),
            "message.new",
            {"conversation_id": str(conv.id), "message_id": str(msg_id), "rol": "assistant"},
        )
    except Exception:  # noqa: BLE001 — el inbox se pone al día al recargar
        pass
    return msg_id


async def mandar_archivos_de_n8n(archivos: Any, ctx: dict[str, Any], herramienta: str) -> list[dict]:
    """Los archivos que devolvió una herramienta: bajarlos, guardarlos y
    mandarlos (o dejarlos en la sugerencia). Devuelve qué pasó con cada uno,
    para Sofía. Nunca lanza: un archivo que no sale no tumba la respuesta."""
    from app.db.session import db_session
    from app.services.channel_sender import MessagingWindowClosed, SendNotConfirmed
    from app.services.delivery_status import marcar_enviado

    if not isinstance(archivos, list) or not archivos:
        return []
    resultado: list[dict] = []
    from app.services.n8n_tools import es_llamada

    if es_llamada(ctx):
        return [
            {"nombre": str((a or {}).get("nombre") or ""), "estado": "no enviado",
             "motivo": "en una llamada no se pueden mandar archivos"}
            for a in archivos[:MAX_ARCHIVOS_POR_LLAMADA] if isinstance(a, dict)
        ]
    conv_id = ctx.get("conversation_id")
    conv = None
    if conv_id:
        async with db_session() as db:
            conv = await db.get(Conversation, uuid.UUID(str(conv_id)))
            if conv is not None:
                db.expunge(conv)
    for i, a in enumerate(archivos):
        if not isinstance(a, dict):
            continue
        tipo = str(a.get("tipo") or "")
        nombre = str(a.get("nombre") or "").strip() or None
        pie = (str(a.get("pie") or "").strip() or None)
        item: dict[str, Any] = {"tipo": tipo, "nombre": nombre or ""}
        if i >= MAX_ARCHIVOS_POR_LLAMADA:
            resultado.append({**item, "estado": "no enviado", "motivo": "como mucho 3 archivos por llamada"})
            continue
        if conv is None:
            resultado.append({**item, "estado": "no enviado", "motivo": "no hay conversación"})
            continue
        kind = TIPOS.get(tipo.strip().lower())
        if kind and not canal_admite(conv.canal, kind):
            resultado.append({**item, "estado": "no enviado",
                              "motivo": f"este canal no admite {tipo}; ofrécelo de otra forma"})
            continue
        try:
            guardado = await descargar(str(a.get("url") or ""), tipo, nombre)
            item["nombre"] = guardado.nombre
            base = {"origen": "n8n", "herramienta": herramienta}
            if ctx.get("is_training"):
                await registrar(conv, guardado, pie=pie, extra={**base, "is_draft": True,
                                                                  "draft_sent": False, "training": True})
                resultado.append({**item, "estado": "en la sugerencia",
                                  "motivo": "Entrenamiento: sale cuando una persona la envíe"})
                continue
            ext_id = await enviar_archivo(conv, guardado, pie=pie)
            await registrar(conv, guardado, pie=pie, extra=marcar_enviado(base, ext_id, conv.canal))
            resultado.append({**item, "estado": "enviado"})
        except (ArchivoRechazado, MessagingWindowClosed, SendNotConfirmed) as e:
            resultado.append({**item, "estado": "no enviado", "motivo": str(e)[:200]})
        except Exception as e:  # noqa: BLE001
            logger.error("n8n.archivo.fallo", herramienta=herramienta, error=str(e)[:200])
            resultado.append({**item, "estado": "no enviado", "motivo": "falló el envío"})
    return resultado
