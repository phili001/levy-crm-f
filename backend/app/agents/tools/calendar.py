"""Herramientas de agenda del agente (Fase 9): consultar, agendar, mover y cancelar.

Trabajan sobre la agenda propia del CRM (`services/agenda.py`); Google
Calendar, si está conectado, solo recibe una copia. Antes escribían directo en
Google: sin Google no se podía agendar, y la cita no quedaba ligada ni al
contacto ni a la conversación.

  - consultar_disponibilidad(fecha, servicio, hora): horarios libres, que
    quedan apuntados como ofrecidos a esta conversación.
  - agendar_cita(start, servicio, notas): solo un horario ofrecido en los
    últimos 30 minutos y todavía libre.
  - reagendar_cita(start, cita_id?) y cancelar_cita(cita_id?): sobre la
    próxima cita de la persona, o la que se indique.
  - confirmar_cita(cita_id?): la persona dice que sí va (SPEC-18), casi siempre
    al contestar un recordatorio. La Agenda enseña «✓ Confirmó».

La persona es siempre la de la conversación: se saca de `conversation_id`, no
del teléfono (en Instagram, Messenger y correo ese campo no es un teléfono), y
ninguna herramienta toca citas de otro contacto.

Si el horario pedido no vale (no se ofreció, se acaba de ocupar), la respuesta
trae horarios nuevos: el modelo ofrece con datos reales en vez de discutir.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.agents.tools.registry import Tool, register_tool
from app.agents.tools.schedule_config import (
    DEFAULT_TZ_NAME,
    ScheduleConfig,
    get_schedule_config,
    now_in,
)
from app.core.logging import get_logger
from app.db.session import db_session
from app.models.appointment import CREADA_POR_AGENTE, Appointment
from app.models.conversation import Conversation, ConversationCanal
from app.providers.llm.base import LLMToolSchema
from app.services import agenda
from app.services.agenda import AgendaError, Hueco, Servicio
from app.services.runtime_config import _DIAS, _MESES

logger = get_logger(__name__)

# Solo para parsear entradas sin zona cuando aún no se ha leído la config (y
# como red de seguridad). El horario REAL sale de `schedule_config`.
DEFAULT_TZ = ZoneInfo(DEFAULT_TZ_NAME)

_CANAL_VOZ = ConversationCanal.retell_voice.value

# Cuántos horarios de un día se le dan al modelo. Él enseña uno o dos; el resto
# son alternativas legítimas si la persona pide otra hora.
_MAX_HUECOS = 8
# Si el día pedido no tiene nada, se buscan los siguientes con hueco hasta aquí.
_DIAS_BUSQUEDA = 14
_MAX_OTROS_DIAS = 6

# Por teléfono la hora se entrega ya dicha (Fase 8.3, lección del curso 02). El
# modelo no tiene reloj ni calendario: con "2026-09-29T16:30:00-05:00" delante
# confunde hoy con mañana y lo dice en voz alta con toda seguridad. El backend
# sabe la zona del negocio y la hora de ahora; el modelo solo lee la etiqueta.
_HORAS = [
    "doce", "una", "dos", "tres", "cuatro", "cinco",
    "seis", "siete", "ocho", "nueve", "diez", "once",
]


def _spoken_time(moment: datetime) -> str:
    """16:30 → "las cuatro y media de la tarde"; 12:00 → "las doce del día"."""
    hora = moment.hour % 12
    minutos = {0: "", 15: " y cuarto", 30: " y media"}.get(moment.minute, f" y {moment.minute}")
    if moment.hour == 0 or moment.hour >= 19:
        periodo = "de la noche"
    elif moment.hour < 12:
        periodo = "de la mañana"
    elif moment.hour == 12:
        periodo = "del día"
    else:
        periodo = "de la tarde"
    return f"{'la' if hora == 1 else 'las'} {_HORAS[hora]}{minutos} {periodo}"


def _spoken_day(moment: datetime, now: datetime) -> str:
    """"hoy", "mañana" o "el martes 29", con el mes solo si no es el de ahora."""
    dias = (moment.date() - now.date()).days
    if dias == 0:
        return "hoy"
    if dias == 1:
        return "mañana"
    dia = f"el {_DIAS[moment.weekday()]} {moment.day}"
    if (moment.year, moment.month) != (now.year, now.month):
        dia += f" de {_MESES[moment.month - 1]}"
    return dia


def _spoken_label(moment: datetime, now: datetime) -> str:
    """La frase entera, sin cuentas que hacer: "mañana a las diez de la mañana".

    `moment` y `now` en la zona del negocio: la hora que se dice es la de allí.
    """
    return f"{_spoken_day(moment, now)} a {_spoken_time(moment)}"


def _parse_date(value: str, tz: ZoneInfo) -> datetime | None:
    """Acepta varios formatos comunes: '2026-05-20', '20/05/2026', etc."""
    value = (value or "").strip()
    formats = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def _parse_hora(value: Any) -> time | None:
    """"16:30" o "9:00" → time. Cualquier otra cosa, sin hora preferida."""
    try:
        return time.fromisoformat(str(value).strip().zfill(5))
    except (TypeError, ValueError):
        return None


def _etiqueta(momento: datetime, ahora: datetime, *, voz: bool, con_dia: bool) -> str:
    """Cómo se le enseña un horario al modelo.

    Por teléfono, la frase ya dicha ("mañana a las diez de la mañana"). En
    texto, la hora ("10:00"), con el día delante cuando no es el que se pidió.
    """
    if voz:
        return _spoken_label(momento, ahora)
    if con_dia:
        return f"{_spoken_day(momento, ahora)} a las {momento:%H:%M}"
    return f"{momento:%H:%M}"


def _hueco_json(h: Hueco, ahora: datetime, *, voz: bool, con_dia: bool) -> dict[str, str]:
    # `start` ya lleva el desfase de la zona del negocio (nunca escrito a
    # mano): es lo que el modelo devuelve al agendar.
    return {
        "start": h.inicio.isoformat(),
        "label": _etiqueta(h.inicio, ahora, voz=voz, con_dia=con_dia),
    }


def _es_voz(ctx: dict[str, Any]) -> bool:
    return (ctx or {}).get("channel") == _CANAL_VOZ


async def _conversacion(
    ctx: dict[str, Any],
) -> tuple[uuid.UUID, uuid.UUID, ConversationCanal] | None:
    """(conversación, contacto, canal) de quien está hablando, o None."""
    crudo = (ctx or {}).get("conversation_id")
    if not crudo:
        return None
    try:
        conv_id = uuid.UUID(str(crudo))
    except ValueError:
        return None
    async with db_session() as db:
        fila = (
            await db.execute(
                select(Conversation.id, Conversation.contact_id, Conversation.canal).where(
                    Conversation.id == conv_id
                )
            )
        ).first()
    return (fila[0], fila[1], fila[2]) if fila else None


async def _otros_dias(
    dia: date, servicio: Servicio, preferida: time | None, cfg: ScheduleConfig, ahora: datetime
) -> list[Hueco]:
    """Horarios de los dos primeros días con hueco después de `dia`."""
    libres = await agenda.huecos(
        dia + timedelta(days=1),
        dia + timedelta(days=_DIAS_BUSQUEDA),
        servicio.duracion_min,
        cfg=cfg,
        ahora=ahora,
    )
    dias: list[date] = []
    for h in libres:
        if h.inicio.date() not in dias:
            dias.append(h.inicio.date())
    primeros = [h for h in libres if h.inicio.date() in dias[:2]]
    return agenda.repartir(primeros, _MAX_OTROS_DIAS, preferida)


async def _oferta(
    dia: date,
    servicio: Servicio,
    preferida: time | None,
    *,
    cfg: ScheduleConfig,
    ahora: datetime,
    voz: bool,
    conv_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Los horarios de un día (o de los siguientes, si ese no tiene), ya ofrecidos."""
    cerrado = cfg.closed_reason(dia)
    libres = [] if cerrado else await agenda.huecos(
        dia, dia, servicio.duracion_min, cfg=cfg, ahora=ahora
    )
    elegidos = agenda.repartir(libres, _MAX_HUECOS, preferida)
    otros = [] if elegidos else await _otros_dias(dia, servicio, preferida, cfg, ahora)
    if conv_id is not None:
        await agenda.ofrecer(conv_id, elegidos + otros)

    out: dict[str, Any] = {
        "fecha": dia.isoformat(),
        "zona_horaria": cfg.tz_name,
        "servicio": servicio.nombre,
        "duracion_min": servicio.duracion_min,
        "slots_libres": [_hueco_json(h, ahora, voz=voz, con_dia=False) for h in elegidos],
        "total_libres": len(libres),
    }
    if cerrado:
        out["cerrado"] = True
        out["motivo"] = cerrado
    elif not elegidos:
        out["motivo"] = "Ese día ya no quedan horarios libres."
    if otros:
        out["otros_dias"] = [_hueco_json(h, ahora, voz=voz, con_dia=True) for h in otros]
    return out


