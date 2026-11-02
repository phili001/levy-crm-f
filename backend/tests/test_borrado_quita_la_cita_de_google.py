"""Borrar un contacto quita también su cita de Google Calendar.

QUÉ PASABA (SPEC-21, A4 de la revisión de Francisco). El borrado del contacto
se llevaba sus citas por el cascade, pero no la copia en Google, que lleva
«servicio · nombre». Después ya no había desde dónde arreglarlo: la barrida
solo recorre citas que existen y `sincronizar` contestaba `sin_cita`. Y el
informe del borrado lo daba por completo.

Ahora el borrado apunta los eventos en una cola antes del cascade, y la tarea
y la barrida los borran en Google. Lo que se comprueba aquí:
  - el evento desaparece de Google;
  - con Google caído queda pendiente y la barrida lo termina;
  - el informe no dice «completo» mientras quede un evento;
  - al terminar queda en la auditoría;
  - una copia que iba de camino al borrar no resucita el evento;
  - una cita que nunca llegó a Google no deja nada pendiente;
  - el borrado que pide Meta pasa por lo mismo.

Google es el calendario en memoria de `test_google_sync_reintenta_sin_duplicar.py`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid

import httpx

from app.services import calendar_sync, google_calendar
from tests._agenda import limpiar, persona
from tests.test_google_sync_reintenta_sin_duplicar import _agendar, _google
from tests.test_voice_gating import pytestmark_db


def _encolar_borrados(monkeypatch) -> list:
    llamadas: list = []
    monkeypatch.setattr(calendar_sync, "encolar_borrados", lambda: llamadas.append(1))
    return llamadas


async def _borrar_contacto(contact_id: uuid.UUID) -> dict:
    """El borrado completo, como lo hace el panel (erase → commit → finish)."""
    from app.db.session import db_session
    from app.services import data_erasure

    async with db_session() as db:
        report = await data_erasure.erase_contact(db, contact_id)
        await db.commit()
    return await data_erasure.finish_erasure(report)


async def _cola(contact_id: uuid.UUID) -> list:
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.calendar_event_deletion import CalendarEventDeletion

    async with db_session() as db:
        return list(
            (
                await db.execute(
                    select(CalendarEventDeletion).where(
                        CalendarEventDeletion.contact_id == contact_id
                    )
                )
            ).scalars()
        )


async def _auditoria(contact_id: uuid.UUID) -> list:
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.audit_log import AuditLog

    async with db_session() as db:
        return list(
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == contact_id,
                        AuditLog.action == "contact.calendar_erased",
                    )
                )
            ).scalars()
        )


async def _limpiar(contact_ids: list[uuid.UUID]) -> None:
    from sqlalchemy import delete

    from app.db.session import db_session
    from app.models.audit_log import AuditLog
    from app.models.calendar_event_deletion import CalendarEventDeletion

    async with db_session() as db:
        await db.execute(
            delete(CalendarEventDeletion).where(CalendarEventDeletion.contact_id.in_(contact_ids))
        )
        await db.execute(delete(AuditLog).where(AuditLog.entity_id.in_(contact_ids)))
        await db.commit()
    await limpiar(contact_ids)


# ---------------------------------------------------------------------------


@pytestmark_db
async def test_borrar_el_contacto_borra_su_evento_de_google(monkeypatch):
    google, _ = _google(monkeypatch, cuenta={"activa": True})
    encolados = _encolar_borrados(monkeypatch)
    _conv, contacto = await persona(nombre="Ana López")
    try:
        cita = await _agendar(contacto, 10)
        await calendar_sync.sincronizar(cita.id)
        assert google.eventos[cita.id.hex]["summary"] == "Limpieza dental · Ana López"

        report = await _borrar_contacto(contacto)
        assert report["calendar_events"] == 1
        assert encolados, "el borrado no mandó a borrar en Google"

        assert await calendar_sync.borrar_eventos_pendientes() >= 1
        assert cita.id.hex not in google.eventos, "la cita sigue en Google con su nombre"
        assert await _cola(contacto) == []
    finally:
        await _limpiar([contacto])


@pytestmark_db
async def test_con_google_caido_el_borrado_queda_pendiente_y_la_barrida_lo_termina(monkeypatch):
    google, _ = _google(monkeypatch, cuenta={"activa": True})
    _encolar_borrados(monkeypatch)
    _conv, contacto = await persona()
    try:
        cita = await _agendar(contacto, 10)
        await calendar_sync.sincronizar(cita.id)

        borrar_de_verdad = google_calendar.delete_event

        async def _caido(event_id, **_kw):
            raise httpx.ConnectError("Google no contesta")

        monkeypatch.setattr(google_calendar, "delete_event", _caido)
        await _borrar_contacto(contacto)
        await calendar_sync.borrar_eventos_pendientes()

        [fila] = await _cola(contacto)
        assert fila.borrado_at is None and fila.intentos == 1
        assert fila.ultimo_error == "Google no contesta"
        assert cita.id.hex in google.eventos, "con Google caído no se pudo borrar"

        monkeypatch.setattr(google_calendar, "delete_event", borrar_de_verdad)
        await calendar_sync.barrer_pendientes()
        assert cita.id.hex not in google.eventos, "la barrida no terminó el borrado"
        assert await _cola(contacto) == []
    finally:
        await _limpiar([contacto])


@pytestmark_db
async def test_el_informe_no_dice_completo_mientras_quede_un_evento(monkeypatch):
    _google(monkeypatch, cuenta={"activa": True})
    _encolar_borrados(monkeypatch)
    _conv, contacto = await persona()
    try:
        cita = await _agendar(contacto, 11)
        await calendar_sync.sincronizar(cita.id)

        report = await _borrar_contacto(contacto)
        assert report["calendar_events_pending"] == 1
        assert report["complete"] is False, "se declaró completo con el evento aún en Google"
    finally:
        await _limpiar([contacto])


@pytestmark_db
async def test_al_terminar_queda_en_la_auditoria(monkeypatch):
    google, _ = _google(monkeypatch, cuenta={"activa": True})
    _encolar_borrados(monkeypatch)
    _conv, contacto = await persona()
    try:
        for hora in (10, 12):
            cita = await _agendar(contacto, hora)
            await calendar_sync.sincronizar(cita.id)
        assert len(google.eventos) >= 2

        await _borrar_contacto(contacto)
        assert await _auditoria(contacto) == [], "todavía no terminó: no se audita"

        await calendar_sync.borrar_eventos_pendientes()
        [entrada] = await _auditoria(contacto)
        assert entrada.user_id is None, "lo hizo el sistema"
        assert entrada.after == {"eventos_borrados": 2}
    finally:
        await _limpiar([contacto])


@pytestmark_db
async def test_si_la_cita_se_borra_mientras_se_copia_el_evento_no_revive(monkeypatch):
    """La copia lee la cita, el contacto se borra y su cola se vacía, y LUEGO
    llega a Google el evento que la copia iba a crear."""
    google, _ = _google(monkeypatch, cuenta={"activa": True})
    encolados = _encolar_borrados(monkeypatch)
    _conv, contacto = await persona()
    try:
        cita = await _agendar(contacto, 15)

        crear = google_calendar.create_event

        async def _crear_tarde(**kw):
            await _borrar_contacto(contacto)
            await calendar_sync.borrar_eventos_pendientes()
            return await crear(**kw)

        monkeypatch.setattr(google_calendar, "create_event", _crear_tarde)
        assert await calendar_sync.sincronizar(cita.id) == "borrada_entretanto"
        assert cita.id.hex in google.eventos, "la copia llegó a Google después del borrado"
        assert len(encolados) == 2, "el borrado y la copia tardía mandan a borrar"

        await calendar_sync.borrar_eventos_pendientes()
        assert cita.id.hex not in google.eventos, "el evento revivió en Google"
    finally:
        await _limpiar([contacto])


@pytestmark_db
async def test_una_cita_que_nunca_llego_a_google_no_deja_pendientes(monkeypatch):
    _google(monkeypatch, cuenta={"activa": True}, modo=calendar_sync.MODO_OFF)
    encolados = _encolar_borrados(monkeypatch)
    _conv, contacto = await persona()
    try:
        cita = await _agendar(contacto, 10)
        assert not cita.google_sync_pendiente and cita.google_event_id is None

        report = await _borrar_contacto(contacto)
        assert report["calendar_events"] == 0
        assert report["complete"] is True
        assert encolados == [] and await _cola(contacto) == []
    finally:
        await _limpiar([contacto])


def _signed_request(payload: dict, secreto: str) -> str:
    def b64(datos: bytes) -> str:
        return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()

    cuerpo = b64(json.dumps(payload).encode())
    firma = hmac.new(secreto.encode(), cuerpo.encode(), hashlib.sha256).digest()
    return f"{b64(firma)}.{cuerpo}"


@pytestmark_db
async def test_el_borrado_que_pide_meta_tambien_quita_la_cita(monkeypatch):
    from app.api import oauth
    from app.db.session import db_session
    from app.main import app
    from app.models.contact import Contact, ContactOrigen

    google, _ = _google(monkeypatch, cuenta={"activa": True})
    encolados = _encolar_borrados(monkeypatch)

    async def _secreto():
        return "secreto-de-la-app"

    monkeypatch.setattr(oauth, "_get_meta_app_secret", _secreto)
    user_id = f"17841{uuid.uuid4().int % 10**10:010d}"
    async with db_session() as db:
        ficha = Contact(
            telefono=f"ig:{user_id}", nombre="Paciente IG", origen=ContactOrigen.instagram
        )
        db.add(ficha)
        await db.commit()
        contacto = ficha.id
    try:
        cita = await _agendar(contacto, 10)
        await calendar_sync.sincronizar(cita.id)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post(
                "/api/v1/oauth/instagram/data-deletion",
                data={"signed_request": _signed_request({"user_id": user_id}, "secreto-de-la-app")},
            )
        assert r.status_code == 200, r.text
        assert encolados, "el borrado de Meta no mandó a borrar en Google"

        await calendar_sync.borrar_eventos_pendientes()
        assert cita.id.hex not in google.eventos
    finally:
        await _limpiar([contacto])
