"""SPEC-18, tramo 1: crear plantillas de WhatsApp desde Canales → Plantillas.

Lo que Meta rechaza siempre se dice antes de mandarla (la revisión tarda hasta
24 h y un rechazo obliga a cambiar de nombre), la IA devuelve un borrador que el
editor puede abrir aunque venga a medias, y un tap en un botón de respuesta
rápida le llega a Sofía como texto.

El ida y vuelta con YCloud de verdad (simulador por HTTP) está en
`test_simuladores_proveedores.py`.
"""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.plantillas_biblioteca import SUGERIDAS
from app.services.plantillas_whatsapp import (
    BotonPlantilla,
    GeneracionFallida,
    PlantillaIn,
    VariablePlantilla,
    _leer_json,
    a_ycloud,
    plantilla_de_la_ia,
    problemas,
    sanear_nombre,
)

CAMPOS = frozenset({"nombre", "email", "telefono", "servicio_interes", "origen"})


def _plantilla(**cambios) -> PlantillaIn:
    base = {
        "nombre": "aviso_prueba",
        "cuerpo": "Hola {{1}}, tu cita es el {{2}}.",
        "variables": [
            VariablePlantilla(n=1, etiqueta="Nombre", ejemplo="Ana"),
            VariablePlantilla(n=2, etiqueta="Fecha", ejemplo="martes"),
        ],
    }
    return PlantillaIn(**{**base, **cambios})


# --- Reglas de Meta, antes de mandarla ------------------------------------------


def test_las_sugeridas_se_pueden_mandar_tal_cual():
    for s in SUGERIDAS:
        assert problemas(s.plantilla, campos_permitidos=CAMPOS) == [], s.clave


def test_una_variable_al_final_se_avisa_antes_de_mandarla():
    p = _plantilla(cuerpo="Hola {{1}}, tu cita es el {{2}}")
    assert any("empezar ni terminar" in f for f in problemas(p, campos_permitidos=CAMPOS))


def test_las_variables_saltadas_o_pegadas_se_avisan():
    saltada = _plantilla(cuerpo="Hola {{1}}, tu cita es el {{3}}.")
    assert any("seguidas" in f for f in problemas(saltada, campos_permitidos=CAMPOS))
    pegadas = _plantilla(cuerpo="Hola {{1}}{{2}} y más.")
    assert any("pegadas" in f for f in problemas(pegadas, campos_permitidos=CAMPOS))


def test_sin_ejemplo_meta_no_la_revisa():
    p = _plantilla(variables=[VariablePlantilla(n=1, ejemplo="Ana")])
    assert any("ejemplo de {{2}}" in f for f in problemas(p, campos_permitidos=CAMPOS))


def test_las_respuestas_rapidas_van_juntas():
    p = _plantilla(
        botones=[
            BotonPlantilla(tipo="respuesta", texto="Sí"),
            BotonPlantilla(tipo="enlace", texto="Ver", valor="https://x.mx"),
            BotonPlantilla(tipo="respuesta", texto="No"),
        ]
    )
    assert any("juntas" in f for f in problemas(p, campos_permitidos=CAMPOS))


def test_no_se_enlaza_un_campo_arbitrario_de_la_ficha():
    p = _plantilla(variables=[
        VariablePlantilla(n=1, ejemplo="Ana", campo="notas_internas"),
        VariablePlantilla(n=2, ejemplo="martes"),
    ])
    assert any("notas_internas" in f for f in problemas(p, campos_permitidos=CAMPOS))


def test_para_ycloud_lleva_ejemplos_en_orden_y_botones_en_mayusculas():
    p = _plantilla(
        cabecera="Tu cita",
        pie="Clínica Sonrisa",
        botones=[
            BotonPlantilla(tipo="respuesta", texto="Confirmo"),
            BotonPlantilla(tipo="telefono", texto="Llamar", valor="+52 998 123 4567"),
        ],
    )
    datos = a_ycloud(p)
    assert datos["category"] == "UTILITY" and datos["language"] == "es_MX"
    tipos = [c["type"] for c in datos["components"]]
    assert tipos == ["HEADER", "BODY", "FOOTER", "BUTTONS"]
    assert datos["components"][1]["example"] == {"body_text": [["Ana", "martes"]]}
    assert datos["components"][3]["buttons"] == [
        {"type": "QUICK_REPLY", "text": "Confirmo"},
        {"type": "PHONE_NUMBER", "text": "Llamar", "phone_number": "+529981234567"},
    ]


