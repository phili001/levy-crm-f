"""`GET /admin/channels/estado`: lo que mira `omni.py connect` para saber si el
canal quedó en verde (Fase 10.2).

Tiene que decir, por canal, si está conectado y en pausa y cuándo entró el
último mensaje, junto con la hora del SERVIDOR: el instalador compara con ella
y no con el reloj de la computadora de quien instala. Solo admin, como el resto
de Conexiones.
"""
from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from tests._fichas import borrar, conversacion_y_ficha, entra, telefono_mx
from tests.test_voice_gating import pytestmark_db


@pytest.fixture
async def http():
    from app.main import app
    from tests.test_agenda_desde_el_panel import _borrar_usuarios, _usuarios

    admin, operador, revisor, usuarios = await _usuarios()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, admin, operador, revisor
    await _borrar_usuarios(usuarios)


@pytestmark_db
@pytest.mark.asyncio
async def test_dice_la_hora_del_servidor_y_el_ultimo_mensaje_de_cada_canal(http):
    c, admin, _op, _rev = http
    antes = (await c.get("/api/v1/admin/channels/estado", headers=admin)).json()
    desde = datetime.fromisoformat(antes["ahora"])
    assert isinstance(antes["ahora_ms"], int)
    assert {x["canal"] for x in antes["channels"]} >= {"whatsapp", "instagram_dm", "email", "web"}

    _conv, ficha = await conversacion_y_ficha(await entra(telefono_mx(), "prueba"))
    try:
        despues = (await c.get("/api/v1/admin/channels/estado", headers=admin)).json()
        whatsapp = next(x for x in despues["channels"] if x["canal"] == "whatsapp")
        assert datetime.fromisoformat(whatsapp["last_inbound_at"]) > desde
    finally:
        await borrar(ficha)


@pytestmark_db
@pytest.mark.asyncio
async def test_solo_lo_ve_un_administrador(http):
    c, _admin, operador, revisor = http
    assert (await c.get("/api/v1/admin/channels/estado", headers=operador)).status_code == 403
    assert (await c.get("/api/v1/admin/channels/estado", headers=revisor)).status_code == 403
