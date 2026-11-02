"""Copia opcional de la agenda en Google Calendar (Fase 9, T-9.1.5).

La cita vive en el CRM. Google, si está conectado, recibe una copia para que el
equipo la vea en su calendario de siempre. Nunca al revés: lo que se toque en
Google no vuelve al CRM, y la descripción del evento lo dice.

Modos (`calendar.google_sync` en app_settings):

    off            no se copia nada
    push           se copian las citas (crear, mover, cancelar)
    push_and_busy  además, lo ocupado en Google no se ofrece en la agenda

Sin la clave, decide la cuenta de Google Calendar: si hay una dada de alta,
`push_and_busy`, que es lo que ya hacía la instalación antes de la agenda
propia (miraba lo ocupado en Google y escribía allí la cita); si no, `off`.
Así nadie cambia de comportamiento al actualizar.

Sin duplicados: el evento se crea con un id fijo sacado del de la cita. Si un
reintento vuelve a crearlo (el primero llegó a Google pero la respuesta se
perdió), Google contesta 409 y se actualiza el que ya existe.

Con el permiso de Google revocado las citas se siguen guardando: quedan con
`google_sync_pendiente` y la barrida del beat (cada 10 minutos, solo con la
cuenta activa) las pone al día en cuanto se reconecta. Que la tarjeta de
Google salga en rojo ya lo hace `google_oauth` al fallar la renovación.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import db_session
from app.models.appointment import Appointment
from app.models.calendar_event_deletion import CalendarEventDeletion
from app.models.contact import Contact
from app.models.external_api import ExternalAPI
from app.services import google_calendar
from app.services.app_settings import get_app_setting
from app.services.audit import record_audit

logger = get_logger(__name__)

SETTING_GOOGLE_SYNC = "calendar.google_sync"
MODO_OFF = "off"
MODO_PUSH = "push"
MODO_PUSH_AND_BUSY = "push_and_busy"
MODOS = (MODO_OFF, MODO_PUSH, MODO_PUSH_AND_BUSY)

_PROVEEDOR = "google_calendar"
_LOTE_BARRIDA = 50


class GoogleNoDisponible(Exception):
    """No hay cuenta de Google activa o no dio un token: se reintenta más tarde."""


async def _cuenta_google() -> bool | None:
    """None: no hay cuenta. True: activa. False: dada de alta pero sin permiso.

    Se mira la fila y no el token a propósito: pedir el token sin cuenta deja
    un error en Monitorización cada vez, y esto se consulta en cada búsqueda
    de huecos.
    """
    async with db_session() as db:
        return (
            await db.execute(
                select(ExternalAPI.is_active).where(
                    ExternalAPI.provider == _PROVEEDOR, ExternalAPI.name == _PROVEEDOR
                )
            )
        ).scalar_one_or_none()


async def estado_cuenta() -> str:
    """"sin_cuenta", "activa" o "sin_permiso" (revocado: las citas esperan)."""
    cuenta = await _cuenta_google()
    if cuenta is None:
        return "sin_cuenta"
    return "activa" if cuenta else "sin_permiso"


async def modo() -> str:
    try:
        valor = (await get_app_setting(SETTING_GOOGLE_SYNC, "")).strip()
        if valor in MODOS:
            return valor
        return MODO_PUSH_AND_BUSY if await _cuenta_google() is not None else MODO_OFF
    except Exception as e:  # noqa: BLE001 — sin saber el modo, la agenda sigue sola
        logger.warning("calendar_sync.modo_ilegible", error=str(e))
        return MODO_OFF


async def ocupado(desde: datetime, hasta: datetime) -> list[tuple[datetime, datetime]]:
    """Lo ocupado en Google, solo en `push_and_busy` y con la cuenta activa.

    Si Google no contesta, la agenda ofrece igual: es la fuente de verdad, y
    perder un cliente por un corte de Google es peor que un hueco doble que
    alguien ve en su calendario.
    """
    if await modo() != MODO_PUSH_AND_BUSY or not await _cuenta_google():
        return []
    try:
        busy = await google_calendar.list_busy_slots(desde, hasta)
    except Exception as e:  # noqa: BLE001
        logger.warning("calendar_sync.busy_failed", error=str(e))
        return []
    intervalos: list[tuple[datetime, datetime]] = []
    for b in busy:
        try:
            inicio = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
            fin = datetime.fromisoformat(b["end"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        intervalos.append((inicio, fin))
    return intervalos


def event_id_de(cita_id: uuid.UUID) -> str:
    """Id del evento en Google: el de la cita en hexadecimal (Google admite 0-9 y a-v)."""
    return cita_id.hex


def encolar(cita_ids: Sequence[uuid.UUID]) -> None:
    """Manda a copiar. Si el broker no contesta, la barrida del beat lo recoge."""
    if not cita_ids:
        return
    try:
        from app.tasks.calendar_sync import sync_appointment

        for cita_id in cita_ids:
            sync_appointment.delay(str(cita_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("calendar_sync.encolar_fallo", error=str(e))


async def marcar_sincronizada(
    cita_id: uuid.UUID, *, event_id: str | None, version: datetime
) -> bool:
    """Baja la marca de pendiente si la cita no cambió mientras se copiaba.

    `version` es el `updated_at` leído antes de hablar con Google: si alguien la
    movió entretanto, la marca se queda y la siguiente pasada copia lo nuevo.
    """
    async with db_session() as db:
        res = await db.execute(
            update(Appointment)
            .where(Appointment.id == cita_id, Appointment.updated_at == version)
            .values(google_sync_pendiente=False, google_event_id=event_id)
        )
        await db.commit()
    return bool(res.rowcount)


def _cuerpo(cita: Appointment, nombre: str | None, zona: str) -> dict:
    canal = cita.canal.value if cita.canal else "panel"
    return {
        "start": cita.starts_at,
        "end": cita.ends_at,
        "summary": f"{cita.servicio} · {nombre}" if nombre else cita.servicio,
        "description": (
            f"Cita de la agenda del CRM (canal: {canal}). Se gestiona desde el CRM: "
            "lo que se cambie aquí no vuelve allí."
        ),
        "timezone_str": zona,
    }


async def _copiar(event_id: str, ya_copiada: bool, cuerpo: dict) -> str:
    if ya_copiada:
        try:
            if await google_calendar.update_event(event_id, **cuerpo) is None:
                raise GoogleNoDisponible("sin token de Google")
            return "actualizada"
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in (404, 410):
                raise
        # La borraron a mano en Google y la cita sigue en pie: se vuelve a crear.
    try:
        creada = await google_calendar.create_event(event_id=event_id, send_updates="none", **cuerpo)
        if creada is None:
            raise GoogleNoDisponible("sin token de Google")
        return "creada"
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 409:
            raise
    # Ya existía: un intento anterior la creó y no llegó a apuntarlo.
    if await google_calendar.update_event(event_id, **cuerpo) is None:
        raise GoogleNoDisponible("sin token de Google")
    return "actualizada"


async def sincronizar(cita_id: uuid.UUID) -> str:
    """Pone la copia de Google al día con la cita. Devuelve qué hizo.

    Lanza `GoogleNoDisponible` o `httpx.HTTPError` para que la tarea reintente.
    """
    from app.agents.tools.schedule_config import get_schedule_config

    async with db_session() as db:
        fila = (
            await db.execute(
                select(Appointment, Contact.nombre)
                .join(Contact, Contact.id == Appointment.contact_id)
                .where(Appointment.id == cita_id)
            )
        ).first()
    if fila is None:
        return "sin_cita"
    cita, nombre = fila
    if not cita.google_sync_pendiente:
        return "al_dia"
    if not await _cuenta_google():
        raise GoogleNoDisponible("Google Calendar no está conectado")

    event_id = event_id_de(cita.id)
    if cita.activa:
        cfg = await get_schedule_config()
        hecho = await _copiar(event_id, cita.google_event_id is not None, _cuerpo(cita, nombre, cfg.tz_name))
        copia: str | None = event_id
    else:
        if await google_calendar.delete_event(event_id) is None:
            raise GoogleNoDisponible("sin token de Google")
        hecho, copia = "borrada", None
    if not await marcar_sincronizada(cita.id, event_id=copia, version=cita.updated_at):
        if copia and not await _existe(cita.id):
            # La borraron con su contacto mientras se copiaba: lo que acaba de
            # escribirse en Google ya no tiene cita detrás y el borrado del
            # contacto no lo vio. Se apunta para que no se quede allí.
            async with db_session() as db:
                await apuntar_para_borrar(db, [(event_id, cita.contact_id)])
                await db.commit()
            encolar_borrados()
            return "borrada_entretanto"
        return "cambio_entretanto"
    logger.info("calendar_sync.ok", cita_id=str(cita.id), hecho=hecho)
    return hecho


async def barrer_pendientes() -> int:
    """Copia lo que quedó pendiente (Google caído, permiso revocado, broker caído).

    Solo con la cuenta activa: con el permiso revocado, pedir el token dejaría
    un error en Monitorización cada diez minutos. Al reconectar, la primera
    pasada lo pone todo al día.

    Antes de copiar, termina los borrados en Google de contactos borrados
    (`borrar_eventos_pendientes`). Devuelve solo las citas copiadas.
    """
    if not await _cuenta_google():
        return 0
    await borrar_eventos_pendientes()
    async with db_session() as db:
        ids = (
            await db.execute(
                select(Appointment.id)
                .where(Appointment.google_sync_pendiente.is_(True))
                .order_by(Appointment.updated_at)
                .limit(_LOTE_BARRIDA)
            )
        ).scalars().all()
    hechas = 0
    for cita_id in ids:
        try:
            await sincronizar(cita_id)
            hechas += 1
        except (GoogleNoDisponible, httpx.HTTPError) as e:
            # Google no contesta: el resto tampoco va a entrar en esta pasada.
            logger.warning("calendar_sync.barrida_cortada", cita_id=str(cita_id), error=str(e))
            break
        except Exception as e:  # noqa: BLE001 — una cita rara no frena a las demás
            logger.error("calendar_sync.barrida_error", cita_id=str(cita_id), error=str(e))
    return hechas


# ---------------------------------------------------------------------------
# Eventos de contactos borrados (SPEC-21, A4)
#
# El borrado de un contacto se lleva sus citas por el cascade. Antes de eso
# apunta sus eventos en `calendar_event_deletions`, y esto los borra en Google.
# Va por cola y no en el momento para que un Google caído no deje el evento
# allí para siempre: la barrida lo reintenta cada 10 minutos.


async def _existe(cita_id: uuid.UUID) -> bool:
    async with db_session() as db:
        return (
            await db.execute(select(Appointment.id).where(Appointment.id == cita_id))
        ).scalar_one_or_none() is not None


async def apuntar_para_borrar(
    db: AsyncSession, eventos: Sequence[tuple[str, uuid.UUID]]
) -> int:
    """Apunta eventos de Google por borrar, en la transacción del llamante.

    `eventos`: pares (id del evento, contacto). Si el evento ya estaba en la
    cola (ya borrado o no), vuelve a quedar pendiente: puede que una copia que
    iba de camino lo haya vuelto a crear.
    """
    if not eventos:
        return 0
    stmt = pg_insert(CalendarEventDeletion).values(
        [{"id": uuid.uuid4(), "event_id": e, "contact_id": c} for e, c in eventos]
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["event_id"],
            set_={"borrado_at": None, "contact_id": stmt.excluded.contact_id},
        )
    )
    return len(eventos)


def encolar_borrados() -> None:
    """Manda a borrar ya. Si el broker no contesta, la barrida del beat lo recoge."""
    try:
        from app.tasks.calendar_sync import borrar_eventos

        borrar_eventos.delay()
    except Exception as e:  # noqa: BLE001
        logger.warning("calendar_sync.encolar_borrados_fallo", error=str(e))


def _motivo(e: Exception) -> str:
    """Lo que se guarda del fallo: sin el cuerpo de la respuesta de Google."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"Google respondió {e.response.status_code}"
    return (str(e) or type(e).__name__)[:300]


