"""Los proveedores del CRM hablan con los simuladores igual que con los reales.

Estas pruebas levantan los simuladores de `tests/mocks` de verdad (servidor
HTTP, IMAP y SMTP en puertos locales) y usan el código de producción SIN
parchear su forma de hablar: el cliente de YCloud envía por HTTP, el de Retell
valida llamadas, el de correo entra por TLS verificado. Si alguien cambia una
ruta, una cabecera o una firma en un lado y no en el otro, se rompe aquí y no
en la instalación de un cliente.

Lo único que se sustituye es de dónde salen las credenciales (en producción,
de la base de datos cifrada): así no necesitan Postgres y corren en todas
partes.

El recorrido completo (webhook → agente → envío) con la API y el worker de
verdad lo cubre `scripts/smoke_simuladores.py`, en su propio job de la CI.
"""
from __future__ import annotations

import asyncio
import json
import socket
import ssl
import threading
import time
from unittest.mock import AsyncMock

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request

from tests.mocks import imap_mock, n8n_mock, retell_mock, wa_mock, zernio_mock
from tests.mocks._comun import cuerpo_json, firma_retell, firma_ycloud

# aiosmtpd avisa de AUTH sin STARTTLS en el SMTP de TLS implícito, que ya va
# cifrado desde el primer byte (ver `imap_mock.servir_smtp`).
pytestmark = pytest.mark.filterwarnings("ignore:Requiring AUTH while not requiring TLS")


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Servidor:
    """Un simulador HTTP en un hilo, con su propio bucle de eventos."""

    def __init__(self, app) -> None:
        self.puerto = _puerto_libre()
        self.url = f"http://127.0.0.1:{self.puerto}"
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.puerto, log_level="error")
        )
        self._hilo = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> _Servidor:
        self._hilo.start()
        limite = time.time() + 10
        while not self._server.started:
            if time.time() > limite:
                raise RuntimeError("el simulador no arrancó")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc) -> None:
        self._server.should_exit = True
        self._hilo.join(timeout=5)


# --------------------------------------------------------------------- YCloud
@pytest.fixture
def ycloud(monkeypatch):
    """Simulador de YCloud y el proveedor del CRM apuntándole, con credenciales
    en memoria. `credenciales` se puede tocar desde la prueba."""
    from app.core.config import settings
    from app.providers.whatsapp import ycloud as modulo

    credenciales = {
        "ycloud_api_key": "mock-key",
        "ycloud_phone_number": wa_mock.NUMERO_POR_DEFECTO,
        "ycloud_webhook_secret": "whsec_pruebas",
    }

    async def leer(clave, **_):
        return credenciales.get(clave)

    monkeypatch.setattr(modulo, "get_credential", leer)
    monkeypatch.setattr(modulo, "push_runtime_log", AsyncMock())
    with _Servidor(wa_mock.crear_app()) as servidor:
        monkeypatch.setattr(settings, "YCLOUD_BASE_URL", f"{servidor.url}/v2")
        yield modulo.YCloudProvider(), servidor, credenciales


def _outbox(servidor) -> list[dict]:
    return httpx.get(f"{servidor.url}/_mock/outbox").json()


async def test_el_texto_sale_por_el_envio_de_ycloud(ycloud):
    proveedor, servidor, _ = ycloud
    id_devuelto = await proveedor.send_text("+529981234567", "Hola desde las pruebas")
    enviado = _outbox(servidor)[-1]
    assert enviado["to"] == "+529981234567"
    assert enviado["texto"] == "Hola desde las pruebas"
    assert id_devuelto == enviado["id"], "el id guardado tiene que ser el que dio YCloud"


async def test_un_archivo_se_sube_con_el_numero_del_negocio_y_sale_por_su_id(ycloud):
    # YCloud sube media en `/v2/whatsapp/media/{número del negocio}/upload`. Sin el
    # número en la ruta contesta 404, y hasta la 1.1.1 eso tumbaba en silencio
    # todo archivo saliente por WhatsApp: adjuntos del inbox y fotos de n8n.
    proveedor, servidor, _ = ycloud
    media_id = await proveedor.upload_media(b"\xff\xd8\xff\xe0foto", "image/jpeg", "blanqueamiento.jpg")
    subida = httpx.get(f"{servidor.url}/_mock/subidas").json()[-1]
    assert subida["numero"] == wa_mock.NUMERO_POR_DEFECTO, "la subida va al número del negocio"
    assert subida["id"] == media_id and subida["nombre"] == "blanqueamiento.jpg"
    await proveedor.send_media("+529981234567", media_id, "image", caption="Blanqueamiento")
    enviado = _outbox(servidor)[-1]
    assert enviado["type"] == "image"
    assert enviado["cuerpo"]["image"] == {"id": media_id, "caption": "Blanqueamiento"}