async def consultar_disponibilidad(args: dict[str, Any], ctx: dict[str, Any]) -> str:
    fecha_str = args.get("fecha")
    if not fecha_str:
        return json.dumps({"error": "Falta 'fecha'"})

    cfg = await get_schedule_config()
    fecha = _parse_date(fecha_str, cfg.tz)
    if not fecha:
        return json.dumps({
            "error": "No entendí la fecha. Usa formato AAAA-MM-DD o DD/MM/AAAA.",
        })

    now = now_in(cfg.tz)
    # Día pasado: no hay nada que ofrecer.
    if fecha.date() < now.date():
        return json.dumps({
            "error": "Esa fecha ya ha pasado. Pregunta al cliente por un día futuro.",
            "hoy": now.strftime("%Y-%m-%d"),
        })

    servicio = agenda.resolver_servicio(args.get("servicio"), await agenda.servicios())
    conv = await _conversacion(ctx)
    out = await _oferta(
        fecha.date(),
        servicio,
        _parse_hora(args.get("hora")),
        cfg=cfg,
        ahora=now,
        voz=_es_voz(ctx),
        conv_id=conv[0] if conv else None,
    )
    # La etiqueta se lee en voz alta: con sus eñes, sin escapar.
    return json.dumps(out, ensure_ascii=False)


