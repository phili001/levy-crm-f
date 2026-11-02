"""Agenda del panel (Fase 9.2): la vista, las citas y los bloqueos.

Lo que el operador ve y toca en la pestaña Agenda, en «Citas de hoy» y en la
ficha. La lógica (huecos, exclusión, mover = cancelar + crear, copia en Google)
vive en `services/agenda.py`; aquí se valida la entrada y se da forma a la
salida.

Las horas salen ya en la hora del negocio (`inicio_local`, "AAAA-MM-DDTHH:MM"):
la recepción puede estar en otro país, y pintar la agenda con la hora del
navegador movería todas las citas. Al crear o mover, una hora sin desfase se
entiende también en la zona del negocio.

- Operador y admin: ver, crear, mover, cambiar, marcar y cancelar citas; poner
  y quitar bloqueos. El revisor de Meta no entra (lista blanca de `deps.py`).
- El operador puede agendar fuera de horario o encima de un bloqueo (es una
  persona decidiendo), pero no encima de otra cita ni en el pasado.
"""
import uuid
from datetime import date, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.schedule_config import ScheduleConfig, get_schedule_config, now_in
from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.appointment import (
    CREADA_POR_OPERADOR,
    ESTADOS_ACTIVOS,
    Appointment,
)
from app.models.contact import Contact
from app.models.schedule_block import ScheduleBlock
from app.models.user import User
from app.schemas.common import OkResponse
from app.services import agenda
from app.services.agenda import AgendaError, Servicio
from app.services.audit import record_audit

router = APIRouter(tags=["agenda"])

# Rango máximo de la vista: la semana del panel pide 7 días; esto deja margen
# para un mes sin que una consulta mal hecha se traiga el año entero.
_MAX_DIAS_VISTA = 42

_HTTP_POR_CODIGO = {
    "ocupado": status.HTTP_409_CONFLICT,
    "no_encontrada": status.HTTP_404_NOT_FOUND,
    "no_activa": status.HTTP_409_CONFLICT,
    "pasado": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "invalido": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "no_ofrecido": status.HTTP_409_CONFLICT,
}


def _error(e: AgendaError) -> HTTPException:
    return HTTPException(_HTTP_POR_CODIGO.get(e.codigo, 400), e.mensaje)


def _local(momento: datetime, cfg: ScheduleConfig) -> str:
    return momento.astimezone(cfg.tz).strftime("%Y-%m-%dT%H:%M")


def _en_zona(momento: datetime, cfg: ScheduleConfig) -> datetime:
    return momento if momento.tzinfo else momento.replace(tzinfo=cfg.tz)


class ServicioOut(BaseModel):
    nombre: str
    duracion_min: int


class AppointmentOut(BaseModel):
    id: uuid.UUID
    contact_id: uuid.UUID
    contact_nombre: str | None
    contact_telefono: str
    conversation_id: uuid.UUID | None
    canal: str | None
    servicio: str
    starts_at: datetime
    ends_at: datetime
    # En la hora del negocio, "AAAA-MM-DDTHH:MM": lo que pinta el panel.
    inicio_local: str
    fin_local: str
    duracion_min: int
    estado: str
    activa: bool
    notas: str | None
    creada_por: str
    en_google: bool
    google_sync_pendiente: bool
    created_at: datetime
    # Recordatorios y seguimientos (SPEC-18): qué se le mandó y si confirmó.
    confirmada_at: datetime | None = None
    recordatorio_enviado_at: datetime | None = None
    inasistencia_enviada_at: datetime | None = None
    # Cómo acabó cada aviso: {"recordatorio": {"resultado": "enviado", "canal": …}}.
    avisos: dict = {}


class ScheduleBlockOut(BaseModel):
    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    inicio_local: str
    fin_local: str
    motivo: str | None


class AgendaVistaOut(BaseModel):
    zona: str
    hoy: date
    ahora_local: str
    work_days: list[int]
    work_hours: list[str]
    holidays: list[date]
    slot_step_min: int
    servicios: list[ServicioOut]
    citas: list[AppointmentOut]
    bloqueos: list[ScheduleBlockOut]


