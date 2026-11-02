"""Cada canal con su ritmo, su estilo y su formato (Fase 10.2, US-25).

Antes el ritmo lo ponía el agente, y el agente de texto es uno solo para todos
los canales: el chat web esperaba 8 segundos antes de contestar, igual que
WhatsApp, y el correo también. Y el modelo escribe markdown: en WhatsApp se
veían los asteriscos dobles y en Instagram, Messenger, el chat web y el correo,
todos los asteriscos y las almohadillas.

Lo que se comprueba aquí:
  - el chat web espera 3 s, WhatsApp/Instagram/Messenger 8 s y el correo nada,
    diga lo que diga el agente; la voz no cambia;
  - el piso de 6 s solo vale donde la gente escribe en ráfagas;
  - el buffer de un canal se puede cambiar en su fila de `channels`;
  - Sofía recibe la línea de estilo de su canal;
  - el formato: negrita de WhatsApp, texto plano en el resto, y sin romper
    listas, cuentas ni la voz.
"""
from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

from tests.test_voice_gating import pytestmark_db


def _runtime(buffer_seconds: int = 8):
    from app.services.runtime_config import AgentRuntime

    return AgentRuntime(
        id=None,
        name="Sofía",
        prompt_system="Eres Sofía.",
        model_name="gpt-4.1-mini",
        temperature=0.3,
        max_tokens=400,
        buffer_seconds=buffer_seconds,
        response_split_max_parts=3,
        context_window=12,
        handoff_bridge_message=None,
        monthly_budget_usd=None,
        tools_enabled=None,
        source="agents",
    )


# --------------------------------------------------------------- ritmo


@pytest.mark.parametrize(
    "canal, segundos",
    [("web", 3), ("webchat", 3), ("email", 0), ("whatsapp", 8), ("instagram_dm", 8), ("messenger", 8)],
)
async def test_cada_canal_espera_lo_suyo(monkeypatch, canal, segundos):
    from app.core import channel_capabilities as cc

    async def _de_fabrica(c):
        return cc.capacidades(c)

    monkeypatch.setattr(cc, "capacidades_de", _de_fabrica)
    assert await cc.buffer_del_canal(canal) == segundos


async def test_el_piso_de_6_segundos_solo_donde_hay_rafagas(monkeypatch):
    from app.core import channel_capabilities as cc

    async def _con_2_segundos(c):
        return replace(cc.capacidades(c), buffer_seconds=2)

    monkeypatch.setattr(cc, "capacidades_de", _con_2_segundos)
    for canal in ("whatsapp", "instagram_dm", "messenger"):
        assert await cc.buffer_del_canal(canal) == 6, canal
    assert await cc.buffer_del_canal("web") == 2
    assert await cc.buffer_del_canal("email") == 2


@pytest.mark.parametrize("canal, esperado", [("web", 3), ("whatsapp", 8), ("email", 0)])
async def test_el_agente_toma_el_ritmo_del_canal_y_no_el_suyo(monkeypatch, canal, esperado):
    from app.core import channel_capabilities as cc
    from app.services import runtime_config

    async def _agente_con_20(_tipo):
        return _runtime(buffer_seconds=20)

    async def _de_fabrica(c):
        return cc.capacidades(c)

    monkeypatch.setattr(runtime_config, "_resolve_runtime", _agente_con_20)
    monkeypatch.setattr(cc, "capacidades_de", _de_fabrica)
    runtime = await runtime_config.get_runtime_for_channel(canal)
    assert runtime.buffer_seconds == esperado


async def test_la_voz_no_cambia_de_ritmo(monkeypatch):
    from app.services import runtime_config

    async def _agente_de_voz(_tipo):
        return _runtime(buffer_seconds=1)

    monkeypatch.setattr(runtime_config, "_resolve_runtime", _agente_de_voz)
    runtime = await runtime_config.get_runtime_for_channel("retell_voice")
    assert runtime.buffer_seconds == 1


