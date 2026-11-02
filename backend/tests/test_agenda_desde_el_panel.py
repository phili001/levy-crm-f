"""La agenda desde el panel: ver, agendar, mover, marcar, cancelar y bloquear (Fase 9.2, US-23).

Lo que usa la pestaña Agenda, «Citas de hoy» en Para hacer, la ficha y la
cabecera del chat. Las horas salen en la hora del negocio (`inicio_local`) y
una hora sin desfase se entiende en Cancún: la recepción puede estar en otro
país. El operador puede agendar fuera de horario o encima de un bloqueo, pero
no encima de otra cita ni en el pasado; el revisor de Meta no entra.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pytest

from tests._agenda import LUNES_9, MARTES, a_las, citas_de, fijar_agenda, limpiar, persona
from tests.test_voice_gating import pytestmark_db

MIERCOLES = MARTES + timedelta(days=1)


async def _usuarios() -> tuple[dict, dict, dict, list[uuid.UUID]]:
    """Cabeceras de admin, operador y revisor, y sus ids para limpiar."""
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.models.user import User

    suf = uuid.uuid4().hex[:8]
    usuarios = {
        rol: User(
            email=f"agenda-{rol}-{suf}@test.local",
            password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
            role=rol,
        )
        for rol in ("admin", "cliente", "revisor")
    }
    async with db_session() as db:
        db.add_all(usuarios.values())
        await db.commit()
    cabeceras = {
        rol: {"Authorization": f"Bearer {create_access_token(subject=str(u.id), extra={'role': rol})}"}
        for rol, u in usuarios.items()
    }
    return (
        cabeceras["admin"],
        cabeceras["cliente"],
        cabeceras["revisor"],
        [u.id for u in usuarios.values()],
    )


async def _borrar_usuarios(ids: list[uuid.UUID]) -> None:
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        for uid in ids:
            await db.execute(text("DELETE FROM audit_log WHERE user_id = :i"), {"i": str(uid)})
            await db.execute(
                text("UPDATE contact_activity SET actor_user_id = NULL WHERE actor_user_id = :i"),
                {"i": str(uid)},
            )
            await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(uid)})
        await db.commit()


@pytest.fixture
async def panel(monkeypatch):
    """Cliente HTTP con la agenda fijada al lunes 28-sep a las 9:00 de Cancún."""
    from app.main import app

    fijar_agenda(monkeypatch)
    admin, operador, revisor, ids = await _usuarios()
    _conv, contacto = await persona(nombre="Ana López")
    bloqueos: list[uuid.UUID] = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield {
            "c": c,
            "admin": admin,
            "op": operador,
            "revisor": revisor,
            "contacto": contacto,
            "bloqueos": bloqueos,
        }
    await limpiar([contacto], bloqueos)
    await _borrar_usuarios(ids)


async def _agendar(p, hora: int, servicio: str = "limpieza", dia=MARTES) -> httpx.Response:
    return await p["c"].post(
        "/api/v1/appointments",
        headers=p["op"],
        json={
            "contact_id": str(p["contacto"]),
            "servicio": servicio,
            "starts_at": f"{dia.isoformat()}T{hora:02d}:00",
        },
    )


@pytestmark_db
async def test_la_vista_trae_citas_bloqueos_y_la_hora_del_negocio(panel):
    c = panel["c"]
    assert (await _agendar(panel, 10)).status_code == 201
    r = await c.post(
        "/api/v1/schedule-blocks",
        headers=panel["op"],
        json={"starts_at": "2026-09-29T13:00", "ends_at": "2026-09-29T14:00", "motivo": "comida"},
    )
    assert r.status_code == 201, r.text
    panel["bloqueos"].append(uuid.UUID(r.json()["id"]))

    r = await c.get(
        "/api/v1/agenda",
        headers=panel["op"],
        params={"desde": MARTES.isoformat(), "hasta": MIERCOLES.isoformat()},
    )

    assert r.status_code == 200, r.text
    vista = r.json()
    assert vista["zona"] == "America/Cancun"
    assert vista["hoy"] == LUNES_9.date().isoformat()
    assert vista["ahora_local"] == "2026-09-28T09:00"
    assert vista["work_hours"] == ["08:00-17:00"]
    assert {"nombre": "Limpieza dental", "duracion_min": 45} in vista["servicios"]
    [cita] = vista["citas"]
    assert (cita["inicio_local"], cita["fin_local"]) == ("2026-09-29T10:00", "2026-09-29T10:45")
    assert cita["starts_at"].startswith("2026-09-29T15:00"), "guardada en UTC: las 10 de Cancún"
    assert cita["contact_nombre"] == "Ana López" and cita["creada_por"] == "operador"
    assert cita["activa"] is True
    [bloqueo] = vista["bloqueos"]
    assert (bloqueo["inicio_local"], bloqueo["motivo"]) == ("2026-09-29T13:00", "comida")


@pytestmark_db
async def test_el_operador_agenda_mueve_marca_y_cancela(panel):
    c, op = panel["c"], panel["op"]
    r = await _agendar(panel, 10)
    assert r.status_code == 201, r.text
    cita = r.json()

    # Encima de otra cita no: lo para la base de datos.
    r = await _agendar(panel, 10, servicio="valoración")
    assert r.status_code == 409 and "ocupar" in r.json()["detail"]
    # En el pasado tampoco.
    r = await _agendar(panel, 8, dia=LUNES_9.date())
    assert r.status_code == 422

    # Mover devuelve la cita nueva; la vieja queda cancelada.
    r = await c.patch(f"/api/v1/appointments/{cita['id']}", headers=op, json={"starts_at": "2026-09-29T12:00"})
    assert r.status_code == 200, r.text
    movida = r.json()
    assert movida["id"] != cita["id"] and movida["estado"] == "reagendada"
    assert movida["inicio_local"] == "2026-09-29T12:00" and movida["duracion_min"] == 45

    # Cambiar el servicio en el sitio cambia la duración.
    r = await c.patch(f"/api/v1/appointments/{movida['id']}", headers=op, json={"servicio": "valoración"})
    assert r.status_code == 200 and r.json()["fin_local"] == "2026-09-29T12:30"
    r = await c.patch(f"/api/v1/appointments/{movida['id']}", headers=op, json={"notas": "Trae radiografía"})
    assert r.json()["notas"] == "Trae radiografía"

    # Marcarla como completada la saca de las activas; ya no se puede cancelar.
    r = await c.patch(f"/api/v1/appointments/{movida['id']}", headers=op, json={"estado": "completada"})
    assert r.status_code == 200 and r.json()["activa"] is False
    r = await c.delete(f"/api/v1/appointments/{movida['id']}", headers=op)
    assert r.status_code == 409

    # Cancelar una activa: la fila se queda, con su estado.
    otra = (await _agendar(panel, 15)).json()
    r = await c.delete(f"/api/v1/appointments/{otra['id']}", headers=op)
    assert r.status_code == 200 and r.json()["estado"] == "cancelada"

    estados = sorted(x.estado for x in await citas_de(panel["contacto"]))
    assert estados == ["cancelada", "cancelada", "completada"]


@pytestmark_db
async def test_horarios_libres_para_el_formulario(panel):
    c, op = panel["c"], panel["op"]
    cita = (await _agendar(panel, 10)).json()

    r = await c.get(
        "/api/v1/appointments/slots",
        headers=op,
        params={"fecha": MARTES.isoformat(), "servicio": "limpieza"},
    )
    assert r.status_code == 200, r.text
    libres = r.json()
    assert (libres["servicio"], libres["duracion_min"]) == ("Limpieza dental", 45)
    assert libres["horarios"][0] == "08:00" and libres["horarios"][-1] == "16:00"
    assert "10:00" not in libres["horarios"] and "09:30" not in libres["horarios"]

    # Al mover la cita, su propio hueco cuenta como libre.
    r = await c.get(
        "/api/v1/appointments/slots",
        headers=op,
        params={"fecha": MARTES.isoformat(), "servicio": "limpieza", "excluir_cita": cita["id"]},
    )
    assert "10:00" in r.json()["horarios"]

    sabado = (LUNES_9.date() + timedelta(days=5)).isoformat()
    r = await c.get("/api/v1/appointments/slots", headers=op, params={"fecha": sabado})
    assert r.json()["horarios"] == [] and "sábado" in r.json()["cerrado"]


@pytestmark_db
async def test_un_bloqueo_avisa_de_las_citas_que_quedan_dentro(panel):
    c, op = panel["c"], panel["op"]
    await _agendar(panel, 10)
    r = await c.post(
        "/api/v1/schedule-blocks",
        headers=op,
        json={"starts_at": "2026-09-29T00:00", "ends_at": "2026-09-30T00:00", "motivo": "vacaciones"},
    )
    assert r.status_code == 201, r.text
    bloqueo = r.json()
    assert bloqueo["citas_afectadas"] == 1, "la cita no se toca, pero se avisa"

    r = await c.post(
        "/api/v1/schedule-blocks",
        headers=op,
        json={"starts_at": "2026-09-29T14:00", "ends_at": "2026-09-29T13:00"},
    )
    assert r.status_code == 422

    r = await c.get("/api/v1/schedule-blocks", headers=op)
    assert bloqueo["id"] in [b["id"] for b in r.json()]
    assert (await c.delete(f"/api/v1/schedule-blocks/{bloqueo['id']}", headers=op)).status_code == 200
    assert (await c.delete(f"/api/v1/schedule-blocks/{bloqueo['id']}", headers=op)).status_code == 404


@pytestmark_db
async def test_citas_de_hoy_y_proximas_de_una_persona(monkeypatch, panel):
    c, op = panel["c"], panel["op"]
    await _agendar(panel, 10)
    await _agendar(panel, 16, dia=MIERCOLES)

    r = await c.get(
        "/api/v1/appointments",
        headers=op,
        params={"contact_id": str(panel["contacto"]), "proximas": "true"},
    )
    assert [x["inicio_local"] for x in r.json()] == ["2026-09-29T10:00", "2026-09-30T16:00"]

    # «Citas de hoy» con el reloj del negocio el martes a las 7:00.
    fijar_agenda(monkeypatch, ahora=a_las(MARTES, 7))
    r = await c.get("/api/v1/appointments/today", headers=op)
    hoy = r.json()
    assert hoy["hoy"] == MARTES.isoformat()
    assert [x["inicio_local"] for x in hoy["citas"]] == ["2026-09-29T10:00"]


@pytestmark_db
async def test_el_revisor_de_meta_no_ve_la_agenda(panel):
    c = panel["c"]
    for url in ("/api/v1/agenda?desde=2026-09-29&hasta=2026-09-29", "/api/v1/appointments/today"):
        assert (await c.get(url, headers=panel["revisor"])).status_code == 403
    assert (await c.get("/api/v1/appointments/today")).status_code == 401


@pytestmark_db
async def test_la_exportacion_de_datos_incluye_las_citas(panel):
    await _agendar(panel, 10)
    r = await panel["c"].get(f"/api/v1/contacts/{panel['contacto']}/export", headers=panel["admin"])
    assert r.status_code == 200, r.text
    [cita] = r.json()["citas"]
    assert cita["servicio"] == "Limpieza dental" and cita["estado"] == "agendada"


@pytestmark_db
async def test_borrar_a_la_persona_se_lleva_sus_citas(panel):
    """Derecho de supresión: la cascada de `contact_id` borra las citas."""
    from sqlalchemy import delete

    from app.db.session import db_session
    from app.models.contact import Contact

    await _agendar(panel, 10)
    async with db_session() as db:
        await db.execute(delete(Contact).where(Contact.id == panel["contacto"]))
        await db.commit()
    assert await citas_de(panel["contacto"]) == []


@pytestmark_db
async def test_el_horario_guarda_el_modo_de_la_copia_en_google(panel):
    from sqlalchemy import text

    from app.db.session import db_session

    c, admin = panel["c"], panel["admin"]
    previo = (await c.get("/api/v1/admin/settings/calendar", headers=admin)).json()
    assert previo["google_cuenta"] in ("sin_cuenta", "activa", "sin_permiso")
    try:
        cuerpo = {k: previo[k] for k in ("timezone", "work_days", "work_hours", "holidays", "slot_step_min", "min_notice_min")}
        r = await c.put("/api/v1/admin/settings/calendar", headers=admin, json={**cuerpo, "google_sync": "push"})
        assert r.status_code == 200, r.text
        r = await c.put("/api/v1/admin/settings/calendar", headers=admin, json={**cuerpo, "google_sync": "a_veces"})
        assert r.status_code == 422
        async with db_session() as db:
            guardado = (
                await db.execute(text("SELECT value FROM app_settings WHERE key = 'calendar.google_sync'"))
            ).scalar_one()
        assert guardado == "push"
        # El operador no toca el horario.
        assert (await c.get("/api/v1/admin/settings/calendar", headers=panel["op"])).status_code == 403
    finally:
        async with db_session() as db:
            await db.execute(text("DELETE FROM app_settings WHERE key = 'calendar.google_sync'"))
            await db.commit()
