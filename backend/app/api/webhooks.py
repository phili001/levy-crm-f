import json
from typing import cast

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.core.config import settings
from app.core.logging import get_logger
from app.core.ratelimit import make_limiter
from app.providers.instagram import get_instagram_provider
from app.providers.whatsapp import (
    PROVIDER_META,
    PROVIDER_YCLOUD,
    IncomingMessage,
    MetaCloudProvider,
    YCloudProvider,
)
from app.services.runtime_logs import push_runtime_log
from app.services.conversation import (
    mark_agent_queued,
    mark_message_deleted,
    store_incoming,
    store_outgoing_echo,
    store_outgoing_instagram_echo,
)
from app.tasks.process_message import process_message as task_process

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger(__name__)
limiter = make_limiter()

MAX_WEBHOOK_BYTES = 1 * 1024 * 1024  # 1 MB — los payloads de WhatsApp son pequeños


def get_ycloud_provider() -> YCloudProvider:
    """Instancia de YCloud para SU endpoint. Ver el comentario de abajo."""
    from app.providers.whatsapp.selector import provider_by_name

    return cast(YCloudProvider, provider_by_name(PROVIDER_YCLOUD))


def get_meta_provider() -> MetaCloudProvider:
    """Instancia de Meta para SU endpoint."""
    from app.providers.whatsapp.selector import provider_by_name

    return cast(MetaCloudProvider, provider_by_name(PROVIDER_META))


async def _enqueue_agent(msg_id, canal: str) -> bool:
    """Encola el runtime del agente para un mensaje ya guardado.

    Antes esto era un `task_process.delay(...)` a pelo. Si el broker no
    respondía, la excepción rompía el BUCLE del webhook: el mensaje quedaba en
    la BD sin procesar para siempre (su reintento se descartaba por duplicado) y
    los mensajes siguientes del mismo envío ni se llegaban a guardar.

    Ahora el fallo se aísla por mensaje y se deja VISIBLE en Monitorización. El
    mensaje conserva `extra.agent_queued = False`, que es lo que permite que el
    reintento del proveedor lo reencole en vez de tirarlo (ver `store_incoming`).
    """
    try:
        task_process.delay(str(msg_id))
    except Exception as e:  # noqa: BLE001 — un mensaje no puede tumbar el lote
        logger.error("webhook.enqueue_failed", canal=canal, error=str(e))
        await push_runtime_log(
            level="error",
            event="message.enqueue_failed",
            message=(
                "El mensaje se ha guardado pero NO se ha podido encolar para el "
                "agente (¿broker caído?). Queda pendiente de reintento."
            ),
            message_id=str(msg_id),
            channel=canal,
        )
        return False
    # Marca "ya encolado": a partir de aquí un duplicado sí se descarta.
    await mark_agent_queued(msg_id)
    return True


def _estado_meta(st: dict) -> dict:
    """Estado de la Cloud API de Meta → la forma común que usa el anotador.

    Meta mete el motivo en una lista `errors` con `code`, `title`, `message` y
    un `error_data.details` que suele ser lo más explicativo de los cuatro.
    YCloud lo manda plano en `errorCode`/`errorMessage`.
    """
    errores = st.get("errors") or []
    primero = errores[0] if errores and isinstance(errores[0], dict) else {}
    detalles = ""
    datos = primero.get("error_data")
    if isinstance(datos, dict):
        detalles = str(datos.get("details") or "")
    return {
        "id": str(st.get("id") or ""),
        "wamid": "",
        "status": str(st.get("status") or ""),
        "error_code": str(primero.get("code") or ""),
        "error_message": detalles or str(primero.get("title") or primero.get("message") or ""),
    }


def _motivo_del_fallo(error_code: str, error_message: str) -> str:
    """Texto corto y en cristiano del porqué WhatsApp no ha entregado algo."""
    partes = [p for p in (error_message.strip(), f"código {error_code}" if error_code else "") if p]
    return " · ".join(partes) or "sin motivo"