async def test_un_numero_guardado_sin_mas_se_sube_igual(ycloud):
    proveedor, servidor, credenciales = ycloud
    credenciales["ycloud_phone_number"] = wa_mock.NUMERO_POR_DEFECTO.lstrip("+")
    await proveedor.upload_media(b"%PDF-1.4", "application/pdf", "presupuesto.pdf")
    assert httpx.get(f"{servidor.url}/_mock/subidas").json()[-1]["numero"] == wa_mock.NUMERO_POR_DEFECTO


async def test_sin_numero_del_negocio_no_se_sube_nada(ycloud):
    from app.providers.whatsapp.ycloud import WhatsAppNotConfiguredError

    proveedor, servidor, credenciales = ycloud
    credenciales["ycloud_phone_number"] = None
    with pytest.raises(WhatsAppNotConfiguredError):
        await proveedor.upload_media(b"\xff\xd8foto", "image/jpeg", "foto.jpg")
    assert httpx.get(f"{servidor.url}/_mock/subidas").json() == []


async def test_un_contacto_sin_telefono_se_envia_por_bsuid(ycloud):
    """YCloud exige `to` o `recipient`, nunca los dos: el simulador rechaza
    ambos a la vez, igual que la API real."""
    proveedor, servidor, _ = ycloud
    await proveedor.send_text("wa:MX.1234567890", "Hola por BSUID")
    enviado = _outbox(servidor)[-1]
    assert enviado["recipient"] == "MX.1234567890"
    assert enviado["to"] is None


async def test_la_plantilla_del_recordatorio_sale_con_su_nombre(ycloud):
    proveedor, servidor, _ = ycloud
    plantillas = await proveedor.list_templates()
    assert "recordatorio_cita" in {p["name"] for p in plantillas}
    await proveedor.send_template(
        "+529981234567", "recordatorio_cita", "es_MX", ["Ana", "lunes 10:00"],
        idempotency_key="recordatorio-1",
    )
    await proveedor.send_template(
        "+529981234567", "recordatorio_cita", "es_MX", ["Ana", "lunes 10:00"],
        idempotency_key="recordatorio-1",
    )
    enviados = [m for m in _outbox(servidor) if m["plantilla"] == "recordatorio_cita"]
    assert len(enviados) == 1, "el reintento con la misma Idempotency-Key no manda dos veces"


async def test_una_plantilla_creada_desde_el_panel_queda_en_revision_y_se_ve_su_rechazo(ycloud):
    """SPEC-18: la plantilla sale con el `wabaId` del número (que no se guarda) y
    queda en `PENDING`; si Meta la rechaza, el listado trae el motivo."""
    from app.api.admin import _template_to_out
    from app.services.plantillas_biblioteca import SUGERIDAS
    from app.services.plantillas_whatsapp import a_ycloud

    proveedor, servidor, _ = ycloud
    sugerida = SUGERIDAS[0].plantilla
    creada = await proveedor.create_template(a_ycloud(sugerida))
    assert creada["status"] == "PENDING"
    assert creada["wabaId"] == wa_mock.WABA_POR_DEFECTO

    httpx.put(
        f"{servidor.url}/_mock/plantillas/{sugerida.nombre}",
        json={"status": "REJECTED", "reason": "INVALID_FORMAT"},
    )
    listada = next(t for t in await proveedor.list_templates() if t["name"] == sugerida.nombre)
    salida = _template_to_out(listada)
    assert salida.status == "REJECTED"
    assert salida.reason == "INVALID_FORMAT"
    assert [b["texto"] for b in salida.botones] == ["Confirmo", "Necesito cambiarla"]


async def test_un_nombre_repetido_sale_con_el_motivo_de_meta(ycloud):
    from app.providers.whatsapp.base import TemplateCreateError
    from app.services.plantillas_biblioteca import SUGERIDAS
    from app.services.plantillas_whatsapp import a_ycloud

    proveedor, _, _ = ycloud
    plantilla = a_ycloud(SUGERIDAS[1].plantilla)
    await proveedor.create_template(plantilla)
    with pytest.raises(TemplateCreateError, match="Ya existe contenido en este idioma"):
        await proveedor.create_template(plantilla)