class CitasDeHoyOut(BaseModel):
    hoy: date
    zona: str
    citas: list[AppointmentOut]


class HorariosLibresOut(BaseModel):
    fecha: date
    zona: str
    servicio: str
    duracion_min: int
    # Por qué no hay nada ese día (festivo, fin de semana), si es el caso.
    cerrado: str | None
    horarios: list[str]  # "HH:MM" en la hora del negocio


class AppointmentCreate(BaseModel):
    contact_id: uuid.UUID
    servicio: str = Field(min_length=1, max_length=120)
    # Sin desfase = hora del negocio.
    starts_at: datetime
    # Sin ella, la del servicio en el catálogo (o 30 minutos).
    duracion_min: int | None = Field(default=None, ge=5, le=480)
    notas: str | None = Field(default=None, max_length=1000)


class AppointmentUpdate(BaseModel):
    """Una cosa por petición: mover (`starts_at`), marcar (`estado`) o cambiar
    servicio, duración y notas en el sitio."""

    starts_at: datetime | None = None
    servicio: str | None = Field(default=None, min_length=1, max_length=120)
    duracion_min: int | None = Field(default=None, ge=5, le=480)
    notas: str | None = Field(default=None, max_length=1000)
    estado: Literal["completada", "no_asistio"] | None = None


class ScheduleBlockCreate(BaseModel):
    starts_at: datetime
    ends_at: datetime
    motivo: str | None = Field(default=None, max_length=200)


class ScheduleBlockCreated(ScheduleBlockOut):
    # Citas activas que quedan dentro del bloqueo: no se tocan, pero se avisa.
    citas_afectadas: int


