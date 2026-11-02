"""Instalación en un clic: el dueño crea el administrador con el código de arranque.

En Coolify el compose genera sus propios secretos y no trae administrador: el
panel lo pide en el primer arranque. Lo que se comprueba:
  - sin código de arranque no hay primer arranque (instalaciones de siempre);
  - con código y sin administrador, el alta pide el código exacto, aplica la
    política de contraseñas, devuelve la llave de cifrado UNA vez y deja una
    sesión que ya sirve (sin «cambia tu contraseña», ni al entrar ni en el
    checklist de Inicio);
  - con un administrador ya creado, el alta no hace nada;
  - producción arranca con el código y sin INITIAL_ADMIN_*, y el seed no crea el
    administrador de ejemplo.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from tests.test_voice_gating import pytestmark_db

CODIGO = "codigo-de-arranque-de-prueba-2026"


def test_produccion_arranca_con_el_codigo_y_sin_admin_en_variables():
    from app.core.config import Settings

    comunes = {
        "APP_ENV": "production",
        "JWT_SECRET": "j" * 64,
        "ENCRYPTION_KEY": "k" * 64,
        "APP_BASE_URL": "https://crm.ejemplo.mx",
        "FRONTEND_BASE_URL": "https://crm.ejemplo.mx",
        "INITIAL_ADMIN_EMAIL": "",
        "INITIAL_ADMIN_PASSWORD": "",
    }
    Settings(**comunes, SETUP_CODE=CODIGO).validate_production()  # no lanza

    with pytest.raises(RuntimeError) as sin_codigo:
        Settings(**comunes, SETUP_CODE="").validate_production()
    assert "INITIAL_ADMIN_EMAIL" in str(sin_codigo.value)

    with pytest.raises(RuntimeError) as corto:
        Settings(**comunes, SETUP_CODE="corto").validate_production()
    assert "SETUP_CODE" in str(corto.value)


def test_con_codigo_el_seed_no_crea_el_administrador_de_ejemplo(monkeypatch):
    """Con los valores de fábrica, crear admin@local sería justo lo que el
    código de arranque evita."""
    from app.core.config import settings
    from app.scripts.seed import _admin_en_variables

    monkeypatch.setattr(settings, "INITIAL_ADMIN_EMAIL", "admin@local")
    monkeypatch.setattr(settings, "INITIAL_ADMIN_PASSWORD", "ChangeMe123!")
    assert not _admin_en_variables()
    monkeypatch.setattr(settings, "INITIAL_ADMIN_EMAIL", "")
    monkeypatch.setattr(settings, "INITIAL_ADMIN_PASSWORD", "")
    assert not _admin_en_variables()
    monkeypatch.setattr(settings, "INITIAL_ADMIN_EMAIL", "ana@clinica.mx")
    monkeypatch.setattr(settings, "INITIAL_ADMIN_PASSWORD", "UnaClaveLargaDeVerdad2026")
    assert _admin_en_variables(), "con un admin propio en las variables, se crea como siempre"


def test_sin_admin_en_variables_el_contacto_push_es_la_url_del_crm(monkeypatch):
    """Apple rechaza el push con un contacto que no sea mailto: o https válido."""
    from app.core.config import settings
    from app.services.web_push import vapid_subject

    monkeypatch.setattr(settings, "VAPID_SUBJECT", "")
    monkeypatch.setattr(settings, "INITIAL_ADMIN_EMAIL", "")
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://crm.ejemplo.mx")
    assert vapid_subject() == "https://crm.ejemplo.mx"


async def _apagar_administradores() -> list[str]:
    """La base de pruebas tiene administradores de otras pruebas: para probar
    «sin administrador» se desactivan y se devuelven sus ids para restaurarlos."""
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        filas = await db.execute(
            text("UPDATE users SET activo = false WHERE role = 'admin' AND activo RETURNING id")
        )
        ids = [str(f[0]) for f in filas]
        await db.commit()
    return ids


async def _restaurar(ids: list[str], email_creado: str) -> None:
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        await db.execute(
            text("DELETE FROM audit_log WHERE user_id IN (SELECT id FROM users WHERE email = :e)"),
            {"e": email_creado},
        )
        await db.execute(text("DELETE FROM users WHERE email = :e"), {"e": email_creado})
        if ids:
            await db.execute(
                text("UPDATE users SET activo = true WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": ids},
            )
        await db.commit()


@pytestmark_db
@pytest.mark.asyncio
async def test_sin_codigo_no_hay_primer_arranque(monkeypatch):
    from app.core.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "SETUP_CODE", "")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        assert (await c.get("/api/v1/primer-arranque")).json() == {"necesita_admin": False}
        r = await c.post("/api/v1/primer-arranque", json={
            "codigo": "lo-que-sea", "email": "ana@clinica.mx",
            "password": "UnaClaveLargaDeVerdad2026",
        })
        assert r.status_code == 404


@pytestmark_db
@pytest.mark.asyncio
async def test_con_codigo_el_dueno_crea_el_admin_una_sola_vez(monkeypatch):
    from app.core.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "SETUP_CODE", CODIGO)
    email = f"dueno-{uuid.uuid4().hex[:8]}@clinica-pruebas.mx"
    alta = {"codigo": CODIGO, "email": email, "password": "UnaClaveLargaDeVerdad2026",
            "nombre": "Ana"}
    apagados = await _apagar_administradores()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            assert (await c.get("/api/v1/primer-arranque")).json() == {"necesita_admin": True}

            r = await c.post("/api/v1/primer-arranque", json={**alta, "codigo": "otro-codigo"})
            assert r.status_code == 403
            assert "SERVICE_PASSWORD_ARRANQUE" in r.json()["detail"], "dice dónde está el código"

            r = await c.post("/api/v1/primer-arranque", json={**alta, "password": "corta"})
            assert r.status_code == 422, "la misma política que el resto del panel"

            r = await c.post("/api/v1/primer-arranque", json=alta)
            assert r.status_code == 201, r.text
            cuerpo = r.json()
            assert cuerpo["user"]["role"] == "admin"
            assert cuerpo["user"]["debe_cambiar_password"] is False
            assert cuerpo["llave_cifrado"] == settings.ENCRYPTION_KEY

            sesion = {"Authorization": f"Bearer {cuerpo['access_token']}"}
            assert (await c.get("/api/v1/auth/me", headers=sesion)).status_code == 200
            inicio = (await c.get("/api/v1/admin/onboarding", headers=sesion)).json()
            paso = next(p for p in inicio["steps"] if p["key"] == "password")
            assert paso["done"], "la acaba de elegir: el Inicio no le pide cambiarla"

            assert (await c.get("/api/v1/primer-arranque")).json() == {"necesita_admin": False}
            r = await c.post("/api/v1/primer-arranque", json={**alta, "email": "otro@x.mx"})
            assert r.status_code == 409, "con un administrador, el alta ya no hace nada"
    finally:
        await _restaurar(apagados, email)