_MAX_CITAS_POR_CONV_DIA = 3


def _clave_limite(conv_id: uuid.UUID) -> str:
    return f"calendar:citas:{conv_id}"


async def _limite_alcanzado(conv_id: uuid.UUID) -> bool:
    """Tope de citas agendadas o movidas por conversación y día.

    Sin él, un cliente (o una inyección) podía llenar la agenda de citas basura
    dentro de su cuota del modelo. Solo cuentan las que salen bien: un horario
    rechazado no gasta intento.
    """
    try:
        from app.core.redis import get_redis

        hechas = await get_redis().get(_clave_limite(conv_id))
        return int(hechas or 0) >= _MAX_CITAS_POR_CONV_DIA
    except Exception:  # noqa: BLE001 — Redis caído: no bloqueamos la cita por eso
        return False


async def _contar_cita(conv_id: uuid.UUID) -> None:
    try:
        from app.core.redis import get_redis

        r = get_redis()
        if await r.incr(_clave_limite(conv_id)) == 1:
            await r.expire(_clave_limite(conv_id), 24 * 3600)
    except Exception:  # noqa: BLE001
        pass


_LIMITE = {
    "error": "Ya se han agendado o movido varias citas en esta conversación hoy. "
    "Deriva a una persona si el cliente necesita más cambios.",
}


