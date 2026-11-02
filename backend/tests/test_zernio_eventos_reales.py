"""Lo que manda Zernio de verdad se entiende bien (sin red ni BD).

Se prueba contra los eventos reales capturados el 2026-09-18
(`tests/fixtures/zernio/`), no contra la doc: la doc no decía, por ejemplo,
que en un eco el `sender` es la propia clínica, ni que otro bot conectado
llega igual que una persona.

Si esto falla:
  - la firma: entraría cualquiera haciéndose pasar por Zernio, o no entraría
    nadie;
  - el reparto: un mensaje de Messenger acabaría en Instagram (o en WhatsApp);
  - el eco: la clínica aparecería como paciente de sí misma, o Sofía seguiría
    contestando encima de una persona.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.providers.zernio.dispatch import parse_event, route, verify_signature

FIXTURES = Path(__file__).parent / "fixtures" / "zernio"
SECRETO = "fixture-zernio-desechable"
HEADERS = json.loads((FIXTURES / "headers.json").read_text())


def _crudo(nombre: str) -> bytes:
    return (FIXTURES / f"{nombre}.json").read_bytes()


def _cabeceras(nombre: str) -> dict[str, str]:
    return {k.lower(): v for k, v in HEADERS[nombre].items()}


def _evento(nombre: str):
    return parse_event(json.loads(_crudo(nombre)))


# ---------- Firma ----------


def test_la_firma_de_los_seis_eventos_reales_es_valida():
    for nombre in HEADERS:
        assert verify_signature(_cabeceras(nombre), _crudo(nombre), SECRETO), nombre


def test_un_byte_cambiado_invalida_la_firma():
    cuerpo = _crudo("ig_message_received").replace(b"Hola", b"Hol4")
    assert not verify_signature(_cabeceras("ig_message_received"), cuerpo, SECRETO)


def test_sin_firma_o_sin_secreto_no_pasa():
    cabeceras = _cabeceras("ig_message_received")
    sin_firma = {
        k: v for k, v in cabeceras.items() if k not in ("x-zernio-signature", "x-late-signature")
    }
    assert not verify_signature(sin_firma, _crudo("ig_message_received"), SECRETO)
    assert not verify_signature(cabeceras, _crudo("ig_message_received"), "")
    assert not verify_signature(cabeceras, _crudo("ig_message_received"), "otro-secreto")


def test_vale_la_cabecera_heredada_de_late():
    cabeceras = _cabeceras("fb_message_received")
    solo_late = {k: v for k, v in cabeceras.items() if k != "x-zernio-signature"}
    assert verify_signature(solo_late, _crudo("fb_message_received"), SECRETO)


# ---------- Entrantes ----------


def test_un_dm_de_instagram_entra_como_ig_con_su_usuario():
    ev = _evento("ig_message_received")
    assert ev.tipo == "entrante" and route(ev) == "instagram_dm"
    m = ev.incoming
    assert m.from_phone == "ig:1000000000000005"
    assert m.provider_message_id == "aWdfbWlkX3BydWViYV84"
    assert m.text == "Hola"
    # Zernio manda el @usuario también como `name`: no es un nombre real.
    assert m.customer_handle == "@ana.paciente"
    assert m.customer_name == "@ana.paciente"
    z = m.raw["zernio"]
    assert z["conversation_id"] == "000000000000000000000003"
    assert z["account_id"] == "000000000000000000000007"


def test_un_mensaje_a_la_pagina_entra_por_messenger_con_nombre_real():
    ev = _evento("fb_message_received")
    assert route(ev) == "messenger"
    assert ev.incoming.from_phone == "fb:1000000000000016"
    assert ev.incoming.customer_name == "Ana Paciente"
    assert ev.incoming.customer_handle is None


def test_el_clic_en_un_boton_entra_como_texto_y_guarda_el_payload():
    ev = _evento("ig_postback_received")
    assert ev.tipo == "entrante"
    assert ev.incoming.text == "Agendar cita 🦷"
    assert ev.incoming.raw["zernio"]["postback"] == "ACT::boton_prueba"


def test_los_adjuntos_guardan_su_posicion_para_pedirlos_despues():
    payload = json.loads(_crudo("ig_message_received"))
    payload["message"]["text"] = None
    payload["message"]["attachments"] = [
        {"type": "share", "url": "https://lookaside.fbsbx.com/x", "originalType": "story_mention"},
        {"type": "audio", "url": "https://cdn.fbsbx.com/audio.mp4"},
    ]
    m = parse_event(payload).incoming
    assert m.message_type == "audio"
    assert m.audio_url == "https://cdn.fbsbx.com/audio.mp4"
    assert m.media_kind == "image" and m.media_url == "https://lookaside.fbsbx.com/x"
    z = m.raw["zernio"]
    assert (z["media_index"], z["audio_index"]) == (0, 1)
    assert z["attachments"][0]["originalType"] == "story_mention"


# ---------- Ecos ----------


def test_el_eco_de_la_app_es_del_paciente_y_toma_el_control():
    for nombre, cliente, sent_by in (
        ("ig_message_sent", "ig:1000000000000005", "instagram_app"),
        ("fb_message_sent", "fb:1000000000000016", "messenger_app"),
    ):
        ev = _evento(nombre)
        assert ev.tipo == "eco", nombre
        # El `sender` del eco es la clínica; el paciente es el participante.
        assert ev.eco["customer_id"] == cliente
        assert ev.eco["sent_by"] == sent_by
        assert ev.eco["tomar_control"] is True


def test_lo_que_mandamos_nosotros_por_la_api_no_se_vuelve_a_guardar():
    payload = json.loads(_crudo("ig_message_sent"))
    payload["message"]["sentVia"] = "api"
    assert parse_event(payload).tipo == "propio"


def test_el_otro_bot_llega_sin_texto():
    """ManyChat manda plantillas (sin texto): el webhook no las refleja, y por
    eso no le quitan el sitio a Sofía. Con texto serían indistinguibles."""
    ev = _evento("ig_message_sent_otro_bot")
    assert ev.tipo == "eco" and not ev.eco["text"]


# ---------- Resto ----------


def test_reacciones_y_eventos_desconocidos_se_ignoran():
    payload = json.loads(_crudo("ig_message_received"))
    payload["event"] = "reaction.received"
    assert parse_event(payload).tipo == "ignorado"


def test_el_aviso_de_prueba_de_zernio_se_ignora_sin_romper():
    ev = parse_event({"id": "evt_prueba", "event": "webhook.test",
                      "message": "This is a test webhook from Zernio",
                      "timestamp": "2026-10-12T00:00:00Z"})
    assert ev.tipo == "ignorado"
    assert ev.evento == "webhook.test"


def test_desconexion_de_cuenta():
    payload = json.loads(_crudo("fb_message_received"))
    payload["event"] = "account.disconnected"
    ev = parse_event(payload)
    assert ev.tipo == "cuenta" and ev.estado_cuenta == "disconnected"
    assert ev.account_id == "000000000000000000000012"