async def _anotar_estados_de_entrega(estados: list[dict], *, origen: str) -> int:
    """Guarda en cada mensaje qué ha pasado con su entrega, y avisa si falló.

    Antes esto solo existía para Meta y solo para los fallidos, y acababa en
    los logs en vivo: un buffer de 1000 líneas en Redis que se pierde al
    reiniciar. En la bandeja el mensaje seguía apareciendo como enviado, así
    que la operadora daba por contestado a un cliente que no había recibido
    nada. Ahora el estado se pega al mensaje y se ve en su globo.

    Devuelve cuántos estados se han podido anotar (los de mensajes que no son
    nuestros se ignoran en silencio: pueden ser de otra instalación).
    """
    from app.services.delivery_status import (
        FALLIDO,
        registrar_estado_entrega,
        registrar_estado_por_destinatario,
        traducir_estado,
    )

    anotados = 0
    for st in estados:
        motivo = _motivo_del_fallo(st.get("error_code") or "", st.get("error_message") or "")
        # Tres intentos, de más preciso a menos: el wamid de Meta, el id de
        # YCloud y, si el destinatario no tiene teléfono (pasa desde 2026), el
        # último saliente de esa persona por su BSUID.
        casado = False
        for pmid in (st.get("wamid") or "", st.get("id") or ""):
            if pmid and await registrar_estado_entrega(
                pmid, st.get("status") or "", detalle=motivo
            ):
                anotados += 1
                casado = True
                break
        if not casado:
            bsuid = st.get("recipient_user_id") or st.get("parent_recipient_user_id") or ""
            if bsuid and await registrar_estado_por_destinatario(
                bsuid, st.get("status") or "", detalle=motivo
            ):
                anotados += 1
        if traducir_estado(st.get("status")) == FALLIDO:
            await push_runtime_log(
                level="error",
                event="whatsapp.delivery.failed",
                message=f"WhatsApp no ha podido entregar un mensaje: {motivo}",
                provider_message_id=str(st.get("id") or st.get("wamid") or ""),
                error_code=str(st.get("error_code") or ""),
                origen=origen,
            )
    return anotados


async def _sincronizar_contactos_de_la_app(contactos: list[dict]) -> int:
    """Escribe el nombre que la clínica tiene guardado en su celular.

    Solo rellena huecos: nunca pisa un nombre que ya esté en la ficha, porque
    lo que haya escrito el operador en el CRM vale más que la agenda del
    teléfono. Las bajas no borran nada: quitar un contacto del celular no
    significa querer borrar su historial clínico.
    """
    if not contactos:
        return 0
    from sqlalchemy import or_, select

    from app.db.session import db_session
    from app.models.contact import Contact

    escritos = 0
    try:
        async with db_session() as db:
            for c in contactos:
                if c["accion"] == "delete" or not c["nombre"]:
                    continue
                candidatos = []
                if c["telefono"]:
                    candidatos.append(Contact.telefono == c["telefono"])
                if c["bsuid"]:
                    candidatos.append(Contact.wa_user_id == c["bsuid"])
                    candidatos.append(Contact.telefono == f"wa:{c['bsuid']}")
                if not candidatos:
                    continue
                ficha = (
                    await db.execute(select(Contact).where(or_(*candidatos)).limit(1))
                ).scalar_one_or_none()
                if ficha is None or (ficha.nombre or "").strip():
                    continue
                ficha.nombre = c["nombre"][:120]
                escritos += 1
            if escritos:
                await db.commit()
    except Exception as exc:  # noqa: BLE001 — la agenda nunca rompe el webhook
        logger.warning("whatsapp.app_state.error", detalle=str(exc)[:160])
        return 0
    logger.info("whatsapp.app_state.sincronizado", contactos=escritos)
    return escritos


