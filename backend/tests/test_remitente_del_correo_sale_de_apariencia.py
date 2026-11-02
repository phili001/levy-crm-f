"""Los correos de Sofía llegan con el nombre de la clínica, no con el del producto.

El remitente salía de `APP_NAME`, que en la instalación en un clic vale
«CRM Omnicanal». El paciente veía «CRM Omnicanal <contacto@clinica.mx>» aunque
la clínica hubiera puesto su nombre en Apariencia. Ahora manda Apariencia y,
si está vacía, el de la instalación.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text


def _db_available() -> bool:
    try:
        async def _check():
            from app.db.session import db_session

            async with db_session() as db:
                await db.execute(text("SELECT 1"))

        asyncio.run(_check())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="DB no disponible en este entorno (se ejecuta en CI con Postgres)",
)


def test_el_remitente_es_el_nombre_de_apariencia_y_si_no_hay_el_de_la_instalacion(monkeypatch):
    from app.core.config import settings
    from app.db.session import db_session
    from app.services import apariencia
    from app.services.correo_smtp import _nombre_remitente, construir_respuesta

    monkeypatch.setattr(settings, "APP_NAME", "CRM Omnicanal")

    async def _run():
        async with db_session() as db:
            antes = await apariencia.leer(db)
        try:
            async with db_session() as db:
                con_nombre = apariencia.validar({**antes.a_dict(), "nombre": "Clínica Sonrisa Perfecta"})
                await apariencia.guardar(db, con_nombre)
                await db.commit()
            nombre = await _nombre_remitente()
            assert nombre == "Clínica Sonrisa Perfecta", "el remitente no sale de Apariencia"
            msg = construir_respuesta(
                desde="contacto@sonrisaperfecta.mx",
                para="paciente@ejemplo.com",
                asunto="Cita",
                cuerpo="Hola",
                in_reply_to=None,
                references=None,
                nombre=nombre,
            )
            assert "Clínica Sonrisa Perfecta" in str(msg["From"])
            assert "CRM Omnicanal" not in str(msg["From"])

            async with db_session() as db:
                sin_nombre = apariencia.validar({**antes.a_dict(), "nombre": None})
                await apariencia.guardar(db, sin_nombre)
                await db.commit()
            assert await _nombre_remitente() == "CRM Omnicanal"
        finally:
            async with db_session() as db:
                await apariencia.guardar(db, antes)
                await db.commit()

    asyncio.run(_run())
