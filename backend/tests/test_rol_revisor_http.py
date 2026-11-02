"""Qué puede hacer el rol «revisor», comprobado POR HTTP contra la app real.

Este rol existe para la revisión de la app en Meta: se le da una cuenta del
panel a quien revisa, y esa cuenta no puede ser de administrador. Lo que ve es
la bandeja de Instagram, puede responder en ella y ve de qué cuenta de
Instagram se trata. Nada más.

Se comprueba por HTTP y no llamando a las funciones porque el candado vive en
la dependencia `get_current_user` (lista blanca de rutas) y en el filtro de
canal: llamar al endpoint a mano se los salta y el test pasaría en verde con
todo abierto.

Lo que de verdad importa aquí, y por lo que existe el fichero:
  - No puede ver conversaciones de otros canales (son clientes reales).
  - No puede lanzar la conexión de Instagram: la app guarda UNA cuenta y
    conectar otra dejaría el chatbot de casa sin cuenta.

DB-gated: se salta si no hay Postgres (en CI sí lo hay).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest


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


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="DB no disponible en este entorno (se ejecuta en CI con Postgres)",
)


class _Escenario:
    def __init__(self) -> None:
        self.token = ""
        self.contact_id: uuid.UUID | None = None
        self.conv_ig: uuid.UUID | None = None
        self.conv_wa: uuid.UUID | None = None
        self.user_id: uuid.UUID | None = None


async def _crear_escenario() -> _Escenario:
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.models.contact import Contact, ContactOrigen
    from app.models.conversation import Conversation, ConversationCanal, ConversationStatus
    from app.models.user import User

    e = _Escenario()
    suf = uuid.uuid4().hex[:8]
    async with db_session() as db:
        revisor = User(
            email=f"revisor-{suf}@test.local",
            password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
            nombre="Revisor Meta",
            role="revisor",
        )
        contact = Contact(telefono=f"ig:PSID{suf}", origen=ContactOrigen.instagram)
        db.add_all([revisor, contact])
        await db.flush()
        conv_ig = Conversation(
            contact_id=contact.id,
            canal=ConversationCanal.instagram_dm,
            session_id=f"ig-{suf}",
            status=ConversationStatus.bot,
        )
        conv_wa = Conversation(
            contact_id=contact.id,
            canal=ConversationCanal.whatsapp,
            session_id=f"wa-{suf}",
            status=ConversationStatus.bot,
        )
        db.add_all([conv_ig, conv_wa])
        await db.commit()
        e.token = create_access_token(subject=str(revisor.id), extra={"role": "revisor"})
        e.contact_id, e.conv_ig, e.conv_wa, e.user_id = (
            contact.id,
            conv_ig.id,
            conv_wa.id,
            revisor.id,
        )
    return e


async def _borrar_escenario(e: _Escenario) -> None:
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        if e.contact_id:
            await db.execute(text("DELETE FROM contacts WHERE id = :i"), {"i": str(e.contact_id)})
        if e.user_id:
            await db.execute(text("DELETE FROM audit_log WHERE user_id = :i"), {"i": str(e.user_id)})
            await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(e.user_id)})
        await db.commit()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_el_revisor_solo_llega_a_la_bandeja_de_instagram():
    import httpx

    from app.main import app

    async def _run():
        e = await _crear_escenario()
        transport = httpx.ASGITransport(app=app)
        base = "/api/v1"
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                h = _headers(e.token)

                # --- Vetado ------------------------------------------------
                vetado = [
                    ("GET", f"{base}/contacts", None, "la lista de contactos"),
                    ("GET", f"{base}/contacts/{e.contact_id}", None, "la ficha de un contacto"),
                    ("GET", f"{base}/tags", None, "las etiquetas"),
                    ("GET", f"{base}/admin/users", None, "el área de administración"),
                    ("GET", f"{base}/admin/credentials", None, "las claves de las integraciones"),
                    (
                        "POST",
                        f"{base}/conversations/bulk-archive",
                        {"ids": [str(e.conv_ig)]},
                        "archivar en lote",
                    ),
                    (
                        "POST",
                        f"{base}/conversations/{e.conv_ig}/archive",
                        None,
                        "archivar una conversación",
                    ),
                    (
                        "POST",
                        f"{base}/conversations/{e.conv_ig}/close",
                        None,
                        "cerrar una conversación",
                    ),
                ]
                for metodo, url, body, que in vetado:
                    r = await c.request(metodo, url, headers=h, json=body)
                    assert r.status_code == 403, (
                        f"un revisor puede {que} → {metodo} {url} devolvió {r.status_code}"
                    )

                # --- Lo suyo sí funciona ----------------------------------
                permitido = [
                    ("GET", f"{base}/conversations", None),
                    ("GET", f"{base}/conversations/counts", None),
                    ("GET", f"{base}/conversations/{e.conv_ig}", None),
                    ("GET", f"{base}/conversations/{e.conv_ig}/messages", None),
                    ("POST", f"{base}/conversations/{e.conv_ig}/mark-read", None),
                    ("GET", f"{base}/admin/oauth/instagram/profile", None),
                    ("GET", f"{base}/admin/oauth/instagram/revisor-profile", None),
                ]
                for metodo, url, body in permitido:
                    r = await c.request(metodo, url, headers=h, json=body)
                    assert r.status_code < 400, (
                        f"un revisor NO puede {metodo} {url} → {r.status_code} {r.text[:200]}"
                    )

                # Conectar SU cuenta sí puede: es lo que Meta exige ver en la
                # videograbación. Que eso no eche la cuenta del negocio no
                # depende del permiso sino de dónde acaba el token, y eso lo
                # cubre tests/test_instagram_revisor_conecta.py.
                #
                # 400 es una respuesta legítima aquí (el entorno de pruebas no
                # tiene App ID de Meta); lo que no puede ser es 403.
                r = await c.post(f"{base}/admin/oauth/instagram/start", headers=h)
                assert r.status_code != 403, (
                    "el revisor no puede lanzar el Business Login: la revisión de Meta "
                    "no pasa sin esa pantalla"
                )

                # --- Los otros canales no existen para él ------------------
                # 404 y no 403: tampoco se le confirma que la conversación exista.
                for url in (
                    f"{base}/conversations/{e.conv_wa}",
                    f"{base}/conversations/{e.conv_wa}/messages",
                ):
                    r = await c.get(url, headers=h)
                    assert r.status_code == 404, f"{url} devolvió {r.status_code}"

                # Y pedir otro canal a mano tampoco sirve: se le fija Instagram.
                r = await c.get(f"{base}/conversations", headers=h, params={"canal": "whatsapp"})
                assert r.status_code == 200
                canales = {item["canal"] for item in r.json()["items"]}
                assert canales <= {"instagram_dm"}, f"se le colaron otros canales: {canales}"
        finally:
            await _borrar_escenario(e)

    asyncio.run(_run())
