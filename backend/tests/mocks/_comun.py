"""Piezas que comparten los simuladores: claves de prueba, firmas y envío.

Las firmas se calculan igual que el proveedor real (y que el verificador del
CRM en `app/providers/`), para que un webhook del simulador pase por la puerta
de verdad y no por un atajo de pruebas.
"""
from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import os
import secrets
import time

import httpx

# Una clave que termina así hace que el simulador conteste como el proveedor
# ante una clave mala (401). Sirve para probar el camino infeliz sin tener que
# configurar nada más.
SUFIJO_CLAVE_INVALIDA = "-invalid"

# Sello por arranque: sin él, reiniciar el simulador vuelve a emitir los mismos
# ids y chocan con los UNIQUE de una corrida anterior en la BD de pruebas. No es
# un fallo del CRM (la idempotencia hace su trabajo), pero confunde.
_ARRANQUE = secrets.token_hex(3)
_contador = itertools.count(1)


def nuevo_id(prefijo: str) -> str:
    return f"{prefijo}.{_ARRANQUE}.{next(_contador)}"


def clave_invalida(clave: str | None) -> bool:
    return not clave or clave.endswith(SUFIJO_CLAVE_INVALIDA)


def cuerpo_json(datos: object) -> bytes:
    """El cuerpo EXACTO que se firma y se envía. Firmar un dict y enviar otro
    serializado distinto es la forma clásica de que la firma no cuadre."""
    return json.dumps(datos, ensure_ascii=False, separators=(",", ":")).encode()


def firma_ycloud(secreto: str, cuerpo: bytes, ts: int | None = None) -> str:
    """`YCloud-Signature: t=<unix>,s=<hex>` con HMAC-SHA256 sobre `<t>.<cuerpo>`."""
    t = str(ts if ts is not None else int(time.time()))
    s = hmac.new(secreto.encode(), f"{t}.".encode() + cuerpo, hashlib.sha256).hexdigest()
    return f"t={t},s={s}"


def firma_retell(api_key: str, cuerpo: bytes, ts_ms: int | None = None) -> str:
    """`X-Retell-Signature: v=<ms>,d=<hex>` con HMAC-SHA256(api_key, cuerpo + ms)."""
    v = str(ts_ms if ts_ms is not None else int(time.time() * 1000))
    d = hmac.new(api_key.encode(), cuerpo + v.encode(), hashlib.sha256).hexdigest()
    return f"v={v},d={d}"


async def entregar(url: str, cuerpo: bytes, cabeceras: dict[str, str]) -> dict:
    """POST del webhook al CRM. Devuelve lo que contestó, para que la prueba
    compruebe el código (200 bueno, 401 firma mala) sin adivinar."""
    async with httpx.AsyncClient(timeout=15.0) as cliente:
        r = await cliente.post(
            url, content=cuerpo, headers={"Content-Type": "application/json", **cabeceras}
        )
    try:
        respuesta: object = r.json()
    except ValueError:
        respuesta = r.text[:500]
    return {"status_code": r.status_code, "respuesta": respuesta}


def entorno(nombre: str, por_defecto: str = "") -> str:
    return os.environ.get(nombre, por_defecto).strip()
