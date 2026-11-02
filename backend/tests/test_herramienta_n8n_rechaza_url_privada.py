"""Una herramienta de n8n no puede apuntar dentro del servidor.

El CRM hace la llamada desde el servidor: si aceptara `http://10.0.0.5` o
`https://localhost`, cualquiera con acceso al panel podría usarlo para
llegar a servicios internos (Redis, la base de datos, el panel de Coolify).
Solo HTTPS y hosts públicos, al guardar y otra vez en cada llamada.
"""
from __future__ import annotations

import pytest

from tests._n8n import admin, cliente, db_disponible, herramienta, limpiar, n8n_falso

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


def test_al_guardar_solo_https_y_publica():
    import asyncio

    async def _run():
        cab, admin_id = await admin()
        try:
            async with cliente() as c:
                for url in ("http://n8n.ejemplo.mx/webhook/x", "https://localhost/webhook/x",
                            "https://127.0.0.1/webhook/x", "https://10.0.0.5/webhook/x"):
                    r = await c.post(
                        "/api/v1/admin/n8n/tools",
                        headers=cab,
                        json={"nombre": "interna_x", "descripcion": "x", "url": url, "accion": "lectura"},
                    )
                    assert r.status_code == 422, (url, r.text)
                    assert "URL del webhook" in r.json()["detail"]
        finally:
            await limpiar(admin_id, "interna_x")

    asyncio.run(_run())


async def test_en_cada_llamada_tambien(monkeypatch):
    """Una URL guardada que hoy resuelve a una IP interna no se llama."""
    from app.services.n8n_tools import llamar_a_n8n

    n8n = n8n_falso(monkeypatch, (200, {"resultado": "no debería llegar"}))
    hecho = await llamar_a_n8n(herramienta(url="https://127.0.0.1/webhook/x"), {"numero": "1"}, {})
    assert not hecho["ok"] and "URL rechazada" in hecho["error"]
    assert n8n.peticiones == []
