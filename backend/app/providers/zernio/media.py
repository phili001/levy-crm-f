"""Descarga de adjuntos que llegan por Zernio (fotos, audios, documentos).

En Instagram y Messenger la URL del adjunto es de la CDN de Meta y CADUCA. Por
eso se intenta primero la URL del evento (que suele servir si se baja al
momento) y, si ya no vale, se le pide a Zernio una nueva con
`resolve_attachment`, que es lo único que no caduca (`messageId` + índice).

Mismas defensas que `providers/instagram/meta.py::_download_from_meta` y la
descarga de YCloud (SEC-8): solo hosts de la lista, que no resuelvan a una IP
interna, comprobando también el host donde acaba una redirección, y con tope de
bytes aunque el servidor no mande `Content-Length`. Aquí nunca viaja un token:
la URL de la CDN no lo pide y la llamada a Zernio va por su cliente.
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx

from app.core.logging import get_logger
from app.core.url_guard import resolves_to_private

logger = get_logger(__name__)

HOSTS_PERMITIDOS = (
    "fbcdn.net",
    "fbsbx.com",
    "cdninstagram.com",
    "zernio.com",
)


def host_permitido(host: str) -> bool:
    host = (host or "").lower()
    return bool(host) and any(host == h or host.endswith("." + h) for h in HOSTS_PERMITIDOS)


class DescargaBloqueada(RuntimeError):
    """La URL no es de un host permitido o apunta a la red interna."""


async def descargar(
    url: str, *, max_bytes: int, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[bytes, str]:
    """(bytes, mime) de un adjunto. Lanza si no se puede o no se debe bajar."""
    host = (urlparse(url).hostname or "").lower()
    if not host_permitido(host):
        raise DescargaBloqueada(f"host no permitido: {host or '?'}")
    if transport is None and await resolves_to_private(host):
        raise DescargaBloqueada(f"el host {host} apunta a una IP interna")

    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=True, transport=transport
    ) as client:
        async with client.stream("GET", url) as r:
            final = (r.url.host or "").lower()
            if not host_permitido(final):
                await r.aread()
                raise DescargaBloqueada(f"redirección a host no permitido: {final or '?'}")
            if r.status_code >= 400:
                await r.aread()
                raise RuntimeError(f"HTTP {r.status_code} al descargar el adjunto")
            cl = r.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > max_bytes:
                raise ValueError(f"adjunto demasiado grande: {cl} > {max_bytes}")
            mime = r.headers.get("content-type", "application/octet-stream")
            partes: list[bytes] = []
            total = 0
            async for trozo in r.aiter_bytes():
                total += len(trozo)
                if total > max_bytes:
                    raise ValueError(f"el adjunto excede {max_bytes} bytes")
                partes.append(trozo)
    return b"".join(partes), mime
