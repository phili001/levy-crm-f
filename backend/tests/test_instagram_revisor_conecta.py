"""Quien revisa la app en Meta puede conectar SU cuenta de Instagram sin echar
la del negocio.

Por qué existe esto: el guion de revisión de Meta le pide al revisor hacer el
Business Login entero, con su pantalla de permisos, y ver después el dato que la
app obtiene (su @usuario y su foto). Pero esta app guarda UNA sola cuenta de
Instagram: si su token cayera en la fila de casa, el chatbot se quedaría atado a
la cuenta del revisor y dejarían de entrar los DM de verdad hasta reconectar a
mano.

El reparto se decide en el marcador que el arranque deja en Redis junto al
`state` (no en nada que venga del navegador) y se aplica en el callback:

  - rol revisor  → fila `REVISOR_NAME`, y NO se toca la config del canal.
  - lo demás     → fila de casa, como siempre.

Sin BD ni red: todo lo que sale fuera está sustituido.
"""
from __future__ import annotations

import pytest
from starlette.requests import Request


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


def _request(state: str) -> Request:
    from app.api import oauth

    raw = f"{oauth._STATE_COOKIE}={state}"
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/oauth/instagram/callback",
            "headers": [(b"cookie", raw.encode())],
            "query_string": b"",
            "client": ("203.0.113.9", 51234),
            "scheme": "https",
            "server": ("api.test", 443),
        }
    )