def _validar_inicio(
    start_str: Any, cfg: ScheduleConfig, now: datetime
) -> tuple[datetime | None, str | None]:
    """El `start` que manda el modelo, o el motivo por el que no vale."""
    if not start_str:
        return None, "Falta 'start': usa el de uno de los horarios de consultar_disponibilidad."
    try:
        start = datetime.fromisoformat(str(start_str))
    except ValueError:
        return None, "Formato de fecha/hora inválido. Usa ISO 8601."
    if start.tzinfo is None:
        start = start.replace(tzinfo=cfg.tz)
    # EN EL PASADO NO. "Apúntame el 3 de enero" creaba la cita en el año
    # pasado, y el cliente se quedaba con una cita que nadie iba a ver.
    if start <= now:
        return None, (
            "Esa fecha y hora ya han pasado. Confirma con el cliente un momento "
            "futuro (y el año, si te ha dado solo el día y el mes)."
        )
    cerrado = cfg.closed_reason(start.astimezone(cfg.tz).date())
    if cerrado:
        return None, f"{cerrado} Propón otro día."
    return start, None


async def _rechazo(
    e: AgendaError,
    inicio: datetime,
    servicio: Servicio,
    *,
    cfg: ScheduleConfig,
    ahora: datetime,
    voz: bool,
    conv_id: uuid.UUID,
) -> str:
    """El motivo, y si el horario no valía, horarios nuevos de ese día para ofrecer."""
    out: dict[str, Any] = {
        "error": f"{e.mensaje} No confirmes ninguna cita: ofrece uno de los horarios de esta respuesta.",
        "codigo": e.codigo,
    }
    if e.codigo in ("no_ofrecido", "ocupado"):
        local = inicio.astimezone(cfg.tz)
        oferta = await _oferta(
            local.date(), servicio, local.time(), cfg=cfg, ahora=ahora, voz=voz, conv_id=conv_id
        )
        for clave in ("slots_libres", "otros_dias", "motivo"):
            if clave in oferta:
                out[clave] = oferta[clave]
    else:
        out["error"] = e.mensaje
    return json.dumps(out, ensure_ascii=False)


def _cita_json(cita: Appointment, cfg: ScheduleConfig, ahora: datetime, *, voz: bool) -> dict[str, str]:
    inicio = cita.starts_at.astimezone(cfg.tz)
    return {
        "cita_id": str(cita.id),
        "servicio": cita.servicio,
        "start": inicio.isoformat(),
        "end": cita.ends_at.astimezone(cfg.tz).isoformat(),
        "label": _etiqueta(inicio, ahora, voz=voz, con_dia=True),
    }


async def agendar_cita(args: dict[str, Any], ctx: dict[str, Any]) -> str:
    cfg = await get_schedule_config()
    now = now_in(cfg.tz)
    start, motivo = _validar_inicio(args.get("start"), cfg, now)
    if start is None:
        return json.dumps({"error": motivo, "ahora": now.isoformat()}, ensure_ascii=False)

    conv = await _conversacion(ctx)
    if conv is None:
        return json.dumps({"error": "Solo puedo agendar dentro de una conversación con la persona."})
    conv_id, contact_id, canal = conv
    if await _limite_alcanzado(conv_id):
        return json.dumps(_LIMITE, ensure_ascii=False)

    # `motivo` es el nombre del parámetro antiguo: un prompt viejo lo sigue mandando.
    texto_servicio = args.get("servicio") or args.get("motivo")
    servicio = agenda.resolver_servicio(texto_servicio, await agenda.servicios())
    voz = _es_voz(ctx)
    try:
        cita = await agenda.agendar(
            contact_id=contact_id,
            conversation_id=conv_id,
            canal=canal,
            servicio=servicio,
            inicio=start,
            creada_por=CREADA_POR_AGENTE,
            notas=args.get("notas"),
            cfg=cfg,
            ahora=now,
        )
    except AgendaError as e:
        return await _rechazo(e, start, servicio, cfg=cfg, ahora=now, voz=voz, conv_id=conv_id)
    await _contar_cita(conv_id)
    return json.dumps({"ok": True, **_cita_json(cita, cfg, now, voz=voz)}, ensure_ascii=False)


async def _cita_del_contacto(
    contact_id: uuid.UUID, cita_id: Any, ahora: datetime
) -> Appointment | None:
    """La cita que se pide mover o cancelar: la indicada (si es de este
    contacto, está vigente y no ha empezado) o, sin indicar, la próxima."""
    proximas = await agenda.citas_proximas(contact_id, ahora=ahora, limite=10)
    if not cita_id:
        return proximas[0] if proximas else None
    return next((c for c in proximas if str(c.id) == str(cita_id).strip()), None)


