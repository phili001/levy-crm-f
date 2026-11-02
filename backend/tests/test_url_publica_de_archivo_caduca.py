"""El enlace público a un archivo guardado vale una hora y solo para ese archivo.

Zernio y el visitante del chat web bajan los archivos de Sofía sin sesión del
panel. El enlace va firmado: sin la firma no se puede pedir otro archivo ni
alargarle la vida, y caducado da 404 (sin decir si el archivo existe).
"""
from __future__ import annotations

import time

import httpx

from app.services import media_publica


def test_firma_caducidad_y_manipulacion():
    token = media_publica.firmar("foto.jpg", ttl_s=3600)
    assert media_publica.verificar(token) == "foto.jpg"
    assert media_publica.verificar(token, ahora=time.time() + 3601) is None, "un enlace caducado sigue valiendo"

    carga, firma = token.split(".", 1)
    otro = media_publica.firmar("otra.jpg").split(".", 1)[0]
    assert media_publica.verificar(f"{otro}.{firma}") is None, "con la firma de un archivo se baja otro"
    assert media_publica.verificar("basura") is None
    assert media_publica.verificar(f"{carga}.") is None


async def test_el_endpoint_sirve_el_archivo_solo_con_un_enlace_bueno(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "UPLOADS_PATH", str(tmp_path))
    (tmp_path / "abc123.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)

    url = media_publica.url_publica("/uploads/abc123.jpg")
    ruta = url.split("://", 1)[1].split("/", 1)[1]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/" + ruta)
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert r.headers["x-content-type-options"] == "nosniff"

        caducado = media_publica.firmar("abc123.jpg", ttl_s=-1)
        assert (await c.get(f"/api/v1/media/p/{caducado}")).status_code == 404
        fuera = media_publica.firmar("../.env")
        assert (await c.get(f"/api/v1/media/p/{fuera}")).status_code == 404