@pytestmark_db
async def test_el_buffer_de_un_canal_se_cambia_en_su_fila():
    from sqlalchemy import delete

    from app.core.channel_capabilities import buffer_del_canal
    from app.db.session import db_session
    from app.models.channel import Channel, ChannelType

    async with db_session() as db:
        fila = Channel(
            type=ChannelType.messenger,
            name="Messenger de prueba",
            enabled=True,
            config={"capacidades": {"buffer_seconds": 12, "style_hint": "Muy breve."}},
        )
        db.add(fila)
        await db.commit()
        fila_id = fila.id
    try:
        assert await buffer_del_canal("messenger") == 12
    finally:
        async with db_session() as db:
            await db.execute(delete(Channel).where(Channel.id == fila_id))
            await db.commit()


# --------------------------------------------------------------- estilo


async def test_sofia_recibe_la_linea_de_estilo_de_su_canal(monkeypatch):
    from app.core import channel_capabilities as cc
    from app.services import runtime_config

    async def _de_fabrica(c):
        return cc.capacidades(c)

    monkeypatch.setattr(cc, "capacidades_de", _de_fabrica)
    web = await runtime_config.assemble_system_prompt("Eres Sofía.", canal="webchat")
    assert "[ESTE CANAL] Estás en el chat de la página web" in web
    assert web.index("Eres Sofía.") < web.index("[ESTE CANAL]")
    correo = await runtime_config.assemble_system_prompt("Eres Sofía.", canal="email")
    assert "la firma se añade sola" in correo
    sin_canal = await runtime_config.assemble_system_prompt("Eres Sofía.")
    assert "[ESTE CANAL]" not in sin_canal, "«Ver prompt efectivo» no es de un canal"
    voz = await runtime_config.assemble_system_prompt("Eres Sofía.", canal="retell_voice")
    assert "[ESTE CANAL]" not in voz, "la voz tiene su propia capa"


# --------------------------------------------------------------- formato


@pytest.mark.parametrize(
    "canal, entra, sale",
    [
        ("whatsapp", "Te espero el **lunes**.", "Te espero el *lunes*."),
        ("whatsapp", "### Horarios\nDe 9 a 6.", "Horarios\nDe 9 a 6."),
        ("whatsapp", "Agenda [aquí](https://clinica.mx/a).", "Agenda aquí (https://clinica.mx/a)."),
        ("instagram_dm", "Te espero el **lunes** a las *10*.", "Te espero el lunes a las 10."),
        ("messenger", "__Importante__: trae tu INE.", "Importante: trae tu INE."),
        ("web", "## Precios\n**Limpieza**: $800", "Precios\nLimpieza: $800"),
        ("email", "Hola:\n\n**Tu cita** es el martes.", "Hola:\n\nTu cita es el martes."),
    ],
)
def test_cada_canal_recibe_el_formato_que_entiende(canal, entra, sale):
    from app.core.channel_capabilities import formatear_para_canal

    assert formatear_para_canal(entra, canal) == sale


def test_el_formato_no_rompe_listas_cuentas_ni_la_voz():
    from app.core.channel_capabilities import formatear_para_canal

    lista = "Traes:\n* INE\n* comprobante"
    assert formatear_para_canal(lista, "instagram_dm") == lista
    assert formatear_para_canal("Son 2*400 pesos.", "web") == "Son 2*400 pesos."
    assert formatear_para_canal("[x](https://a.mx)", "web") == "x (https://a.mx)"
    assert formatear_para_canal("**hola**", "retell_voice") == "**hola**"


def test_el_formato_se_aplica_antes_de_cualquier_salida():
    """Envío, borrador de correo y sugerencia salen del mismo `response_text`."""
    from app.services import conversation

    fuente = inspect.getsource(conversation._process_buffered_locked)
    formato = fuente.index("formatear_para_canal(response_text")
    assert formato < fuente.index("create_email_draft_for_conversation(conv, response_text)")
    assert formato < fuente.index("_split_response(response_text")


# --------------------------------------------------------------- ventanas y topes


def test_ventanas_y_topes_salen_de_la_tabla():
    from app.core.channel_capabilities import capacidades
    from app.services import channel_sender

    assert channel_sender.WA_SESSION_WINDOW_HOURS == 24
    assert channel_sender.IG_STANDARD_WINDOW_HOURS == 24
    assert channel_sender.IG_HUMAN_AGENT_WINDOW_HOURS == 24 * 7
    assert capacidades("instagram_dm").max_text_bytes == 1000
    assert capacidades("messenger").max_text_bytes == 2000