async def test_sin_el_numero_en_ycloud_no_se_crea_y_dice_que_revisar(ycloud):
    from app.providers.whatsapp.base import TemplateCreateError
    from app.services.plantillas_biblioteca import SUGERIDAS
    from app.services.plantillas_whatsapp import a_ycloud

    proveedor, _, credenciales = ycloud
    credenciales["ycloud_phone_number"] = "+5215550000000"
    with pytest.raises(TemplateCreateError, match="cuenta de WhatsApp de tu número"):
        await proveedor.create_template(a_ycloud(SUGERIDAS[0].plantilla))


async def test_una_clave_mala_no_se_da_por_enviada(ycloud):
    proveedor, servidor, credenciales = ycloud
    credenciales["ycloud_api_key"] = "clave-invalid"
    with pytest.raises(httpx.HTTPStatusError):
        await proveedor.send_text("+529981234567", "no debería salir")
    assert _outbox(servidor) == []


async def test_un_fallo_de_ycloud_al_enviar_no_se_da_por_bueno(ycloud):
    proveedor, servidor, _ = ycloud
    httpx.put(f"{servidor.url}/_mock/fallos", json={"envio": 500})
    with pytest.raises(httpx.HTTPStatusError):
        await proveedor.send_text("+529981234567", "no debería salir")
    assert _outbox(servidor) == []


async def test_el_webhook_se_registra_por_api_y_el_secreto_se_puede_rotar(ycloud):
    proveedor, _, _ = ycloud
    url = "https://crm.ejemplo.mx/api/v1/webhooks/ycloud"
    creado = await proveedor.create_or_update_webhook_endpoint(url)
    assert creado["reused"] is False and creado["secret"].startswith("whsec_")
    # Instalar dos veces no deja dos endpoints mandando lo mismo.
    repetido = await proveedor.create_or_update_webhook_endpoint(url)
    assert repetido["reused"] is True and "secret" not in repetido
    assert len(await proveedor.list_webhook_endpoints()) == 1
    nuevo = await proveedor.rotate_webhook_secret(creado["id"])
    assert nuevo and nuevo != creado["secret"]


async def test_la_firma_del_simulador_es_la_que_acepta_el_crm(ycloud):
    """Si el simulador firmara distinto que YCloud, todas las pruebas con
    webhooks darían 401 o, peor, pasarían por el motivo equivocado."""
    proveedor, _, _ = ycloud
    cuerpo = cuerpo_json({"id": "evt.1", "type": "whatsapp.inbound_message.received"})
    buena = {"ycloud-signature": firma_ycloud("whsec_pruebas", cuerpo)}
    mala = {"ycloud-signature": firma_ycloud("otro-secreto", cuerpo)}
    vieja = {"ycloud-signature": firma_ycloud("whsec_pruebas", cuerpo, ts=int(time.time()) - 3600)}
    assert await proveedor.verify_webhook_signature(buena, cuerpo) is True
    assert await proveedor.verify_webhook_signature(mala, cuerpo) is False
    assert await proveedor.verify_webhook_signature(vieja, cuerpo) is False


async def test_el_mensaje_simulado_lo_entiende_el_parser_de_ycloud(ycloud):
    """El `inbound` del simulador entrega a una URL; aquí se captura lo que
    manda para pasarlo por el parser real."""
    proveedor, servidor, _ = ycloud
    recibido: dict = {}
    receptor = FastAPI()

    @receptor.post("/hook")
    async def hook(request: Request) -> dict:
        recibido["cuerpo"] = await request.body()
        recibido["cabeceras"] = {k.lower(): v for k, v in request.headers.items()}
        return {"ok": True}

    with _Servidor(receptor) as crm:
        r = httpx.post(
            f"{servidor.url}/_mock/inbound",
            json={
                "from": "+529981234567",
                "name": "Ana",
                "text": "Quiero una cita",
                "ctwa_clid": "clid-123",
                "url": f"{crm.url}/hook",
                "secret": "whsec_pruebas",
            },
            timeout=10,
        ).json()
    assert r["status_code"] == 200
    assert await proveedor.verify_webhook_signature(recibido["cabeceras"], recibido["cuerpo"])
    [mensaje] = proveedor.parse_webhook(json.loads(recibido["cuerpo"]))
    assert mensaje.from_phone == "+529981234567"
    assert mensaje.to_phone == wa_mock.NUMERO_POR_DEFECTO
    assert mensaje.text == "Quiero una cita"
    assert mensaje.customer_name == "Ana"
    assert mensaje.provider_message_id == r["wamid"]


