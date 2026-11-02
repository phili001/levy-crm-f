"""Agenda propia del CRM (Fase 9): huecos, horarios ofrecidos y citas.

Antes, `agendar_cita` escribía directo en Google Calendar: sin Google no había
agenda, y con Google la cita no quedaba ligada al contacto ni a la
conversación. Ahora la agenda vive aquí y Google, si está conectado, recibe
una copia (`services/calendar_sync.py`).

    huecos = horario de `schedule_config` − citas activas − bloqueos
             (− lo ocupado en Google, si el modo es `push_and_busy`)

Tres reglas que no se negocian:

- El modelo no inventa horas. Lo que agenda o mueve el agente tiene que
  haberse ofrecido a esa conversación en los últimos 30 minutos
  (`offered_slots`) y seguir libre en el momento de guardarlo.
- Dos citas no se encima nunca. Lo impide la base de datos (exclusión de
  `0060_appointments`): dos canales pidiendo el mismo hueco a la vez acaban en
  una cita y en un "ese horario se acaba de ocupar", sin carrera que ganar.
- Mover una cita es una sola transacción. Si el hueco nuevo no entra, la cita
  vieja sigue en pie.

Las funciones de escritura lanzan `AgendaError` con el motivo en lenguaje
llano; la herramienta del agente lo devuelve junto con horarios nuevos.
"""
from __future__ import annotations

import json
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from types import ModuleType
from typing import TYPE_CHECKING, TypeVar

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import db_session
from app.models.appointment import (
    CREADA_POR_AGENTE,
    ESTADO_AGENDADA,
    ESTADO_CANCELADA,
    ESTADO_COMPLETADA,
    ESTADO_NO_ASISTIO,
    ESTADO_REAGENDADA,
    ESTADOS_ACTIVOS,
    RECURSO_PRINCIPAL,
    Appointment,
)
from app.models.conversation import ConversationCanal
from app.models.offered_slot import OfferedSlot
from app.models.schedule_block import ScheduleBlock
from app.services import calendar_sync
from app.services.app_settings import get_app_setting
from app.services.contact_activity import record_activity

if TYPE_CHECKING:
    from app.agents.tools.schedule_config import ScheduleConfig

logger = get_logger(__name__)


def _horario() -> ModuleType:
    """El módulo del horario, importado al usarlo y no arriba: importar
    `app.agents.tools` carga todas las herramientas, y las de agenda importan
    este servicio. Llamarlo por el módulo deja que los tests fijen la hora."""
    from app.agents.tools import schedule_config

    return schedule_config


# Catálogo de servicios con su duración. Lo siembra el perfil de instalación
# (`apply_profile.py`) como JSON: [{"nombre": ..., "duracion_min": ...}, ...].
SETTING_AGENDA_SERVICIOS = "agenda.servicios"

# Duración de una cita cuyo servicio no está en el catálogo (o sin servicio).
DURACION_POR_DEFECTO_MIN = 30

# Cuánto vale un horario ofrecido. Pasado esto, la herramienta vuelve a
# consultar: la agenda pudo cambiar mientras la persona se lo pensaba.
VIGENCIA_OFERTA = timedelta(minutes=30)

# Lo ofrecido hace más de esto ya no sirve para nada: lo borra el beat.
RETENCION_OFERTAS = timedelta(hours=24)

# Nombre de la restricción de exclusión de `0060_appointments`.
_EXCLUSION = "ex_appointments_sin_encimar"


@dataclass(frozen=True)
class Servicio:
    nombre: str
    duracion_min: int


@dataclass(frozen=True)
class Hueco:
    """Un horario libre, en la zona del negocio."""

    inicio: datetime
    fin: datetime


class AgendaError(Exception):
    """Motivo de un rechazo, en lenguaje llano.

    `codigo`: `pasado`, `no_ofrecido`, `ocupado`, `no_encontrada`, `no_activa` o
    `invalido`.
    """

    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


# ---------------------------------------------------------------------------
# Servicios
# ---------------------------------------------------------------------------