def test_el_nombre_se_sanea_como_lo_pide_meta():
    assert sanear_nombre("Recordatorio de Cita — mañana") == "recordatorio_de_cita_manana"


# --- «Generar con IA» --------------------------------------------------------------


def test_lo_que_devuelve_la_ia_se_lee_aunque_venga_entre_comillas_de_codigo():
    datos = _leer_json('```json\n{"nombre": "Aviso Cita", "categoria": "rara", "cuerpo": "Hola {{1}}."}\n```')
    p = plantilla_de_la_ia(datos, idioma="es_MX")
    assert p.nombre == "aviso_cita"
    assert p.categoria == "UTILITY", "una categoría que no existe cae en utilidad"
    # Le falta el ejemplo: es un borrador, el editor lo abre y avisa.
    assert any("ejemplo" in f for f in problemas(p, campos_permitidos=CAMPOS))


def test_si_la_ia_no_devuelve_json_se_dice_en_palabras():
    with pytest.raises(GeneracionFallida, match="Intenta de nuevo"):
        _leer_json("Claro, aquí tienes una plantilla genial.")


async def test_generar_usa_el_modelo_del_agente_de_texto_y_el_bloque_del_negocio():
    respuesta = SimpleNamespace(
        content=(
            '{"nombre": "recordatorio_cita", "categoria": "UTILITY", '
            '"cuerpo": "Hola {{1}}, te esperamos mañana.", '
            '"variables": [{"n": 1, "etiqueta": "Nombre", "ejemplo": "Ana"}], '
            '"botones": [{"tipo": "respuesta", "texto": "Confirmo"}]}'
        )
    )
    llm = SimpleNamespace(complete=AsyncMock(return_value=respuesta))
    agente = SimpleNamespace(
        llm_provider_id=None, fallback_provider_id=None, fallback_model=None, model_name="gpt-x"
    )
    with patch(
        "app.services.runtime_config.get_runtime_for_channel", AsyncMock(return_value=agente)
    ), patch("app.providers.llm.resolve_llm_provider", AsyncMock(return_value=llm)), patch(
        "app.services.runtime_config.render_common_block",
        AsyncMock(return_value="Clínica Sonrisa, tel {{PENDIENTE: teléfono}}"),
    ):
        from app.services.plantillas_whatsapp import generar_con_ia

        p = await generar_con_ia("recordar la cita un día antes", categoria=None, idioma="es_MX")

    assert p.nombre == "recordatorio_cita"
    assert p.botones[0].texto == "Confirmo"
    llamada = llm.complete.await_args.kwargs
    assert llamada["source"] == "plantillas", "el gasto sale aparte del de Sofía"
    sistema = llamada["messages"][0].content
    assert "Clínica Sonrisa" in sistema
    assert "PENDIENTE" not in sistema, "un hueco sin rellenar no llega al modelo"


# --- El tap de un botón de respuesta rápida -----------------------------------------


def test_el_tap_de_un_boton_de_ycloud_le_llega_a_sofia_como_texto():
    from app.providers.whatsapp.ycloud import YCloudProvider

    evento = {
        "type": "whatsapp.inbound_message.received",
        "whatsappInboundMessage": {
            "wamid": "wamid.tap1",
            "from": "+529981234567",
            "to": "+529980000000",
            "type": "button",
            "button": {"text": "Confirmo", "payload": "Confirmo"},
        },
    }
    mensajes = YCloudProvider().parse_webhook(evento)
    assert len(mensajes) == 1
    assert mensajes[0].text == "Confirmo"


def test_una_respuesta_de_lista_tambien():
    from app.providers.whatsapp.ycloud import YCloudProvider

    evento = {
        "type": "whatsapp.inbound_message.received",
        "whatsappInboundMessage": {
            "wamid": "wamid.lista1",
            "from": "+529981234567",
            "type": "interactive",
            "interactive": {"type": "list_reply", "list_reply": {"id": "m", "title": "Martes 10:00"}},
        },
    }
    assert YCloudProvider().parse_webhook(evento)[0].text == "Martes 10:00"


