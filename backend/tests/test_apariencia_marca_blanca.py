"""La apariencia de la instalación (marca blanca): qué se acepta, quién puede
cambiarla y que el login la vea sin sesión.

Las primeras pruebas no necesitan base de datos: validan lo que entra. Las
de HTTP montan la app real con un JWT de verdad, porque los permisos viven en
las dependencias y llamar al endpoint a mano se las salta.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.services import apariencia as svc

PNG_MINIMO = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01"
    b"\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
VALIDA = {
    "nombre": "Clínica Sonrisa",
    "paleta": "personalizada",
    "color_marca": "#0f766e",
    "tipografia": "Inter",
    "trazo_iconos": "fino",
}


# --- Lo que entra -----------------------------------------------------------


def test_una_apariencia_valida_se_guarda_con_el_color_normalizado():
    ap = svc.validar(VALIDA)
    assert ap.color_marca == "#0F766E"
    assert ap.nombre == "Clínica Sonrisa"


def test_el_nombre_vacio_cae_al_de_la_instalacion():
    assert svc.validar({**VALIDA, "nombre": "   "}).nombre is None


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("color_marca", "azul"),
        ("color_marca", "#12345"),
        ("color_marca", "#2350E6; background:url(x)"),
        ("tipografia", "Comic Sans MS"),
        # El nombre de la tipografía acaba en la URL de Google Fonts: nada
        # fuera de la lista cerrada, ni siquiera "parecido".
        ("tipografia", "Inter&family=Evil"),
        ("trazo_iconos", "extra"),
        ("paleta", "<b>"),
        ("nombre", "x" * 61),
    ],
)
def test_lo_que_no_esta_en_la_lista_no_se_guarda(campo, valor):
    with pytest.raises(svc.AparienciaInvalida):
        svc.validar({**VALIDA, campo: valor})


def test_el_tipo_del_logo_sale_de_los_bytes_y_no_del_nombre():
    assert svc.tipo_de_logo(PNG_MINIMO) == "image/png"
    assert svc.tipo_de_logo(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>') == "image/svg+xml"
    with pytest.raises(svc.AparienciaInvalida):
        svc.tipo_de_logo(b"%PDF-1.7 esto no es una imagen")


@pytest.mark.parametrize(
    "svg",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
        b'<svg><a href="javascript:alert(1)"><rect/></a></svg>',
        b"<svg><foreignObject><iframe/></foreignObject></svg>",
    ],
)
def test_un_svg_con_codigo_dentro_se_rechaza(svg):
    with pytest.raises(svc.AparienciaInvalida):
        svc.tipo_de_logo(svg)


# --- Por HTTP ---------------------------------------------------------------


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


async def _usuarios() -> tuple[str, str, list[uuid.UUID]]:
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.models.user import User

    suf = uuid.uuid4().hex[:8]
    async with db_session() as db:
        admin = User(
            email=f"admin-ap-{suf}@test.local",
            password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
            role="admin",
        )
        operador = User(
            email=f"operador-ap-{suf}@test.local",
            password_hash=hash_password("OtraClaveLargaDeVerdad2026"),
            role="cliente",
        )
        db.add_all([admin, operador])
        await db.commit()
        return (
            create_access_token(subject=str(admin.id), extra={"role": "admin"}),
            create_access_token(subject=str(operador.id), extra={"role": "cliente"}),
            [admin.id, operador.id],
        )


async def _limpiar(ids: list[uuid.UUID]) -> None:
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        await db.execute(
            text("DELETE FROM app_settings WHERE key IN ('apariencia', 'apariencia_logo')")
        )
        for uid in ids:
            await db.execute(text("DELETE FROM audit_log WHERE user_id = :i"), {"i": str(uid)})
            await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(uid)})
        await db.commit()


def _con_cliente(prueba):
    """Monta la app, crea los usuarios, corre la prueba y limpia pase lo que pase."""
    import httpx

    from app.main import app

    async def _run():
        admin, operador, ids = await _usuarios()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                await prueba(
                    c,
                    {"Authorization": f"Bearer {admin}"},
                    {"Authorization": f"Bearer {operador}"},
                )
        finally:
            await _limpiar(ids)

    asyncio.run(_run())


URL = "/api/v1/apariencia"


@con_db
def test_el_login_ve_la_apariencia_sin_haber_entrado():
    async def prueba(c, _admin, _op):
        r = await c.get(URL)
        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert cuerpo["color_marca"] == svc.COLOR_DE_FABRICA
        assert cuerpo["logo_url"] is None
        assert "Figtree" in cuerpo["tipografias"]

    _con_cliente(prueba)


@con_db
def test_un_operador_no_puede_cambiar_la_marca_de_la_instalacion():
    async def prueba(c, _admin, op):
        assert (await c.put(URL, json=VALIDA, headers=op)).status_code == 403
        assert (await c.delete(URL, headers=op)).status_code == 403
        r = await c.put(f"{URL}/logo", files={"fichero": ("l.png", PNG_MINIMO)}, headers=op)
        assert r.status_code == 403
        assert (await c.delete(f"{URL}/logo", headers=op)).status_code == 403
        # Y sin sesión, tampoco.
        assert (await c.put(URL, json=VALIDA)).status_code == 401

    _con_cliente(prueba)


@con_db
def test_lo_que_guarda_el_admin_lo_ve_el_login_y_queda_en_la_auditoria():
    from sqlalchemy import text

    from app.db.session import db_session

    async def prueba(c, admin, _op):
        r = await c.put(URL, json=VALIDA, headers=admin)
        assert r.status_code == 200, r.text
        publico = (await c.get(URL)).json()
        assert publico["nombre"] == "Clínica Sonrisa"
        assert publico["color_marca"] == "#0F766E"
        assert publico["tipografia"] == "Inter"

        async with db_session() as db:
            n = (
                await db.execute(
                    text("SELECT count(*) FROM audit_log WHERE action = 'apariencia.updated'")
                )
            ).scalar_one()
        assert n >= 1

        malo = await c.put(URL, json={**VALIDA, "tipografia": "Papyrus"}, headers=admin)
        assert malo.status_code == 422
        assert "tipografía" in malo.text

    _con_cliente(prueba)


@con_db
def test_el_logo_se_sirve_sin_sesion_y_encerrado_en_su_propia_csp():
    async def prueba(c, admin, _op):
        r = await c.put(f"{URL}/logo", files={"fichero": ("l.png", PNG_MINIMO)}, headers=admin)
        assert r.status_code == 200, r.text
        logo_url = r.json()["logo_url"]
        assert logo_url and "?v=" in logo_url

        img = await c.get(logo_url)
        assert img.status_code == 200
        assert img.content == PNG_MINIMO
        assert img.headers["content-type"] == "image/png"
        assert "sandbox" in img.headers["content-security-policy"]

        # Cambiar el logotipo cambia la URL: el navegador no sirve el viejo.
        otro = PNG_MINIMO + b"\x00"
        r2 = await c.put(f"{URL}/logo", files={"fichero": ("l.png", otro)}, headers=admin)
        assert r2.json()["logo_url"] != logo_url

        grande = b"\x89PNG\r\n\x1a\n" + b"\x00" * svc.LOGO_MAX_BYTES
        r3 = await c.put(f"{URL}/logo", files={"fichero": ("g.png", grande)}, headers=admin)
        assert r3.status_code == 422

        assert (await c.delete(f"{URL}/logo", headers=admin)).json()["logo_url"] is None
        assert (await c.get(f"{URL}/logo")).status_code == 404

    _con_cliente(prueba)


@con_db
def test_restablecer_vuelve_a_la_de_fabrica_con_logo_incluido():
    async def prueba(c, admin, _op):
        await c.put(URL, json=VALIDA, headers=admin)
        await c.put(f"{URL}/logo", files={"fichero": ("l.png", PNG_MINIMO)}, headers=admin)
        r = await c.delete(URL, headers=admin)
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["color_marca"] == svc.COLOR_DE_FABRICA
        assert cuerpo["nombre"] is None
        assert cuerpo["logo_url"] is None

    _con_cliente(prueba)


@con_db
def test_una_apariencia_guardada_rota_no_tumba_el_login():
    from app.db.session import db_session
    from app.services.app_settings import set_app_setting

    async def prueba(c, _admin, _op):
        async with db_session() as db:
            await set_app_setting(svc.SETTING_APARIENCIA, '{"tipografia": "Quitada"', db)
            await db.commit()
        r = await c.get(URL)
        assert r.status_code == 200
        assert r.json()["tipografia"] == "Figtree"

    _con_cliente(prueba)