async def servicios() -> list[Servicio]:
    """El catálogo de `agenda.servicios`. Si falta o no se entiende, vacío."""
    try:
        datos = json.loads(await get_app_setting(SETTING_AGENDA_SERVICIOS, "[]"))
    except Exception as e:  # noqa: BLE001 — un JSON roto no puede tumbar la agenda
        logger.warning("agenda.servicios_ilegibles", error=str(e))
        return []
    catalogo: list[Servicio] = []
    for item in datos if isinstance(datos, list) else []:
        if not isinstance(item, dict):
            continue
        nombre = str(item.get("nombre") or "").strip()
        try:
            duracion = int(item.get("duracion_min") or 0)
        except (TypeError, ValueError):
            continue
        if nombre and 5 <= duracion <= 8 * 60:
            catalogo.append(Servicio(nombre[:120], duracion))
    return catalogo


def normalizar(texto: str | None) -> str:
    """Minúsculas, sin acentos y con los espacios de uno en uno."""
    sin_acentos = unicodedata.normalize("NFKD", texto or "")
    sin_acentos = "".join(c for c in sin_acentos if not unicodedata.combining(c))
    return " ".join(sin_acentos.lower().split())


def _palabras(texto: str) -> set[str]:
    """Las palabras con peso (4 letras o más): "una limpieza" → {"limpieza"}."""
    limpio = "".join(c if c.isalnum() else " " for c in texto)
    return {p for p in limpio.split() if len(p) >= 4}


def resolver_servicio(texto: str | None, catalogo: Sequence[Servicio]) -> Servicio:
    """Del texto libre del modelo ("una limpieza") al servicio del catálogo.

    Primero igual; luego contenido en cualquier sentido; luego el que comparta
    más palabras, si no hay empate ("revisión dental" casa igual con limpieza
    que con implante: ahí no se adivina). Si no casa, se guarda lo que dijo con
    la duración por defecto: mejor una cita de 30 minutos con el nombre que
    dio la persona que ninguna cita.
    """
    buscado = normalizar(texto)
    if not buscado:
        return Servicio("Cita", DURACION_POR_DEFECTO_MIN)
    for s in catalogo:
        if normalizar(s.nombre) == buscado:
            return s
    if len(buscado) >= 4:
        for s in catalogo:
            nombre = normalizar(s.nombre)
            if buscado in nombre or nombre in buscado:
                return s
    palabras = _palabras(buscado)
    puntos = [(len(palabras & _palabras(normalizar(s.nombre))), s) for s in catalogo]
    mejores = [s for n, s in puntos if n and n == max(p for p, _ in puntos)]
    if len(mejores) == 1:
        return mejores[0]
    return Servicio((texto or "").strip()[:120], DURACION_POR_DEFECTO_MIN)


# ---------------------------------------------------------------------------
# Huecos (puro: sin base de datos)
# ---------------------------------------------------------------------------


def huecos_del_dia(
    dia: date,
    duracion_min: int,
    ocupados: Sequence[tuple[datetime, datetime]],
    cfg: ScheduleConfig,
    ahora: datetime,
) -> list[Hueco]:
    """Horarios libres de un día: tramos del horario, sin lo ocupado ni lo pasado.

    Descarta lo que ya pasó y lo que no da la antelación mínima (a las 17:00
    no se ofrecen las 9:30 de esa mañana), y los días cerrados no dan nada.
    """
    if not cfg.is_working_day(dia):
        return []
    piso = ahora + timedelta(minutes=cfg.min_notice_min)
    duracion = timedelta(minutes=duracion_min)
    paso = timedelta(minutes=cfg.slot_step_min)
    libres: list[Hueco] = []
    for inicio_tramo, fin_tramo in cfg.work_hours:
        cursor = datetime.combine(dia, inicio_tramo, tzinfo=cfg.tz)
        fin = datetime.combine(dia, fin_tramo, tzinfo=cfg.tz)
        while cursor + duracion <= fin:
            final = cursor + duracion
            encima = any(cursor < o_fin and final > o_ini for o_ini, o_fin in ocupados)
            if cursor >= piso and not encima:
                libres.append(Hueco(cursor, final))
            cursor += paso
    return libres