async def _apuntar_fallo(fila_id: uuid.UUID, e: Exception) -> None:
    async with db_session() as db:
        await db.execute(
            update(CalendarEventDeletion)
            .where(CalendarEventDeletion.id == fila_id)
            .values(intentos=CalendarEventDeletion.intentos + 1, ultimo_error=_motivo(e))
        )
        await db.commit()


async def _dar_por_borrado(fila_id: uuid.UUID, contact_id: uuid.UUID) -> None:
    """Marca el evento como borrado. Si era el último de ese contacto, quita sus
    filas y lo deja en la auditoría: ahí se ve que su borrado terminó."""
    async with db_session() as db:
        res = await db.execute(
            update(CalendarEventDeletion)
            .where(CalendarEventDeletion.id == fila_id, CalendarEventDeletion.borrado_at.is_(None))
            .values(borrado_at=func.now())
        )
        if not res.rowcount:
            return  # otra pasada se adelantó
        quedan = (
            await db.execute(
                select(func.count()).where(
                    CalendarEventDeletion.contact_id == contact_id,
                    CalendarEventDeletion.borrado_at.is_(None),
                )
            )
        ).scalar_one()
        if not quedan:
            borrados = (
                await db.execute(
                    delete(CalendarEventDeletion).where(
                        CalendarEventDeletion.contact_id == contact_id
                    )
                )
            ).rowcount
            await record_audit(
                db,
                user_id=None,
                action="contact.calendar_erased",
                entity="contact",
                entity_id=contact_id,
                after={"eventos_borrados": int(borrados)},
            )
        await db.commit()


