"""La descarga de media de Instagram sigue redirecciones, pero no a cualquier sitio.

Por qué sigue redirecciones (y los proveedores de WhatsApp no): las URLs de
media que manda el webhook de Instagram son de `lookaside.fbsbx.com`, que no
sirve el fichero — responde un 302 a la CDN real (fbcdn.net / cdninstagram.com).
Con `follow_redirects=False` no se descargaría ni una foto. En WhatsApp no hace
falta: la Graph API ya devuelve la URL directa de la CDN y YCloud sirve el
fichero él mismo, por eso ahí va en False.

El agujero que abría: la comprobación de host anti-SSRF solo mira la URL de
PARTIDA. Siguiendo el salto, un 302 sacaba la descarga fuera de Meta — y en el
reintento por 401/403 el page access token viaja en la propia query string, así
que se lo llevaba puesto. Ahora se comprueba también el host donde se acaba.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.instagram.meta import InstagramProvider


def _cliente_falso(final_url: str, *, cuerpo: bytes = b"datos", status: int = 200):
    """AsyncClient falso cuya respuesta dice haber acabado en `final_url`."""
    resp = MagicMock()
    resp.status_code = status
    resp.url = MagicMock()
    resp.url.host = final_url
    resp.headers = {"content-type": "image/jpeg"}
    resp.aread = AsyncMock(return_value=b"")

    async def _iter():
        yield cuerpo

    resp.aiter_bytes = MagicMock(return_value=_iter())

    stream_ctx = MagicMock()
    stream_ctx.__aenter__ = AsyncMock(return_value=resp)
    stream_ctx.__aexit__ = AsyncMock(return_value=False)

    client = MagicMock()
    client.stream = MagicMock(return_value=stream_ctx)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


async def _sin_ip_privada(_host):
    return False


@pytest.mark.asyncio
async def test_redireccion_dentro_de_meta_se_descarga():
    """lookaside → cdninstagram: el salto legítimo tiene que seguir funcionando."""
    prov = InstagramProvider()
    with (
        patch("app.providers.instagram.meta.resolves_to_private", _sin_ip_privada),
        patch(
            "httpx.AsyncClient",
            return_value=_cliente_falso("scontent-mad1-1.cdninstagram.com"),
        ),
    ):
        contenido, mime = await prov.download_media(
            "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=1", max_bytes=10_000
        )

    assert contenido == b"datos"
    assert mime == "image/jpeg"


@pytest.mark.asyncio
async def test_redireccion_fuera_de_meta_se_corta():
    """El host de partida es de Meta, el de llegada no: se aborta la descarga."""
    prov = InstagramProvider()
    with (
        patch("app.providers.instagram.meta.resolves_to_private", _sin_ip_privada),
        patch("httpx.AsyncClient", return_value=_cliente_falso("atacante.example")),
        pytest.raises(RuntimeError) as exc,
    ):
        await prov.download_media(
            "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=1",
            max_bytes=10_000,
        )

    assert "host no permitido" in str(exc.value)


@pytest.mark.asyncio
async def test_el_host_de_partida_se_sigue_comprobando():
    """La comprobación nueva se SUMA a la de siempre, no la sustituye."""
    prov = InstagramProvider()
    with (
        patch("app.providers.instagram.meta.resolves_to_private", _sin_ip_privada),
        pytest.raises(RuntimeError),
    ):
        await prov.download_media("https://atacante.example/foto.jpg", max_bytes=10_000)


def test_whatsapp_no_sigue_redirecciones():
    """Los otros dos proveedores van en False, y eso es correcto: no lo necesitan."""
    import inspect

    from app.providers.whatsapp import meta as wa_meta
    from app.providers.whatsapp import ycloud

    assert "follow_redirects=False" in inspect.getsource(wa_meta.MetaCloudProvider._download)
    assert "follow_redirects=False" in inspect.getsource(ycloud.YCloudProvider._download_file)