# --------------------------------------------------------------------- Retell
@pytest.fixture
def retell(monkeypatch):
    from app.core.config import settings
    from app.providers.voice import retell as modulo

    credenciales = modulo.RetellCredentials(api_key="retell-key", agent_id_retell="agent_mock")
    monkeypatch.setattr(modulo, "get_retell_credentials", AsyncMock(return_value=credenciales))
    monkeypatch.setattr(modulo, "push_runtime_log", AsyncMock())
    with _Servidor(retell_mock.crear_app()) as servidor:
        monkeypatch.setattr(settings, "RETELL_BASE_URL", servidor.url)
        yield modulo, servidor


async def test_solo_se_atiende_una_llamada_que_existe_y_sigue_en_curso(retell):
    modulo, servidor = retell
    httpx.post(f"{servidor.url}/_mock/calls", json={"call_id": "call_viva"})
    httpx.post(
        f"{servidor.url}/_mock/calls", json={"call_id": "call_vieja", "call_status": "ended"}
    )
    assert await modulo.is_call_authentic("call_viva") is True
    assert await modulo.is_call_authentic("call_vieja") is False, "reenvío de una llamada terminada"
    assert await modulo.is_call_authentic("call_inventada") is False


async def test_las_grabaciones_firmadas_se_activan_y_el_agente_se_publica(retell):
    modulo, servidor = retell
    # El agente de V-3: creado a mano en la consola y ya publicado (solo lectura).
    httpx.post(f"{servidor.url}/_mock/agentes", json={"agent_id": "agent_mock"})
    ok, detalle = await modulo.enable_signed_recordings("retell-key", "agent_mock")
    assert ok, detalle
    agente = httpx.get(f"{servidor.url}/_mock/agentes").json()["agent_mock"]
    assert agente["opt_in_signed_url"] is True
    assert agente["publicado"] is True, "sin publicar, el cambio no aplica a llamadas nuevas"
    assert agente["versiones"] == 2, "el cambio va en un borrador nuevo, no en la versión publicada"

    # Volver a guardar el canal no amontona versiones: ya estaba así.
    ok, _ = await modulo.enable_signed_recordings("retell-key", "agent_mock")
    assert ok
    assert httpx.get(f"{servidor.url}/_mock/agentes").json()["agent_mock"]["versiones"] == 2


async def test_si_retell_no_acepta_el_cambio_se_explica_y_no_revienta(retell):
    modulo, _ = retell
    ok, detalle = await modulo.enable_signed_recordings("retell-key", "agent_que_no_existe")
    assert ok is False
    assert "404" in detalle


async def test_la_firma_del_webhook_de_retell_es_la_que_acepta_el_crm(retell):
    modulo, _ = retell
    proveedor = modulo.RetellProvider()
    cuerpo = cuerpo_json({"event": "call_analyzed", "call": {"call_id": "c1"}})
    buena = {"x-retell-signature": firma_retell("retell-key", cuerpo)}
    mala = {"x-retell-signature": firma_retell("otra-clave", cuerpo)}
    assert await proveedor.verify_call_webhook_signature(buena, cuerpo) is True
    assert await proveedor.verify_call_webhook_signature(mala, cuerpo) is False


# --------------------------------------------------------------------- Zernio
@pytest.fixture
def zernio(monkeypatch):
    from app.core.config import settings
    from app.providers.zernio.client import ZernioClient

    with _Servidor(zernio_mock.crear_app()) as servidor:
        monkeypatch.setattr(settings, "ZERNIO_BASE_URL", f"{servidor.url}/api/v1")
        yield ZernioClient("zk_prueba"), servidor


class _Receptor:
    """Un CRM de mentira que guarda lo que le llega por webhook."""

    def __init__(self) -> None:
        self.recibido: list[tuple[dict, bytes]] = []
        app = FastAPI()

        @app.post("/hook")
        async def hook(request: Request) -> dict:
            self.recibido.append(
                ({k.lower(): v for k, v in request.headers.items()}, await request.body())
            )
            return {"ok": True}

        self.app = app


async def test_la_llave_se_valida_listando_cuentas_y_una_mala_da_401(zernio):
    from app.providers.zernio.client import ZernioAuthError, ZernioClient

    cliente, _ = zernio
    cuentas = await cliente.list_accounts()
    assert {c["platform"] for c in cuentas} == {"instagram", "facebook"}
    with pytest.raises(ZernioAuthError):
        await ZernioClient("zk-invalid").list_accounts()


