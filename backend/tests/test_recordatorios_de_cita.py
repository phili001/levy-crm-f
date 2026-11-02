"""SPEC-18, tramo 2: el recordatorio antes de la cita, la confirmación y «no llegó».

Lo que no se puede romper:
- Un aviso sale UNA vez, aunque la vuelta del beat se repita.
- Nada sale con el canal en pausa, a quien pidió la baja ni a un número
  bloqueado, y un problema de configuración no gasta el aviso de nadie.
- Por Instagram solo con la ventana de 24 h abierta (nunca con HUMAN_AGENT).
- Lo que se manda queda en el hilo, con el texto que leyó el paciente, y la
  conversación sigue con Sofía para que ella atienda la respuesta.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.api.seguimientos import dato_por_etiqueta
from app.services import seguimientos as svc
from app.services.plantillas_biblioteca import SUGERIDAS
from app.services.plantillas_whatsapp import a_ycloud

TZ = ZoneInfo("America/Cancun")
MAPA_RECORDATORIO = {"1": "paciente", "2": "negocio", "3": "cita_fecha", "4": "cita_hora"}
MAPA_INASISTENCIA = {"1": "paciente", "2": "negocio", "3": "cita_fecha"}


def _plantilla(i: int, estado: str = "APPROVED") -> dict:
    datos = a_ycloud(SUGERIDAS[i].plantilla)
    datos["status"] = estado
    return datos


# --- Sin base de datos -------------------------------------------------------------


def test_los_datos_de_la_cita_salen_como_los_lee_el_paciente():
    cita = SimpleNamespace(
        starts_at=datetime(2026, 10, 28, 15, 30, tzinfo=timezone.utc), servicio="Limpieza dental"
    )
    contacto = SimpleNamespace(nombre="ana maría lópez")
    valores = svc.valores_de(cita, contacto, svc.SeguimientosConfig(negocio="Clínica Sonrisa"), TZ)
    assert valores == {
        "paciente": "Ana",
        "negocio": "Clínica Sonrisa",
        "cita_fecha": "miércoles 28 de octubre",
        "cita_hora": "10:30",
        "cita_servicio": "Limpieza dental",
    }
    sin_nombre = svc.valores_de(cita, SimpleNamespace(nombre=None), svc.SeguimientosConfig(), TZ)
    assert sin_nombre["paciente"] == "paciente", "Meta no deja mandar una variable vacía"


def test_una_variable_sin_dato_no_se_manda_vacia():
    with pytest.raises(svc.Omitido, match=r"\{\{2\}\}"):
        svc.variables_para(["1", "2"], {"1": "paciente"}, {"paciente": "Ana"})


def test_no_se_enciende_sin_plantilla_ni_con_el_negocio_vacio():
    cfg = svc.SeguimientosConfig(
        recordatorio=svc.AvisoConfig(activo=True, horas=24, variables={"2": "negocio"})
    )
    fallos = " ".join(svc.problemas_de_config(cfg))
    assert "plantilla del recordatorio" in fallos
    assert "nombre del negocio" in fallos


def test_una_plantilla_en_revision_o_con_variables_sin_dato_dice_por_que_no_sale():
    from app.api.admin import _template_to_out

    aviso = svc.AvisoConfig(activo=True, plantilla="recordatorio_cita_v1", variables=MAPA_RECORDATORIO)
    cfg = svc.SeguimientosConfig(negocio="Clínica")
    pendiente = _template_to_out(_plantilla(0, "PENDING"))
    assert "pendiente de aprobación" in svc.problema_de_plantilla(pendiente, aviso, cfg)
    aprobada = _template_to_out(_plantilla(0))
    assert svc.problema_de_plantilla(aprobada, aviso, cfg) is None
    corto = svc.AvisoConfig(activo=True, plantilla="recordatorio_cita_v1", variables={"1": "paciente"})
    assert "{{2}}" in svc.problema_de_plantilla(aprobada, corto, cfg)
    assert "no está en tu cuenta" in svc.problema_de_plantilla(None, aviso, cfg)


def test_las_etiquetas_de_las_variables_proponen_su_dato():
    assert dato_por_etiqueta("Nombre del paciente") == "paciente"
    assert dato_por_etiqueta("Nombre de la clínica") == "negocio"
    assert dato_por_etiqueta("Fecha de la cita") == "cita_fecha"
    assert dato_por_etiqueta("Hora de su cita") == "cita_hora"
    assert dato_por_etiqueta("Tratamiento") == "cita_servicio"
    assert dato_por_etiqueta("Código de descuento") is None


def test_solo_se_usa_el_whatsapp_de_la_ficha_no_un_id_de_instagram():
    assert svc.whatsapp_de(SimpleNamespace(telefono="+529981234567")) == "+529981234567"
    assert svc.whatsapp_de(SimpleNamespace(telefono="wa:MX.123")) == "wa:MX.123"
    assert svc.whatsapp_de(SimpleNamespace(telefono="ig:178414")) is None


def test_nunca_de_noche():
    cfg = svc.SeguimientosConfig()
    assert svc.en_horario(datetime(2026, 10, 28, 8, 0, tzinfo=TZ), cfg)
    assert not svc.en_horario(datetime(2026, 10, 28, 20, 0, tzinfo=TZ), cfg)
    assert not svc.en_horario(datetime(2026, 10, 28, 6, 59, tzinfo=TZ), cfg)


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


class _WhatsAppFalso:
    def __init__(self) -> None:
        self.enviadas: list[dict] = []

    async def list_templates(self):
        return [_plantilla(0), _plantilla(1)]

    async def send_template(self, to, name, language, variables=None, **kw):
        self.enviadas.append({"to": to, "name": name, "vars": variables, **kw})
        return f"wamid.{len(self.enviadas)}"


def _config(**cambios) -> svc.SeguimientosConfig:
    base = {
        "negocio": "Clínica Sonrisa",
        "recordatorio": svc.AvisoConfig(
            activo=True, horas=24, plantilla="recordatorio_cita_v1", variables=MAPA_RECORDATORIO
        ),
        "inasistencia": svc.AvisoConfig(
            activo=True, horas=1, plantilla="cita_perdida_v1", variables=MAPA_INASISTENCIA
        ),
    }
    return svc.SeguimientosConfig(**{**base, **cambios})


async def _ficha_con_cita(
    *, telefono: str | None = None, inicio: datetime, creada: datetime, estado: str = "agendada",
    canal: str | None = None,
):
    """Contacto, (conversación) y cita en un recurso propio, para no chocar con
    otras citas de la base de pruebas."""
    from sqlalchemy import text

    from app.db.session import db_session
    from app.models.appointment import Appointment
    from app.models.contact import Contact, ContactOrigen
    from app.models.conversation import Conversation, ConversationCanal

    tel = telefono or f"+52998{uuid.uuid4().int % 10**7:07d}"
    async with db_session() as db:
        contacto = Contact(telefono=tel, nombre="Ana López", origen=ContactOrigen.whatsapp)
        db.add(contacto)
        await db.flush()
        conv = None
        if canal:
            conv = Conversation(
                contact_id=contacto.id, canal=ConversationCanal(canal), session_id=f"s-{uuid.uuid4().hex[:8]}"
            )
            db.add(conv)
            await db.flush()
        cita = Appointment(
            contact_id=contacto.id,
            conversation_id=conv.id if conv else None,
            canal=ConversationCanal(canal) if canal else None,
            servicio="Limpieza dental",
            starts_at=inicio,
            ends_at=inicio + timedelta(minutes=30),
            estado=estado,
            creada_por="agente",
            recurso=f"prueba-{uuid.uuid4().hex[:10]}",
        )
        db.add(cita)
        await db.flush()
        await db.execute(
            text("UPDATE appointments SET created_at = :c, updated_at = :c WHERE id = :i"),
            {"c": creada, "i": cita.id},
        )
        await db.commit()
        return contacto.id, cita.id, conv.id if conv else None


async def _borrar(contact_id) -> None:
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        await db.execute(text("DELETE FROM contacts WHERE id = :i"), {"i": str(contact_id)})
        await db.commit()


async def _cita(cita_id):
    from app.db.session import db_session
    from app.models.appointment import Appointment

    async with db_session() as db:
        return await db.get(Appointment, cita_id)


def _las_tres_de_la_tarde() -> datetime:
    return datetime.now(TZ).replace(hour=15, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _correr(prueba, cfg: svc.SeguimientosConfig | None = None):
    falso = _WhatsAppFalso()

    async def _run():
        with patch("app.providers.whatsapp.get_whatsapp_provider", return_value=falso), patch(
            "app.services.seguimientos.leer_config", AsyncMock(return_value=cfg or _config())
        ), patch(
            "app.agents.tools.schedule_config.get_schedule_config",
            AsyncMock(return_value=SimpleNamespace(tz=TZ, tz_name="America/Cancun")),
        ):
            await prueba(falso)

    asyncio.run(_run())


def _mias(falso, cita_id, tipo="recordatorio"):
    return [e for e in falso.enviadas if e.get("idempotency_key") == f"{tipo}:{cita_id}"]


@con_db
def test_el_recordatorio_sale_una_vez_queda_en_el_hilo_y_sigue_con_sofia():
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.conversation import Conversation, ConversationStatus
    from app.models.message import Message

    async def prueba(falso):
        ahora = _las_tres_de_la_tarde()
        contacto, cita_id, _ = await _ficha_con_cita(
            inicio=ahora + timedelta(hours=20), creada=ahora - timedelta(days=3)
        )
        try:
            await svc.barrer(ahora)
            await svc.barrer(ahora)
            enviadas = _mias(falso, cita_id)
            assert len(enviadas) == 1, "la segunda vuelta no lo repite"
            assert enviadas[0]["name"] == "recordatorio_cita_v1"
            assert enviadas[0]["vars"][:2] == ["Ana", "Clínica Sonrisa"]
            cita = await _cita(cita_id)
            assert cita.recordatorio_enviado_at is not None
            assert cita.avisos["recordatorio"] == {"resultado": "enviado", "canal": "whatsapp"}

            async with db_session() as db:
                conv = (
                    await db.execute(select(Conversation).where(Conversation.contact_id == contacto))
                ).scalar_one()
                msg = (
                    await db.execute(select(Message).where(Message.conversation_id == conv.id))
                ).scalar_one()
            assert conv.status == ConversationStatus.bot, "la respuesta la atiende Sofía"
            assert msg.rol.value == "assistant"
            assert msg.contenido.startswith("Hola Ana, te recordamos tu cita en Clínica Sonrisa")
            assert msg.extra["kind"] == "recordatorio"
        finally:
            await _borrar(contacto)

    _correr(prueba)


@con_db
def test_a_quien_agendo_hace_un_rato_no_se_le_recuerda():
    async def prueba(falso):
        ahora = _las_tres_de_la_tarde()
        contacto, cita_id, _ = await _ficha_con_cita(
            inicio=ahora + timedelta(hours=5), creada=ahora - timedelta(hours=1)
        )
        try:
            await svc.barrer(ahora)
            assert _mias(falso, cita_id) == []
            assert (await _cita(cita_id)).recordatorio_enviado_at is None
        finally:
            await _borrar(contacto)

    _correr(prueba)


@con_db
def test_con_el_canal_en_pausa_no_sale_ni_se_gasta_el_aviso():
    from app.services.agent_pause import set_channel_paused

    async def prueba(falso):
        ahora = _las_tres_de_la_tarde()
        contacto, cita_id, _ = await _ficha_con_cita(
            inicio=ahora + timedelta(hours=20), creada=ahora - timedelta(days=3)
        )
        await set_channel_paused("whatsapp", True)
        try:
            await svc.barrer(ahora)
            assert _mias(falso, cita_id) == []
            assert (await _cita(cita_id)).recordatorio_enviado_at is None, "sale cuando vuelva"
        finally:
            await set_channel_paused("whatsapp", False)
            await _borrar(contacto)

    _correr(prueba)


@con_db
def test_a_quien_pidio_la_baja_no_le_llega_y_queda_dicho_en_su_ficha():
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.contact_activity import ContactActivity
    from app.models.outbound_optout import add_outbound_optout

    async def prueba(falso):
        ahora = _las_tres_de_la_tarde()
        tel = f"+52998{uuid.uuid4().int % 10**7:07d}"
        contacto, cita_id, _ = await _ficha_con_cita(
            telefono=tel, inicio=ahora + timedelta(hours=20), creada=ahora - timedelta(days=3)
        )
        async with db_session() as db:
            await add_outbound_optout(db, tel, source="manual")
            await db.commit()
        try:
            await svc.barrer(ahora)
            assert _mias(falso, cita_id) == []
            async with db_session() as db:
                act = (
                    await db.execute(
                        select(ContactActivity).where(
                            ContactActivity.contact_id == contacto,
                            ContactActivity.tipo == "recordatorio_omitido",
                        )
                    )
                ).scalar_one()
            assert "baja" in act.meta["motivo"]
            aviso = (await _cita(cita_id)).avisos["recordatorio"]
            assert aviso["resultado"] == "omitido", "la Agenda tiene que decir que NO se mandó"
        finally:
            from sqlalchemy import text

            async with db_session() as db:
                await db.execute(text("DELETE FROM outbound_optout WHERE phone = :p"), {"p": tel})
                await db.commit()
            await _borrar(contacto)

    _correr(prueba)


@con_db
def test_una_plantilla_mal_configurada_no_gasta_el_recordatorio_de_nadie():
    async def prueba(falso):
        ahora = _las_tres_de_la_tarde()
        contacto, cita_id, _ = await _ficha_con_cita(
            inicio=ahora + timedelta(hours=20), creada=ahora - timedelta(days=3)
        )
        try:
            resultados = await svc.barrer(ahora)
            assert _mias(falso, cita_id) == []
            assert resultados["recordatorio"].pendientes >= 1
            assert (await _cita(cita_id)).recordatorio_enviado_at is None
        finally:
            await _borrar(contacto)

    corto = svc.AvisoConfig(
        activo=True, horas=24, plantilla="recordatorio_cita_v1", variables={"1": "paciente"}
    )
    _correr(prueba, _config(recordatorio=corto))


@con_db
def test_no_llego_sale_una_hora_despues_de_marcarla():
    async def prueba(falso):
        ahora = _las_tres_de_la_tarde()
        contacto, cita_id, _ = await _ficha_con_cita(
            inicio=ahora - timedelta(hours=3), creada=ahora - timedelta(hours=2), estado="no_asistio"
        )
        try:
            await svc.barrer(ahora)
            enviadas = _mias(falso, cita_id, "inasistencia")
            assert len(enviadas) == 1
            assert enviadas[0]["name"] == "cita_perdida_v1"
            assert (await _cita(cita_id)).inasistencia_enviada_at is not None
        finally:
            await _borrar(contacto)

    _correr(prueba)


@con_db
def test_por_instagram_solo_con_la_ventana_de_24_h_abierta():
    from app.db.session import db_session
    from app.models.message import Message, MessageRole

    async def prueba(falso):
        ahora = _las_tres_de_la_tarde()
        mandar = AsyncMock(return_value="ig.mid.1")
        contacto, cita_id, conv_id = await _ficha_con_cita(
            telefono=f"ig:{uuid.uuid4().int % 10**12}",
            inicio=ahora + timedelta(hours=20),
            creada=ahora - timedelta(days=3),
            canal="instagram_dm",
        )
        async with db_session() as db:
            # El paciente escribió hace 30 h: la ventana normal ya cerró.
            db.add(
                Message(
                    conversation_id=conv_id,
                    rol=MessageRole.user,
                    contenido="Hola",
                    created_at=datetime.now(timezone.utc) - timedelta(hours=30),
                )
            )
            await db.commit()
        try:
            with patch("app.services.channel_sender.send_text_to_conversation", mandar):
                await svc.barrer(ahora)
            mandar.assert_not_awaited()
            assert _mias(falso, cita_id) == []
            assert (await _cita(cita_id)).recordatorio_enviado_at is not None, "queda dicho por qué"
        finally:
            await _borrar(contacto)

    _correr(prueba)


@con_db
def test_sofia_confirma_la_cita_y_la_agenda_lo_ve():
    from sqlalchemy import select

    from app.agents.tools.calendar import confirmar_cita
    from app.db.session import db_session
    from app.models.contact_activity import ContactActivity

    async def _run():
        ahora = datetime.now(timezone.utc)
        contacto, cita_id, conv_id = await _ficha_con_cita(
            inicio=ahora + timedelta(days=2), creada=ahora - timedelta(days=1), canal="whatsapp"
        )
        try:
            r1 = await confirmar_cita({}, {"conversation_id": str(conv_id)})
            r2 = await confirmar_cita({}, {"conversation_id": str(conv_id)})
            assert '"ok": true' in r1 and '"ok": true' in r2
            assert (await _cita(cita_id)).confirmada_at is not None
            async with db_session() as db:
                n = len(
                    (
                        await db.execute(
                            select(ContactActivity).where(
                                ContactActivity.contact_id == contacto,
                                ContactActivity.tipo == "appointment_confirmed",
                            )
                        )
                    ).scalars().all()
                )
            assert n == 1, "confirmar dos veces no repite la actividad"
        finally:
            await _borrar(contacto)

    asyncio.run(_run())