def _cita_out(cita: Appointment, nombre: str | None, telefono: str, cfg: ScheduleConfig) -> AppointmentOut:
    return AppointmentOut(
        id=cita.id,
        contact_id=cita.contact_id,
        contact_nombre=nombre,
        contact_telefono=telefono,
        conversation_id=cita.conversation_id,
        canal=cita.canal.value if cita.canal else None,
        servicio=cita.servicio,
        starts_at=cita.starts_at,
        ends_at=cita.ends_at,
        inicio_local=_local(cita.starts_at, cfg),
        fin_local=_local(cita.ends_at, cfg),
        duracion_min=int((cita.ends_at - cita.starts_at).total_seconds() // 60),
        estado=cita.estado,
        activa=cita.estado in ESTADOS_ACTIVOS,
        notas=cita.notas,
        creada_por=cita.creada_por,
        en_google=cita.google_event_id is not None,
        google_sync_pendiente=cita.google_sync_pendiente,
        created_at=cita.created_at,
        confirmada_at=cita.confirmada_at,
        recordatorio_enviado_at=cita.recordatorio_enviado_at,
        inasistencia_enviada_at=cita.inasistencia_enviada_at,
        avisos=dict(cita.avisos or {}),
    )


def _bloqueo_out(b: ScheduleBlock, cfg: ScheduleConfig) -> ScheduleBlockOut:
    return ScheduleBlockOut(
        id=b.id,
        starts_at=b.starts_at,
        ends_at=b.ends_at,
        inicio_local=_local(b.starts_at, cfg),
        fin_local=_local(b.ends_at, cfg),
        motivo=b.motivo,
    )


async def _con_contacto(db: AsyncSession, cita: Appointment, cfg: ScheduleConfig) -> AppointmentOut:
    nombre, telefono = (
        await db.execute(select(Contact.nombre, Contact.telefono).where(Contact.id == cita.contact_id))
    ).one()
    return _cita_out(cita, nombre, telefono, cfg)


def _rango(desde: date, hasta: date, cfg: ScheduleConfig) -> tuple[datetime, datetime]:
    if hasta < desde:
        raise HTTPException(422, "«hasta» no puede ser anterior a «desde».")
    if (hasta - desde).days >= _MAX_DIAS_VISTA:
        raise HTTPException(422, f"Como mucho {_MAX_DIAS_VISTA} días por consulta.")
    return (
        datetime.combine(desde, time.min, tzinfo=cfg.tz),
        datetime.combine(hasta + timedelta(days=1), time.min, tzinfo=cfg.tz),
    )


async def _citas(db: AsyncSession, cfg: ScheduleConfig, *filtros, orden=None, limite=None) -> list[AppointmentOut]:
    q = (
        select(Appointment, Contact.nombre, Contact.telefono)
        .join(Contact, Contact.id == Appointment.contact_id)
        .where(*filtros)
        .order_by(orden if orden is not None else Appointment.starts_at)
    )
    if limite:
        q = q.limit(limite)
    return [_cita_out(c, nombre, tel, cfg) for c, nombre, tel in (await db.execute(q)).all()]


@router.get("/agenda", response_model=AgendaVistaOut)
async def vista_agenda(
    desde: date,
    hasta: date,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> AgendaVistaOut:
    """Todo lo que pinta la pestaña Agenda para un rango de días: citas (todas,
    también canceladas: el panel decide si enseñarlas), bloqueos que se cruzan
    con el rango, horario del negocio, servicios y el "ahora" del negocio."""
    cfg = await get_schedule_config()
    inicio, fin = _rango(desde, hasta, cfg)
    citas = await _citas(db, cfg, Appointment.starts_at >= inicio, Appointment.starts_at < fin)
    bloqueos = (
        await db.execute(
            select(ScheduleBlock)
            .where(ScheduleBlock.starts_at < fin, ScheduleBlock.ends_at > inicio)
            .order_by(ScheduleBlock.starts_at)
        )
    ).scalars().all()
    ahora = now_in(cfg.tz)
    return AgendaVistaOut(
        zona=cfg.tz_name,
        hoy=ahora.date(),
        ahora_local=ahora.strftime("%Y-%m-%dT%H:%M"),
        work_days=sorted(cfg.work_days),
        work_hours=[f"{a:%H:%M}-{b:%H:%M}" for a, b in cfg.work_hours],
        holidays=sorted(cfg.holidays),
        slot_step_min=cfg.slot_step_min,
        servicios=[ServicioOut(nombre=s.nombre, duracion_min=s.duracion_min) for s in await agenda.servicios()],
        citas=citas,
        bloqueos=[_bloqueo_out(b, cfg) for b in bloqueos],
    )


@router.get("/appointments/today", response_model=CitasDeHoyOut)
async def citas_de_hoy(
    db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> CitasDeHoyOut:
    """Las citas activas de hoy (día del negocio): el bloque de «Para hacer»."""
    cfg = await get_schedule_config()
    hoy = now_in(cfg.tz).date()
    inicio = datetime.combine(hoy, time.min, tzinfo=cfg.tz)
    citas = await _citas(
        db,
        cfg,
        Appointment.estado.in_(ESTADOS_ACTIVOS),
        Appointment.starts_at >= inicio,
        Appointment.starts_at < inicio + timedelta(days=1),
    )
    return CitasDeHoyOut(hoy=hoy, zona=cfg.tz_name, citas=citas)


@router.get("/appointments", response_model=list[AppointmentOut])
async def citas_de_contacto(
    contact_id: uuid.UUID,
    proximas: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[AppointmentOut]:
    """Las citas de una persona: todas, de la más nueva a la más vieja (ficha),
    o solo las próximas activas, en orden (cabecera del chat)."""
    cfg = await get_schedule_config()
    if proximas:
        return await _citas(
            db,
            cfg,
            Appointment.contact_id == contact_id,
            Appointment.estado.in_(ESTADOS_ACTIVOS),
            Appointment.starts_at >= now_in(cfg.tz),
            limite=3,
        )
    return await _citas(
        db, cfg, Appointment.contact_id == contact_id, orden=Appointment.starts_at.desc(), limite=100
    )


@router.get("/appointments/services", response_model=list[ServicioOut])
async def servicios_de_la_agenda(_: User = Depends(get_current_user)) -> list[ServicioOut]:
    """El catálogo con su duración, para el formulario de cita de la ficha."""
    return [ServicioOut(nombre=s.nombre, duracion_min=s.duracion_min) for s in await agenda.servicios()]


@router.get("/appointments/slots", response_model=HorariosLibresOut)
async def horarios_libres(
    fecha: date,
    servicio: str = "",
    duracion_min: int | None = None,
    excluir_cita: uuid.UUID | None = None,
    _: User = Depends(get_current_user),
) -> HorariosLibresOut:
    """Horarios libres de un día para el formulario de cita (sin tope de ocho
    ni reparto: el operador ve todos). `excluir_cita` = la que se está moviendo."""
    cfg = await get_schedule_config()
    elegido = agenda.resolver_servicio(servicio, await agenda.servicios())
    if duracion_min:
        elegido = Servicio(elegido.nombre, max(5, min(duracion_min, 480)))
    cerrado = cfg.closed_reason(fecha)
    libres = [] if cerrado else await agenda.huecos(
        fecha, fecha, elegido.duracion_min, cfg=cfg, excluir_cita=excluir_cita
    )
    return HorariosLibresOut(
        fecha=fecha,
        zona=cfg.tz_name,
        servicio=elegido.nombre,
        duracion_min=elegido.duracion_min,
        cerrado=cerrado,
        horarios=[h.inicio.strftime("%H:%M") for h in libres],
    )


async def _servicio_pedido(nombre: str, duracion_min: int | None) -> Servicio:
    elegido = agenda.resolver_servicio(nombre, await agenda.servicios())
    return Servicio(elegido.nombre, duracion_min) if duracion_min else elegido


def _no_en_el_pasado(inicio: datetime, cfg: ScheduleConfig) -> None:
    if inicio <= now_in(cfg.tz):
        raise HTTPException(422, "Esa hora ya pasó.")


@router.post("/appointments", response_model=AppointmentOut, status_code=201)
async def crear_cita(
    body: AppointmentCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AppointmentOut:
    cfg = await get_schedule_config()
    existe = (await db.execute(select(Contact.id).where(Contact.id == body.contact_id))).scalar_one_or_none()
    if existe is None:
        raise HTTPException(404, "Contacto no encontrado.")
    inicio = _en_zona(body.starts_at, cfg)
    _no_en_el_pasado(inicio, cfg)
    try:
        cita = await agenda.agendar(
            contact_id=body.contact_id,
            conversation_id=None,
            canal=None,
            servicio=await _servicio_pedido(body.servicio, body.duracion_min),
            inicio=inicio,
            creada_por=CREADA_POR_OPERADOR,
            notas=body.notas,
            cfg=cfg,
            actor_user_id=user.id,
        )
    except AgendaError as e:
        raise _error(e) from e
    return await _con_contacto(db, cita, cfg)


@router.patch("/appointments/{cita_id}", response_model=AppointmentOut)
async def cambiar_cita(
    cita_id: uuid.UUID,
    body: AppointmentUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AppointmentOut:
    """Mover devuelve la cita NUEVA (la vieja queda cancelada, para la historia)."""
    cfg = await get_schedule_config()
    try:
        if body.estado:
            cita = await agenda.marcar(cita_id, body.estado, actor_user_id=user.id)
        elif body.starts_at:
            inicio = _en_zona(body.starts_at, cfg)
            _no_en_el_pasado(inicio, cfg)
            servicio = (
                await _servicio_pedido(body.servicio, body.duracion_min) if body.servicio else None
            )
            cita = await agenda.reagendar(
                cita_id,
                inicio=inicio,
                creada_por=CREADA_POR_OPERADOR,
                servicio=servicio,
                cfg=cfg,
                actor_user_id=user.id,
            )
        elif body.servicio or body.duracion_min or "notas" in body.model_fields_set:
            servicio = None
            if body.servicio or body.duracion_min:
                actual = (
                    await db.execute(select(Appointment).where(Appointment.id == cita_id))
                ).scalar_one_or_none()
                if actual is None:
                    raise HTTPException(404, "No encuentro esa cita.")
                servicio = await _servicio_pedido(body.servicio or actual.servicio, body.duracion_min)
            cita = await agenda.editar(
                cita_id,
                servicio=servicio,
                notas=body.notas,
                cambiar_notas="notas" in body.model_fields_set,
                actor_user_id=user.id,
            )
        else:
            raise HTTPException(422, "No hay nada que cambiar.")
    except AgendaError as e:
        raise _error(e) from e
    return await _con_contacto(db, cita, cfg)


@router.delete("/appointments/{cita_id}", response_model=AppointmentOut)
async def cancelar_cita(
    cita_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AppointmentOut:
    """Cancela (no borra: la cita queda en la ficha con su estado)."""
    cfg = await get_schedule_config()
    try:
        cita = await agenda.cancelar(cita_id, cancelada_por=CREADA_POR_OPERADOR, actor_user_id=user.id)
    except AgendaError as e:
        raise _error(e) from e
    return await _con_contacto(db, cita, cfg)


@router.get("/schedule-blocks", response_model=list[ScheduleBlockOut])
async def listar_bloqueos(
    desde: date | None = None,
    hasta: date | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ScheduleBlockOut]:
    """Bloqueos que se cruzan con el rango (por defecto, de hoy a 180 días)."""
    cfg = await get_schedule_config()
    desde = desde or now_in(cfg.tz).date()
    hasta = hasta or desde + timedelta(days=180)
    if hasta < desde:
        raise HTTPException(422, "«hasta» no puede ser anterior a «desde».")
    inicio = datetime.combine(desde, time.min, tzinfo=cfg.tz)
    fin = datetime.combine(hasta + timedelta(days=1), time.min, tzinfo=cfg.tz)
    filas = (
        await db.execute(
            select(ScheduleBlock)
            .where(ScheduleBlock.starts_at < fin, ScheduleBlock.ends_at > inicio)
            .order_by(ScheduleBlock.starts_at)
        )
    ).scalars().all()
    return [_bloqueo_out(b, cfg) for b in filas]


@router.post("/schedule-blocks", response_model=ScheduleBlockCreated, status_code=201)
async def crear_bloqueo(
    body: ScheduleBlockCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ScheduleBlockCreated:
    cfg = await get_schedule_config()
    inicio, fin = _en_zona(body.starts_at, cfg), _en_zona(body.ends_at, cfg)
    if fin - inicio > timedelta(days=366):
        raise HTTPException(422, "Un bloqueo no puede durar más de un año.")
    try:
        bloqueo, dentro = await agenda.crear_bloqueo(inicio, fin, body.motivo)
    except AgendaError as e:
        raise _error(e) from e
    await record_audit(
        db,
        user_id=user.id,
        action="agenda.bloqueo_creado",
        entity="schedule_block",
        entity_id=bloqueo.id,
        after={"inicio": _local(inicio, cfg), "fin": _local(fin, cfg), "motivo": bloqueo.motivo},
    )
    await db.commit()
    return ScheduleBlockCreated(**_bloqueo_out(bloqueo, cfg).model_dump(), citas_afectadas=dentro)


@router.delete("/schedule-blocks/{bloqueo_id}", response_model=OkResponse)
async def borrar_bloqueo(
    bloqueo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OkResponse:
    if not await agenda.borrar_bloqueo(bloqueo_id):
        raise HTTPException(404, "No encuentro ese bloqueo.")
    await record_audit(
        db,
        user_id=user.id,
        action="agenda.bloqueo_borrado",
        entity="schedule_block",
        entity_id=bloqueo_id,
    )
    await db.commit()
    return OkResponse()
