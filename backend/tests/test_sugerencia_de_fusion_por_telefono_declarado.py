"""«¿Es el mismo paciente?»: el CRM propone unir fichas, nunca las une solo (Fase 10.1, US-24).

El mismo paciente escribe por WhatsApp, pregunta por Instagram y llama por
teléfono. Cuando dice su teléfono por Instagram (o escribe desde un correo que
ya figura en otra ficha), el CRM deja una propuesta para el equipo. No fusiona
solo: un teléfono escrito en un chat no está verificado, y una fusión
automática le entregaría a un desconocido la ficha de otra persona.

Lo que se comprueba aquí:
  - Instagram después de WhatsApp: el teléfono dicho propone unir;
  - al revés: la llamada (o un WhatsApp nuevo) desde el número que alguien dijo
    por Instagram también propone;
  - el correo desde una dirección que ya tiene otra ficha también propone;
  - no se repite, ni en el otro sentido, ni vuelve una propuesta descartada;
  - la conversación sale en «Para hacer», pero NO dispara el aviso push;
  - el panel la lee desde las dos fichas y solo un administrador la descarta.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from tests._fichas import (
    borrar,
    conversacion_y_ficha,
    entra,
    ficha,
    leer,
    llave_ig,
    propuestas_de,
    telefono_mx,
)
from tests.test_voice_gating import pytestmark_db


async def _dice_su_telefono(llave: str, dicho: str) -> None:
    from app.agents.tools.contact_upsert import crear_actualizar_contacto

    await crear_actualizar_contacto({"telefono_declarado": dicho}, {"telefono": llave})


@pytestmark_db
@pytest.mark.asyncio
async def test_el_telefono_dicho_por_instagram_propone_unir_con_su_whatsapp():
    telefono = telefono_mx()
    wa = await ficha(telefono, "whatsapp", nombre="Juan Pérez")
    llave = llave_ig()
    ig = await ficha(llave, "instagram")
    try:
        await _dice_su_telefono(llave, telefono[3:])  # lo dice sin lada: 998…

        propuestas = await propuestas_de(ig)
        assert len(propuestas) == 1
        p = propuestas[0]
        assert (p.contact_id, p.candidate_id) == (ig, wa), "se queda la ficha del teléfono"
        assert (p.motivo, p.dato, p.estado) == ("telefono", telefono, "pendiente")
        # Y no se ha fusionado nada: lo decide una persona.
        assert await leer(ig) is not None and await leer(wa) is not None
    finally:
        await borrar(ig, wa)


@pytestmark_db
@pytest.mark.asyncio
async def test_al_reves_la_llamada_desde_el_numero_dicho_tambien_propone():
    """C-1, caso inverso: primero Instagram, después la llamada."""
    from app.api.voice import _resolve_contact_and_conversation

    telefono = telefono_mx()
    ig = await ficha(llave_ig(), "instagram", telefono_declarado=telefono)
    voz = None
    try:
        _conv, voz = await _resolve_contact_and_conversation(
            f"call_{uuid.uuid4().hex[:12]}", telefono, "hola, quería una cita", datetime.now(timezone.utc)
        )
        propuestas = await propuestas_de(ig)
        assert len(propuestas) == 1
        assert (propuestas[0].contact_id, propuestas[0].candidate_id) == (ig, voz)
    finally:
        await borrar(ig, voz)


@pytestmark_db
@pytest.mark.asyncio
async def test_un_whatsapp_nuevo_desde_el_numero_dicho_tambien_propone():
    telefono = telefono_mx()
    ig = await ficha(llave_ig(), "instagram", telefono_declarado=telefono)
    wa = None
    try:
        _conv, wa = await conversacion_y_ficha(await entra(telefono, "hola"))
        propuestas = await propuestas_de(ig)
        assert [(p.contact_id, p.candidate_id) for p in propuestas] == [(ig, wa)]
    finally:
        await borrar(ig, wa)


@pytestmark_db
@pytest.mark.asyncio
async def test_quien_escribe_desde_el_correo_de_otra_ficha_tambien_se_propone():
    direccion = f"ana.{uuid.uuid4().hex[:8]}@example.com"
    wa = await ficha(telefono_mx(), "whatsapp", email=direccion)
    correo = None
    try:
        _conv, correo = await conversacion_y_ficha(
            await entra(
                f"email:{direccion}",
                "Buenos días, quería información",
                raw={"threadId": f"hilo-{uuid.uuid4()}", "subject": "Consulta"},
            )
        )
        propuestas = await propuestas_de(wa)
        assert [(p.contact_id, p.candidate_id, p.motivo) for p in propuestas] == [
            (correo, wa, "email")
        ], "se queda la ficha que no es la del correo"
    finally:
        await borrar(wa, correo)


@pytestmark_db
@pytest.mark.asyncio
async def test_no_se_repite_ni_vuelve_una_descartada():
    from sqlalchemy import update

    from app.db.session import db_session
    from app.models.merge_suggestion import MergeSuggestion
    from app.services import identity_linking

    telefono = telefono_mx()
    wa = await ficha(telefono, "whatsapp")
    llave = llave_ig()
    ig = await ficha(llave, "instagram")
    try:
        await _dice_su_telefono(llave, telefono)
        await _dice_su_telefono(llave, telefono)
        # El mismo par desde el otro lado (nace la ficha del teléfono).
        assert await identity_linking.proponer_para(wa, telefono=True) == 0
        assert len(await propuestas_de(ig)) == 1

        async with db_session() as db:
            await db.execute(update(MergeSuggestion).values(estado="descartada").where(
                MergeSuggestion.contact_id == ig
            ))
            await db.commit()
        assert await identity_linking.proponer_para(ig, telefono_declarado=True) == 0
        assert await identity_linking.proponer_para(wa, telefono=True) == 0
        assert [p.estado for p in await propuestas_de(ig)] == ["descartada"]
    finally:
        await borrar(ig, wa)


@pytestmark_db
@pytest.mark.asyncio
async def test_en_para_hacer_sale_la_conversacion_pero_no_avisa_por_push():
    """Es trabajo de la ficha, no un cliente esperando respuesta."""
    from app.db.session import db_session
    from app.models.message import Message, MessageRole
    from app.services.inbox_pending import conversation_needs_action
    from tests.test_agenda_desde_el_panel import _borrar_usuarios, _usuarios

    telefono = telefono_mx()
    wa = await ficha(telefono, "whatsapp")
    llave = llave_ig()
    conv, ig = await conversacion_y_ficha(await entra(llave, "mi número es el de siempre"))
    admin, _op, _rev, usuarios = await _usuarios()
    try:
        # Sofía ya contestó: por mensajes no hay nada pendiente.
        async with db_session() as db:
            db.add(Message(conversation_id=conv, rol=MessageRole.assistant, contenido="¡Gracias!"))
            await db.commit()
        await _dice_su_telefono(llave, telefono)

        async with db_session() as db:
            assert await conversation_needs_action(db, conv) is False

        from app.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get(
                "/api/v1/conversations",
                params={"action_only": "true", "canal": "instagram_dm", "page_size": 100},
                headers=admin,
            )
            assert r.status_code == 200
            fila = next((i for i in r.json()["items"] if i["id"] == str(conv)), None)
            assert fila is not None, "la conversación no salió en «Para hacer»"
            assert fila["fusion_pendiente"] is True
            assert fila["needs_action"] is False

            cuentas = (await c.get("/api/v1/conversations/counts", headers=admin)).json()
            assert cuentas["fusiones"] >= 1
    finally:
        await borrar(ig, wa)
        await _borrar_usuarios(usuarios)


@pytestmark_db
@pytest.mark.asyncio
async def test_el_panel_la_lee_desde_las_dos_fichas_y_solo_admin_la_descarta():
    from tests.test_agenda_desde_el_panel import _borrar_usuarios, _usuarios

    telefono = telefono_mx()
    wa = await ficha(telefono, "whatsapp", nombre="Juan Pérez")
    llave = llave_ig()
    ig = await ficha(llave, "instagram", social_handle="juanp")
    admin, operador, revisor, usuarios = await _usuarios()
    try:
        await _dice_su_telefono(llave, telefono)
        from app.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            desde_ig = await c.get(f"/api/v1/contacts/{ig}/merge-suggestions", headers=operador)
            assert desde_ig.status_code == 200
            [p] = desde_ig.json()
            assert p["absorbe"]["id"] == str(ig) and p["absorbe"]["canal"] == "Instagram"
            assert p["absorbe"]["social_handle"] == "juanp"
            assert p["queda"]["id"] == str(wa) and p["queda"]["canal"] == "WhatsApp"
            assert p["queda"]["nombre"] == "Juan Pérez"
            assert p["telefono_final"] == telefono
            assert (p["citas_activas"], p["bloqueo"]) == (0, None)

            desde_wa = await c.get(f"/api/v1/contacts/{wa}/merge-suggestions", headers=operador)
            assert [x["id"] for x in desde_wa.json()] == [p["id"]]

            r = await c.get(f"/api/v1/contacts/{ig}/merge-suggestions", headers=revisor)
            assert r.status_code == 403

            descartar = f"/api/v1/contacts/merge-suggestions/{p['id']}/dismiss"
            assert (await c.post(descartar, headers=operador)).status_code == 403
            assert (await c.post(descartar, headers=admin)).status_code == 200
            assert (await c.post(descartar, headers=admin)).status_code == 404

            despues = await c.get(f"/api/v1/contacts/{ig}/merge-suggestions", headers=operador)
            assert despues.json() == []
        # Descartada, no borrada: ese par no se vuelve a proponer.
        assert [x.estado for x in await propuestas_de(ig)] == ["descartada"]
    finally:
        await borrar(ig, wa)
        await _borrar_usuarios(usuarios)


def test_la_herramienta_explica_el_parametro_nuevo():
    import app.agents.tools.contact_upsert  # noqa: F401 — registra la herramienta
    from app.agents.tools.registry import ALL_TOOLS

    tool = ALL_TOOLS["crear_actualizar_contacto"]
    propiedades = tool.schema.parameters["properties"]
    assert "telefono_declarado" in propiedades
    assert "telefono" not in propiedades, "la llave no puede ser un parámetro"
    assert json.dumps(propiedades["telefono_declarado"], ensure_ascii=False).count("equipo") == 1
