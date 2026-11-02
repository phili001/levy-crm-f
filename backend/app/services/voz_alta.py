"""Conectar la voz en un comando (Fase 8, US-19).

El CRM crea (o pone al día) su propio agente en Retell, le da número si se pide
y comprueba leyendo de vuelta que todo quedó como debe. Lo usan el alta del
canal (`POST /admin/channels/retell/provision` con `create_agent`) y el
cableado de un número de Twilio (`POST /admin/channels/retell/twilio`), y por
encima de los dos, `omni.py connect voice`.

Orden a propósito:

1. Primero se habla con Retell, sin haber guardado nada. Una llave mala se
   queda en el 401 y no pisa la buena que ya tuviera el canal (con la que se
   verifican las llamadas que entran).
2. El agente se guarda en el canal ANTES de comprar o atar el número. Si la
   compra falla, el siguiente intento reutiliza ese agente en vez de crear
   otro; al revés, cada reintento dejaría un agente huérfano en Retell.

Un número solo se ata cuando se pide en esa misma petición. Nunca se "arregla"
el que hubiera guardado en el canal: en una instalación que conectó la voz a
mano, ese número puede ser de otro agente (el del curso 02, por ejemplo), y
moverlo en silencio dejaría a ese otro sin teléfono.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.channel import Channel, ChannelType
from app.providers.voice.retell_admin import (
    VOZ_POR_DEFECTO,
    Comprobacion,
    RetellAdmin,
    RetellAdminError,
    asegurar_agente,
    permitir_llamar_a,
    verificar_agente,
    verificar_numero,
)
from app.providers.voice.twilio_sip import (
    TroncalLista,
    TwilioSip,
    conectar_numero,
    prefijo_para,
    verificar,
)
from app.services.channel_secrets import apply_channel_values, channel_config


@dataclass
class AltaVoz:
    agent_id: str
    creado: bool
    telefono: str
    comprobaciones: list[Comprobacion] = field(default_factory=list)


@dataclass
class NumeroTwilio:
    troncal: TroncalLista
    comprobaciones: list[Comprobacion] = field(default_factory=list)


def urls_del_crm() -> tuple[str, str]:
    """(WebSocket del Custom LLM, webhook de la llamada) de esta instalación.

    Retell le añade `/{call_id}` al WebSocket al conectar.
    """
    base = settings.APP_BASE_URL.rstrip("/")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_base}/api/v1/voice/retell/llm-ws", f"{base}/api/v1/voice/retell/webhook"


def _host() -> str:
    return urlparse(settings.APP_BASE_URL).hostname or "crm"


def nombre_del_agente() -> str:
    """Con el dominio: en una cuenta de Retell con varios agentes (el del curso,
    el de pruebas…) se ve a qué instalación pertenece cada uno."""
    return f"Sofía · {_host()}"


async def alta_gestionada(
    db: AsyncSession,
    ch: Channel | None,
    *,
    api_key: str,
    valores: dict,
    voice_id: str | None,
    comprar: bool,
    area_code: int | None,
    numero: str | None,
) -> tuple[Channel, AltaVoz]:
    """Agente del CRM publicado y, si se pide, con número. Crea el canal si no
    lo hay y guarda `valores` (recepción, saludo) con él. Hace commit."""
    cfg = channel_config(ch) if ch else {}
    admin = RetellAdmin(api_key)
    ws_url, webhook_url = urls_del_crm()
    # Solo se reutiliza un agente que creó el CRM. Uno pegado a mano puede
    # estar sirviendo a otra cosa, y repuntarlo al CRM la rompería.
    anterior = str(cfg.get("agent_id_retell") or "") if cfg.get("agente_gestionado") else ""
    listo = await asegurar_agente(
        admin,
        agent_id=anterior or None,
        ws_url=ws_url,
        webhook_url=webhook_url,
        voice_id=voice_id,
        nombre=nombre_del_agente(),
    )
    if ch is None:
        ch = Channel(type=ChannelType.retell_voice, name="Retell Voz", enabled=True, config={})
        db.add(ch)
    # La voz apuntada es la del agente: uno recién creado lleva la pedida o la
    # de por defecto, no la que hubiera apuntada de un agente anterior.
    voz = voice_id or (None if listo.creado else cfg.get("voice_id")) or VOZ_POR_DEFECTO
    apply_channel_values(
        ch,
        {
            **valores,
            "api_key": api_key,
            "agent_id_retell": listo.agent_id,
            "agente_gestionado": True,
            "voice_id": voz,
        },
    )
    ch.enabled = True
    await db.commit()
    await db.refresh(ch)

    telefono = ""
    if comprar:
        comprado = await admin.comprar_numero(
            listo.agent_id, nickname=nombre_del_agente(), area_code=area_code
        )
        telefono = str(comprado.get("phone_number") or "")
    elif numero:
        if await admin.numero(numero) is None:
            raise RetellAdminError(
                f"El número {numero} no está en tu cuenta de Retell. Cómpralo ahí, o si es "
                "de Twilio, conéctalo con la troncal (`omni.py connect voice --twilio`).",
                status=404,
            )
        await admin.atar_numero(numero, listo.agent_id)
        telefono = numero
    if telefono:
        apply_channel_values(ch, {"phone_number": telefono})
        await db.commit()

    checks = await verificar_agente(admin, listo.agent_id, ws_url=ws_url, webhook_url=webhook_url)
    if telefono:
        checks += await verificar_numero(admin, telefono, listo.agent_id)
    # Con solo `--transferir-a`, el número es el que el canal ya tenía.
    guardado = channel_config(ch)
    recepcion = str(guardado.get("transfer_number") or "")
    del_canal = telefono or str(guardado.get("phone_number") or "")
    if recepcion and del_canal:
        checks.append(await permitir_llamar_a(admin, del_canal, recepcion))
    return ch, AltaVoz(
        agent_id=listo.agent_id, creado=listo.creado, telefono=telefono, comprobaciones=checks
    )


async def conectar_twilio(
    db: AsyncSession,
    ch: Channel,
    *,
    account_sid: str,
    auth_token: str,
    telefono: str,
    prefijo: str | None,
) -> NumeroTwilio:
    """Número de Twilio → troncal → Retell → agente del canal. Hace commit.

    La credencial de Twilio no se guarda: solo el número y la troncal, que no
    son secretos y sirven para saber después cómo quedó.
    """
    cfg = channel_config(ch)
    agent_id = str(cfg.get("agent_id_retell") or "")
    twilio = TwilioSip(account_sid, auth_token)
    troncal = await conectar_numero(
        twilio,
        telefono=telefono,
        prefijo=prefijo_para(prefijo or _host()),
        nombre=f"{nombre_del_agente()} (Retell)",
    )
    admin = RetellAdmin(str(cfg.get("api_key") or ""))
    await admin.importar_numero(
        telefono,
        termination_uri=troncal.dominio,
        agent_id=agent_id,
        nickname=nombre_del_agente(),
    )
    apply_channel_values(
        ch,
        {
            "phone_number": telefono,
            "twilio_trunk_sid": troncal.trunk_sid,
            "twilio_trunk_domain": troncal.dominio,
        },
    )
    await db.commit()
    checks = await verificar(twilio, telefono)
    checks += await verificar_numero(admin, telefono, agent_id)
    recepcion = str(cfg.get("transfer_number") or "")
    if recepcion:
        checks.append(await permitir_llamar_a(admin, telefono, recepcion))
    return NumeroTwilio(troncal=troncal, comprobaciones=checks)
