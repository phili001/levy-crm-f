"""«Sofía en n8n»: el turno sale a n8n y lo que contesta llega al paciente y al inbox.

Con un agente de motor n8n, el CRM no llama a ningún modelo propio: le pasa a
n8n los mensajes del paciente, el historial y la ficha, y lo que n8n contesta
sigue el mismo camino que una respuesta de Sofía (formato del canal, partir en
mensajes, guardar). En el inbox queda marcado como de n8n.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tests._motor_n8n import (
    agente_en_n8n,
    conversacion,
    mensajes,
    n8n_que_contesta,
    whatsapp_entrante,
)
from tests._motor_n8n import turno as _turno
from tests._n8n import db_disponible

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


@pytest.mark.asyncio
async def test_la_respuesta_directa_llega_al_paciente_y_queda_marcada(monkeypatch):
    n8n = n8n_que_contesta(monkeypatch, (200, {"respuesta": "Sí, mañana a las 10 tengo hueco."}))
    enviar = AsyncMock(return_value="wamid.OUT")
    conv, _msg, clave = await whatsapp_entrante("¿Tienen cita mañana?")

    await _turno(clave, agente_en_n8n(), enviar)

    evento = n8n.eventos[0]
    assert n8n.cabeceras == ["secreto-del-motor"]
    assert evento["evento"] == "mensajes" and evento["prueba"] is False
    assert evento["mensajes"][0]["texto"] == "¿Tienen cita mañana?"
    assert evento["conversacion"]["canal"] == "whatsapp" and evento["conversacion"]["ventana_abierta"] is True
    assert evento["contacto"]["telefono"].startswith("+52998")
    assert evento["api"]["conversacion"] == f"/conversations/{conv.id}"

    enviar.assert_awaited()
    assert "mañana a las 10" in enviar.await_args.args[1]
    de_sofia = [m for m in await mensajes(conv.id) if m.rol.value == "assistant"]
    assert de_sofia and (de_sofia[-1].extra or {}).get("motor") == "n8n", "en el inbox no se ve que vino de n8n"


@pytest.mark.asyncio
async def test_n8n_caido_pasa_a_humano_con_mensaje_puente(monkeypatch):
    n8n = n8n_que_contesta(monkeypatch, httpx.ConnectError("n8n caído"))
    enviar = AsyncMock(return_value="wamid.PUENTE")
    conv, _msg, clave = await whatsapp_entrante()

    await _turno(clave, agente_en_n8n(), enviar)

    assert len(n8n.eventos) == 3, "no se reintentó dos veces"
    assert len({e["evento_id"] for e in n8n.eventos}) == 1, "los reintentos no llevan el mismo evento_id"
    assert (await conversacion(conv.id)).status.value == "humano"
    assert enviar.await_count == 1, "el paciente no recibió el mensaje puente"


@pytest.mark.asyncio
async def test_en_entrenamiento_queda_como_sugerencia(monkeypatch):
    n8n_que_contesta(monkeypatch, (200, {"respuesta": "Tengo hueco el martes."}))
    enviar = AsyncMock(return_value="wamid.X")
    conv, _msg, clave = await whatsapp_entrante()

    await _turno(clave, agente_en_n8n(), enviar,
                 patch("app.services.conversation.is_channel_training", AsyncMock(return_value=True)))

    enviar.assert_not_awaited()
    sugerencia = [m for m in await mensajes(conv.id) if (m.extra or {}).get("is_draft")]
    assert sugerencia and sugerencia[0].contenido == "Tengo hueco el martes."
    assert (sugerencia[0].extra or {}).get("motor") == "n8n"


@pytest.mark.asyncio
async def test_si_contesta_despues_se_programa_la_comprobacion(monkeypatch):
    from app.tasks import n8n_motor_check

    n8n_que_contesta(monkeypatch, (200, {}))
    programada = []
    monkeypatch.setattr(n8n_motor_check.comprobar_respuesta_n8n, "apply_async",
                        lambda args, countdown: programada.append((args, countdown)))
    enviar = AsyncMock(return_value="wamid.X")
    conv, _msg, clave = await whatsapp_entrante()

    await _turno(clave, agente_en_n8n(), enviar)

    enviar.assert_not_awaited()
    assert (await conversacion(conv.id)).status.value == "bot", "se derivó sin esperar a la API"
    assert programada and programada[0][0][0] == str(conv.id) and programada[0][1] == 600


@pytest.mark.asyncio
async def test_la_comprobacion_deriva_solo_si_no_llego_nada():
    from datetime import datetime, timedelta, timezone

    from app.db.session import db_session
    from app.models.message import Message, MessageRole
    from app.services.n8n_motor import comprobar_respuesta

    with patch("app.services.channel_sender.send_text_to_conversation", AsyncMock(return_value="wamid.P")):
        conv, _m, _c = await whatsapp_entrante()
        desde = datetime.now(timezone.utc) - timedelta(seconds=5)
        async with db_session() as db:
            db.add(Message(conversation_id=conv.id, rol=MessageRole.assistant, contenido="Te contesto por la API"))
            await db.commit()
        assert await comprobar_respuesta(conv.id, desde) is False, "derivó aunque n8n sí contestó"

        conv2, _m2, _c2 = await whatsapp_entrante()
        assert await comprobar_respuesta(conv2.id, datetime.now(timezone.utc)) is True
        assert (await conversacion(conv2.id)).status.value == "humano"


@pytest.mark.asyncio
async def test_acciones_pasar_a_humano_etapa_y_etiquetas(monkeypatch):
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.contact import Contact, ContactTag
    from app.models.tag import Tag

    n8n_que_contesta(monkeypatch, (200, {
        "respuesta": "Te paso con recepción para confirmar el precio.",
        "acciones": {"pasar_a_humano": "pide precio especial", "etapa": "cotizacion",
                     "etiquetas": ["precio-especial-n8n"]},
    }))
    enviar = AsyncMock(return_value="wamid.X")
    conv, _msg, clave = await whatsapp_entrante()

    await _turno(clave, agente_en_n8n(), enviar)

    assert (await conversacion(conv.id)).status.value == "humano"
    assert enviar.await_count == 1 and "recepción" in enviar.await_args.args[1], (
        "la respuesta de n8n tenía que salir como mensaje puente, una sola vez"
    )
    async with db_session() as db:
        ficha = await db.get(Contact, conv.contact_id)
        etiquetas = (await db.execute(
            select(Tag.nombre).join(ContactTag, ContactTag.tag_id == Tag.id).where(ContactTag.contact_id == ficha.id)
        )).scalars().all()
    assert ficha.estado.value == "solicitud_presupuesto"
    assert "precio-especial-n8n" in etiquetas
