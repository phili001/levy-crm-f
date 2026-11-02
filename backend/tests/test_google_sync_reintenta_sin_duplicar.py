"""La copia en Google Calendar reintenta sin duplicar y se pone al día sola (Fase 9, US-23 AC4, A-2).

La cita vive en el CRM y Google solo recibe una copia. El evento se crea con un
id fijo sacado del de la cita: si la respuesta de Google se pierde y la tarea
reintenta, Google contesta 409 y se actualiza el evento que ya existe, en vez
de crear otro. Con el permiso revocado las citas se siguen guardando, quedan
pendientes y la barrida del beat las copia en cuanto se reconecta.

Google aquí es un calendario en memoria que contesta como la API de verdad
(409 si el id ya existe, 404 si no), no la red.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.services import agenda, calendar_sync, google_calendar
from app.services.agenda import Servicio
from app.services.calendar_sync import GoogleNoDisponible
from tests._agenda import MARTES, a_las, citas_de, fijar_agenda, limpiar, persona
from tests.test_voice_gating import pytestmark_db

# Antes de que `fijar_agenda` lo sustituya por "apagado".
_MODO_REAL = calendar_sync.modo


def _http(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://www.googleapis.com/calendar/v3/calendars/primary/events")
    return httpx.HTTPStatusError(str(status), request=req, response=httpx.Response(status, request=req))


class _GoogleFalso:
    """Calendario de Google en memoria: 409 si el id ya existe, 404 si no."""

    def __init__(self) -> None:
        self.eventos: dict[str, dict] = {}
        self.perder_respuesta = False
        self.antes_de_contestar = None

    async def create_event(self, *, start, end, summary, event_id=None, **_kw):
        assert event_id, "sin id fijo, cada reintento sería un evento nuevo"
        if event_id in self.eventos:
            raise _http(409)
        self.eventos[event_id] = {"start": start, "end": end, "summary": summary}
        if self.antes_de_contestar:
            await self.antes_de_contestar()
        if self.perder_respuesta:
            self.perder_respuesta = False
            raise httpx.ReadTimeout("Google creó el evento pero la respuesta no llegó")
        return {"id": event_id}

    async def update_event(self, event_id, *, start, end, summary, **_kw):
        if event_id not in self.eventos:
            raise _http(404)
        self.eventos[event_id].update(start=start, end=end, summary=summary)
        return {"id": event_id}

    async def delete_event(self, event_id, **_kw):
        self.eventos.pop(event_id, None)
        return True


def _google(monkeypatch, *, cuenta: dict, modo: str | None = calendar_sync.MODO_PUSH) -> tuple[_GoogleFalso, list]:
    """Conecta el Google falso. `cuenta["activa"]`: True, False (revocada) o None."""
    fijar_agenda(monkeypatch)
    google = _GoogleFalso()
    for nombre in ("create_event", "update_event", "delete_event"):
        monkeypatch.setattr(google_calendar, nombre, getattr(google, nombre))

    async def _cuenta():
        return cuenta["activa"]

    monkeypatch.setattr(calendar_sync, "_cuenta_google", _cuenta)

    async def _modo():
        return modo

    # modo=None: el de verdad, que sin ajuste decide por la cuenta.
    monkeypatch.setattr(calendar_sync, "modo", _MODO_REAL if modo is None else _modo)
    encolados: list = []
    monkeypatch.setattr(calendar_sync, "encolar", lambda ids: encolados.extend(ids))
    return google, encolados


async def _agendar(contacto, hora: int):
    return await agenda.agendar(
        contact_id=contacto,
        conversation_id=None,
        canal=None,
        servicio=Servicio("Limpieza dental", 45),
        inicio=a_las(MARTES, hora),
        creada_por="operador",
    )


@pytestmark_db
async def test_si_la_respuesta_de_google_se_pierde_el_reintento_no_duplica(monkeypatch):
    google, encolados = _google(monkeypatch, cuenta={"activa": True})
    _conv, contacto = await persona(nombre="Ana López")
    try:
        cita = await _agendar(contacto, 10)
        assert cita.google_sync_pendiente and encolados == [cita.id]

        google.perder_respuesta = True
        with pytest.raises(httpx.ReadTimeout):
            await calendar_sync.sincronizar(cita.id)
        [guardada] = await citas_de(contacto)
        assert guardada.google_sync_pendiente, "sin respuesta, sigue pendiente"

        assert await calendar_sync.sincronizar(cita.id) == "actualizada"

        assert list(google.eventos) == [cita.id.hex], "un solo evento, el del id de la cita"
        assert google.eventos[cita.id.hex]["summary"] == "Limpieza dental · Ana López"
        [guardada] = await citas_de(contacto)
        assert not guardada.google_sync_pendiente
        assert guardada.google_event_id == cita.id.hex
        assert await calendar_sync.sincronizar(cita.id) == "al_dia"
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_con_el_permiso_revocado_la_cita_se_guarda_y_se_pone_al_dia_al_reconectar(monkeypatch):
    cuenta = {"activa": False}
    google, _encolados = _google(monkeypatch, cuenta=cuenta, modo=None)
    _conv, contacto = await persona()
    try:
        cita = await _agendar(contacto, 11)
        assert cita.estado == "agendada", "la agenda no depende de Google"
        assert cita.google_sync_pendiente, "con la cuenta dada de alta, se copia"

        with pytest.raises(GoogleNoDisponible):
            await calendar_sync.sincronizar(cita.id)
        assert await calendar_sync.barrer_pendientes() == 0, "sin permiso ni se intenta"
        assert google.eventos == {}

        cuenta["activa"] = True  # se reconecta
        assert await calendar_sync.barrer_pendientes() >= 1
        assert cita.id.hex in google.eventos
        assert await calendar_sync.barrer_pendientes() == 0, "la segunda pasada no copia nada"
        [guardada] = await citas_de(contacto)
        assert not guardada.google_sync_pendiente
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_mover_y_cancelar_tambien_se_copian(monkeypatch):
    google, encolados = _google(monkeypatch, cuenta={"activa": True})
    _conv, contacto = await persona()
    try:
        cita = await _agendar(contacto, 10)
        await calendar_sync.sincronizar(cita.id)

        nueva = await agenda.reagendar(cita.id, inicio=a_las(MARTES, 12), creada_por="operador")
        assert set(encolados[-2:]) == {cita.id, nueva.id}
        assert await calendar_sync.sincronizar(cita.id) == "borrada"
        assert await calendar_sync.sincronizar(nueva.id) == "creada"
        assert list(google.eventos) == [nueva.id.hex]
        assert google.eventos[nueva.id.hex]["start"] == a_las(MARTES, 12)

        await agenda.cancelar(nueva.id, cancelada_por="operador")
        assert await calendar_sync.sincronizar(nueva.id) == "borrada"
        assert google.eventos == {}
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_si_la_cita_cambia_mientras_se_copia_se_queda_pendiente(monkeypatch):
    from sqlalchemy import update

    from app.db.session import db_session
    from app.models.appointment import Appointment

    google, _encolados = _google(monkeypatch, cuenta={"activa": True})
    _conv, contacto = await persona()
    try:
        cita = await _agendar(contacto, 15)

        async def _la_mueven_mientras():
            async with db_session() as db:
                await db.execute(
                    update(Appointment).where(Appointment.id == cita.id).values(notas="cambió")
                )
                await db.commit()

        google.antes_de_contestar = _la_mueven_mientras
        assert await calendar_sync.sincronizar(cita.id) == "cambio_entretanto"
        [guardada] = await citas_de(contacto)
        assert guardada.google_sync_pendiente, "la próxima pasada copia lo nuevo"
    finally:
        await limpiar([contacto])


async def test_el_modo_por_defecto_depende_de_si_hay_cuenta_de_google(monkeypatch):
    """Nadie cambia de comportamiento al actualizar: con Google dado de alta,
    se mira lo ocupado allí y se copia (lo que ya hacía); sin Google, nada."""
    ajustes = {"calendar.google_sync": ""}
    cuenta = {"activa": None}

    async def _ajuste(clave, defecto, db=None):
        return ajustes.get(clave, defecto)

    async def _cuenta():
        return cuenta["activa"]

    monkeypatch.setattr(calendar_sync, "get_app_setting", _ajuste)
    monkeypatch.setattr(calendar_sync, "_cuenta_google", _cuenta)

    assert await calendar_sync.modo() == calendar_sync.MODO_OFF
    cuenta["activa"] = True
    assert await calendar_sync.modo() == calendar_sync.MODO_PUSH_AND_BUSY
    cuenta["activa"] = False
    assert await calendar_sync.modo() == calendar_sync.MODO_PUSH_AND_BUSY, "revocada sigue dada de alta"
    ajustes["calendar.google_sync"] = "push"
    assert await calendar_sync.modo() == calendar_sync.MODO_PUSH
    ajustes["calendar.google_sync"] = "loquesea"
    assert await calendar_sync.modo() == calendar_sync.MODO_PUSH_AND_BUSY


def test_la_tarea_reintenta_con_espera_creciente_y_luego_espera_a_la_barrida(monkeypatch):
    from app.tasks import calendar_sync as tarea

    esperas: list[int] = []

    class _Reintento(Exception):
        pass

    def _retry(exc=None, countdown=None, **_kw):
        esperas.append(countdown)
        raise _Reintento()

    async def _sin_google(_cita_id):
        raise GoogleNoDisponible("sin token")

    monkeypatch.setattr(tarea, "sincronizar", _sin_google)
    monkeypatch.setattr(tarea.sync_appointment, "retry", _retry)

    tarea.sync_appointment.push_request(retries=2)
    try:
        with pytest.raises(_Reintento):
            tarea.sync_appointment.run(str(uuid.uuid4()))
    finally:
        tarea.sync_appointment.pop_request()
    assert esperas == [120]

    tarea.sync_appointment.push_request(retries=tarea.sync_appointment.max_retries)
    try:
        assert tarea.sync_appointment.run(str(uuid.uuid4())) == "pendiente"
    finally:
        tarea.sync_appointment.pop_request()