async def _numero_del_canal_whatsapp() -> tuple[str, bool]:
    """(número del canal, ¿filtrar?) leídos de la configuración del canal.

    Se lee de `Channel.config` y no de las credenciales a propósito: el número
    no es un secreto, está ahí para poder pintarlo en Conexiones, y así esto no
    descifra nada en cada webhook.
    """
    from app.models.channel import Channel, ChannelType
    from app.providers.whatsapp.base import normalize_phone
    from app.db.session import db_session
    from sqlalchemy import select

    try:
        async with db_session() as db:
            ch = (
                await db.execute(
                    select(Channel).where(Channel.type == ChannelType.whatsapp).limit(1)
                )
            ).scalar_one_or_none()
        if ch is None:
            return "", False
        cfg = ch.config or {}
        numero = normalize_phone(str(cfg.get("phone_number") or ""))
        return numero, bool(cfg.get("filtrar_por_numero", True))
    except Exception as exc:  # noqa: BLE001 — sin canal, no se filtra
        logger.warning("ycloud.filtro_numero.error", detalle=str(exc)[:120])
        return "", False


async def _evento_nuevo(event_id: str) -> bool:
    """True la primera vez que se ve este evento; False en las reentregas.

    24 h de memoria: YCloud reintenta durante bastante menos, y guardar más
    solo ocuparía Redis. Si Redis no está, se deja pasar: es preferible un
    mensaje duplicado a perderlo del todo.
    """
    from app.core.redis import get_redis

    try:
        return bool(await get_redis().set(f"dedupe:evt:{event_id}", "1", nx=True, ex=86400))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ycloud.dedupe.sin_redis", detalle=str(exc)[:120])
        return True


async def _olvidar_eventos(event_ids: list[str]) -> None:
    """Suelta la marca de «ya visto» para que el reintento de YCloud sí entre."""
    if not event_ids:
        return
    from app.core.redis import get_redis

    try:
        await get_redis().delete(*[f"dedupe:evt:{i}" for i in event_ids])
    except Exception as exc:  # noqa: BLE001
        logger.warning("ycloud.dedupe.no_se_pudo_soltar", detalle=str(exc)[:120])


