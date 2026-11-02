"""El secreto de una herramienta de n8n no sale en claro sin que quede apuntado.

Lo genera el CRM, se guarda cifrado y en el listado solo se ven sus últimos 4
caracteres. Para pegarlo en n8n hay un endpoint aparte que deja constancia de
quién lo vio, y «Regenerar» lo cambia (el viejo deja de valer).
"""
from __future__ import annotations

import pytest

from tests._n8n import admin, cliente, db_disponible, limpiar, url_publica

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


def test_listado_enmascarado_copia_auditada_y_regenerar(monkeypatch):
    import asyncio

    from sqlalchemy import select, text

    from app.db.session import db_session
    from app.models.audit_log import AuditLog

    url_publica(monkeypatch)

    async def _run():
        cab, admin_id = await admin()
        try:
            async with cliente() as c:
                r = await c.post(
                    "/api/v1/admin/n8n/tools",
                    headers=cab,
                    json={"nombre": "secreto_prueba", "descripcion": "x",
                          "url": "https://n8n.ejemplo.mx/webhook/x", "accion": "lectura"},
                )
                assert r.status_code == 201, r.text
                creada = r.json()
                assert "secreto" not in creada

                r = await c.get(f"/api/v1/admin/n8n/tools/{creada['id']}/secreto", headers=cab)
                secreto = r.json()["secreto"]
                assert len(secreto) >= 32 and r.json()["cabecera"] == "X-Omnicanal-Token"
                assert creada["secreto_enmascarado"].endswith(secreto[-4:])

                r = await c.get("/api/v1/admin/n8n/tools", headers=cab)
                assert secreto not in r.text, "el listado devuelve el secreto entero"

                r = await c.post(f"/api/v1/admin/n8n/tools/{creada['id']}/regenerar-secreto", headers=cab)
                assert r.status_code == 200
                r = await c.get(f"/api/v1/admin/n8n/tools/{creada['id']}/secreto", headers=cab)
                assert r.json()["secreto"] != secreto

            async with db_session() as db:
                crudo = (
                    await db.execute(text("SELECT secreto FROM n8n_tools WHERE nombre = 'secreto_prueba'"))
                ).scalar_one()
                acciones = (
                    await db.execute(select(AuditLog.action).where(AuditLog.user_id == admin_id))
                ).scalars().all()
            assert secreto.encode() not in bytes(crudo), "el secreto está en claro en la base"
            assert acciones.count("n8n_tool.secreto_visto") == 2
            assert "n8n_tool.secreto_regenerado" in acciones
        finally:
            await limpiar(admin_id, "secreto_prueba")

    asyncio.run(_run())