# --- Por HTTP ---------------------------------------------------------------------


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

B = "/api/v1/admin/whatsapp/plantillas"


class _YCloudFalso:
    def __init__(self) -> None:
        self.creadas: list[dict] = []

    async def create_template(self, plantilla: dict) -> dict:
        self.creadas.append(plantilla)
        return {**plantilla, "status": "PENDING"}


def _correr(prueba, *, proveedor: str = "ycloud"):
    import httpx

    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.main import app
    from app.models.user import User

    async def _run():
        suf = uuid.uuid4().hex[:8]
        nombre = f"prueba_{suf}"
        async with db_session() as db:
            admin = User(
                email=f"admin-plantillas-{suf}@test.local",
                password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
                role="admin",
            )
            db.add(admin)
            await db.commit()
            admin_id = admin.id
        token = create_access_token(subject=str(admin_id), extra={"role": "admin"})
        falso = _YCloudFalso()
        try:
            with patch(
                "app.providers.whatsapp.selector.get_provider_name",
                AsyncMock(return_value=proveedor),
            ), patch("app.providers.whatsapp.selector.provider_by_name", return_value=falso):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                    await prueba(c, {"Authorization": f"Bearer {token}"}, nombre, falso)
        finally:
            from sqlalchemy import text

            async with db_session() as db:
                await db.execute(
                    text("DELETE FROM whatsapp_template_var WHERE template_name = :n"), {"n": nombre}
                )
                await db.execute(text("DELETE FROM audit_log WHERE user_id = :i"), {"i": str(admin_id)})
                await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(admin_id)})
                await db.commit()

    asyncio.run(_run())


@con_db
def test_enviar_a_aprobacion_la_manda_a_ycloud_y_guarda_las_etiquetas():
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.whatsapp_template_var import WhatsappTemplateVar

    async def prueba(c, h, nombre, falso):
        info = (await c.get(B, headers=h)).json()
        assert info["puede_crear"] is True
        assert [s["clave"] for s in info["sugeridas"]][:2] == ["recordatorio", "inasistencia"]

        cuerpo = SUGERIDAS[0].plantilla.model_dump()
        cuerpo["nombre"] = nombre
        r = await c.post(B, json=cuerpo, headers=h)
        assert r.status_code == 201, r.text
        assert r.json() == {"nombre": nombre, "idioma": "es_MX", "estado": "PENDING"}
        assert falso.creadas[0]["name"] == nombre

        async with db_session() as db:
            filas = (
                await db.execute(
                    select(WhatsappTemplateVar)
                    .where(WhatsappTemplateVar.template_name == nombre)
                    .order_by(WhatsappTemplateVar.idx)
                )
            ).scalars().all()
        assert [(f.idx, f.nombre, f.contact_field) for f in filas] == [
            (1, "Nombre del paciente", "nombre"),
            (2, "Nombre del negocio", None),
            (3, "Fecha de la cita", None),
            (4, "Hora de la cita", None),
        ]

    _correr(prueba)


@con_db
def test_una_plantilla_que_meta_rechazaria_no_sale_y_dice_por_que():
    async def prueba(c, h, nombre, falso):
        cuerpo = _plantilla(nombre=nombre, cuerpo="{{1}}, tu cita es el {{2}}.").model_dump()
        r = await c.post(B, json=cuerpo, headers=h)
        assert r.status_code == 400
        assert "empezar ni terminar" in r.json()["detail"]
        assert falso.creadas == []

    _correr(prueba)


@con_db
def test_con_la_api_de_meta_se_dice_donde_se_crean():
    async def prueba(c, h, nombre, falso):
        info = (await c.get(B, headers=h)).json()
        assert info["puede_crear"] is False
        r = await c.post(B, json=_plantilla(nombre=nombre).model_dump(), headers=h)
        assert r.status_code == 409
        assert "Administrador de WhatsApp de Meta" in r.json()["detail"]
        assert falso.creadas == []

    _correr(prueba, proveedor="meta")
