"""Por teléfono, la cita queda a la hora que se dijo en Cancún (Fase 9, V-1 sin llamadas).

La V-1 pedía comprobar con llamadas reales que la cita cae "a la hora
correcta (−05:00)". Aquí se comprueba el camino entero de la voz sin
teléfono: `consultar_disponibilidad` entrega la hora ya dicha ("mañana a las
diez de la mañana") y el `start` con el desfase sacado de la zona IANA (nunca
escrito a mano); `agendar_cita` con ese `start` deja la cita a las 15:00 UTC,
que son las 10:00 en Cancún. Si el modelo devuelve la hora sin desfase, se
entiende en la zona del negocio, no en la del servidor.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from app.agents.tools import calendar as cal
from app.models.conversation import ConversationCanal
from tests._agenda import MARTES, citas_de, ctx, fijar_agenda, limpiar, persona
from tests.test_voice_gating import pytestmark_db


@pytestmark_db
async def test_la_voz_agenda_a_las_diez_de_cancun(monkeypatch):
    fijar_agenda(monkeypatch)
    conv, contacto = await persona(ConversationCanal.retell_voice)
    try:
        oferta = json.loads(
            await cal.consultar_disponibilidad(
                {"fecha": MARTES.isoformat(), "hora": "10:00"}, ctx(conv, voz=True)
            )
        )
        diez = next(s for s in oferta["slots_libres"] if s["start"].startswith("2026-09-29T10:00"))
        assert diez["label"] == "mañana a las diez de la mañana"
        assert diez["start"] == "2026-09-29T10:00:00-05:00"

        cita = json.loads(
            await cal.agendar_cita({"start": diez["start"], "servicio": "valoración"}, ctx(conv, voz=True))
        )

        assert cita["ok"] is True
        assert cita["label"] == "mañana a las diez de la mañana"
        [guardada] = await citas_de(contacto)
        assert guardada.starts_at == datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
        assert guardada.canal == ConversationCanal.retell_voice
    finally:
        await limpiar([contacto])


@pytestmark_db
async def test_una_hora_sin_desfase_se_entiende_en_cancun(monkeypatch):
    fijar_agenda(monkeypatch)
    conv, contacto = await persona(ConversationCanal.retell_voice)
    try:
        await cal.consultar_disponibilidad({"fecha": MARTES.isoformat(), "hora": "16:30"}, ctx(conv, voz=True))
        cita = json.loads(
            await cal.agendar_cita(
                {"start": "2026-09-29T16:30:00", "servicio": "valoración"}, ctx(conv, voz=True)
            )
        )
        assert cita["ok"] is True
        assert cita["label"] == "mañana a las cuatro y media de la tarde"
        [guardada] = await citas_de(contacto)
        assert guardada.starts_at == datetime(2026, 9, 29, 21, 30, tzinfo=UTC)
    finally:
        await limpiar([contacto])