def _espaciados(lista: Sequence[Hueco], cuantos: int) -> list[Hueco]:
    """`cuantos` elementos repartidos de punta a punta de la lista."""
    if cuantos >= len(lista):
        return list(lista)
    if cuantos == 1:
        return [lista[0]]
    indices = sorted({round(i * (len(lista) - 1) / (cuantos - 1)) for i in range(cuantos)})
    return [lista[i] for i in indices]


def repartir(huecos: Sequence[Hueco], cuantos: int, preferida: time | None = None) -> list[Hueco]:
    """Qué horarios ofrecer de entre los libres.

    Los N primeros caían todos a primera hora, y a quien pedía "por la tarde"
    no había nada que ofrecerle. Con una hora preferida se dan los más
    cercanos a ella (el exacto, si está libre); sin ella, repartidos por el día
    y, si hay varios días, unos cuantos de cada uno.
    """
    if cuantos <= 0 or not huecos:
        return []
    if preferida is not None:
        objetivo = preferida.hour * 60 + preferida.minute

        def distancia(h: Hueco) -> tuple[int, datetime]:
            return abs(h.inicio.hour * 60 + h.inicio.minute - objetivo), h.inicio

        return sorted(sorted(huecos, key=distancia)[:cuantos], key=lambda h: h.inicio)
    por_dia: dict[date, list[Hueco]] = {}
    for h in huecos:
        por_dia.setdefault(h.inicio.date(), []).append(h)
    cupo = -(-cuantos // len(por_dia))  # redondeo hacia arriba
    elegidos: list[Hueco] = []
    for del_dia in por_dia.values():
        elegidos.extend(_espaciados(del_dia, min(cupo, cuantos - len(elegidos))))
        if len(elegidos) >= cuantos:
            break
    return elegidos


# ---------------------------------------------------------------------------
# Huecos (con base de datos)
# ---------------------------------------------------------------------------


async def ocupados_entre(
    db: AsyncSession,
    desde: datetime,
    hasta: datetime,
    *,
    excluir_cita: uuid.UUID | None = None,
) -> list[tuple[datetime, datetime]]:
    """Citas activas y bloqueos que se cruzan con [desde, hasta)."""
    citas = select(Appointment.starts_at, Appointment.ends_at).where(
        Appointment.recurso == RECURSO_PRINCIPAL,
        Appointment.estado.in_(ESTADOS_ACTIVOS),
        Appointment.starts_at < hasta,
        Appointment.ends_at > desde,
    )
    if excluir_cita is not None:
        citas = citas.where(Appointment.id != excluir_cita)
    bloqueos = select(ScheduleBlock.starts_at, ScheduleBlock.ends_at).where(
        ScheduleBlock.recurso == RECURSO_PRINCIPAL,
        ScheduleBlock.starts_at < hasta,
        ScheduleBlock.ends_at > desde,
    )
    filas = list((await db.execute(citas)).all()) + list((await db.execute(bloqueos)).all())
    return [tuple(fila) for fila in filas]


async def huecos(
    desde: date,
    hasta: date,
    duracion_min: int,
    *,
    cfg: ScheduleConfig | None = None,
    ahora: datetime | None = None,
    excluir_cita: uuid.UUID | None = None,
) -> list[Hueco]:
    """Horarios libres entre dos días (ambos incluidos), en orden.

    `excluir_cita`: la que se está moviendo desde el panel, cuyo hueco cuenta
    como libre (moverla media hora no puede chocar consigo misma).
    """
    cfg = cfg or await _horario().get_schedule_config()
    ahora = ahora or _horario().now_in(cfg.tz)
    inicio = datetime.combine(desde, time.min, tzinfo=cfg.tz)
    fin = datetime.combine(hasta + timedelta(days=1), time.min, tzinfo=cfg.tz)
    async with db_session() as db:
        ocupados = await ocupados_entre(db, inicio, fin, excluir_cita=excluir_cita)
    ocupados += await calendar_sync.ocupado(inicio, fin)
    libres: list[Hueco] = []
    dia = desde
    while dia <= hasta:
        libres.extend(huecos_del_dia(dia, duracion_min, ocupados, cfg, ahora))
        dia += timedelta(days=1)
    return libres


async def en_bloqueo(momento: datetime) -> bool:
    """¿Hay un bloqueo de la agenda en ese momento? (vacaciones, comida…)

    Lo usa la voz para no pasar la llamada a una recepción que no está. Ante
    un fallo de lectura, no: mejor intentar pasar la llamada que tomar un
    recado sin motivo.
    """
    try:
        async with db_session() as db:
            fila = (
                await db.execute(
                    select(ScheduleBlock.id)
                    .where(
                        ScheduleBlock.recurso == RECURSO_PRINCIPAL,
                        ScheduleBlock.starts_at <= momento,
                        ScheduleBlock.ends_at > momento,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        return fila is not None
    except Exception as e:  # noqa: BLE001
        logger.warning("agenda.bloqueo_ilegible", error=str(e))
        return False


# ---------------------------------------------------------------------------
# Horarios ofrecidos
# ---------------------------------------------------------------------------


async def ofrecer(conversation_id: uuid.UUID, ofrecidos: Sequence[Hueco]) -> None:
    """Apunta lo que se le enseñó al modelo en esta conversación.

    Se acumula (con la hora renovada si se repite): lo ofrecido hace diez
    minutos para otro día sigue valiendo si la persona vuelve a él.
    """
    if not ofrecidos:
        return
    filas = [{"conversation_id": conversation_id, "starts_at": h.inicio} for h in ofrecidos]
    stmt = pg_insert(OfferedSlot).values(filas)
    stmt = stmt.on_conflict_do_update(
        index_elements=[OfferedSlot.conversation_id, OfferedSlot.starts_at],
        set_={"offered_at": func.now()},
    )
    async with db_session() as db:
        await db.execute(stmt)
        await db.commit()


async def _fue_ofrecido(db: AsyncSession, conversation_id: uuid.UUID, inicio: datetime) -> bool:
    fila = (
        await db.execute(
            select(OfferedSlot.starts_at).where(
                OfferedSlot.conversation_id == conversation_id,
                OfferedSlot.starts_at == inicio,
                OfferedSlot.offered_at >= func.now() - VIGENCIA_OFERTA,
            )
        )
    ).scalar_one_or_none()
    return fila is not None


async def purgar_ofertas() -> int:
    """Borra lo ofrecido hace más de un día. Devuelve cuántas filas."""
    async with db_session() as db:
        res = await db.execute(
            delete(OfferedSlot).where(OfferedSlot.offered_at < func.now() - RETENCION_OFERTAS)
        )
        await db.commit()
    return int(res.rowcount or 0)


# ---------------------------------------------------------------------------
# Consultas de citas
# ---------------------------------------------------------------------------


async def citas_proximas(
    contact_id: uuid.UUID, *, ahora: datetime, limite: int = 3
) -> list[Appointment]:
    """Las citas activas de un contacto que aún no han empezado, en orden."""
    async with db_session() as db:
        filas = (
            await db.execute(
                select(Appointment)
                .where(
                    Appointment.contact_id == contact_id,
                    Appointment.estado.in_(ESTADOS_ACTIVOS),
                    Appointment.starts_at >= ahora,
                )
                .order_by(Appointment.starts_at)
                .limit(limite)
            )
        ).scalars().all()
    return list(filas)


async def proxima_cita(contact_id: uuid.UUID, *, ahora: datetime) -> Appointment | None:
    proximas = await citas_proximas(contact_id, ahora=ahora, limite=1)
    return proximas[0] if proximas else None


async def citas_del_dia(dia: date, *, cfg: ScheduleConfig | None = None) -> list[Appointment]:
    """Las citas activas de un día del negocio, en orden."""
    cfg = cfg or await _horario().get_schedule_config()
    inicio = datetime.combine(dia, time.min, tzinfo=cfg.tz)
    async with db_session() as db:
        filas = (
            await db.execute(
                select(Appointment)
                .where(
                    Appointment.estado.in_(ESTADOS_ACTIVOS),
                    Appointment.starts_at >= inicio,
                    Appointment.starts_at < inicio + timedelta(days=1),
                )
                .order_by(Appointment.starts_at)
            )
        ).scalars().all()
    return list(filas)


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------


async def avisar_panel() -> None:
    """Avisa por el bus de la bandeja de que la agenda cambió.

    La pestaña Agenda y el bloque «Citas de hoy» escuchan `agenda.changed` y se
    recargan: una cita que Sofía agenda por WhatsApp aparece sin refrescar la
    página. Sin payload con datos: cada pantalla vuelve a pedir lo suyo.
    """
    try:
        from app.core.events import event_bus, inbox_channel

        await event_bus.publish(inbox_channel(), "agenda.changed", {})
    except Exception as e:  # noqa: BLE001 — sin aviso, el panel se entera al recargar
        logger.warning("agenda.aviso_panel_fallo", error=str(e))


def _es_encimada(e: IntegrityError) -> bool:
    causa = getattr(e.orig, "__cause__", None)
    return getattr(causa, "constraint_name", None) == _EXCLUSION or _EXCLUSION in str(e.orig)


_T = TypeVar("_T")


def _es_bloqueo_mutuo(e: DBAPIError) -> bool:
    """Postgres cortó la transacción por bloqueo mutuo (SQLSTATE 40P01)."""
    causa = getattr(e.orig, "__cause__", None)
    return getattr(causa, "sqlstate", None) == "40P01" or "deadlock detected" in str(e.orig)


async def _reintentar_si_bloqueo(hacer: Callable[[], Awaitable[_T]]) -> _T:
    """La operación, y una vez más si Postgres la cortó por bloqueo mutuo.

    Dos reservas del mismo hueco a la vez (dos canales en el mismo instante)
    esperan cada una a la otra por la restricción de exclusión, y Postgres
    aborta una con un «deadlock detected». Sin esto, quien perdía recibía un
    error en vez de «ocupado». Al repetirla, su fila espera a que la otra se
    guarde y cae en «ocupado» con la ganadora ya visible: los horarios nuevos
    que se le ofrecen ya no incluyen el que se acaba de ocupar.
    """
    try:
        return await hacer()
    except DBAPIError as e:
        if not _es_bloqueo_mutuo(e):
            raise
        logger.warning("agenda.bloqueo_mutuo_reintento")
    return await hacer()


async def _validar_para_agente(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID | None,
    inicio: datetime,
    duracion_min: int,
    cfg: ScheduleConfig,
    ahora: datetime,
    excluir_cita: uuid.UUID | None = None,
) -> None:
    """Lo que el agente agenda: ofrecido hace poco y todavía libre.

    "Todavía libre" se recalcula con horario, antelación, bloqueos y citas: en
    media hora pudo entrar un bloqueo o pasar la antelación mínima. La
    exclusión de la base de datos sigue siendo la última palabra para dos
    escrituras simultáneas.
    """
    if conversation_id is None or not await _fue_ofrecido(db, conversation_id, inicio):
        raise AgendaError(
            "no_ofrecido",
            "Ese horario no se le ofreció a esta persona en los últimos 30 minutos.",
        )
    dia = inicio.astimezone(cfg.tz).date()
    desde = datetime.combine(dia, time.min, tzinfo=cfg.tz)
    ocupados = await ocupados_entre(
        db, desde, desde + timedelta(days=1), excluir_cita=excluir_cita
    )
    libres = huecos_del_dia(dia, duracion_min, ocupados, cfg, ahora)
    if not any(h.inicio == inicio for h in libres):
        raise AgendaError("ocupado", "Ese horario ya no está libre.")


def _cita_nueva(
    *,
    contact_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    canal: ConversationCanal | None,
    servicio: Servicio,
    inicio: datetime,
    estado: str,
    creada_por: str,
    notas: str | None,
    sincronizar: bool,
) -> Appointment:
    return Appointment(
        id=uuid.uuid4(),
        contact_id=contact_id,
        conversation_id=conversation_id,
        canal=canal,
        servicio=servicio.nombre,
        starts_at=inicio,
        ends_at=inicio + timedelta(minutes=servicio.duracion_min),
        estado=estado,
        creada_por=creada_por,
        notas=(notas or "").strip()[:1000] or None,
        google_sync_pendiente=sincronizar,
    )


async def agendar(
    *,
    contact_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    canal: ConversationCanal | None,
    servicio: Servicio,
    inicio: datetime,
    creada_por: str,
    notas: str | None = None,
    cfg: ScheduleConfig | None = None,
    ahora: datetime | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> Appointment:
    """Crea una cita. Si la crea el agente, solo en un horario ofrecido y libre.

    El operador (panel) puede agendar fuera de horario o encima de un
    bloqueo, pero tampoco encima de otra cita: eso lo para la base de datos.
    """
    cfg = cfg or await _horario().get_schedule_config()
    ahora = ahora or _horario().now_in(cfg.tz)
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=cfg.tz)
    if creada_por == CREADA_POR_AGENTE and inicio <= ahora:
        raise AgendaError("pasado", "Esa hora ya pasó.")
    sincronizar = await calendar_sync.modo() != calendar_sync.MODO_OFF

    async def _guardar() -> Appointment:
        async with db_session() as db:
            if creada_por == CREADA_POR_AGENTE:
                await _validar_para_agente(
                    db,
                    conversation_id=conversation_id,
                    inicio=inicio,
                    duracion_min=servicio.duracion_min,
                    cfg=cfg,
                    ahora=ahora,
                )
            cita = _cita_nueva(
                contact_id=contact_id,
                conversation_id=conversation_id,
                canal=canal,
                servicio=servicio,
                inicio=inicio,
                estado=ESTADO_AGENDADA,
                creada_por=creada_por,
                notas=notas,
                sincronizar=sincronizar,
            )
            db.add(cita)
            await record_activity(
                db,
                contact_id,
                "appointment_booked",
                actor_user_id=actor_user_id,
                meta={"cita_id": str(cita.id), "canal": canal.value if canal else None,
                      "por": creada_por},
            )
            try:
                await db.commit()
            except IntegrityError as e:
                if _es_encimada(e):
                    raise AgendaError("ocupado", "Ese horario se acaba de ocupar.") from e
                raise
            await db.refresh(cita)
            return cita

    cita = await _reintentar_si_bloqueo(_guardar)
    if sincronizar:
        calendar_sync.encolar([cita.id])
    await avisar_panel()
    logger.info("agenda.cita_agendada", cita_id=str(cita.id), por=creada_por)
    return cita


async def _cita_activa(db: AsyncSession, cita_id: uuid.UUID) -> Appointment:
    cita = (
        await db.execute(select(Appointment).where(Appointment.id == cita_id).with_for_update())
    ).scalar_one_or_none()
    if cita is None:
        raise AgendaError("no_encontrada", "No encuentro esa cita.")
    if not cita.activa:
        raise AgendaError("no_activa", "Esa cita ya no está vigente (cancelada o pasada).")
    return cita


async def reagendar(
    cita_id: uuid.UUID,
    *,
    inicio: datetime,
    creada_por: str,
    servicio: Servicio | None = None,
    conversation_id: uuid.UUID | None = None,
    cfg: ScheduleConfig | None = None,
    ahora: datetime | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> Appointment:
    """Mueve una cita: cancela la vieja y crea la nueva en UNA transacción.

    La nueva queda `reagendada` (ocupa hueco) y la vieja `cancelada` (lo
    libera). Si la nueva choca con otra cita, el rollback deja la vieja tal
    cual: nadie se queda sin cita por intentar moverla.
    """
    cfg = cfg or await _horario().get_schedule_config()
    ahora = ahora or _horario().now_in(cfg.tz)
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=cfg.tz)
    if creada_por == CREADA_POR_AGENTE and inicio <= ahora:
        raise AgendaError("pasado", "Esa hora ya pasó.")
    sincronizar = await calendar_sync.modo() != calendar_sync.MODO_OFF

    async def _mover() -> tuple[Appointment, list[uuid.UUID]]:
        async with db_session() as db:
            vieja = await _cita_activa(db, cita_id)
            serv = servicio or Servicio(
                vieja.servicio,
                int((vieja.ends_at - vieja.starts_at).total_seconds() // 60),
            )
            conv_id = conversation_id or vieja.conversation_id
            if creada_por == CREADA_POR_AGENTE:
                await _validar_para_agente(
                    db,
                    conversation_id=conv_id,
                    inicio=inicio,
                    duracion_min=serv.duracion_min,
                    cfg=cfg,
                    ahora=ahora,
                    excluir_cita=vieja.id,
                )
            vieja.estado = ESTADO_CANCELADA
            vieja.google_sync_pendiente = sincronizar or bool(vieja.google_event_id)
            # La vieja tiene que soltar el hueco ANTES de insertar la nueva: la
            # exclusión se comprueba fila a fila, no al final de la transacción.
            await db.flush()
            nueva = _cita_nueva(
                contact_id=vieja.contact_id,
                conversation_id=conv_id,
                canal=vieja.canal,
                servicio=serv,
                inicio=inicio,
                estado=ESTADO_REAGENDADA,
                creada_por=creada_por,
                notas=vieja.notas,
                sincronizar=sincronizar,
            )
            db.add(nueva)
            await record_activity(
                db,
                vieja.contact_id,
                "appointment_rescheduled",
                actor_user_id=actor_user_id,
                meta={"cita_id": str(nueva.id), "antes": str(vieja.id), "por": creada_por},
            )
            try:
                await db.commit()
            except IntegrityError as e:
                if _es_encimada(e):
                    raise AgendaError("ocupado", "Ese horario se acaba de ocupar.") from e
                raise
            await db.refresh(nueva)
            pendientes = ((vieja.id, vieja.google_sync_pendiente), (nueva.id, sincronizar))
            return nueva, [i for i, pend in pendientes if pend]

    nueva, ids = await _reintentar_si_bloqueo(_mover)
    calendar_sync.encolar(ids)
    await avisar_panel()
    logger.info("agenda.cita_reagendada", antes=str(cita_id), ahora=str(nueva.id), por=creada_por)
    return nueva


async def cancelar(
    cita_id: uuid.UUID, *, cancelada_por: str, actor_user_id: uuid.UUID | None = None
) -> Appointment:
    """Cancela una cita activa. La fila se queda (historia); el hueco se libera."""
    async with db_session() as db:
        cita = await _cita_activa(db, cita_id)
        cita.estado = ESTADO_CANCELADA
        # Si ya tenía copia en Google, hay que borrarla aunque la sincronización
        # esté apagada ahora: si no, el hueco seguiría ocupado allí.
        sincronizar = bool(cita.google_event_id) or (
            await calendar_sync.modo() != calendar_sync.MODO_OFF
        )
        cita.google_sync_pendiente = sincronizar
        await record_activity(
            db,
            cita.contact_id,
            "appointment_cancelled",
            actor_user_id=actor_user_id,
            meta={"cita_id": str(cita.id), "por": cancelada_por},
        )
        await db.commit()
        await db.refresh(cita)
    if sincronizar:
        calendar_sync.encolar([cita.id])
    await avisar_panel()
    logger.info("agenda.cita_cancelada", cita_id=str(cita_id), por=cancelada_por)
    return cita


async def editar(
    cita_id: uuid.UUID,
    *,
    servicio: Servicio | None = None,
    notas: str | None = None,
    cambiar_notas: bool = False,
    actor_user_id: uuid.UUID | None = None,
) -> Appointment:
    """Cambia el servicio (y con él la duración) o las notas sin mover la cita.

    Alargarla puede pisar la siguiente: la exclusión lo rechaza igual que al
    agendar. Cambiar el servicio cambia el título de la copia en Google; las
    notas no van a Google, así que no la tocan.
    """
    async def _cambiar() -> Appointment:
        async with db_session() as db:
            cita = await _cita_activa(db, cita_id)
            if servicio is not None:
                cita.servicio = servicio.nombre
                cita.ends_at = cita.starts_at + timedelta(minutes=servicio.duracion_min)
                if cita.google_event_id or await calendar_sync.modo() != calendar_sync.MODO_OFF:
                    cita.google_sync_pendiente = True
            if cambiar_notas:
                cita.notas = (notas or "").strip()[:1000] or None
            await record_activity(
                db,
                cita.contact_id,
                "appointment_edited",
                actor_user_id=actor_user_id,
                meta={"cita_id": str(cita.id)},
            )
            try:
                await db.commit()
            except IntegrityError as e:
                if _es_encimada(e):
                    raise AgendaError(
                        "ocupado", "Con esa duración pisa la cita siguiente."
                    ) from e
                raise
            await db.refresh(cita)
            return cita

    cita = await _reintentar_si_bloqueo(_cambiar)
    if cita.google_sync_pendiente:
        calendar_sync.encolar([cita.id])
    await avisar_panel()
    return cita


async def marcar(
    cita_id: uuid.UUID, estado: str, *, actor_user_id: uuid.UUID | None = None
) -> Appointment:
    """Marca una cita como completada o como no asistió.

    Suelta su hueco (deja de estar activa), pero la copia en Google se queda:
    la cita existió, y borrarla del calendario del equipo confundiría más que
    ayudaría.
    """
    if estado not in (ESTADO_COMPLETADA, ESTADO_NO_ASISTIO):
        raise AgendaError("invalido", "Solo se puede marcar como completada o no asistió.")
    async with db_session() as db:
        cita = await _cita_activa(db, cita_id)
        cita.estado = estado
        await record_activity(
            db,
            cita.contact_id,
            "appointment_marked",
            actor_user_id=actor_user_id,
            meta={"cita_id": str(cita.id), "estado": estado},
        )
        await db.commit()
        await db.refresh(cita)
    await avisar_panel()
    return cita


async def confirmar(cita_id: uuid.UUID) -> Appointment:
    """El paciente dijo que sí va (SPEC-18): la Agenda enseña «✓ Confirmó».

    No cambia el estado (sigue agendada y ocupando su hueco): confirmar es un
    dato más de la cita, no otro paso. Confirmar dos veces no repite la
    actividad en la ficha.
    """
    async with db_session() as db:
        cita = await _cita_activa(db, cita_id)
        if cita.confirmada_at is None:
            cita.confirmada_at = datetime.now(timezone.utc)
            await record_activity(
                db, cita.contact_id, "appointment_confirmed", meta={"cita_id": str(cita.id)}
            )
            await db.commit()
            await db.refresh(cita)
    await avisar_panel()
    return cita


# ---------------------------------------------------------------------------
# Bloqueos
# ---------------------------------------------------------------------------


async def crear_bloqueo(
    inicio: datetime, fin: datetime, motivo: str | None
) -> tuple[ScheduleBlock, int]:
    """Cierra un tramo de la agenda. Devuelve el bloqueo y cuántas citas activas
    quedan dentro: no se tocan (alguien decide si moverlas), pero hay que decirlo."""
    if fin <= inicio:
        raise AgendaError("invalido", "El bloqueo tiene que terminar después de empezar.")
    async with db_session() as db:
        bloqueo = ScheduleBlock(
            starts_at=inicio, ends_at=fin, motivo=(motivo or "").strip()[:200] or None
        )
        db.add(bloqueo)
        await db.commit()
        await db.refresh(bloqueo)
        dentro = (
            await db.execute(
                select(func.count(Appointment.id)).where(
                    Appointment.recurso == RECURSO_PRINCIPAL,
                    Appointment.estado.in_(ESTADOS_ACTIVOS),
                    Appointment.starts_at < fin,
                    Appointment.ends_at > inicio,
                )
            )
        ).scalar_one()
    await avisar_panel()
    return bloqueo, int(dentro)


async def borrar_bloqueo(bloqueo_id: uuid.UUID) -> bool:
    async with db_session() as db:
        res = await db.execute(delete(ScheduleBlock).where(ScheduleBlock.id == bloqueo_id))
        await db.commit()
    if res.rowcount:
        await avisar_panel()
    return bool(res.rowcount)

