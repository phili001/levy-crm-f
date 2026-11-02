"""Enlaces públicos y firmados a un archivo guardado (Fase 11, SPEC-06).

Los archivos del volumen se sirven solo con sesión del panel (`/uploads/…`).
Dos que no tienen sesión necesitan bajarlos: Zernio, que manda a Instagram o
Messenger lo que encuentre en una URL, y el visitante del chat de la web.

Un enlace lleva el nombre del archivo y cuándo caduca, firmados con una llave
derivada de `JWT_SECRET`. Sin la firma no se puede pedir otro archivo ni
alargar la caducidad, y no hay listado de nada. Caducan en una hora por defecto:
Zernio lo baja al momento, y el widget pide enlaces nuevos al leer el historial.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from app.core.config import settings

TTL_POR_DEFECTO_S = 3600
_RUTA = "/api/v1/media/p/"


def _llave() -> bytes:
    # Una llave propia para esto, derivada: un token de estos no sirve como
    # nada más, y rotar JWT_SECRET los invalida todos.
    return hmac.new(settings.JWT_SECRET.encode(), b"media-publica", hashlib.sha256).digest()


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def _de_b64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def firmar(archivo: str, *, ttl_s: int = TTL_POR_DEFECTO_S, ahora: float | None = None) -> str:
    """Token para `archivo` (el nombre guardado, sin ruta) que caduca en `ttl_s`."""
    carga = _b64(json.dumps({"f": archivo, "e": int((ahora or time.time()) + ttl_s)}).encode())
    firma = _b64(hmac.new(_llave(), carga.encode(), hashlib.sha256).digest())
    return f"{carga}.{firma}"


def verificar(token: str, *, ahora: float | None = None) -> str | None:
    """El nombre del archivo si el token es bueno y no caducó; si no, None."""
    try:
        carga, firma = token.split(".", 1)
        esperada = _b64(hmac.new(_llave(), carga.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(firma, esperada):
            return None
        datos = json.loads(_de_b64(carga))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(datos.get("e") or 0) < (ahora or time.time()):
        return None
    archivo = str(datos.get("f") or "")
    return archivo or None


def url_publica(ruta_guardada: str, *, ttl_s: int = TTL_POR_DEFECTO_S) -> str:
    """De `/uploads/<archivo>` (lo que guarda `messages.media_url`) a un enlace público."""
    archivo = ruta_guardada.rsplit("/", 1)[-1]
    return f"{settings.APP_BASE_URL.rstrip('/')}{_RUTA}{firmar(archivo, ttl_s=ttl_s)}"
