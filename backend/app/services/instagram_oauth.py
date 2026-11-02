"""OAuth 2.0 con Instagram Business Login (F8c).

Flujo de Instagram with Instagram Login (la API moderna, sin Facebook Page):

  1. `build_auth_url(state)` → URL de Instagram con los scopes IG.
  2. Usuario acepta en instagram.com → redirige a /oauth/instagram/callback.
  3. `exchange_code_for_short_lived` (1h de duración).
  4. `exchange_short_for_long_lived` (60 días, refrescable).
  5. Guardamos token long-lived cifrado en ExternalAPI(provider=instagram_business).
  6. En cada uso, `get_access_token()` refresca si está cerca de expirar.
  7. Para mandar mensajes: `POST https://graph.instagram.com/v22.0/me/messages`.

Docs: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.encryption import get_encryption_service
from app.core.logging import get_logger
from app.db.session import db_session
from app.models.external_api import ExternalAPI

logger = get_logger(__name__)

PROVIDER = "instagram_business"

# Dónde se guarda cada token. La cuenta del negocio —la que recibe los DM y con
# la que se contesta— vive en la fila `name == PROVIDER`, y es la única que mira
# el runtime.
#
# Quien revisa la app en Meta tiene que poder hacer el Business Login entero con
# SU cuenta (es lo que exige el guion de la videograbación), y esta app guarda
# una sola cuenta: si su token se guardara en la fila de casa, echaría a la
# nuestra y dejarían de entrar mensajes hasta reconectar a mano. Por eso el suyo
# va a una fila APARTE, con el mismo provider y otro `name`. Nada del runtime la
# lee: `get_access_token()` y `get_instagram_user_id()` filtran por
# `name == PROVIDER`, así que ese token no envía, no refresca la conexión del
# canal ni toca el webhook. Solo sirve para pintarle su propio perfil.
REVISOR_NAME = "instagram_business_revisor"

# Marcador que el endpoint de arranque deja en Redis junto al `state` y que el
# callback lee para saber en qué fila guardar. Va por Redis a propósito: el
# navegador solo maneja el `state`, así que nadie puede pedir "guárdalo en la
# fila de casa" desde fuera.
STATE_MARKER = PROVIDER
STATE_MARKER_REVISOR = f"{PROVIDER}:revisor"


def row_name_for_marker(marker: str) -> str:
    """Fila de destino según el marcador del `state`."""
    return REVISOR_NAME if marker == STATE_MARKER_REVISOR else PROVIDER


IG_AUTH_BASE = "https://www.instagram.com/oauth/authorize"
IG_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
IG_GRAPH_BASE = "https://graph.instagram.com"

# Solo lo que la app usa de verdad y lo que Meta tiene aprobado en la revisión:
# leer la cuenta y gestionar los DM. No se pide manage_comments —la app no toca
# comentarios por ningún lado— y pedir un permiso que no está en la revisión hace
# que Instagram corte el login con un error de scope invalidado.
SCOPES = [
    "instagram_business_basic",
    "instagram_business_manage_messages",
]


def _cuerpo_del_token(data: dict) -> dict:
    """Saca el token de la respuesta, venga envuelta o plana.

    Instagram Business Login contesta al canje del `code` con el token dentro de
    una lista: {"data": [{"access_token": ..., "user_id": ..., "permissions": ...}]}.
    El endpoint del long-lived, en cambio, lo devuelve plano. Leer siempre
    `data["access_token"]` funciona en el segundo y revienta con KeyError en el
    primero, que es justo el paso por el que pasa todo el que conecta su cuenta.
    """
    envuelto = data.get("data") if isinstance(data, dict) else None
    if isinstance(envuelto, list) and envuelto and isinstance(envuelto[0], dict):
        return envuelto[0]
    return data if isinstance(data, dict) else {}


def _access_token(data: dict, paso: str) -> tuple[str, dict]:
    """Devuelve (token, cuerpo) o explica qué contestó Meta.

    Sin esto, un cambio de formato o un error con HTTP 200 sale como KeyError
    pelado y quien conecta solo ve "no se pudo completar la conexión".
    """
    cuerpo = _cuerpo_del_token(data)
    token = cuerpo.get("access_token")
    if not token:
        logger.error("ig.token.sin_access_token", paso=paso, claves=list(cuerpo.keys()))
        raise RuntimeError(
            f"Instagram no devolvió access_token en el paso '{paso}'. "
            f"Respuesta: {json.dumps(data)[:200]}"
        )
    return str(token), cuerpo


@dataclass
class IGTokens:
    access_token: str
    user_id: str  # Instagram Business User ID
    expires_at: datetime
    scope: str


def _redirect_uri() -> str:
    if settings.INSTAGRAM_OAUTH_REDIRECT_URI:
        return settings.INSTAGRAM_OAUTH_REDIRECT_URI
    base = settings.APP_BASE_URL.rstrip("/")
    return f"{base}/api/v1/oauth/instagram/callback"


async def _get_oauth_app_credentials() -> tuple[str | None, str | None]:
    """Lee client_id/secret de la tabla credentials (panel) con fallback a env."""
    from app.services.credentials import get_credential
    client_id = (
        await get_credential("instagram_oauth_client_id")
        or settings.INSTAGRAM_OAUTH_CLIENT_ID
    )
    client_secret = (
        await get_credential("instagram_oauth_client_secret")
        or settings.INSTAGRAM_OAUTH_CLIENT_SECRET
    )
    return client_id or None, client_secret or None


async def build_auth_url(state: str) -> str:
    client_id, _ = await _get_oauth_app_credentials()
    if not client_id:
        raise RuntimeError(
            "Instagram OAuth no configurado. Ve a /admin/credentials y rellena "
            "instagram_oauth_client_id e instagram_oauth_client_secret (App ID + App Secret de Meta)."
        )
    params = {
        "client_id": client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": ",".join(SCOPES),
        "state": state,
        # Pide las credenciales SIEMPRE, aunque el navegador ya tenga la sesión
        # de Instagram abierta. Sin esto, quien ya está dentro solo ve una
        # pantalla corta de "continuar" y nunca la de permisos completa — que es
        # justo lo que Meta exige ver en la videograbación de la revisión de la
        # app. Conectar es una acción de una vez cada 60 días: teclear la clave
        # no molesta a nadie y evita repetir la grabación.
        # Docs: developers.facebook.com/docs/instagram-platform/
        #       instagram-api-with-instagram-login/business-login
        "force_reauth": "true",
    }
    return f"{IG_AUTH_BASE}?{urlencode(params)}"


def generate_state() -> str:
    return secrets.token_urlsafe(32)


async def exchange_code(code: str) -> IGTokens:
    """Intercambia el code por un token short-lived (1h) y luego lo cambia
    por uno long-lived (60 días) en un solo paso."""
    client_id, client_secret = await _get_oauth_app_credentials()
    if not (client_id and client_secret):
        raise RuntimeError("Credenciales Instagram OAuth no configuradas")

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1: code → short-lived (form-urlencoded)
        r = await client.post(
            IG_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": _redirect_uri(),
                "code": code,
            },
        )
        if r.status_code >= 400:
            logger.error("ig.token.exchange_failed", status=r.status_code, body=r.text[:300])
            r.raise_for_status()
        short_token, short = _access_token(r.json(), "code→short-lived")
        user_id = str(short.get("user_id", ""))
        # Lo que Meta ha concedido de verdad, que no tiene por qué ser lo pedido
        # (el usuario puede desmarcar permisos en la pantalla de autorización).
        concedidos = str(short.get("permissions") or "") or ",".join(SCOPES)

        # Step 2: short → long-lived (60 días)
        r = await client.get(
            f"{IG_GRAPH_BASE}/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": client_secret,
                "access_token": short_token,
            },
        )
        if r.status_code >= 400:
            logger.error("ig.token.long_lived_failed", status=r.status_code, body=r.text[:300])
            r.raise_for_status()
        long_token, long = _access_token(r.json(), "short→long-lived")

    expires_in = int(long.get("expires_in", 60 * 24 * 3600))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60 * 60)
    return IGTokens(
        access_token=long_token,
        user_id=user_id,
        expires_at=expires_at,
        scope=concedidos,
    )


async def refresh_access_token(token: str) -> IGTokens:
    """Refresca un long-lived token antes de que caduque. Solo funciona si el
    token tiene al menos 24h de vida usada (Instagram lo exige)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{IG_GRAPH_BASE}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
        )
        if r.status_code >= 400:
            logger.error("ig.token.refresh_failed", status=r.status_code, body=r.text[:300])
            r.raise_for_status()
        token, data = _access_token(r.json(), "refresh")
    expires_in = int(data.get("expires_in", 60 * 24 * 3600))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60 * 60)
    return IGTokens(
        access_token=token,
        user_id="",  # se conserva del save anterior
        expires_at=expires_at,
        scope="",  # ídem: refrescar no cambia los permisos concedidos
    )


