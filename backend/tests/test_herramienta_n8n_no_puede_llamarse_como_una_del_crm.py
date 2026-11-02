"""El panel no deja guardar una herramienta de n8n que confundiría a Sofía.

Una con el nombre de una del CRM (`consultar_kb`, `derivar_humano`…) sería la
misma para el modelo; dos con el mismo nombre, también. Y un nombre con
espacios o acentos no lo acepta el modelo como nombre de herramienta.
"""
from __future__ import annotations

import pytest

from tests._n8n import admin, cliente, db_disponible, limpiar, url_publica

pytestmark = pytest.mark.skipif(not db_disponible(), reason="DB no disponible (se ejecuta en CI con Postgres)")


def _cuerpo(**campos) -> dict:
    datos = {
        "nombre": "buscar_pedido_prueba",
        "descripcion": "Busca un pedido por su número.",
        "parametros": [{"clave": "numero", "tipo": "texto", "descripcion": "El número", "obligatorio": True}],
        "url": "https://n8n.ejemplo.mx/webhook/pedidos",
        "accion": "lectura",
    }
    datos.update(campos)
    return datos


def test_nombres_reservados_repetidos_o_mal_escritos(monkeypatch):
    import asyncio

    url_publica(monkeypatch)

    async def _run():
        cab, admin_id = await admin()
        try:
            async with cliente() as c:
                r = await c.post("/api/v1/admin/n8n/tools", headers=cab, json=_cuerpo(nombre="consultar_kb"))
                assert r.status_code == 422 and "ya es una herramienta del CRM" in r.text

                r = await c.post("/api/v1/admin/n8n/tools", headers=cab, json=_cuerpo(nombre="Buscar pedido"))
                assert r.status_code == 422 and "minúsculas" in r.text

                r = await c.post("/api/v1/admin/n8n/tools", headers=cab, json=_cuerpo())
                assert r.status_code == 201, r.text
                r = await c.post("/api/v1/admin/n8n/tools", headers=cab, json=_cuerpo())
                assert r.status_code == 409, "dos herramientas con el mismo nombre"

                malos = [
                    _cuerpo(nombre="sin_descripcion_x", descripcion=""),
                    _cuerpo(nombre="clave_mala_x", parametros=[{"clave": "Número", "tipo": "texto"}]),
                    _cuerpo(nombre="opciones_vacias_x", parametros=[{"clave": "talla", "tipo": "opciones"}]),
                    _cuerpo(nombre="espera_larga_x", espera_s=60),
                ]
                for malo in malos:
                    r = await c.post("/api/v1/admin/n8n/tools", headers=cab, json=malo)
                    assert r.status_code == 422, (malo["nombre"], r.text)
        finally:
            await limpiar(admin_id, "buscar_pedido_prueba")

    asyncio.run(_run())