async def test_el_envio_de_zernio_respeta_la_llave_de_idempotencia(zernio):
    cliente, servidor = zernio
    receptor = _Receptor()
    with _Servidor(receptor.app) as crm:
        httpx.post(f"{servidor.url}/_mock/entrante", json={
            "platform": "facebook", "participant_id": "psid1",
            "url": f"{crm.url}/hook", "secreto": "s",
        })
    evento = json.loads(receptor.recibido[0][1])
    cid, cuenta = evento["message"]["conversationId"], evento["account"]["accountId"]

    uno = await cliente.send_message(cid, cuenta, "Hola", idempotency_key="k1")
    otra_vez = await cliente.send_message(cid, cuenta, "Hola", idempotency_key="k1")
    assert otra_vez.message_id == uno.message_id and otra_vez.replayed is True
    assert len(httpx.get(f"{servidor.url}/_mock/enviados").json()) == 1, "salió dos veces"


async def test_zernio_caido_no_se_da_por_enviado(zernio):
    from app.providers.zernio.client import ZernioError

    cliente, servidor = zernio
    httpx.post(f"{servidor.url}/_mock/fallo", json={"status": 500})
    with pytest.raises(ZernioError):
        await cliente.send_message("c", "a", "Hola", idempotency_key="k2")
    assert httpx.get(f"{servidor.url}/_mock/enviados").json() == []


async def test_el_webhook_se_registra_y_sus_eventos_los_acepta_el_crm(zernio):
    from app.providers.zernio.dispatch import parse_event, verify_signature

    cliente, servidor = zernio
    receptor = _Receptor()
    with _Servidor(receptor.app) as crm:
        registrado = await cliente.register_webhook(
            f"{crm.url}/hook", "secreto-del-crm", ["message.received", "message.sent"]
        )
        assert registrado["_id"] and "secret" not in registrado
        httpx.post(f"{servidor.url}/_mock/entrante", json={"platform": "instagram", "participant_id": "ig1"})
        httpx.post(f"{servidor.url}/_mock/eco", json={"platform": "instagram", "participant_id": "ig1"})
        httpx.post(f"{servidor.url}/_mock/entrante", json={"firma_mala": True})

    (cab1, c1), (cab2, c2), (cab3, c3) = receptor.recibido
    assert verify_signature(cab1, c1, "secreto-del-crm")
    assert verify_signature(cab2, c2, "secreto-del-crm")
    assert not verify_signature(cab3, c3, "secreto-del-crm")
    entrante, eco = parse_event(json.loads(c1)), parse_event(json.loads(c2))
    assert entrante.tipo == "entrante" and entrante.incoming.from_phone == "ig:ig1"
    assert eco.tipo == "eco" and eco.eco["customer_id"] == "ig:ig1" and eco.eco["tomar_control"]


# --------------------------------------------------------------------- Correo
@pytest.fixture(scope="module")
def correo():
    """Buzón simulado (IMAP y SMTP con TLS) en un hilo con su propio bucle."""
    cert = imap_mock.generar_certificado()
    estado = imap_mock.EstadoCorreo()
    puertos = [_puerto_libre() for _ in range(4)]
    bucle = asyncio.new_event_loop()
    listo = threading.Event()

    def correr():
        asyncio.set_event_loop(bucle)
        bucle.run_until_complete(imap_mock.servir_imap(estado, cert, "127.0.0.1", *puertos[:2]))
        listo.set()
        bucle.run_forever()

    threading.Thread(target=correr, daemon=True).start()
    listo.wait(10)
    smtp = imap_mock.servir_smtp(estado, cert, "127.0.0.1", *puertos[2:])
    yield estado, cert, puertos
    for c in smtp:
        c.stop()
    bucle.call_soon_threadsafe(bucle.stop)


@pytest.fixture
def buzon(correo, monkeypatch):
    estado, cert, puertos = correo
    # El CRM verifica el certificado siempre; aquí además confía en el del
    # simulador. No hay modo "sin verificar" que probar, y así debe seguir.
    monkeypatch.setattr(ssl, "create_default_context", imap_mock.confiar_en(cert))
    estado.carpetas = imap_mock.EstadoCorreo().carpetas
    estado.enviados_smtp.clear()
    estado.uidvalidity = 1
    return estado, puertos


def _llega(estado, asunto: str) -> None:
    crudo, _ = imap_mock.crear_correo(imap_mock.NuevoCorreo(subject=asunto))
    estado.carpeta("INBOX").guardar(crudo)


