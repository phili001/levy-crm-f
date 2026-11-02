"""Estado operativo de cada canal: conectado, pausado, último mensaje y errores.

Vivía dentro de `GET /agent-api/monitor/channels`. Desde la Fase 10.2 lo usa
también `GET /admin/channels/estado`, que es lo que mira `omni.py connect` para
esperar a que el canal quede en verde: el alta puede salir bien y el canal no
recibir nada (un webhook que el proveedor no llama, otro bot conectado a la
cuenta…), y la única prueba de que funciona es un mensaje que entra.

Los errores se devuelven SANEADOS (solo ts/level/event/canal): las líneas del
buffer de logs pueden llevar campos libres y aquí no se exponen.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def estado_de_canales(db: AsyncSession) -> dict:
    from app.models.channel import Channel
    from app.models.conversation import Conversation
    from app.models.message import Message, MessageRole
    from app.services.agent_pause import (
        KNOWN_CHANNELS,
        get_channels_pause_state,
        is_agent_paused,
    )
    from app.services.runtime_logs import list_runtime_logs

    global_paused = await is_agent_paused()
    pause_state = await get_channels_pause_state()

    # Conexiones configuradas (tabla channels; puede no haber fila para un canal).
    channel_rows = (await db.execute(select(Channel))).scalars().all()
    by_type: dict[str, list[Channel]] = {}
    for ch in channel_rows:
        by_type.setdefault(ch.type.value, []).append(ch)
    # El canal "web" del runtime corresponde al tipo "webchat" de conexiones.
    canal_to_type = {"web": "webchat"}

    # Última entrada de cliente por canal (max created_at de mensajes 'user'):
    # el mejor proxy disponible de "último webhook procesado con éxito".
    last_in_rows = (
        await db.execute(
            select(Conversation.canal, func.max(Message.created_at))
            .join(Message, Message.conversation_id == Conversation.id)
            .where(Message.rol == MessageRole.user)
            .group_by(Conversation.canal)
        )
    ).all()
    last_inbound = {r[0].value: r[1] for r in last_in_rows}

    # Errores recientes del buffer de runtime (Redis), saneados.
    raw_errors = await list_runtime_logs(limit=20, level="error")
    recent_errors = [
        {
            "ts": e.get("ts"),
            "level": e.get("level"),
            "event": e.get("event"),
            "canal": e.get("canal"),
        }
        for e in raw_errors
    ]

    channels = []
    for canal in KNOWN_CHANNELS:
        rows = by_type.get(canal_to_type.get(canal, canal), [])
        li = last_inbound.get(canal)
        avisos = []
        for c in rows:
            # Eventos que YCloud no activó al conectar (se guardan en el alta y
            # en «Volver a comprobar»): el canal puede salir en verde y no
            # enterarse, por ejemplo, de lo que la clínica contesta desde el celular.
            faltan = (c.config or {}).get("webhook_eventos_faltantes") or []
            if faltan:
                avisos.append(
                    "YCloud no activó estos eventos del webhook: " + ", ".join(faltan)
                    + ". Márcalos a mano en YCloud → Developers → Webhooks."
                )
        channels.append(
            {
                "canal": canal,
                "paused": bool(pause_state.get(canal, False)),
                "configured": bool(rows),
                "enabled": any(c.enabled for c in rows) if rows else None,
                "agent_assigned": any(c.agent_id for c in rows) if rows else None,
                "last_inbound_at": li.isoformat() if li else None,
                "avisos": avisos,
            }
        )

    return {
        "global_paused": global_paused,
        "channels": channels,
        "recent_errors": recent_errors,
    }