async def _read_webhook_body(request: Request) -> bytes:
    """Cuerpo del webhook con tope de tamaño (defensa contra body bombs)."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Payload demasiado grande")
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Payload demasiado grande")
    return body


@router.post("/ycloud")
@limiter.limit("120/minute")
async def ycloud_webhook(request: Request) -> dict:
    body = await _read_webhook_body(request)

    headers = {k.lower(): v for k, v in request.headers.items()}
    # Explícitamente YCloud, no el proveedor "activo": esta URL es la que se
    # pega en el panel de YCloud, así que quien llama aquí es YCloud y solo él.
    # Meta tiene la suya (`/webhooks/whatsapp/meta`), y así ninguno de los dos
    # tiene que adivinar quién le está escribiendo.
    provider = get_ycloud_provider()

    if not await provider.verify_webhook_signature(headers, body):
        # El detalle ya lo loguea verify_webhook_signature en runtime_logs
        # (no_secret / no_header / malformed / mismatch). Aqui solo HTTP 401.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Firma inválida")

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payload no JSON")

    # YCloud reintenta hasta 7 veces si no contestamos rápido, y el reintento
    # trae el MISMO evento. Sin esto, un pico de latencia se traduce en el
    # paciente recibiendo la respuesta del agente tres veces. Se deduplica por
    # `id` de evento ANTES de parsear, así cubre entrantes, estados, ecos e
    # historial de una sola vez.
    eventos = payload if isinstance(payload, list) else [payload]
    nuevos = []
    marcados: list[str] = []
    for ev in eventos:
        if not isinstance(ev, dict):
            continue
        tipo = str(ev.get("type") or "")
        # Los mensajes de grupo no entran al CRM: no hay un contacto detrás,
        # y el agente contestando en un grupo de vecinos es peor que no estar.
        if tipo.startswith("whatsapp.group."):
            logger.info("whatsapp.group.ignored", tipo=tipo)
            continue
        ev_id = str(ev.get("id") or "")
        if ev_id and not await _evento_nuevo(ev_id):
            logger.info("ycloud.evento.repetido", event_id=ev_id, tipo=tipo)
            continue
        if ev_id:
            marcados.append(ev_id)
        nuevos.append(ev)
    if not nuevos:
        return {"ok": True, "processed": 0, "enqueued": 0, "statuses": 0, "repetidos": len(eventos)}
    payload = nuevos if isinstance(payload, list) else nuevos[0]
    try:
        return await _procesar_eventos_ycloud(provider, payload)
    except Exception:
        # La marca se puso antes de guardar. Si algo falla a medias (la base un
        # instante caída), YCloud reintenta con el mismo evento y, con la marca
        # puesta, el reintento se tiraba como repetido: el mensaje del paciente
        # se perdía sin aviso. Lo que ya se guardó lo frena el dedupe de
        # `store_incoming` al reintentar.
        await _olvidar_eventos(marcados)
        raise


async def _procesar_eventos_ycloud(provider: YCloudProvider, payload) -> dict:
    """Entrantes, estados, ecos, historial y agenda de un webhook ya deduplicado."""
    incomings = provider.parse_webhook(payload)

    # Una clave de YCloud puede tener varios números (una agencia, o el número
    # de pruebas junto al de producción). Sin este filtro, los mensajes de un
    # número ajeno entrarían a ESTE CRM y el agente contestaría en nombre de
    # otra clínica. Se puede apagar desde la configuración del canal para quien
    # sepa lo que hace.
    numero_propio, filtrar = await _numero_del_canal_whatsapp()
    if filtrar and numero_propio:
        de_otro = [m for m in incomings if m.to_phone and m.to_phone != numero_propio]
        for m in de_otro:
            logger.warning(
                "ycloud.inbound.otro_numero",
                destinatario=m.to_phone,
                numero_propio=numero_propio,
            )
        incomings = [m for m in incomings if m not in de_otro]

    logger.info("webhook.ycloud", parsed=len(incomings))

    enqueued = 0
    for incoming in incomings:
        msg_id = await store_incoming(incoming)
        if msg_id is not None and await _enqueue_agent(msg_id, "whatsapp"):
            enqueued += 1

    # `whatsapp.message.updated`: lo que pasa con lo que enviamos nosotros.
    # Este evento llegaba y se tiraba entero.
    anotados = await _anotar_estados_de_entrega(
        provider.parse_statuses(payload), origen="ycloud"
    )

    # Coexistencia: lo que la clínica contesta desde su celular. Se refleja en
    # el panel Y el agente se aparta, porque ya hay una persona atendiendo.
    ecos = 0
    for eco in provider.parse_echoes(payload):
        if not eco["text"]:
            continue  # fotos y audios del celular: v1 no los refleja
        if await store_outgoing_echo(
            "whatsapp",
            eco["provider_message_id"],
            eco["customer_id"],
            eco["text"],
            sent_by="whatsapp_business_app",
            tomar_control=True,
        ):
            ecos += 1

    # Historial de la app al conectar por QR: entra como contexto, sin
    # despertar al agente. Son conversaciones viejas; contestarlas ahora sería
    # una avalancha a gente que ya no espera nada.
    historial = 0
    for h in provider.parse_history(payload):
        if not h["text"]:
            continue
        if h["direccion"] == "saliente":
            if await store_outgoing_echo(
                "whatsapp", h["provider_message_id"], h["customer_id"], h["text"],
                sent_by="whatsapp_business_app", tomar_control=False,
            ):
                historial += 1
            continue
        importado = IncomingMessage(
            provider_message_id=h["provider_message_id"],
            from_phone=h["customer_id"],
            to_phone="",
            message_type="text",
            text=h["text"],
        )
        if await store_incoming(importado, historial=True) is not None:
            historial += 1

    # Agenda del celular: solo para que la ficha tenga nombre en vez de un
    # número pelado. Best-effort y sin abrir conversación.
    contactos = await _sincronizar_contactos_de_la_app(provider.parse_app_state(payload))

    return {
        "ok": True,
        "processed": len(incomings),
        "enqueued": enqueued,
        "statuses": anotados,
        "ecos": ecos,
        "historial": historial,
        "contactos": contactos,
    }


# ---------- WhatsApp por la API Cloud oficial de Meta ----------
#
# Endpoint APARTE del de YCloud a propósito. Son dos proveedores con dos
# panales distintos, dos formas de firmar y —solo Meta— un handshake previo:
# una URL por proveedor es lo que hace que quien recibe un mensaje sepa sin
# ambigüedad de quién viene, y que cambiar de proveedor sea cambiar de URL en
# vez de rezar para que el payload se parezca al del otro.


@router.get("/whatsapp/meta")
async def meta_whatsapp_verify(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
) -> Response:
    """Handshake inicial de Meta: hay que devolver el challenge tal cual.

    Meta llama aquí al guardar la URL del webhook en su panel. Si la palabra
    de verificación coincide con la guardada, se devuelve el challenge en
    texto plano; si no, 403 y Meta no deja guardar la URL.
    """
    meta = get_meta_provider()
    challenge = await meta.verify_webhook_handshake(
        hub_mode, hub_verify_token, hub_challenge
    )
    if challenge is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "La palabra de verificación no coincide con la guardada en Conexiones → WhatsApp",
        )
    return Response(content=challenge, media_type="text/plain")


@router.post("/whatsapp/meta")
@limiter.limit("120/minute")
async def meta_whatsapp_webhook(request: Request) -> dict:
    body = await _read_webhook_body(request)
    headers = {k.lower(): v for k, v in request.headers.items()}
    meta = get_meta_provider()

    if not await meta.verify_webhook_signature(headers, body):
        # El motivo ya queda en Monitorización (no_secret / no_header /
        # mismatch); aquí solo el 401.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Firma inválida")

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payload no JSON")

    incomings = meta.parse_webhook(payload)
    logger.info("webhook.whatsapp.meta", parsed=len(incomings))

    enqueued = 0
    for incoming in incomings:
        msg_id = await store_incoming(incoming)
        if msg_id is not None and await _enqueue_agent(msg_id, "whatsapp"):
            enqueued += 1

    # Estados de entrega. Ya no solo los fallidos ni solo a Monitorización: el
    # estado se pega al propio mensaje (`extra.delivery_status`) para que en la
    # bandeja se distinga "enviado" de "el cliente no lo ha recibido". Un
    # mensaje que Meta rechaza —número inexistente, cliente que bloqueó a la
    # empresa, ventana cerrada, plantilla pausada— se daba por contestado.
    estados = [_estado_meta(s) for s in meta.parse_statuses(payload)]
    anotados = await _anotar_estados_de_entrega(estados, origen="meta")
    fallidos = sum(1 for e in estados if e["status"] == "failed")

    # Meta espera 200 siempre que la firma sea válida, aunque el evento no sea
    # de mensajes. Si devolvemos 4xx/5xx reintenta sin parar.
    return {
        "ok": True,
        "processed": len(incomings),
        "enqueued": enqueued,
        "failed_statuses": fallidos,
        "statuses": anotados,
    }


# ---------- Instagram DM (F5) ----------


@router.get("/instagram")
async def instagram_verify(
    request: Request,
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
) -> Response:
    """Handshake inicial de Meta: debe devolver el challenge tal cual.

    Meta llama a este endpoint con ?hub.mode=subscribe&hub.verify_token=...
    cuando configuras el webhook. Si el verify_token coincide con el
    configurado en el Channel, devolvemos el challenge en plano. Si no, 403.
    """
    ig = get_instagram_provider()
    challenge = await ig.verify_webhook_handshake(hub_mode, hub_verify_token, hub_challenge)
    if challenge is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "verify_token incorrecto")
    return Response(content=challenge, media_type="text/plain")


@router.post("/instagram")
@limiter.limit("120/minute")
async def instagram_webhook(request: Request) -> dict:
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Payload demasiado grande")
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Payload demasiado grande")

    headers = {k.lower(): v for k, v in request.headers.items()}
    provider = get_instagram_provider()
    if not await provider.verify_webhook_signature(headers, body):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Firma inválida")

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payload no JSON")

    incomings = provider.parse_webhook(payload)
    logger.info("webhook.instagram", parsed=len(incomings))

    # Diagnóstico TEMPORAL (visible en "Logs en vivo"): qué estructura manda
    # realmente Meta para cada mensaje IG. Sirve para entender por qué las notas
    # de voz no se capturan. NO incluye contenido: solo tipos de adjunto y nombres
    # de campos. Apagado por defecto (settings.IG_WEBHOOK_DEBUG); se enciende solo
    # para depurar.
    try:
        if settings.IG_WEBHOOK_DEBUG:
            for _entry in payload.get("entry") or []:
                for _ev in _entry.get("messaging") or []:
                    _m = _ev.get("message") or {}
                    if _m.get("is_echo"):
                        continue
                    _atts = _m.get("attachments") or []
                    # Solo registramos lo interesante: mensajes con adjuntos o sin
                    # texto (un texto normal no aporta nada al diagnóstico).
                    if _atts or not _m.get("text"):
                        await push_runtime_log(
                            level="info",
                            event="instagram.msg.debug",
                            message="Diagnóstico de mensaje IG (estructura)",
                            has_text=bool(_m.get("text")),
                            attachment_types=[a.get("type") for a in _atts],
                            attachment_payload_keys=[
                                sorted((a.get("payload") or {}).keys()) for a in _atts
                            ],
                            message_keys=sorted(_m.keys()),
                        )
    except Exception:
        pass

    # Visibilidad en el panel "Logs en vivo": confirmamos cuándo entra una nota
    # de voz de Instagram (el administrador no puede revisar los logs raw del contenedor).
    audio_count = sum(1 for m in incomings if m.audio_url)
    if audio_count:
        await push_runtime_log(
            level="info",
            event="instagram.audio.received",
            message=f"{audio_count} nota(s) de voz de Instagram recibida(s)",
            count=audio_count,
        )

    for incoming in incomings:
        # Enriquecer el contacto de Instagram con su @usuario/nombre real (el
        # webhook solo trae el PSID numérico). Best-effort + cacheado en Redis.
        if incoming.customer_name is None and incoming.from_phone.startswith("ig:"):
            ig_name, ig_handle = await _resolve_ig_profile(provider, incoming.from_phone)
            # Nombre real arriba; si no hay nombre público, usamos el @usuario.
            incoming.customer_name = ig_name or ig_handle
            incoming.customer_handle = ig_handle
        msg_id = await store_incoming(incoming)
        if msg_id is not None:
            await _enqueue_agent(msg_id, "instagram_dm")

    # Ecos: respuestas salientes de la cuenta (incluidas las escritas a mano
    # desde la app de Instagram). Se reflejan en el panel como saliente; NO se
    # encola el agente. El dedupe (no duplicar lo enviado desde el panel/el
    # agente) lo hace store_outgoing_instagram_echo por provider_message_id.
    echoes = provider.parse_echoes(payload)
    echoes_stored = 0
    for echo in echoes:
        if await store_outgoing_instagram_echo(
            echo.provider_message_id, echo.customer_id, echo.text
        ):
            echoes_stored += 1
    if echoes:
        logger.info("webhook.instagram.echoes", parsed=len(echoes), stored=echoes_stored)

    # Retiradas ("deshacer envío"): el cliente borra un mensaje que ya nos había
    # mandado y Meta nos avisa. Lo aplicamos igual que Instagram: se vacía el
    # contenido y la burbuja queda como "Mensaje eliminado". No se encola el
    # agente (no hay nada nuevo que contestar).
    deletions = provider.parse_deletions(payload)
    deletions_applied = 0
    for deletion in deletions:
        if await mark_message_deleted(deletion.provider_message_id, deletion.customer_id):
            deletions_applied += 1
    if deletions:
        logger.info(
            "webhook.instagram.deletions", parsed=len(deletions), applied=deletions_applied
        )
        if deletions_applied:
            await push_runtime_log(
                level="info",
                event="instagram.message.deleted",
                message=(
                    f"{deletions_applied} mensaje(s) retirado(s) por quien los envió: "
                    "el contenido se ha borrado de la bandeja"
                ),
                count=deletions_applied,
            )

    # Meta espera 200 OK siempre que firma sea válida (incluso para eventos no
    # de mensajes). Si devolvemos 4xx/5xx, reintentará indefinidamente.
    return {
        "ok": True,
        "processed": len(incomings),
        "echoes": echoes_stored,
        "deletions": deletions_applied,
    }


async def _resolve_ig_profile(provider, from_phone: str) -> tuple[str | None, str | None]:
    """(nombre_real, @handle) de Instagram con caché en Redis, para no llamar a
    la Graph API en cada mensaje del mismo contacto. Best-effort."""
    import json

    from app.core.redis import get_redis

    psid = from_phone[3:] if from_phone.startswith("ig:") else from_phone
    redis = get_redis()
    # Clave nueva (v2): la antigua guardaba solo un string ("@usuario").
    key = f"ig:profile:v2:{psid}"
    try:
        cached = await redis.get(key)
        if cached:
            raw = cached if isinstance(cached, str) else cached.decode()
            obj = json.loads(raw)
            return obj.get("name"), obj.get("handle")
    except Exception:
        pass
    name, username = await provider.get_user_profile(psid)
    handle = f"@{username}" if username else None
    if name or handle:
        try:
            await redis.setex(
                key, 7 * 24 * 3600, json.dumps({"name": name, "handle": handle})
            )
        except Exception:
            pass
    return name, handle


# ---------- Instagram, Messenger y WhatsApp por Zernio (Fase 7) ----------
#
# UN endpoint para todas las plataformas: Zernio manda a una sola URL por
# cuenta y el reparto es por `account.platform`. La ruta oficial de Instagram
# (`/webhooks/instagram`, app de Meta propia) sigue ahí para quien la use.

# Zernio reintenta durante ~51 h (7 intentos). La memoria de "ya visto" dura
# un poco más para que el último reintento también se reconozca.
ZERNIO_DEDUPE_TTL_S = 52 * 3600


def _clave_evento_zernio(event_id: str) -> str:
    return f"dedupe:evt:zernio:{event_id}"


async def _evento_zernio_visto(event_id: str) -> bool:
    from app.core.redis import get_redis

    try:
        return bool(await get_redis().exists(_clave_evento_zernio(event_id)))
    except Exception as exc:  # noqa: BLE001 — sin Redis se procesa: mejor un duplicado
        logger.warning("zernio.dedupe.sin_redis", detalle=str(exc)[:120])
        return False


async def _marcar_evento_zernio(event_id: str) -> None:
    """Se marca DESPUÉS de procesar, no antes: si el proceso se cae a medias,
    el reintento de Zernio tiene que poder entrar. El duplicado que eso pueda
    colar lo frena `store_incoming`, que es idempotente por `platformMessageId`."""
    from app.core.redis import get_redis

    try:
        await get_redis().set(_clave_evento_zernio(event_id), "1", ex=ZERNIO_DEDUPE_TTL_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning("zernio.dedupe.sin_redis", detalle=str(exc)[:120])


async def _estado_cuenta_zernio(ev, canales) -> int:
    """`account.connected|disconnected`: se apunta en el canal para que la
    tarjeta de Conexiones se ponga en rojo. Los tokens de Facebook caducan a
    menudo y, sin esto, el canal parecía conectado mientras no entraba nada."""
    from datetime import datetime, timezone

    from app.db.session import db_session
    from app.models.channel import Channel
    from app.providers.zernio.dispatch import CANAL_POR_PLATAFORMA
    from app.services.channel_secrets import apply_channel_values

    tipo = CANAL_POR_PLATAFORMA.get(ev.plataforma)
    afectados = [
        c for c in canales
        if c.tipo == tipo and (not c.account_id or c.account_id == ev.account_id)
    ]
    async with db_session() as db:
        for c in afectados:
            ch = await db.get(Channel, c.channel_id)
            if ch is None:
                continue
            apply_channel_values(
                ch,
                {
                    "zernio_estado": ev.estado_cuenta,
                    "zernio_estado_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        await db.commit()
    if ev.estado_cuenta == "disconnected" and afectados:
        await push_runtime_log(
            level="error",
            event="zernio.account.disconnected",
            message=(
                f"Zernio avisa de que la cuenta de {ev.plataforma} se desconectó. "
                "No entrarán mensajes hasta reconectarla en Conexiones."
            ),
            plataforma=ev.plataforma,
        )
    return len(afectados)


@router.post("/zernio")
@limiter.limit("120/minute")
async def zernio_webhook(request: Request) -> dict:
    from app.providers.zernio.dispatch import parse_event, verify_signature
    from app.services.zernio import canal_para, canales_zernio, secreto_webhook

    body = await _read_webhook_body(request)
    headers = {k.lower(): v for k, v in request.headers.items()}

    # El secreto es uno por endpoint (lo elegimos al registrar el webhook) y lo
    # guardan igual los canales de Instagram y Messenger. Sin firma, o con una
    # que no casa, no se mira nada más.
    canales = await canales_zernio()
    if not verify_signature(headers, body, secreto_webhook(canales)):
        logger.warning("zernio.firma_invalida", tiene_firma=bool(
            headers.get("x-zernio-signature") or headers.get("x-late-signature")
        ))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Firma inválida")

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payload no JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payload no es un objeto")

    event_id = str(
        payload.get("id")
        or headers.get("x-zernio-event-id")
        or headers.get("x-late-event-id")
        or ""
    )
    if event_id and await _evento_zernio_visto(event_id):
        logger.info("zernio.evento.repetido", event_id=event_id)
        return {"ok": True, "repetido": True}

    ev = parse_event(payload, event_id=event_id)
    resultado: dict = {"ok": True, "evento": ev.evento, "tipo": ev.tipo}

    if ev.tipo == "cuenta":
        resultado["canales"] = await _estado_cuenta_zernio(ev, canales)
    elif ev.tipo in ("entrante", "eco"):
        canal = canal_para(canales, ev.plataforma, ev.account_id)
        if canal is None:
            # Plataforma no habilitada aquí (p. ej. WhatsApp sin el interruptor)
            # o cuenta de otra clínica: 200 para que Zernio no reintente.
            logger.info(
                "zernio.platform.ignored", plataforma=ev.plataforma, evento=ev.evento
            )
            resultado["ignorado"] = "plataforma_no_habilitada"
        elif ev.tipo == "entrante" and ev.incoming is not None:
            nombre_canal = ev.canal or canal.tipo
            msg_id = await store_incoming(ev.incoming)
            resultado["guardado"] = msg_id is not None
            if msg_id is not None:
                resultado["encolado"] = await _enqueue_agent(msg_id, nombre_canal)
        elif ev.tipo == "eco" and ev.eco.get("text"):
            # Lo que la clínica contesta desde la app de Instagram/Facebook (o
            # desde el inbox de Zernio). Si fue una persona, Sofía se aparta.
            resultado["guardado"] = bool(
                await store_outgoing_echo(
                    ev.canal or canal.tipo,
                    ev.eco["provider_message_id"],
                    ev.eco["customer_id"],
                    ev.eco["text"],
                    sent_by=ev.eco["sent_by"],
                    tomar_control=ev.eco["tomar_control"],
                )
            )
    else:
        # `propio` (nuestro envío por API), `reaction.received` y el resto.
        logger.info("zernio.evento.ignorado", evento=ev.evento, motivo=ev.motivo)

    if event_id:
        await _marcar_evento_zernio(event_id)
    return resultado
