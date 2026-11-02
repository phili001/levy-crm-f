"""Zernio del lado del CRM: qué canales van por Zernio, con qué llave, y cómo
se contesta y se baja un adjunto de una conversación que entró por ahí.

El adaptador puro (HTTP y lectura del webhook) vive en `app/providers/zernio/`.
Aquí está lo que necesita la BD.

Dónde vive cada cosa (Fase 7, T-7.1.3):
- Canal `instagram_dm` con `config.transport = "zernio"` y canal `messenger`:
  `zernio_api_key` y `zernio_webhook_secret` cifrados, `zernio_account_id` y
  `zernio_profile_id` en claro. Los dos comparten llave y secreto: es UNA
  cuenta de Zernio por instalación y el secreto es por endpoint, no por cuenta.
- Canal `whatsapp` con `config.wa_por_zernio = true`: usa la llave de los
  otros dos; su `zernio_account_id` es opcional.
- Por conversación: el `conversationId` y el `accountId` de Zernio van en el
  `extra.zernio` de cada mensaje que entra por ahí. Se contesta con los del
  último. Nunca se comparan con los ids del listado (en IG/FB no coinciden).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import desc, select

from app.core.channel_capabilities import capacidades
from app.core.logging import get_logger
from app.models.channel import Channel, ChannelType
from app.models.conversation import Conversation, ConversationCanal
from app.models.message import Message
from app.providers.zernio.client import ZernioClient, ZernioError
from app.providers.zernio.dispatch import CANAL_POR_PLATAFORMA
from app.services.channel_secrets import channel_config

# `db_session` se importa dentro de cada función, como en `channel_sender`: las
# pruebas lo sustituyen con `patch("app.db.session.db_session")` y un import
# de módulo se quedaría con el doble para el resto de la suite.

logger = get_logger(__name__)

TRANSPORT_ZERNIO = "zernio"

# El tope de un mensaje por plataforma (en BYTES de UTF-8, no en caracteres)
# vive con el resto de lo de cada canal: `core/channel_capabilities.py`.


@dataclass
class CanalZernio:
    channel_id: uuid.UUID
    tipo: str
    enabled: bool
    account_id: str = ""
    api_key: str = ""
    webhook_secret: str = ""
    config: dict = field(default_factory=dict)


def _va_por_zernio(ch: Channel, cfg: dict) -> bool:
    if ch.type == ChannelType.messenger:
        return True  # Messenger solo existe por Zernio
    if ch.type == ChannelType.instagram_dm:
        return cfg.get("transport") == TRANSPORT_ZERNIO
    if ch.type == ChannelType.whatsapp:
        return bool(cfg.get("wa_por_zernio"))
    return False


async def canales_zernio() -> list[CanalZernio]:
    """Los canales que van por Zernio, activos o no, con sus secretos."""
    from app.db.session import db_session

    async with db_session() as db:
        filas = (
            await db.execute(
                select(Channel).where(
                    Channel.type.in_(
                        [ChannelType.instagram_dm, ChannelType.messenger, ChannelType.whatsapp]
                    )
                )
            )
        ).scalars().all()
    canales = []
    for ch in filas:
        cfg = channel_config(ch)
        if not _va_por_zernio(ch, cfg):
            continue
        canales.append(
            CanalZernio(
                channel_id=ch.id,
                tipo=ch.type.value,
                enabled=bool(ch.enabled),
                account_id=str(cfg.get("zernio_account_id") or ""),
                api_key=str(cfg.get("zernio_api_key") or ""),
                webhook_secret=str(cfg.get("zernio_webhook_secret") or ""),
                config=cfg,
            )
        )
    return canales


def secreto_webhook(canales: list[CanalZernio]) -> str:
    return next((c.webhook_secret for c in canales if c.webhook_secret), "")


def api_key(canales: list[CanalZernio]) -> str:
    return next((c.api_key for c in canales if c.api_key), "")


def canal_para(canales: list[CanalZernio], plataforma: str, account_id: str) -> CanalZernio | None:
    """El canal ACTIVO que recibe esta plataforma y esta cuenta, o None.

    Si el canal tiene cuenta guardada y no coincide, no es nuestro: una llave
    de Zernio puede tener cuentas de otras clínicas (modo agencia) y sin este
    filtro Sofía contestaría en nombre de otra.
    """
    tipo = CANAL_POR_PLATAFORMA.get(plataforma)
    for c in canales:
        if c.tipo != tipo or not c.enabled:
            continue
        if c.account_id and account_id and c.account_id != account_id:
            continue
        return c
    return None


async def cliente() -> ZernioClient | None:
    llave = api_key(await canales_zernio())
    return ZernioClient(llave) if llave else None


async def destino_de_conversacion(conv: Conversation) -> tuple[str, str] | None:
    """(conversationId, accountId) de Zernio del último mensaje que los trae."""
    from app.db.session import db_session

    async with db_session() as db:
        filas = (
            await db.execute(
                select(Message.extra)
                .where(
                    Message.conversation_id == conv.id,
                    Message.extra["zernio"].isnot(None),
                )
                .order_by(desc(Message.created_at))
                .limit(1)
            )
        ).scalars().all()
    for extra in filas:
        z = (extra or {}).get("zernio") or {}
        if z.get("conversation_id") and z.get("account_id"):
            return str(z["conversation_id"]), str(z["account_id"])
    return None


async def conversacion_va_por_zernio(conv: Conversation) -> bool:
    return await destino_de_conversacion(conv) is not None


def trocear(texto: str, max_bytes: int) -> list[str]:
    """Parte un texto largo en trozos que quepan, cortando por párrafo, luego
    por frase y luego por palabra. Nunca parte un carácter por la mitad."""
    texto = (texto or "").strip()

    def cabe(s: str) -> bool:
        return len(s.encode()) <= max_bytes

    if cabe(texto):
        return [texto] if texto else []

    # Piezas mínimas con el separador que las unía en el original, para que
    # al volver a juntarlas un párrafo siga siendo párrafo y una frase, frase.
    piezas: list[tuple[str, str]] = []
    for parrafo in texto.split("\n\n"):
        sep = "\n\n"
        for linea in parrafo.split("\n"):
            for palabra in linea.split(" "):
                while palabra and not cabe(palabra):
                    # Una "palabra" enorme (una URL): se corta a lo bruto.
                    corte = max_bytes
                    while not cabe(palabra[:corte]):
                        corte -= 1
                    piezas.append((palabra[:corte], sep))
                    palabra, sep = palabra[corte:], ""
                if palabra:
                    piezas.append((palabra, sep))
                sep = " "
            sep = "\n"

    trozos: list[str] = []
    actual = ""
    for pieza, sep in piezas:
        candidato = f"{actual}{sep}{pieza}" if actual else pieza
        if cabe(candidato):
            actual = candidato
        else:
            trozos.append(actual)
            actual = pieza
    if actual:
        trozos.append(actual)
    return trozos


async def enviar_texto(conv: Conversation, texto: str, *, human_agent: bool = False) -> str:
    """Contesta por Zernio. Devuelve el `mid` del último trozo enviado.

    Lanza `ZernioError` (o una subclase) si no se puede: el llamante
    (`channel_sender`) la traduce a lo que entiende el panel y el agente.
    """
    destino = await destino_de_conversacion(conv)
    if destino is None:
        raise ZernioError("Esta conversación no tiene conversación de Zernio a la que contestar")
    conversation_id, account_id = destino
    zc = await cliente()
    if zc is None:
        raise ZernioError("No hay API key de Zernio guardada", status=0)

    trozos = trocear(texto, capacidades(conv.canal).max_text_bytes or 2000) or [texto]
    ultimo = ""
    for trozo in trozos:
        envio = await zc.send_message(
            conversation_id,
            account_id,
            trozo,
            idempotency_key=str(uuid.uuid4()),
            # Entre 24 h y 7 días Meta solo admite la etiqueta de agente humano
            # (documentada por Zernio para IG y FB).
            messaging_type="MESSAGE_TAG" if human_agent else None,
            message_tag="HUMAN_AGENT" if human_agent else None,
        )
        if envio.warnings:
            logger.info("zernio.send.warnings", warnings=envio.warnings[:3])
        ultimo = envio.message_id
    return ultimo


async def descargar_adjunto(
    zernio: dict, index: int, url: str | None, *, max_bytes: int
) -> tuple[bytes, str]:
    """(bytes, mime) de un adjunto recibido por Zernio.

    Primero la URL del evento; si falla (caducó, 403 de la CDN), se pide una
    nueva a Zernio con `messageId` + índice y se reintenta una vez.
    """
    from app.providers.zernio import media

    if url:
        try:
            return await media.descargar(url, max_bytes=max_bytes)
        except media.DescargaBloqueada:
            raise
        except ValueError:
            raise  # demasiado grande: pedir otra URL no lo arregla
        except Exception as e:  # noqa: BLE001 — la URL caducó: se resuelve abajo
            logger.info("zernio.media.url_caducada", error=str(e)[:120])
    zc = await cliente()
    if zc is None:
        raise ZernioError("No hay API key de Zernio para resolver el adjunto")
    nueva = await zc.resolve_attachment(
        str(zernio.get("conversation_id") or ""),
        str(zernio.get("platform_message_id") or ""),
        index,
        str(zernio.get("account_id") or ""),
    )
    if not nueva:
        raise ZernioError("Zernio no devolvió URL para el adjunto")
    return await media.descargar(nueva, max_bytes=max_bytes)


# ---------- Historial al conectar (T-7.2.5) ----------
#
# Lo que ya había en la cuenta antes de conectarla NO dispara webhooks: sin
# esto, la primera vez que un paciente antiguo escribe, Sofía contesta sin saber
# nada de lo que se habló antes. Zernio conserva hasta 500 conversaciones y 500
# mensajes por conversación; se traen tal cual, como contexto.

MAX_CONVERSACIONES_HISTORIAL = 500
MAX_MENSAJES_POR_CONVERSACION = 500
# Zernio da 60 peticiones por minuto con 1-2 cuentas: una cada 1.1 s cabe con
# holgura. Sin esta pausa, un Instagram con historial chocaba con el límite a
# los pocos segundos y la importación se quedaba a medias.
PAUSA_HISTORIAL_S = 1.1
# Lo que puede pedir esperar Zernio tras un 429 (su ventana es de un minuto).
ESPERA_MAX_429_HISTORIAL_S = 90.0
# Marca de los ecos que entran con el historial (ver `store_outgoing_echo`).
SENT_BY_HISTORIAL = "zernio_historial"


def cliente_historial(llave: str) -> ZernioClient:
    return ZernioClient(
        llave,
        max_espera_429=ESPERA_MAX_429_HISTORIAL_S,
        intentos_429=4,
        intervalo_min_s=PAUSA_HISTORIAL_S,
    )


async def _mensajes_de(zc: ZernioClient, conversation_id: str, account_id: str) -> list[dict]:
    mensajes: list[dict] = []
    cursor = None
    while len(mensajes) < MAX_MENSAJES_POR_CONVERSACION:
        pagina, cursor = await zc.get_messages(conversation_id, account_id, cursor)
        mensajes.extend(pagina)
        if not cursor:
            break
    return mensajes[:MAX_MENSAJES_POR_CONVERSACION]


async def cerrar_historial(cliente_id: str, canal: ConversationCanal) -> None:
    """Deja como historial lo importado de este cliente en este canal.

    - Sus mensajes importados quedan leídos: no son pendientes de nadie.
    - La conversación se archiva SOLO si todo lo que tiene es historial. Si hay
      algo más (un mensaje de verdad, una respuesta del CRM), es una
      conversación en curso y se queda en la bandeja.

    Se decide por el contenido y no por "quién la creó" a propósito: así una
    importación que se cortó a medias se arregla con solo volver a lanzarla.
    """
    from datetime import datetime, timezone

    from sqlalchemy import and_, func, not_, or_, update

    from app.db.session import db_session
    from app.models.contact import Contact
    from app.models.message import MessageRole

    # `coalesce`: un mensaje sin la marca da NULL, y un NOT(NULL) lo dejaría
    # fuera de la cuenta de "mensajes que no son historial". Así, una
    # conversación con mensajes reales parecía solo historial y se archivaba.
    es_entrante_historial = and_(
        Message.rol == MessageRole.user,
        func.coalesce(Message.extra["zernio"]["historial"].astext, "") == "true",
    )
    es_eco_historial = func.coalesce(Message.extra["sent_by"].astext, "") == SENT_BY_HISTORIAL
    ahora = datetime.now(timezone.utc)
    async with db_session() as db:
        convs = (
            await db.execute(
                select(Conversation)
                .join(Contact, Contact.id == Conversation.contact_id)
                .where(
                    # Por `identidad_canal` también: la ficha puede haberse
                    # unido a la de su WhatsApp (Fase 10.1).
                    or_(Contact.telefono == cliente_id, Contact.identidad_canal == cliente_id),
                    Conversation.canal == canal,
                )
            )
        ).scalars().all()
        for conv in convs:
            await db.execute(
                update(Message)
                .where(
                    Message.conversation_id == conv.id,
                    es_entrante_historial,
                    Message.leido_at.is_(None),
                )
                .values(leido_at=ahora)
            )
            ajenos = (
                await db.execute(
                    select(Message.id)
                    .where(
                        Message.conversation_id == conv.id,
                        not_(or_(es_entrante_historial, es_eco_historial)),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if ajenos is None and conv.archived_at is None:
                conv.archived_at = ahora
        await db.commit()


async def importar_historial(channel_id: uuid.UUID) -> dict:
    """Trae el historial de la cuenta de un canal de Zernio.

    Entra como ya leído y SIN encolar al agente: son conversaciones viejas,
    contestarlas ahora sería escribir a gente que ya no espera nada. Las que
    solo tienen historial se archivan; en cuanto el paciente vuelva a
    escribir, salen de archivadas solas (`store_incoming`) con todo su
    contexto. Las que tienen algo más (p. ej. las de la vía oficial de
    Instagram) se quedan en la bandeja. Cada conversación se cierra al
    terminarla, así que un corte a medias no deja la bandeja llena de
    conversaciones viejas. Idempotente: relanzarla no duplica nada.
    """
    from app.providers.whatsapp.base import IncomingMessage
    from app.providers.zernio.dispatch import identificador_cliente
    from app.services.conversation import store_incoming, store_outgoing_echo

    canales = await canales_zernio()
    canal = next((c for c in canales if c.channel_id == channel_id), None)
    llave = api_key(canales)
    if canal is None or not canal.account_id or not llave:
        return {"ok": False, "motivo": "canal_sin_cuenta_o_llave"}
    plataforma = {"instagram_dm": "instagram", "messenger": "facebook", "whatsapp": "whatsapp"}[
        canal.tipo
    ]
    canal_enum = ConversationCanal(canal.tipo)
    zc = cliente_historial(llave)

    conversaciones: list[dict] = []
    cursor = None
    while len(conversaciones) < MAX_CONVERSACIONES_HISTORIAL:
        pagina, cursor = await zc.list_conversations(canal.account_id, cursor)
        conversaciones.extend(pagina)
        if not cursor:
            break

    entrantes = ecos = 0
    for c in conversaciones[:MAX_CONVERSACIONES_HISTORIAL]:
        conv_id = str(c.get("id") or "")
        participante = str(c.get("participantId") or "")
        if not conv_id or not participante:
            continue
        cliente_id = identificador_cliente(plataforma, participante)
        for m in await _mensajes_de(zc, conv_id, canal.account_id):
            mid = str(m.get("id") or "")
            texto = m.get("message")
            if not mid or not texto:
                continue  # adjuntos sueltos del historial: v1 no los trae
            if m.get("direction") == "outgoing":
                if await store_outgoing_echo(
                    canal.tipo, mid, cliente_id, texto,
                    sent_by=SENT_BY_HISTORIAL, tomar_control=False,
                ):
                    ecos += 1
                continue
            msg_id = await store_incoming(
                IncomingMessage(
                    provider_message_id=mid,
                    from_phone=cliente_id,
                    to_phone="",
                    message_type="text",
                    text=texto,
                    customer_name=str(c.get("participantName") or "") or None,
                    raw={
                        "zernio": {
                            "conversation_id": conv_id,
                            "platform_message_id": mid,
                            "account_id": canal.account_id,
                            "platform": plataforma,
                            "sent_at": m.get("sentAt") or m.get("createdAt"),
                            "historial": True,
                        }
                    },
                ),
                historial=True,
            )
            if msg_id is not None:
                entrantes += 1
        await cerrar_historial(cliente_id, canal_enum)

    resultado = {
        "ok": True,
        "conversaciones": min(len(conversaciones), MAX_CONVERSACIONES_HISTORIAL),
        "entrantes": entrantes,
        "ecos": ecos,
    }
    logger.info("zernio.historial.importado", canal=canal.tipo, **resultado)
    return resultado