@pytest.mark.parametrize("seguridad", ["ssl", "starttls"])
def test_el_buzon_solo_lee_lo_nuevo_desde_que_se_conecta(buzon, seguridad):
    from app.providers.email import imap_client as ic

    estado, puertos = buzon
    puerto = puertos[0] if seguridad == "ssl" else puertos[1]
    cfg = ic.ConfigImap("clinica@ejemplo.mx", "app-pass", ic.Servidor("localhost", puerto, seguridad))
    _llega(estado, "Correo viejo")
    ic.probar(cfg)
    ancla = ic.traer_nuevos(cfg, None, 0)
    assert ancla.correos == [], "al conectar no se lee hacia atrás"
    _llega(estado, "Quiero una cita")
    nuevos = ic.traer_nuevos(cfg, ancla.uidvalidity, ancla.ultimo_uid)
    assert [b"Quiero una cita" in c.crudo for c in nuevos.correos] == [True]
    otra_vez = ic.traer_nuevos(cfg, nuevos.uidvalidity, nuevos.ultimo_uid)
    assert otra_vez.correos == [], "lo ya leído no vuelve a entrar"


def test_si_el_buzon_cambia_uidvalidity_se_relee_el_ultimo_dia(buzon):
    from app.providers.email import imap_client as ic

    estado, puertos = buzon
    cfg = ic.ConfigImap("clinica@ejemplo.mx", "app-pass", ic.Servidor("localhost", puertos[0], "ssl"))
    _llega(estado, "Antes de migrar")
    ancla = ic.traer_nuevos(cfg, None, 0)
    estado.uidvalidity = 99
    pasada = ic.traer_nuevos(cfg, ancla.uidvalidity, ancla.ultimo_uid)
    assert pasada.resincronizado and len(pasada.correos) == 1


def test_una_contrasena_mala_se_explica_y_no_revienta(buzon):
    from app.providers.email import imap_client as ic

    _, puertos = buzon
    cfg = ic.ConfigImap("clinica@ejemplo.mx", "mala-invalid", ic.Servidor("localhost", puertos[0], "ssl"))
    with pytest.raises(ic.ImapError, match="contraseña"):
        ic.probar(cfg)


@pytest.mark.parametrize("seguridad", ["ssl", "starttls"])
def test_la_respuesta_sale_por_smtp_en_el_mismo_hilo_y_queda_en_enviados(buzon, seguridad):
    from app.providers.email import imap_client as ic
    from app.services import correo_smtp as cs

    estado, puertos = buzon
    smtp = ic.Servidor("localhost", puertos[2] if seguridad == "ssl" else puertos[3], seguridad)
    cs.probar_smtp(smtp, "clinica@ejemplo.mx", "app-pass")
    msg = cs.construir_respuesta(
        desde="clinica@ejemplo.mx",
        para="paciente@example.com",
        asunto="Quiero una cita",
        cuerpo="Claro, ¿el martes a las 10?",
        in_reply_to="<original@example.com>",
        references="<original@example.com>",
    )
    cs._enviar_sync(smtp, "clinica@ejemplo.mx", "app-pass", msg)
    [enviado] = estado.enviados_smtp
    assert enviado["in_reply_to"] == "<original@example.com>"
    assert enviado["subject"] == "Re: Quiero una cita"

    imap = ic.ConfigImap("clinica@ejemplo.mx", "app-pass", ic.Servidor("localhost", puertos[0], "ssl"))
    carpeta = ic.detectar_enviados(imap)
    assert carpeta == imap_mock.CARPETA_ENVIADOS
    ic.guardar_en_enviados(imap, carpeta, msg.as_bytes())
    assert len(estado.carpeta(carpeta).correos) == 1


def test_el_correo_del_sistema_sale_por_starttls_verificando_el_certificado(buzon):
    """El SMTP del sistema (restablecer la contraseña, resúmenes) cifra y verifica."""
    from app.providers.email import smtp_client

    estado, puertos = buzon
    smtp_client._send_sync(
        "localhost", puertos[3], "clinica@ejemplo.mx", "app-pass", "clinica@ejemplo.mx",
        "paciente@ejemplo.mx", "Restablece tu contraseña", "<p>Enlace</p>", "Enlace",
    )
    assert len(estado.enviados_smtp) == 1


def test_el_correo_del_sistema_no_sale_a_un_servidor_con_certificado_no_confiable(correo):
    """Salió en el dry-run 2 (SEC-9): `starttls()` sin contexto cifraba sin
    comprobar el certificado. Aquí el del simulador no es de confianza."""
    from app.providers.email import smtp_client

    estado, _, puertos = correo
    antes = len(estado.enviados_smtp)
    with pytest.raises(ssl.SSLCertVerificationError):
        smtp_client._send_sync(
            "localhost", puertos[3], "clinica@ejemplo.mx", "app-pass", "clinica@ejemplo.mx",
            "paciente@ejemplo.mx", "Restablece tu contraseña", "<p>Enlace</p>", "Enlace",
        )
    assert len(estado.enviados_smtp) == antes