_SIN_CITA = {
    "error": "Esta persona no tiene ninguna cita próxima con ese dato. Revisa sus "
    "citas con buscar_contacto antes de mover o cancelar nada.",
}


async def reagendar_cita(args: dict[str, Any], ctx: dict[str, Any]) -> str:
    cfg = await get_schedule_config()
    now = now_in(cfg.tz)
    start, motivo = _validar_inicio(args.get("start"), cfg, now)
    if start is None:
        return json.dumps({"error": motivo, "ahora": now.isoformat()}, ensure_ascii=False)

    conv = await _conversacion(ctx)
    if conv is None:
        return json.dumps({"error": "Solo puedo mover citas dentro de una conversación con la persona."})
    conv_id, contact_id, _canal = conv
    vieja = await _cita_del_contacto(contact_id, args.get("cita_id"), now)
    if vieja is None:
        return json.dumps(_SIN_CITA, ensure_ascii=False)
    if await _limite_alcanzado(conv_id):
        return json.dumps(_LIMITE, ensure_ascii=False)

    servicio = (
        agenda.resolver_servicio(args["servicio"], await agenda.servicios())
        if args.get("servicio")
        else None
    )
    voz = _es_voz(ctx)
    try:
        nueva = await agenda.reagendar(
            vieja.id,
            inicio=start,
            creada_por=CREADA_POR_AGENTE,
            servicio=servicio,
            conversation_id=conv_id,
            cfg=cfg,
            ahora=now,
        )
    except AgendaError as e:
        igual = Servicio(vieja.servicio, int((vieja.ends_at - vieja.starts_at).total_seconds() // 60))
        return await _rechazo(
            e, start, servicio or igual, cfg=cfg, ahora=now, voz=voz, conv_id=conv_id
        )
    await _contar_cita(conv_id)
    return json.dumps(
        {
            "ok": True,
            **_cita_json(nueva, cfg, now, voz=voz),
            "antes": _etiqueta(vieja.starts_at.astimezone(cfg.tz), now, voz=voz, con_dia=True),
        },
        ensure_ascii=False,
    )


async def cancelar_cita(args: dict[str, Any], ctx: dict[str, Any]) -> str:
    cfg = await get_schedule_config()
    now = now_in(cfg.tz)
    conv = await _conversacion(ctx)
    if conv is None:
        return json.dumps({"error": "Solo puedo cancelar citas dentro de una conversación con la persona."})
    _conv_id, contact_id, _canal = conv
    cita = await _cita_del_contacto(contact_id, args.get("cita_id"), now)
    if cita is None:
        return json.dumps(_SIN_CITA, ensure_ascii=False)
    try:
        cancelada = await agenda.cancelar(cita.id, cancelada_por=CREADA_POR_AGENTE)
    except AgendaError as e:
        return json.dumps({"error": e.mensaje, "codigo": e.codigo}, ensure_ascii=False)
    return json.dumps(
        {"ok": True, "cancelada": _cita_json(cancelada, cfg, now, voz=_es_voz(ctx))},
        ensure_ascii=False,
    )


async def confirmar_cita(args: dict[str, Any], ctx: dict[str, Any]) -> str:
    cfg = await get_schedule_config()
    now = now_in(cfg.tz)
    conv = await _conversacion(ctx)
    if conv is None:
        return json.dumps({"error": "Solo puedo confirmar citas dentro de una conversación con la persona."})
    _conv_id, contact_id, _canal = conv
    cita = await _cita_del_contacto(contact_id, args.get("cita_id"), now)
    if cita is None:
        return json.dumps(_SIN_CITA, ensure_ascii=False)
    try:
        confirmada = await agenda.confirmar(cita.id)
    except AgendaError as e:
        return json.dumps({"error": e.mensaje, "codigo": e.codigo}, ensure_ascii=False)
    return json.dumps(
        {"ok": True, "confirmada": _cita_json(confirmada, cfg, now, voz=_es_voz(ctx))},
        ensure_ascii=False,
    )


register_tool(
    Tool(
        schema=LLMToolSchema(
            name="consultar_disponibilidad",
            description=(
                "Consulta los horarios libres de la agenda del negocio para una fecha. "
                "Devuelve hasta 8 (`slots_libres`, cada uno con `start` y `label`) y, si "
                "ese día no hay, los de los días siguientes (`otros_dias`). Ofrece al "
                "cliente uno o dos. Para agendar usa el `start` EXACTO de uno de ellos."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "fecha": {
                        "type": "string",
                        "description": "Fecha a consultar en formato AAAA-MM-DD o DD/MM/AAAA",
                    },
                    "servicio": {
                        "type": "string",
                        "description": "Servicio que quiere la persona (ej. Limpieza dental): "
                        "de él depende cuánto dura la cita.",
                    },
                    "hora": {
                        "type": "string",
                        "description": "Hora que pidió la persona, HH:MM en 24 h, si pidió una. "
                        "Se devuelven las más cercanas.",
                    },
                },
                "required": ["fecha"],
            },
        ),
        handler=consultar_disponibilidad,
    )
)

register_tool(
    Tool(
        schema=LLMToolSchema(
            name="agendar_cita",
            description=(
                "Agenda una cita para la persona de esta conversación. Úsalo solo DESPUÉS "
                "de que confirme día y hora, y solo con un horario que te devolvió "
                "`consultar_disponibilidad` en esta conversación (copia su `start` tal "
                "cual). Si el horario ya no vale, la respuesta trae horarios nuevos."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "start": {
                        "type": "string",
                        "description": "El `start` exacto de un horario de consultar_disponibilidad "
                        "(ISO 8601 con zona)",
                    },
                    "servicio": {
                        "type": "string",
                        "description": "Servicio de la cita (ej. Limpieza dental)",
                    },
                    "notas": {
                        "type": "string",
                        "description": "Nota breve para el equipo (motivo, síntomas). Opcional.",
                    },
                },
                "required": ["start", "servicio"],
            },
        ),
        handler=agendar_cita,
    )
)

