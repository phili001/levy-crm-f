"""WA-4: con la ventana de 24 h cerrada, el operador contesta con una plantilla
desde el propio chat, y queda en el hilo.

Por HTTP contra la app real con un JWT de verdad: los permisos viven en las
dependencias. El proveedor de WhatsApp es un doble que apunta lo que le piden.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.api.admin import _template_to_out
from app.api.plantillas_chat import _enviable_desde_el_chat, texto_rellenado

PLANTILLAS = [
    {
        "name": "recordatorio_cita",
        "language": "es",
        "status": "APPROVED",
        "components": [
            {"type": "BODY", "text": "Hola {{1}}, te esperamos el {{2}}."},
            {"type": "FOOTER", "text": "Clínica Sonrisa"},
        ],
    },
    {
        "name": "promo_con_imagen",
        "language": "es",
        "status": "APPROVED",
        "components": [
            {"type": "HEADER", "format": "IMAGE"},
            {"type": "BODY", "text": "Descuento este mes"},
        ],
    },
    {
        "name": "pendiente",
        "language": "es",
        "status": "PENDING",
        "components": [{"type": "BODY", "text": "Aún no"}],
    },
]


# --- Sin base de datos --------------------------------------------------------


def test_en_el_chat_solo_se_ofrecen_las_que_salen_con_texto():
    ofrecidas = [t.name for t in map(_template_to_out, PLANTILLAS) if _enviable_desde_el_chat(t)]
    assert ofrecidas == ["recordatorio_cita"]


def test_el_hilo_guarda_lo_que_lee_el_cliente_con_los_huecos_rellenos():
    t = _template_to_out(PLANTILLAS[0])
    assert texto_rellenado(t, ["Ana", "jueves"]) == (
        "Hola Ana, te esperamos el jueves.\n\nClínica Sonrisa"
    )


# --- Por HTTP -------------------------------------------------------------------


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
        self.textos: list[str] = []

    async def list_templates(self):
        return PLANTILLAS

    async def send_template(self, to, name, language, variables=None, **kw):
        self.enviadas.append({"to": to, "name": name, "vars": variables, **kw})
        return "wamid.PLANTILLA1"

    async def send_text(self, to, text):
        self.textos.append(text)
        return "wamid.TEXTO1"


async def _escenario(canal: str = "whatsapp", horas: int = 30):
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.models.contact import Contact, ContactOrigen
    from app.models.conversation import Conversation, ConversationCanal, ConversationStatus
    from app.models.message import Message, MessageRole
    from app.models.user import User

    suf = uuid.uuid4().hex[:8]
    async with db_session() as db:
        operador = User(
            email=f"op-wa4-{suf}@test.local",
            password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
            role="cliente",
        )
        contacto = Contact(
            telefono=f"+52998{uuid.uuid4().int % 10**7:07d}", origen=ContactOrigen.whatsapp
        )
        db.add_all([operador, contacto])
        await db.flush()
        conv = Conversation(
            contact_id=contacto.id,
            canal=ConversationCanal(canal),
            session_id=f"sess-{suf}",
            status=ConversationStatus.bot,
        )
        db.add(conv)
        await db.flush()
        db.add(
            Message(
                conversation_id=conv.id,
                rol=MessageRole.user,
                contenido="¿Tienen cita?",
                created_at=datetime.now(timezone.utc) - timedelta(hours=horas),
            )
        )
        await db.commit()
        token = create_access_token(subject=str(operador.id), extra={"role": "cliente"})
        return token, conv.id, contacto.id, operador.id


async def _limpiar(conv_id, contact_id, user_id):
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        await db.execute(text("DELETE FROM contacts WHERE id = :i"), {"i": str(contact_id)})
        await db.execute(text("DELETE FROM audit_log WHERE user_id = :i"), {"i": str(user_id)})
        await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(user_id)})
        await db.commit()


def _correr(prueba, **escenario):
    import httpx

    from app.main import app

    async def _run():
        token, conv_id, contact_id, user_id = await _escenario(**escenario)
        wa = _WhatsAppFalso()
        from unittest.mock import patch

        try:
            with patch("app.api.plantillas_chat.get_whatsapp_provider", return_value=wa), patch(
                "app.services.channel_sender.get_whatsapp_provider", return_value=wa
            ):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                    await prueba(c, {"Authorization": f"Bearer {token}"}, conv_id, wa)
        finally:
            await _limpiar(conv_id, contact_id, user_id)

    asyncio.run(_run())


B = "/api/v1"


@con_db
def test_con_la_ventana_cerrada_el_texto_libre_no_sale_y_dice_que_hacer():
    async def prueba(c, h, conv_id, wa):
        r = await c.post(f"{B}/conversations/{conv_id}/messages", json={"contenido": "Hola"}, headers=h)
        assert r.status_code == 400
        assert "Enviar plantilla" in r.json()["detail"]
        assert wa.textos == []

    _correr(prueba)


@con_db
def test_un_operador_manda_la_plantilla_desde_el_chat_y_queda_en_el_hilo():
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.conversation import Conversation

    async def prueba(c, h, conv_id, wa):
        lista = await c.get(f"{B}/whatsapp/plantillas", headers=h)
        assert lista.status_code == 200, lista.text
        assert [t["name"] for t in lista.json()] == ["recordatorio_cita"]

        r = await c.post(
            f"{B}/conversations/{conv_id}/template",
            json={"template_name": "recordatorio_cita", "language": "es", "variables": ["Ana", "jueves"]},
            headers=h,
        )
        assert r.status_code == 200, r.text
        msg = r.json()
        assert msg["rol"] == "operator"
        assert msg["contenido"].startswith("Hola Ana, te esperamos el jueves.")
        assert msg["metadata"]["template"]["name"] == "recordatorio_cita"
        assert msg["metadata"]["provider_message_id"] == "wamid.PLANTILLA1"
        assert wa.enviadas[0]["vars"] == ["Ana", "jueves"]

        hilo = (await c.get(f"{B}/conversations/{conv_id}/messages", headers=h)).json()
        items = hilo["items"] if isinstance(hilo, dict) else hilo
        assert any(m["id"] == msg["id"] for m in items)

        # Quien manda la plantilla está atendiendo: el agente se aparta.
        async with db_session() as db:
            conv = (await db.execute(select(Conversation).where(Conversation.id == conv_id))).scalar_one()
        assert conv.status.value == "humano"

    _correr(prueba)


@con_db
def test_no_se_manda_una_plantilla_a_medias_ni_una_que_no_sale_desde_el_chat():
    async def prueba(c, h, conv_id, wa):
        url = f"{B}/conversations/{conv_id}/template"
        faltan = await c.post(
            url, json={"template_name": "recordatorio_cita", "language": "es", "variables": ["Ana", " "]}, headers=h
        )
        assert faltan.status_code == 400
        assert "variables" in faltan.json()["detail"]

        con_imagen = await c.post(url, json={"template_name": "promo_con_imagen", "language": "es"}, headers=h)
        assert con_imagen.status_code == 400
        assert "Campañas" in con_imagen.json()["detail"]

        pendiente = await c.post(url, json={"template_name": "pendiente", "language": "es"}, headers=h)
        assert pendiente.status_code == 400
        assert "pendiente" in pendiente.json()["detail"]

        assert wa.enviadas == []

    _correr(prueba)


@con_db
def test_fuera_de_whatsapp_no_hay_plantillas():
    async def prueba(c, h, conv_id, wa):
        r = await c.post(
            f"{B}/conversations/{conv_id}/template",
            json={"template_name": "recordatorio_cita", "language": "es", "variables": ["a", "b"]},
            headers=h,
        )
        assert r.status_code == 400
        assert wa.enviadas == []

    _correr(prueba, canal="web")
