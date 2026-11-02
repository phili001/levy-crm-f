"""SPEC-18, tramo 3: retomar a quien preguntó y no agendó.

Lo que no se puede romper:
- Una sola vez por conversación, y Sofía decide si vale la pena (NO_ESCRIBIR).
- Nunca a quien ya tiene cita, ni a quien acaba de escribir, ni si lo último
  fue del paciente (eso es una respuesta pendiente, no un seguimiento).
- Si el paciente escribe mientras el modelo piensa, no sale nada.
- Lo que sale queda en el hilo como mensaje de Sofía.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.services import retomar
from app.services import seguimientos as svc

TZ = ZoneInfo("America/Cancun")


def test_no_escribir_es_no_escribir_aunque_venga_con_adornos():
    assert retomar.decision("NO_ESCRIBIR") is None
    assert retomar.decision("  no_escribir. ") is None
    assert retomar.decision("") is None
    assert retomar.decision('"Hola Ana, ¿te busco un horario?"') == "Hola Ana, ¿te busco un horario?"
    assert len(retomar.decision("x" * 2000)) == retomar.MAX_CARACTERES


def test_no_se_puede_retomar_con_la_ventana_casi_cerrada():
    with pytest.raises(ValidationError):
        svc.RetomarConfig(activo=True, horas=23)


# --- Con base de datos ---------------------------------------------------------------


def _db_available() -> bool:
    try:
        from sqlalchemy import text

        from app.db.session import db_session

        async def _check():
            async with db_session() as db:
                await db.execute(text("select 1"))

        asyncio.run(_check())
        return True
    except Exception:
        return False


con_db = pytest.mark.skipif(
    not _db_available(),
    reason="DB no disponible en este entorno (se ejecuta en CI con Postgres)",
)

AGENTE = SimpleNamespace(
    llm_provider_id=None, fallback_provider_id=None, fallback_model=None,
    model_name="gpt-x", prompt_system="Eres Sofía.", motor="crm",
)


def _ahora() -> datetime:
    return datetime.now(TZ).replace(hour=15, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


async def _conversacion(
    ahora: datetime, *, horas: float = 21, ultimo: str = "assistant", cita: bool = False
) -> tuple[uuid.UUID, uuid.UUID]:
    """Un paciente que preguntó un precio hace `horas` y Sofía le contestó."""
    from app.db.session import db_session
    from app.models.appointment import Appointment
    from app.models.contact import Contact, ContactOrigen
    from app.models.conversation import Conversation, ConversationCanal, ConversationStatus
    from app.models.message import Message, MessageRole

    cuando = ahora - timedelta(hours=horas)
    async with db_session() as db:
        contacto = Contact(
            telefono=f"+52998{uuid.uuid4().int % 10**7:07d}", nombre="Ana", origen=ContactOrigen.whatsapp
        )
        db.add(contacto)
        await db.flush()
        conv = Conversation(
            contact_id=contacto.id, canal=ConversationCanal.whatsapp,
            session_id=f"s-{uuid.uuid4().hex[:8]}", status=ConversationStatus.bot,
            last_message_at=cuando,
        )
        db.add(conv)
        await db.flush()
        db.add(Message(conversation_id=conv.id, rol=MessageRole.user,
                       contenido="¿Cuánto cuesta el blanqueamiento?", created_at=cuando))
        if ultimo == "assistant":
            db.add(Message(conversation_id=conv.id, rol=MessageRole.assistant,
                           contenido="Cuesta 3,500 pesos. ¿Te agendo una valoración?",
                           created_at=cuando + timedelta(seconds=20)))
        if cita:
            inicio = ahora + timedelta(days=3)
            db.add(Appointment(
                contact_id=contacto.id, servicio="Valoración", starts_at=inicio,
                ends_at=inicio + timedelta(minutes=30), estado="agendada", creada_por="agente",
                recurso=f"prueba-{uuid.uuid4().hex[:10]}",
            ))
        await db.commit()
        return contacto.id, conv.id


async def _borrar(contact_id) -> None:
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        await db.execute(text("DELETE FROM contacts WHERE id = :i"), {"i": str(contact_id)})
        await db.commit()


def _correr(prueba, respuesta: str = "Hola Ana, ¿quieres que te busque un horario para la valoración?"):
    llm = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(content=respuesta)))
    mandar = AsyncMock(return_value="wamid.seguimiento")

    async def _run():
        with patch(
            "app.services.runtime_config.get_runtime_for_channel", AsyncMock(return_value=AGENTE)
        ), patch("app.providers.llm.resolve_llm_provider", AsyncMock(return_value=llm)), patch(
            "app.services.channel_sender.send_text_to_conversation", mandar
        ):
            await prueba(llm, mandar)

    asyncio.run(_run())


def _a_esta(mandar, conv_id) -> list:
    return [c for c in mandar.await_args_list if c.args[0].id == conv_id]


@con_db
def test_sofia_retoma_una_vez_y_queda_en_el_hilo():
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.conversation import Conversation
    from app.models.message import Message

    async def prueba(llm, mandar):
        ahora = _ahora()
        contacto, conv_id = await _conversacion(ahora)
        try:
            assert conv_id in await retomar.candidatas(20, ahora)
            assert await retomar.retomar_una(conv_id, 20) == "enviado"
            assert await retomar.retomar_una(conv_id, 20) == "omitido", "una sola vez"
            assert len(_a_esta(mandar, conv_id)) == 1
            assert conv_id not in await retomar.candidatas(20, ahora)

            llamada = llm.complete.await_args.kwargs
            assert llamada["source"] == "seguimiento"
            assert "NO_ESCRIBIR" in llamada["messages"][-1].content
            async with db_session() as db:
                conv = await db.get(Conversation, conv_id)
                ultimo = (
                    await db.execute(
                        select(Message).where(Message.conversation_id == conv_id)
                        .order_by(Message.created_at.desc()).limit(1)
                    )
                ).scalar_one()
            assert conv.seguimiento_enviado_at is not None
            assert ultimo.extra["kind"] == "seguimiento"
            assert ultimo.contenido.startswith("Hola Ana")
        finally:
            await _borrar(contacto)

    _correr(prueba)


@con_db
def test_si_sofia_decide_no_escribir_no_sale_nada_ni_se_vuelve_a_preguntar():
    async def prueba(llm, mandar):
        ahora = _ahora()
        contacto, conv_id = await _conversacion(ahora)
        try:
            assert await retomar.retomar_una(conv_id, 20) == "omitido"
            assert _a_esta(mandar, conv_id) == []
            assert conv_id not in await retomar.candidatas(20, ahora)
        finally:
            await _borrar(contacto)

    _correr(prueba, respuesta="NO_ESCRIBIR")


@con_db
def test_no_se_retoma_a_quien_ya_tiene_cita_ni_a_quien_acaba_de_escribir():
    async def prueba(llm, mandar):
        ahora = _ahora()
        con_cita, conv_cita = await _conversacion(ahora, cita=True)
        reciente, conv_reciente = await _conversacion(ahora, horas=10)
        try:
            quietas = await retomar.candidatas(20, ahora)
            assert conv_cita not in quietas
            assert conv_reciente not in quietas
        finally:
            await _borrar(con_cita)
            await _borrar(reciente)

    _correr(prueba)


@con_db
def test_si_lo_ultimo_es_del_paciente_no_es_un_seguimiento():
    async def prueba(llm, mandar):
        ahora = _ahora()
        contacto, conv_id = await _conversacion(ahora, ultimo="user")
        try:
            assert await retomar.retomar_una(conv_id, 20) == "pendiente"
            llm.complete.assert_not_awaited()
        finally:
            await _borrar(contacto)

    _correr(prueba)


@con_db
def test_si_el_paciente_escribe_mientras_sofia_piensa_no_sale_nada():
    from app.db.session import db_session
    from app.models.message import Message, MessageRole

    async def prueba(llm, mandar):
        ahora = _ahora()
        contacto, conv_id = await _conversacion(ahora)

        async def escribe_justo_ahora(**_):
            async with db_session() as db:
                db.add(Message(conversation_id=conv_id, rol=MessageRole.user, contenido="Sí, ¿el jueves?",
                               created_at=datetime.now(timezone.utc)))
                await db.commit()
            return SimpleNamespace(content="Hola Ana, ¿te busco horario?")

        llm.complete.side_effect = escribe_justo_ahora
        try:
            assert await retomar.retomar_una(conv_id, 20) == "omitido"
            assert _a_esta(mandar, conv_id) == []
        finally:
            await _borrar(contacto)

    _correr(prueba)


@con_db
def test_la_vuelta_del_beat_retoma_con_el_interruptor_encendido():
    cfg = svc.SeguimientosConfig(retomar=svc.RetomarConfig(activo=True, horas=20))

    async def prueba(llm, mandar):
        ahora = _ahora()
        contacto, conv_id = await _conversacion(ahora)
        try:
            with patch("app.services.seguimientos.leer_config", AsyncMock(return_value=cfg)), patch(
                "app.agents.tools.schedule_config.get_schedule_config",
                AsyncMock(return_value=SimpleNamespace(tz=TZ, tz_name="America/Cancun")),
            ):
                resultados = await svc.barrer(ahora)
            assert resultados["retomar"].enviados >= 1
            assert len(_a_esta(mandar, conv_id)) == 1
        finally:
            await _borrar(contacto)

    _correr(prueba)