@pytest.fixture
def entorno(monkeypatch):
    """Callback de Instagram con Redis, Meta y BD sustituidos.

    Devuelve (redis, guardados, canal_tocado, perfiles): lo que se guardó y con
    qué nombre de fila, y si se tocó la configuración del canal.
    """
    from datetime import datetime, timedelta, timezone

    from app.api import oauth
    from app.services import instagram_oauth as ig_oauth

    redis = _FakeRedis()
    monkeypatch.setattr(oauth, "get_redis", lambda: redis)

    async def fake_exchange_code(code: str):
        return ig_oauth.IGTokens(
            access_token="token-del-que-acaba-de-entrar",
            user_id="17841400000000000",
            expires_at=datetime.now(timezone.utc) + timedelta(days=60),
            scope=",".join(ig_oauth.SCOPES),
        )

    guardados: list[str] = []
    perfiles: list[str] = []
    canal_tocado: list[bool] = []

    async def fake_save_tokens(tokens, *, name=ig_oauth.PROVIDER):
        guardados.append(name)

    async def fake_get_profile(force: bool = False, *, name=ig_oauth.PROVIDER):
        perfiles.append(name)
        return {}

    async def fake_ensure_channel():
        canal_tocado.append(True)

    monkeypatch.setattr(oauth.ig_oauth, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(oauth.ig_oauth, "save_tokens", fake_save_tokens)
    monkeypatch.setattr(oauth.ig_oauth, "get_profile", fake_get_profile)
    monkeypatch.setattr(oauth, "_ensure_instagram_channel", fake_ensure_channel)
    return redis, guardados, canal_tocado, perfiles


# ---------------------------------------------------------------------------
# El arranque marca de quién es el flujo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_arranque_marca_el_state_segun_el_rol(monkeypatch):
    from fastapi import Response

    from app.api import admin, oauth
    from app.models.user import UserRole
    from app.services import instagram_oauth as ig_oauth

    redis = _FakeRedis()
    monkeypatch.setattr("app.core.redis.get_redis", lambda: redis)

    async def fake_build_auth_url(state: str) -> str:
        return f"https://www.instagram.com/oauth/authorize?state={state}"

    monkeypatch.setattr("app.services.instagram_oauth.build_auth_url", fake_build_auth_url)

    class _Usuario:
        def __init__(self, role):
            self.role = role

    for role, esperado in (
        (UserRole.admin, ig_oauth.STATE_MARKER),
        (UserRole.revisor, ig_oauth.STATE_MARKER_REVISOR),
    ):
        redis.store.clear()
        await admin.instagram_oauth_start(response=Response(), user=_Usuario(role))
        marcadores = [v for k, v in redis.store.items() if k.startswith(oauth._STATE_PREFIX)]
        assert marcadores == [esperado], (
            f"el arranque con rol {role} dejó {marcadores} en vez de {esperado}"
        )


# ---------------------------------------------------------------------------
# El callback reparte según ese marcador
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_revisor_no_pisa_la_cuenta_de_casa(entorno):
    """Lo que de verdad importa: su token va a su fila y el canal no se toca."""
    from app.api import oauth
    from app.services import instagram_oauth as ig_oauth

    redis, guardados, canal_tocado, perfiles = entorno
    state = "state-del-revisor"
    await redis.setex(oauth._STATE_PREFIX + state, 600, ig_oauth.STATE_MARKER_REVISOR)

    resp = await oauth.instagram_callback(_request(state), code="codigo", state=state, error=None)

    assert resp.status_code == 200
    assert guardados == [ig_oauth.REVISOR_NAME], (
        f"el token del revisor acabó en {guardados}: eso echa la cuenta del negocio"
    )
    assert canal_tocado == [], (
        "conectarse el revisor ha tocado la configuración del canal de Instagram"
    )
    assert perfiles == [ig_oauth.REVISOR_NAME], (
        "se ha refrescado el perfil de la cuenta de casa con el token del revisor"
    )


@pytest.mark.asyncio
async def test_la_conexion_normal_sigue_igual(entorno):
    from app.api import oauth
    from app.services import instagram_oauth as ig_oauth

    redis, guardados, canal_tocado, perfiles = entorno
    state = "state-de-casa"
    await redis.setex(oauth._STATE_PREFIX + state, 600, ig_oauth.STATE_MARKER)

    resp = await oauth.instagram_callback(_request(state), code="codigo", state=state, error=None)

    assert resp.status_code == 200
    assert guardados == [ig_oauth.PROVIDER]
    assert canal_tocado == [True], "la conexión de casa ya no deja el canal listo"
    assert perfiles == [ig_oauth.PROVIDER]


@pytest.mark.asyncio
async def test_la_url_pide_las_credenciales_aunque_ya_haya_sesion(monkeypatch):
    """`force_reauth` es lo que hace grabable el vídeo de la revisión.

    Con la sesión de Instagram abierta en el navegador, la ventana de
    autorización se salta el login y enseña una pantalla corta; Meta pide ver el
    flujo entero, incluida la pantalla de permisos.
    """
    from urllib.parse import parse_qs, urlparse

    from app.services import instagram_oauth as ig_oauth

    async def fake_credentials():
        return "1646161613278242", "secreto"

    monkeypatch.setattr(ig_oauth, "_get_oauth_app_credentials", fake_credentials)

    url = await ig_oauth.build_auth_url("un-state")
    q = parse_qs(urlparse(url).query)
    assert q["force_reauth"] == ["true"], "la URL de Instagram ya no fuerza el login"
    assert q["scope"] == [",".join(ig_oauth.SCOPES)]
    assert q["state"] == ["un-state"]


def test_el_marcador_manda_a_la_fila_correcta():
    from app.services import instagram_oauth as ig_oauth

    assert ig_oauth.row_name_for_marker(ig_oauth.STATE_MARKER) == ig_oauth.PROVIDER
    assert ig_oauth.row_name_for_marker(ig_oauth.STATE_MARKER_REVISOR) == ig_oauth.REVISOR_NAME
    # Cualquier cosa rara cae del lado seguro: la fila de casa es la que ya
    # existía, y un marcador desconocido no puede inventar una cuenta nueva.
    assert ig_oauth.row_name_for_marker("cualquier-cosa") == ig_oauth.PROVIDER


# ---------------------------------------------------------------------------
# Y el runtime sigue mirando SOLO la cuenta de casa
# ---------------------------------------------------------------------------


def test_el_runtime_solo_lee_la_fila_de_casa():
    """`get_access_token()` sin argumentos —como lo llama todo el envío— tiene
    que filtrar por la fila de casa. Si dejara de hacerlo, el chatbot podría
    acabar mandando mensajes con el token del revisor."""
    import inspect

    from app.services import instagram_oauth as ig_oauth

    firma = inspect.signature(ig_oauth.get_access_token)
    assert firma.parameters["name"].default == ig_oauth.PROVIDER
    fuente = inspect.getsource(ig_oauth.get_access_token)
    assert "ExternalAPI.name == name" in fuente, (
        "get_access_token ya no filtra por nombre de fila: podría coger el token del revisor"
    )
