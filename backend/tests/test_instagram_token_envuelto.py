"""El canje del `code` de Instagram funciona con la respuesta envuelta.

Por qué existe esto: Instagram Business Login NO contesta al canje del `code`
con el token plano, sino metido en una lista:

    {"data": [{"access_token": ..., "user_id": ..., "permissions": ...}]}

El endpoint del long-lived, en cambio, sí lo devuelve plano. Leer siempre
`data["access_token"]` funciona en el segundo paso y revienta con KeyError en el
primero — que es por donde pasa TODO el que conecta su cuenta, incluido quien
revisa la app en Meta, y lo único que ve es "no se pudo completar la conexión".

De paso se comprueban dos cosas que dependen de lo mismo: que el `user_id` se
recoge (el webhook lo necesita para saber a qué cuenta llegan los DM) y que se
guardan los permisos CONCEDIDOS, no los pedidos.

Sin BD ni red: la llamada a Instagram está sustituida.
"""
from __future__ import annotations

import pytest


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:  # pragma: no cover — aquí nunca hay error
        raise AssertionError("no debería llamarse con 200")


class _FakeClient:
    """Devuelve la forma real de cada paso: envuelta al canjear, plana al alargar."""

    def __init__(self, *a, **k) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kwargs):
        return _Resp(
            {
                "data": [
                    {
                        "access_token": "IGAA-short",
                        "user_id": 17841476215707635,
                        "permissions": "instagram_business_basic,instagram_business_manage_messages",
                    }
                ]
            }
        )

    async def get(self, url, **kwargs):
        return _Resp(
            {"access_token": "IGAA-long", "token_type": "bearer", "expires_in": 5184000}
        )


@pytest.fixture
def _sin_red(monkeypatch):
    from app.services import instagram_oauth as ig

    monkeypatch.setattr(ig.httpx, "AsyncClient", _FakeClient)

    async def _creds():
        return "app-id", "app-secret"

    monkeypatch.setattr(ig, "_get_oauth_app_credentials", _creds)
    return ig


@pytest.mark.asyncio
async def test_el_canje_lee_el_token_aunque_venga_envuelto(_sin_red):
    tokens = await _sin_red.exchange_code("un-code")

    assert tokens.access_token == "IGAA-long"
    # El user_id vive dentro del envoltorio: si se lee mal se queda vacío y el
    # webhook no sabe a qué cuenta pertenecen los mensajes.
    assert tokens.user_id == "17841476215707635"
    assert tokens.scope == "instagram_business_basic,instagram_business_manage_messages"


@pytest.mark.asyncio
async def test_si_meta_no_manda_token_el_error_dice_que_paso_fallo(_sin_red):
    """Instagram puede contestar 200 con un error dentro. Antes salía KeyError
    pelado y quien conectaba solo veía el mensaje genérico del callback."""

    class _Vacio(_FakeClient):
        async def post(self, url, **kwargs):
            return _Resp({"error_type": "OAuthException", "error_message": "Invalid scope"})

    _sin_red.httpx.AsyncClient = _Vacio
    with pytest.raises(RuntimeError, match="code→short-lived"):
        await _sin_red.exchange_code("un-code")


def test_no_se_pide_el_permiso_de_comentarios(_sin_red):
    """La app no toca comentarios por ningún lado y ese permiso no está en la
    revisión de Meta: pedirlo hace que Instagram corte el login."""
    assert "instagram_business_manage_comments" not in _sin_red.SCOPES
    assert _sin_red.SCOPES == [
        "instagram_business_basic",
        "instagram_business_manage_messages",
    ]