def test_el_smtp_rechaza_una_contrasena_mala_con_un_motivo_legible(buzon):
    from app.providers.email import imap_client as ic
    from app.services import correo_smtp as cs

    _, puertos = buzon
    with pytest.raises(cs.EnvioSmtpError, match="contraseña"):
        cs.probar_smtp(ic.Servidor("localhost", puertos[2], "ssl"), "x@ejemplo.mx", "m-invalid")


# ------------------------------------------------------------- medición V-3
def _medir_v3():
    import importlib.util
    from pathlib import Path

    ruta = Path(__file__).resolve().parents[2] / "scripts" / "medir_v3.py"
    spec = importlib.util.spec_from_file_location("medir_v3", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_v3_junta_los_turnos_de_todas_las_llamadas_y_da_el_veredicto(monkeypatch):
    medir_v3 = _medir_v3()
    with _Servidor(retell_mock.crear_app()) as servidor:
        monkeypatch.setattr(medir_v3, "BASE", servidor.url)
        for i, turnos in enumerate(([900, 1200, 1400], [1100, 1300, 2300]), start=1):
            httpx.post(
                f"{servidor.url}/_mock/calls",
                json={"call_id": f"call_{i}", "agent_id": "agent_v3", "e2e_ms": turnos},
            )
        httpx.post(f"{servidor.url}/_mock/calls", json={"call_id": "otra", "agent_id": "otro"})
        ids = medir_v3.llamadas_del_agente("retell-key", "agent_v3", 0)
        assert sorted(ids) == ["call_1", "call_2"], "solo las de ese agente y terminadas"
        filas, todos = medir_v3.medir("retell-key", ids)
    texto, pasa = medir_v3.informe(filas, todos)
    assert len(todos) == 6
    assert medir_v3.percentil(todos, 50) == 1250
    assert pasa, texto
    assert "provisional" in texto, "con menos de 60 turnos el veredicto no es firme"


def test_v3_no_pasa_si_el_p90_se_dispara():
    medir_v3 = _medir_v3()
    lentos = [1000.0] * 8 + [3000.0, 3200.0]
    _, pasa = medir_v3.informe([{"call_id": "c", "turnos": 10, "p50": 1000, "p90": 3000, "max": 3200}], lentos)
    assert medir_v3.percentil(lentos, 50) <= medir_v3.UMBRAL_P50_MS
    assert pasa is False, "un p50 bueno no tapa unos turnos de tres segundos"


# ----------------------------------------------------------------------- n8n


@pytest.fixture
def n8n(monkeypatch):
    """El simulador de n8n y la guardia de URLs relajada SOLO aquí: el CRM exige
    HTTPS pública y el simulador es http en 127.0.0.1."""
    from app.services import archivos_salientes, n8n_motor, n8n_tools

    async def _cualquiera(url):
        return True, ""

    monkeypatch.setattr(n8n_tools, "validate_public_https_url", _cualquiera)
    monkeypatch.setattr(archivos_salientes, "validate_public_https_url", _cualquiera)
    monkeypatch.setattr(n8n_motor, "validate_public_https_url", _cualquiera)
    monkeypatch.setattr(n8n_motor, "REINTENTOS_S", (0, 0))
    with _Servidor(n8n_mock.crear_app()) as servidor:
        yield servidor


def _herramienta_n8n(servidor, nombre: str, **campos):
    from tests._n8n import herramienta

    return herramienta(nombre=nombre, url=f"{servidor.url}/webhook/{nombre}", **{"secreto": "s3cr3to", **campos})


def _configurar(servidor, nombre: str, **conf):
    httpx.put(f"{servidor.url}/_mock/herramientas/{nombre}", json={"secreto": "s3cr3to", **conf})


async def test_n8n_contesta_si_el_secreto_es_bueno_y_rechaza_si_no(n8n):
    from app.services.n8n_tools import llamar_a_n8n

    _configurar(n8n, "pedido", modo="ok", resultado={"estado": "en camino"})
    hecho = await llamar_a_n8n(_herramienta_n8n(n8n, "pedido"), {"numero": "7"}, {})
    assert hecho["ok"] and hecho["respuesta"]["resultado"] == {"estado": "en camino"}
    llegada = httpx.get(f"{n8n.url}/_mock/llamadas").json()[-1]
    assert llegada["cuerpo"]["args"] == {"numero": "7"}

    hecho = await llamar_a_n8n(_herramienta_n8n(n8n, "pedido", secreto="otro"), {"numero": "7"}, {})
    assert not hecho["ok"] and "403" in hecho["error"], "n8n aceptó una llamada con el secreto equivocado"


async def test_n8n_que_tarda_o_falla(n8n):
    from app.services.n8n_tools import llamar_a_n8n

    _configurar(n8n, "lento", modo="tarda", segundos=3)
    hecho = await llamar_a_n8n(_herramienta_n8n(n8n, "lento", espera_s=1), {}, {})
    assert not hecho["ok"] and "no contestó en 1 s" in hecho["error"]

    _configurar(n8n, "roto", modo="falla", estado=500)
    hecho = await llamar_a_n8n(_herramienta_n8n(n8n, "roto", accion="lectura"), {}, {})
    assert not hecho["ok"] and hecho["intentos"] == 2


async def test_n8n_que_devuelve_un_archivo_lo_baja_y_lo_guarda(n8n, tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.archivos_salientes import descargar
    from app.services.n8n_tools import llamar_a_n8n

    monkeypatch.setattr(settings, "UPLOADS_PATH", str(tmp_path))
    _configurar(n8n, "foto", modo="archivo", resultado="Te mando la foto")
    hecho = await llamar_a_n8n(_herramienta_n8n(n8n, "foto"), {}, {})
    archivo = hecho["respuesta"]["archivos"][0]
    guardado = await descargar(archivo["url"], archivo["tipo"], archivo["nombre"])
    assert guardado.kind == "image" and guardado.mime == "image/jpeg"
    assert (tmp_path / guardado.ruta.rsplit("/", 1)[-1]).exists()


# «Sofía en n8n» (SPEC-07): el flujo hace de cerebro del agente.


def _destino(servidor, nombre: str = "sofia", **campos):
    from app.services.n8n_motor import DestinoN8n

    datos = {"n8n_url": f"{servidor.url}/webhook/{nombre}", "n8n_secreto": "s3cr3to", "n8n_espera_s": 5}
    return DestinoN8n(**{**datos, **campos})


_EVENTO = {"evento": "mensajes", "evento_id": "e-1", "mensajes": [{"tipo": "texto", "texto": "¿Hay cita mañana?"}]}


async def test_sofia_en_n8n_da_la_vuelta_con_la_respuesta_directa(n8n):
    from app.services.n8n_motor import _forma, _interpretar, _leer, llamar

    _configurar(n8n, "sofia", modo="agente", acciones={"etapa": "cliente"})
    r = await llamar(_destino(n8n), _EVENTO)
    lectura = _interpretar(_leer(r))
    assert lectura.texto == "n8n recibió: ¿Hay cita mañana?"
    assert lectura.acciones == {"etapa": "cliente"} and _forma(lectura) == "respuesta directa"
    llegada = httpx.get(f"{n8n.url}/_mock/llamadas").json()[-1]
    assert llegada["cuerpo"]["evento_id"] == "e-1"

    _configurar(n8n, "sofia", modo="agente", despues=True)
    lectura = _interpretar(_leer(await llamar(_destino(n8n), _EVENTO)))
    assert _forma(lectura) == "contesta después por la API"


async def test_sofia_en_n8n_con_secreto_malo_no_reintenta_y_con_500_si(n8n):
    from app.services.n8n_motor import N8nNoContesta, llamar

    _configurar(n8n, "sofia", modo="agente")
    with pytest.raises(N8nNoContesta, match="403"):
        await llamar(_destino(n8n, n8n_secreto="otro"), _EVENTO)
    assert len(httpx.get(f"{n8n.url}/_mock/llamadas").json()) == 0, "el 403 llegó al flujo"

    _configurar(n8n, "sofia", modo="falla", estado=500)
    with pytest.raises(N8nNoContesta, match="500"):
        await llamar(_destino(n8n), _EVENTO)
    eventos = [ll["cuerpo"]["evento_id"] for ll in httpx.get(f"{n8n.url}/_mock/llamadas").json()]
    assert eventos == ["e-1"] * 3, "los reintentos no repiten el mismo evento_id"


async def test_sofia_en_n8n_que_tarda_no_se_repite(n8n):
    from app.services.n8n_motor import llamar

    _configurar(n8n, "sofia", modo="tarda", segundos=3)
    assert await llamar(_destino(n8n, n8n_espera_s=1), _EVENTO) is None
    assert len(httpx.get(f"{n8n.url}/_mock/llamadas").json()) == 1, "una directa lenta no se repite"