register_tool(
    Tool(
        schema=LLMToolSchema(
            name="reagendar_cita",
            description=(
                "Mueve a otro horario la cita de la persona de esta conversación. Antes, "
                "consulta la disponibilidad y confirma el horario nuevo con ella. Mueve su "
                "próxima cita, salvo que indiques otra con `cita_id` (sale de "
                "`buscar_contacto`)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "start": {
                        "type": "string",
                        "description": "El `start` exacto del horario nuevo, de consultar_disponibilidad",
                    },
                    "cita_id": {
                        "type": "string",
                        "description": "Id de la cita a mover, si no es la próxima.",
                    },
                    "servicio": {
                        "type": "string",
                        "description": "Solo si cambia el servicio.",
                    },
                },
                "required": ["start"],
            },
        ),
        handler=reagendar_cita,
    )
)

register_tool(
    Tool(
        schema=LLMToolSchema(
            name="cancelar_cita",
            description=(
                "Cancela la cita de la persona de esta conversación, cuando ella lo pida y "
                "lo confirme. Cancela su próxima cita, salvo que indiques otra con "
                "`cita_id` (sale de `buscar_contacto`)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cita_id": {
                        "type": "string",
                        "description": "Id de la cita a cancelar, si no es la próxima.",
                    },
                },
            },
        ),
        handler=cancelar_cita,
    )
)

register_tool(
    Tool(
        schema=LLMToolSchema(
            name="confirmar_cita",
            description=(
                "Marca como confirmada la próxima cita de la persona de esta conversación "
                "cuando dice que sí va a ir: por ejemplo, cuando contesta «Confirmo» o «ahí "
                "estaré» a un recordatorio. Después dale las gracias y recuérdale el día y la "
                "hora. Si pide cambiarla usa reagendar_cita, y si ya no va a ir, cancelar_cita."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cita_id": {
                        "type": "string",
                        "description": "Id de la cita a confirmar, si no es la próxima.",
                    },
                },
            },
        ),
        handler=confirmar_cita,
    )
)
