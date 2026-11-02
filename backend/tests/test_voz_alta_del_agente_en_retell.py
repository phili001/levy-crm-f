"""El CRM crea su agente de Retell, lo publica y le da número (Fase 8, US-19).

Contra el simulador de Retell (`tests/mocks/retell_mock.py`), que sigue el
modelo de versiones de la API de septiembre de 2026: una versión publicada es
de solo lectura y el `publish-agent` antiguo ya no existe. El cliente del CRM
habla por HTTP igual que con el real; solo se le cambia el transporte para no
abrir un puerto.

Sin base de datos: corren en todas partes.
"""
from __future__ import annotations

import httpx
import pytest

from app.providers.voice import retell_admin as ra
from tests.mocks import retell_mock

WS = "wss://crm.clinica.mx/api/v1/voice/retell/llm-ws"
HOOK = "https://crm.clinica.mx/api/v1/voice/retell/webhook"


@pytest.fixture
def retell():
    app = retell_mock.crear_app()
    transporte = httpx.ASGITransport(app=app)

    def admin(clave: str = "retell-key") -> ra.RetellAdmin:
        return ra.RetellAdmin(clave, base_url="http://retell", transport=transporte)

    control = httpx.AsyncClient(transport=transporte, base_url="http://retell")
    return admin, control


async def _agentes(control) -> dict:
    return (await control.get("/_mock/agentes")).json()


async def _alta(admin, agent_id=None, voice_id=None) -> ra.AgenteListo:
    return await ra.asegurar_agente(
        admin, agent_id=agent_id, ws_url=WS, webhook_url=HOOK, voice_id=voice_id,
        nombre="Sofía · crm.clinica.mx",
    )


async def test_el_crm_crea_su_agente_publicado_y_apuntando_a_esta_instalacion(retell):
    hacer, control = retell
    admin = hacer()
    listo = await _alta(admin)
    assert listo.creado is True

    agente = (await _agentes(control))[listo.agent_id]
    assert agente["publicado"] is True, "sin publicar, el número no contesta"
    assert agente["response_engine"] == {"type": "custom-llm", "llm_websocket_url": WS}
    assert agente["language"] == "es-419", "es-MX no existe en Retell"
    assert agente["webhook_url"] == HOOK
    assert agente["opt_in_signed_url"] is True
    assert agente["end_call_after_silence_ms"] == ra.SILENCIO_PARA_COLGAR_MS
    assert agente["voice_id"] == ra.VOZ_POR_DEFECTO

    checks = await ra.verificar_agente(admin, listo.agent_id, ws_url=WS, webhook_url=HOOK)
    assert [c.nombre for c in checks if not c.ok] == []


async def test_volver_a_conectar_reutiliza_el_agente_sin_amontonar_versiones(retell):
    hacer, control = retell
    admin = hacer()
    primero = await _alta(admin)
    segundo = await _alta(admin, agent_id=primero.agent_id)
    assert segundo.creado is False
    assert segundo.agent_id == primero.agent_id
    assert len(await _agentes(control)) == 1, "instalar dos veces no deja dos agentes"
    assert (await _agentes(control))[primero.agent_id]["versiones"] == 1

    # Cambiar la voz sí publica una versión nueva, sacada de la publicada.
    await _alta(admin, agent_id=primero.agent_id, voice_id="retell-Gaby")
    agente = (await _agentes(control))[primero.agent_id]
    assert agente["versiones"] == 2
    assert agente["publicado"] is True
    assert agente["voice_id"] == "retell-Gaby"


async def test_si_el_agente_ya_no_existe_en_retell_se_crea_otro(retell):
    hacer, control = retell
    listo = await _alta(hacer(), agent_id="agent_que_borraron")
    assert listo.creado is True
    assert listo.agent_id != "agent_que_borraron"
    assert list(await _agentes(control)) == [listo.agent_id]


