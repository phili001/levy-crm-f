"""Conectar Instagram y Messenger por Zernio desde el panel (Fase 7, T-7.3.1).

La clínica pega la API key de SU cuenta de Zernio (gratis con dos cuentas) y
el CRM hace el resto: lista las cuentas conectadas, crea los canales, registra
el webhook por API con un secreto que elegimos nosotros y trae el historial.
Nadie tiene que copiar una URL ni un secreto en ningún panel.

Si el registro del webhook falla (no debería: la API lo permite), se devuelven
la URL y el secreto para pegarlos a mano en el panel de Zernio (US-16 AC2).
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.channel import Channel, ChannelType
from app.models.user import User
from app.providers.zernio.client import ZernioAuthError, ZernioClient, ZernioError
from app.services.audit import record_audit
from app.services.channel_secrets import apply_channel_values, channel_config
from app.services.zernio import TRANSPORT_ZERNIO

router = APIRouter(prefix="/admin/zernio", tags=["admin"])
logger = get_logger(__name__)

EVENTOS_WEBHOOK = ["message.received", "message.sent", "account.connected", "account.disconnected"]

# Plataforma de Zernio → tipo de canal del CRM.
_TIPO_POR_PLATAFORMA = {
    "instagram": ChannelType.instagram_dm,
    "facebook": ChannelType.messenger,
}
_NOMBRE_CANAL = {
    ChannelType.instagram_dm: "Instagram (Zernio)",
    ChannelType.messenger: "Messenger",
}


def webhook_url() -> str:
    return f"{settings.APP_BASE_URL.rstrip('/')}/api/v1/webhooks/zernio"


class CuentaOut(BaseModel):
    id: str
    platform: str
    username: str = ""
    display_name: str = ""
    profile_id: str = ""
    needs_reconnection: bool = False


class LlaveIn(BaseModel):
    # Vacía = la guardada (para volver a probar sin volver a pegarla).
    api_key: str | None = None


class ConectarIn(LlaveIn):
    instagram_account_id: str | None = None
    facebook_account_id: str | None = None
    wa_por_zernio: bool = False
    whatsapp_account_id: str | None = None


class CanalZernioOut(BaseModel):
    channel_id: str
    tipo: str
    nombre: str
    account_id: str
    enabled: bool
    estado: str  # "connected" | "disconnected" | ""


class EstadoOut(BaseModel):
    tiene_llave: bool
    webhook_id: str
    webhook_url: str
    canales: list[CanalZernioOut]
    wa_por_zernio: bool


class ConectarOut(EstadoOut):
    webhook_registrado: bool
    # Solo cuando el registro por API falló: para pegarlo a mano en Zernio.
    webhook_secret_manual: str | None = None
    aviso: str | None = None
    historial_encolado: int = 0


async def _canales(db: AsyncSession) -> list[Channel]:
    filas = (
        await db.execute(
            select(Channel)
            .where(Channel.type.in_([ChannelType.instagram_dm, ChannelType.messenger, ChannelType.whatsapp]))
            .order_by(Channel.created_at.asc())
        )
    ).scalars().all()
    return list(filas)


def _es_zernio(ch: Channel, cfg: dict) -> bool:
    return ch.type == ChannelType.messenger or (
        ch.type == ChannelType.instagram_dm and cfg.get("transport") == TRANSPORT_ZERNIO
    )


async def _llave(body: LlaveIn, db: AsyncSession) -> str:
    llave = (body.api_key or "").strip()
    if llave:
        return llave
    for ch in await _canales(db):
        cfg = channel_config(ch)
        if _es_zernio(ch, cfg) and cfg.get("zernio_api_key"):
            return str(cfg["zernio_api_key"])
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pega la API key de tu cuenta de Zernio.")


async def _cuentas(llave: str) -> list[CuentaOut]:
    try:
        crudas = await ZernioClient(llave).list_accounts()
    except ZernioAuthError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Zernio no acepta esa API key. Crea otra en Zernio → API keys → Create API key.",
        ) from e
    except ZernioError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"No se pudo hablar con Zernio: {e}") from e
    return [
        CuentaOut(
            id=str(a.get("_id") or a.get("id") or ""),
            platform=str(a.get("platform") or ""),
            username=str(a.get("username") or ""),
            display_name=str(a.get("displayName") or ""),
            profile_id=str(a.get("profileId") or ""),
            needs_reconnection=bool(a.get("needsReconnection")),
        )
        for a in crudas
        if a.get("platform") in ("instagram", "facebook", "whatsapp")
    ]


async def _estado(db: AsyncSession) -> EstadoOut:
    canales: list[CanalZernioOut] = []
    webhook_id = ""
    tiene_llave = False
    wa = False
    for ch in await _canales(db):
        cfg = channel_config(ch)
        if ch.type == ChannelType.whatsapp:
            wa = wa or bool(cfg.get("wa_por_zernio"))
            continue
        if not _es_zernio(ch, cfg):
            continue
        tiene_llave = tiene_llave or bool(cfg.get("zernio_api_key"))
        webhook_id = webhook_id or str(cfg.get("zernio_webhook_id") or "")
        canales.append(
            CanalZernioOut(
                channel_id=str(ch.id),
                tipo=ch.type.value,
                nombre=ch.name,
                account_id=str(cfg.get("zernio_account_id") or ""),
                enabled=bool(ch.enabled),
                estado=str(cfg.get("zernio_estado") or ""),
            )
        )
    return EstadoOut(
        tiene_llave=tiene_llave,
        webhook_id=webhook_id,
        webhook_url=webhook_url(),
        canales=canales,
        wa_por_zernio=wa,
    )


@router.get("", response_model=EstadoOut)
async def estado(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
) -> EstadoOut:
    return await _estado(db)


@router.post("/probar", response_model=list[CuentaOut])
async def probar(
    body: LlaveIn, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
) -> list[CuentaOut]:
    """Valida la llave y enseña las cuentas conectadas en Zernio."""
    return await _cuentas(await _llave(body, db))


def _elegir(cuentas: list[CuentaOut], plataforma: str, pedida: str | None) -> CuentaOut | None:
    """La cuenta pedida, o la única de esa plataforma. Con varias y sin
    elegir, ninguna: adivinar sería contestar en nombre de otra marca."""
    de_la_plataforma = [c for c in cuentas if c.platform == plataforma]
    if pedida:
        return next((c for c in de_la_plataforma if c.id == pedida), None)
    return de_la_plataforma[0] if len(de_la_plataforma) == 1 else None


@router.post("", response_model=ConectarOut)
async def conectar(
    body: ConectarIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> ConectarOut:
    llave = await _llave(body, db)
    cuentas = await _cuentas(llave)
    elegidas = {
        "instagram": _elegir(cuentas, "instagram", body.instagram_account_id),
        "facebook": _elegir(cuentas, "facebook", body.facebook_account_id),
    }
    if not any(elegidas.values()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No hay ninguna cuenta de Instagram ni de Facebook elegida. Conéctalas primero en "
            "Zernio (Connections) y vuelve a probar.",
        )

    existentes = await _canales(db)
    agente = next((c.agent_id for c in existentes if c.agent_id), None)

    # Secreto nuevo en cada conexión: lo elegimos nosotros y solo lo sabe Zernio.
    secreto = secrets.token_urlsafe(32)
    webhook_anterior = ""
    for ch in existentes:
        cfg = channel_config(ch)
        if _es_zernio(ch, cfg) and cfg.get("zernio_webhook_id"):
            webhook_anterior = str(cfg["zernio_webhook_id"])
            break

    zc = ZernioClient(llave)
    if webhook_anterior:
        try:
            await zc.delete_webhook(webhook_anterior)
        except ZernioError as e:  # ya no existía, u otra llave: se sigue
            logger.info("zernio.webhook.delete_failed", error=str(e)[:120])
    webhook_id = ""
    aviso = None
    try:
        registrado = await zc.register_webhook(webhook_url(), secreto, EVENTOS_WEBHOOK, name="CRM Omnicanal")
        webhook_id = str(registrado.get("_id") or registrado.get("id") or "")
    except ZernioError as e:
        aviso = (
            f"No se pudo registrar el aviso de mensajes en Zernio ({e}). Pégalo a mano en "
            "Zernio → Settings → Webhooks con esta URL y este secreto."
        )
        logger.warning("zernio.webhook.register_failed", error=str(e)[:200])

    por_historial: list[Channel] = []
    for plataforma, cuenta in elegidas.items():
        if cuenta is None:
            continue
        tipo = _TIPO_POR_PLATAFORMA[plataforma]
        ch = next((c for c in existentes if c.type == tipo), None)
        nueva_cuenta = True
        if ch is None:
            ch = Channel(type=tipo, name=_NOMBRE_CANAL[tipo], enabled=True, config={}, agent_id=agente)
            db.add(ch)
        else:
            nueva_cuenta = channel_config(ch).get("zernio_account_id") != cuenta.id
            ch.enabled = True
        apply_channel_values(
            ch,
            {
                "transport": TRANSPORT_ZERNIO,
                "zernio_api_key": llave,
                "zernio_webhook_secret": secreto,
                "zernio_webhook_id": webhook_id,
                "zernio_account_id": cuenta.id,
                "zernio_profile_id": cuenta.profile_id,
                "zernio_cuenta": cuenta.username or cuenta.display_name,
                "zernio_estado": "disconnected" if cuenta.needs_reconnection else "connected",
            },
        )
        if not webhook_id:
            # `apply_channel_values` no pisa con vacío: el id viejo ya no vale.
            cfg = dict(ch.config or {})
            cfg["zernio_webhook_id"] = None
            ch.config = cfg
        if nueva_cuenta:
            por_historial.append(ch)

    wa = next((c for c in existentes if c.type == ChannelType.whatsapp), None)
    if body.wa_por_zernio and wa is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Para mandar WhatsApp por Zernio primero tiene que existir el canal de WhatsApp.",
        )
    if wa is not None:
        cfg = dict(wa.config or {})
        cfg["wa_por_zernio"] = bool(body.wa_por_zernio)
        if body.wa_por_zernio:
            cuenta_wa = _elegir(cuentas, "whatsapp", body.whatsapp_account_id)
            cfg["zernio_account_id"] = cuenta_wa.id if cuenta_wa else None
        wa.config = cfg

    await db.flush()
    await record_audit(
        db,
        user_id=user.id,
        action="zernio.connected",
        entity="channel",
        entity_id=None,
        after={
            "instagram": elegidas["instagram"].id if elegidas["instagram"] else None,
            "facebook": elegidas["facebook"].id if elegidas["facebook"] else None,
            "webhook_registrado": bool(webhook_id),
            "wa_por_zernio": body.wa_por_zernio,
        },
    )
    await db.commit()

    # El historial previo no llega por webhook: se trae aparte, en segundo
    # plano, solo para cuentas nuevas en este canal.
    encoladas = 0
    for ch in por_historial:
        try:
            from app.tasks.zernio_backfill import zernio_backfill

            zernio_backfill.delay(str(ch.id))
            encoladas += 1
        except Exception as e:  # noqa: BLE001 — sin broker se conecta igual
            logger.warning("zernio.backfill.enqueue_failed", error=str(e)[:120])

    est = await _estado(db)
    return ConectarOut(
        **est.model_dump(),
        webhook_registrado=bool(webhook_id),
        webhook_secret_manual=None if webhook_id else secreto,
        aviso=aviso,
        historial_encolado=encoladas,
    )


@router.post("/historial")
async def traer_historial(
    db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)
) -> dict:
    """Vuelve a traer el historial de los canales conectados por Zernio.

    Es idempotente: no duplica nada, y deja leído y archivado lo que se quedó
    a medias si una importación anterior se cortó (p. ej. por el límite de
    peticiones de Zernio).

    Vale también con el canal apagado: el historial nunca despierta a Sofía,
    y quien apaga Instagram (p. ej. porque ManyChat sigue en la cuenta) tiene
    que poder limpiar la bandeja sin encenderlo.
    """
    from app.tasks.zernio_backfill import zernio_backfill

    canales = [c for c in (await _estado(db)).canales if c.account_id]
    if not canales:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay ninguna cuenta conectada por Zernio.")
    for c in canales:
        zernio_backfill.delay(c.channel_id)
    await record_audit(
        db, user_id=user.id, action="zernio.historial.relanzado", entity="channel",
        after={"canales": [c.tipo for c in canales]},
    )
    await db.commit()
    return {"ok": True, "canales": len(canales)}


@router.post("/probar-webhook")
async def probar_webhook(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
) -> dict:
    """Pide a Zernio que mande un evento de prueba al webhook registrado."""
    est = await _estado(db)
    if not est.webhook_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay aviso de mensajes registrado en Zernio.")
    try:
        await ZernioClient(await _llave(LlaveIn(), db)).test_webhook(est.webhook_id)
    except ZernioError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Zernio no pudo mandar la prueba: {e}") from e
    return {"ok": True}