async def save_tokens(tokens: IGTokens, *, name: str = PROVIDER) -> None:
    """Guarda el token cifrado. `name` elige la fila: la de casa (por defecto) o
    la del revisor de Meta (`REVISOR_NAME`), que no toca a la de casa."""
    enc = get_encryption_service()
    async with db_session() as db:
        existing = (
            await db.execute(
                select(ExternalAPI).where(
                    ExternalAPI.provider == PROVIDER, ExternalAPI.name == name
                )
            )
        ).scalar_one_or_none()
        # Un refresco no trae ni user_id ni permisos: se conserva lo que ya
        # había en vez de borrarlo.
        scope = tokens.scope
        if not scope and existing:
            scope = (existing.extra or {}).get("scope", "")
        blob = {
            "access_token": tokens.access_token,
            "expires_at": tokens.expires_at.isoformat(),
            "scope": scope,
        }
        encrypted = enc.encrypt(json.dumps(blob))
        # Mantenemos el user_id en `extra` para que el provider lo lea sin
        # tener que descifrar el blob cada vez.
        extra_update = {
            "connected_at": datetime.now(timezone.utc).isoformat(),
            "scope": scope,
        }
        if tokens.user_id:
            extra_update["instagram_user_id"] = tokens.user_id

        if existing:
            existing.credentials_encrypted = encrypted
            existing.is_active = True
            existing.extra = {**(existing.extra or {}), **extra_update}
        else:
            db.add(
                ExternalAPI(
                    provider=PROVIDER,
                    name=name,
                    credentials_encrypted=encrypted,
                    is_active=True,
                    extra=extra_update,
                )
            )
        await db.commit()