async def borrar_eventos_pendientes() -> int:
    """Borra en Google los eventos de contactos borrados. Devuelve cuántos.

    Solo con la cuenta activa, como la barrida. Si Google no deja, apunta el
    fallo y corta la vuelta: los demás tampoco van a entrar.
    """
    if not await _cuenta_google():
        return 0
    async with db_session() as db:
        filas = (
            await db.execute(
                select(
                    CalendarEventDeletion.id,
                    CalendarEventDeletion.event_id,
                    CalendarEventDeletion.contact_id,
                )
                .where(CalendarEventDeletion.borrado_at.is_(None))
                .order_by(CalendarEventDeletion.created_at)
                .limit(_LOTE_BARRIDA)
            )
        ).all()
    hechos = 0
    for fila_id, event_id, contact_id in filas:
        try:
            if await google_calendar.delete_event(event_id) is None:
                raise GoogleNoDisponible("sin token de Google")
        except (GoogleNoDisponible, httpx.HTTPError) as e:
            await _apuntar_fallo(fila_id, e)
            logger.warning("calendar_sync.borrado_cortado", error=_motivo(e))
            break
        await _dar_por_borrado(fila_id, contact_id)
        hechos += 1
    if hechos:
        logger.info("calendar_sync.eventos_borrados", hechos=hechos)
    return hechos