async def test_un_cambio_no_toca_la_version_publicada_sino_un_borrador(retell):
    hacer, control = retell
    await control.post("/_mock/agentes", json={"agent_id": "agent_v3", "voice_id": "vieja"})
    version = await hacer().publicar_cambio("agent_v3", {"voice_id": "nueva"})
    assert version == 1
    v0 = (await control.get("/get-agent/agent_v3", params={"version": 0},
                            headers={"Authorization": "Bearer k"})).json()
    assert v0["voice_id"] == "vieja", "la publicada es de solo lectura"
    assert (await _agentes(control))["agent_v3"]["voice_id"] == "nueva"


async def test_un_agente_que_nunca_se_publico_se_publica_al_cambiarlo(retell):
    hacer, control = retell
    await control.post("/_mock/agentes", json={"agent_id": "agent_olvidado", "publicado": False})
    version = await hacer().publicar_cambio("agent_olvidado", {"opt_in_signed_url": True})
    assert version == 0, "se publica su borrador, no se amontona otro"
    agente = (await _agentes(control))["agent_olvidado"]
    assert agente["publicado"] is True
    assert agente["opt_in_signed_url"] is True


async def test_el_numero_comprado_sigue_a_la_ultima_version_publicada(retell):
    hacer, control = retell
    admin = hacer()
    listo = await _alta(admin)
    numero = await admin.comprar_numero(listo.agent_id, nickname="Sofía", area_code=305)
    telefono = numero["phone_number"]
    assert telefono.startswith("+1305")
    atado = (await control.get("/_mock/numeros")).json()[telefono]["inbound_agents"]
    assert atado == [
        {"agent_id": listo.agent_id, "agent_version": "latest_published", "weight": 1}
    ], "atado a una versión fija, se quedaría en la vieja al publicar un cambio"
    checks = await ra.verificar_numero(admin, telefono, listo.agent_id)
    assert all(c.ok for c in checks), checks


async def test_importar_un_numero_que_ya_estaba_lo_actualiza_en_vez_de_fallar(retell):
    hacer, control = retell
    admin = hacer()
    viejo = await _alta(admin)
    await admin.importar_numero(
        "+529981234567", termination_uri="curso.pstn.twilio.com",
        agent_id=viejo.agent_id, nickname="curso",
    )
    nuevo = await _alta(admin)
    await admin.importar_numero(
        "+529981234567", termination_uri="crm.pstn.twilio.com",
        agent_id=nuevo.agent_id, nickname="crm",
    )
    numero = (await control.get("/_mock/numeros")).json()["+529981234567"]
    assert numero["inbound_agents"][0]["agent_id"] == nuevo.agent_id
    assert numero["sip_outbound_trunk_config"]["termination_uri"] == "crm.pstn.twilio.com"


async def test_un_numero_de_otro_agente_se_detecta_al_comprobar(retell):
    hacer, _ = retell
    admin = hacer()
    uno = await _alta(admin)
    otro = await _alta(admin)
    numero = await admin.comprar_numero(uno.agent_id, nickname="uno")
    checks = {c.nombre: c for c in await ra.verificar_numero(admin, numero["phone_number"], otro.agent_id)}
    assert checks["numero_atado_al_agente"].ok is False
    assert uno.agent_id in checks["numero_atado_al_agente"].detalle


async def test_una_api_key_mala_se_explica_en_vez_de_reventar(retell):
    hacer, _ = retell
    with pytest.raises(ra.RetellAdminError) as e:
        await _alta(hacer("key-invalid"))
    assert e.value.status == 401
    assert "API key" in str(e.value)


async def test_sin_version_publicada_la_comprobacion_lo_dice(retell):
    hacer, control = retell
    await control.post("/_mock/agentes", json={"agent_id": "agent_borrador", "publicado": False})
    checks = await ra.verificar_agente(hacer(), "agent_borrador", ws_url=WS, webhook_url=HOOK)
    assert [(c.nombre, c.ok) for c in checks] == [("agente_publicado", False)]