async def get_access_token(*, name: str = PROVIDER) -> str | None:
    """Devuelve un access_token válido. Si está cerca de expirar, refresca.

    Sin `name` devuelve el de casa: es el que usa TODO el runtime (enviar,
    perfil del panel, ventana de mensajería). El del revisor hay que pedirlo a
    propósito.
    """
    enc = get_encryption_service()
    async with db_session() as db:
        api = (
            await db.execute(
                select(ExternalAPI).where(
                    ExternalAPI.provider == PROVIDER,
                    ExternalAPI.name == name,
                    ExternalAPI.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
    if not api or not api.credentials_encrypted:
        return None
    try:
        blob = json.loads(enc.decrypt(api.credentials_encrypted))
    except Exception:
        logger.error("ig.decrypt_failed")
        return None
    expires_at = datetime.fromisoformat(blob["expires_at"])
    # Si quedan más de 24h, devuelve el actual. Si queda menos, refresca.
    if expires_at - datetime.now(timezone.utc) > timedelta(hours=24):
        return str(blob["access_token"])
    try:
        fresh = await refresh_access_token(blob["access_token"])
    except Exception as e:
        logger.error("ig.refresh_failed", error=str(e))
        # Devuelve el actual aunque esté cerca de expirar — mejor responder
        # con error de IG que con None silencioso.
        return str(blob["access_token"])
    await save_tokens(fresh, name=name)
    return fresh.access_token


async def get_instagram_user_id() -> str | None:
    async with db_session() as db:
        api = (
            await db.execute(
                select(ExternalAPI).where(
                    ExternalAPI.provider == PROVIDER, ExternalAPI.name == PROVIDER
                )
            )
        ).scalar_one_or_none()
    if not api:
        return None
    return (api.extra or {}).get("instagram_user_id")


# --------------------------------------------------------------------------
# Perfil de la cuenta conectada
# --------------------------------------------------------------------------
# El panel tiene que ENSEÑAR de qué cuenta de Instagram se trata: el @usuario y
# su foto, no un número. Es lo primero que mira quien revisa la app en Meta, y
# de paso evita el susto de no saber qué cuenta quedó conectada.
#
# El @usuario y el nombre se guardan en `extra` (no caducan). La foto NO: la URL
# que devuelve Meta es de su CDN y expira, así que se vuelve a pedir y solo se
# cachea un rato.

PROFILE_FIELDS = "user_id,username,name,profile_picture_url"
_PROFILE_CACHE_KEY = "ig:profile:me"
_PROFILE_CACHE_TTL = 3600  # 1 h: lo justo para no llamar a Meta en cada pintado


async def fetch_profile(access_token: str) -> dict:
    """Pide a Instagram el perfil de la cuenta conectada (la del token).

    Devuelve {} si Meta no contesta o no da el permiso: el panel enseñará lo
    que tenga guardado y nunca se queda en blanco por esto.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{IG_GRAPH_BASE}/v22.0/me",
            params={"fields": PROFILE_FIELDS, "access_token": access_token},
        )
        if r.status_code >= 400:
            logger.warning("ig.profile.failed", status=r.status_code, body=r.text[:300])
            return {}
        data = r.json()
    return {
        "instagram_user_id": str(data.get("user_id") or "") or None,
        "instagram_username": data.get("username"),
        "instagram_name": data.get("name"),
        "instagram_profile_picture_url": data.get("profile_picture_url"),
    }


async def refresh_profile(*, name: str = PROVIDER) -> dict:
    """Vuelve a pedir el perfil y guarda en `extra` lo que no caduca.

    Se llama al terminar el OAuth y cada vez que el panel pinta la tarjeta (con
    caché de 1 h). Nunca lanza: si falla, se devuelve lo guardado.
    """
    guardado = await get_profile_stored(name=name)
    token = await get_access_token(name=name)
    if not token:
        return guardado
    try:
        perfil = await fetch_profile(token)
    except Exception as e:  # noqa: BLE001 — enseñar la cuenta nunca corta el panel
        logger.warning("ig.profile.error", error=str(e))
        return guardado
    if not perfil:
        return guardado

    duraderos = {k: v for k, v in perfil.items() if v and k != "instagram_profile_picture_url"}
    if duraderos:
        async with db_session() as db:
            api = (
                await db.execute(
                    select(ExternalAPI).where(
                        ExternalAPI.provider == PROVIDER, ExternalAPI.name == name
                    )
                )
            ).scalar_one_or_none()
            if api:
                api.extra = {**(api.extra or {}), **duraderos}
                await db.commit()
    return {**guardado, **{k: v for k, v in perfil.items() if v}}


async def get_profile_stored(*, name: str = PROVIDER) -> dict:
    """Lo que tenemos guardado del perfil, sin llamar a Meta."""
    async with db_session() as db:
        api = (
            await db.execute(
                select(ExternalAPI).where(
                    ExternalAPI.provider == PROVIDER, ExternalAPI.name == name
                )
            )
        ).scalar_one_or_none()
    extra = (api.extra or {}) if api else {}
    return {
        k: extra.get(k)
        for k in (
            "instagram_user_id",
            "instagram_username",
            "instagram_name",
        )
        if extra.get(k)
    }


async def get_profile(force: bool = False, *, name: str = PROVIDER) -> dict:
    """Perfil para pintar en el panel, con caché de 1 h en Redis.

    Cada fila tiene su propia caché (la del revisor no puede pisar la de casa:
    si compartieran clave, entrar en su pantalla dejaría la tarjeta del panel
    enseñando la cuenta equivocada).
    """
    from app.core.redis import get_redis

    redis = get_redis()
    cache_key = _PROFILE_CACHE_KEY if name == PROVIDER else f"{_PROFILE_CACHE_KEY}:{name}"
    if not force:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:  # noqa: BLE001 — sin Redis se pide a Meta y ya
            pass
    perfil = await refresh_profile(name=name)
    try:
        await redis.setex(cache_key, _PROFILE_CACHE_TTL, json.dumps(perfil))
    except Exception:  # noqa: BLE001
        pass
    return perfil
