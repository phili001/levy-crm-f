"""Instagram: perfil de la cuenta conectada (@usuario, nombre y foto).

El panel tiene que enseñar QUÉ cuenta está conectada, no un número. Aquí se
comprueba que se piden los campos correctos a Meta, que se mapean bien y que un
fallo de Meta no deja la tarjeta rota: se devuelve lo que hubiera guardado.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import instagram_oauth as ig_oauth


def _mock_httpx_get(payload: dict, status: int = 200, captured: dict | None = None):
    """AsyncClient falso que responde `payload` y captura los parámetros."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = "error" if status >= 400 else ""
    resp.json.return_value = payload

    async def _get(url, params=None, headers=None):
        if captured is not None:
            captured["url"] = url
            captured["params"] = params
        return resp

    client = MagicMock()
    client.get = AsyncMock(side_effect=_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
async def test_fetch_profile_mapea_usuario_nombre_y_foto():
    captured: dict = {}
    datos = {
        "user_id": "17841400000000000",
        "username": "chatbot",
        "name": "Clínica Ejemplo",
        "profile_picture_url": "https://scontent.cdninstagram.com/foto.jpg",
    }
    with patch("httpx.AsyncClient", return_value=_mock_httpx_get(datos, captured=captured)):
        perfil = await ig_oauth.fetch_profile("TOKEN")

    assert perfil["instagram_username"] == "chatbot"
    assert perfil["instagram_name"] == "Clínica Ejemplo"
    assert perfil["instagram_profile_picture_url"].endswith("foto.jpg")
    assert perfil["instagram_user_id"] == "17841400000000000"
    # Se piden los cuatro campos, y la foto es de ellos: sin ella el panel no
    # puede enseñar el perfil, que es justo lo que exige la revisión de Meta.
    assert "profile_picture_url" in captured["params"]["fields"]
    assert captured["params"]["access_token"] == "TOKEN"


@pytest.mark.asyncio
async def test_fetch_profile_devuelve_vacio_si_meta_falla():
    with patch("httpx.AsyncClient", return_value=_mock_httpx_get({}, status=400)):
        assert await ig_oauth.fetch_profile("TOKEN") == {}


@pytest.mark.asyncio
async def test_refresh_profile_conserva_lo_guardado_si_meta_no_contesta():
    """Meta caído no puede dejar la tarjeta en blanco: manda lo que ya había."""
    guardado = {"instagram_user_id": "123", "instagram_username": "chatbot"}
    with (
        patch.object(ig_oauth, "get_profile_stored", AsyncMock(return_value=guardado)),
        patch.object(ig_oauth, "get_access_token", AsyncMock(return_value="TOKEN")),
        patch.object(ig_oauth, "fetch_profile", AsyncMock(side_effect=RuntimeError("meta"))),
    ):
        assert await ig_oauth.refresh_profile() == guardado


@pytest.mark.asyncio
async def test_refresh_profile_sin_cuenta_conectada():
    with (
        patch.object(ig_oauth, "get_profile_stored", AsyncMock(return_value={})),
        patch.object(ig_oauth, "get_access_token", AsyncMock(return_value=None)),
    ):
        assert await ig_oauth.refresh_profile() == {}


def test_csp_deja_cargar_la_foto_de_perfil():
    """Que el backend devuelva la URL de la foto no basta: el navegador tiene
    que poder pedirla.

    Pasó de verdad — con `img-src 'self' data: blob:` la ficha salía con el
    texto correcto y el hueco de la foto vacío, y los avatares de la bandeja
    caían a iniciales de colores. El CDN de Meta tiene host variable
    (scontent-XXX-N.cdninstagram.com), así que va por comodín.

    De paso, las tipografías: index.html las carga por <link> desde Google, y
    son DOS directivas distintas (la hoja es estilo, los .woff2 son fuente).
    """
    from pathlib import Path

    conf = Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf"
    # Las líneas comentadas se descartan: la cabecera del fichero explica la
    # política en prosa y con un ejemplo, y el test acabaría mirando ahí.
    activas = [
        ln for ln in conf.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")
    ]
    politica = "\n".join(ln for ln in activas if "default-src" in ln)
    assert politica, "no se encontró la política CSP"
    assert "https://*.cdninstagram.com" in politica
    assert "https://*.fbcdn.net" in politica
    assert "font-src 'self' data: https://fonts.gstatic.com" in politica
    assert "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com" in politica
