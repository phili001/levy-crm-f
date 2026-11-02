"""Las claves y las autorizaciones se pueden borrar, no solo pisar.

El agujero que cubre este test: el panel solo sabía guardar encima
(`PUT /admin/credentials/{key}`). Para dejar un servicio virgen —rehacer la
configuración desde cero, o quitar de en medio las claves de una app de Meta
que ya no se usa— había que entrar en la base de datos a mano. Y con el token
de un OAuth pasaba lo mismo: borrar el canal dejaba la autorización viva, así
que el panel seguía diciendo que el servicio estaba conectado.

Sin BD: se llaman las funciones del endpoint con una sesión falsa, que es como
se prueban el resto de endpoints de admin.
"""
from __future__ import annotations

import uuid

import pytest


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeDb:
    def __init__(self, obj):
        self._obj = obj
        self.borrados: list = []
        self.commits = 0

    async def execute(self, *a, **k):
        return _FakeResult(self._obj)

    async def delete(self, obj):
        self.borrados.append(obj)

    async def commit(self):
        self.commits += 1


class _FakeUser:
    def __init__(self):
        self.id = uuid.uuid4()


class _FakeCred:
    def __init__(self, key):
        self.id = uuid.uuid4()
        self.key = key


class _FakeApi:
    def __init__(self, provider):
        self.id = uuid.uuid4()
        self.provider = provider
        self.name = provider


@pytest.fixture
def espias(monkeypatch):
    """Sustituye auditoría e invalidación de caché por espías."""
    from app.api import admin as admin_mod

    auditorias: list[dict] = []
    invalidadas: list[str] = []

    async def fake_audit(_db, **kw):
        auditorias.append(kw)

    async def fake_invalidate(key):
        invalidadas.append(key)

    monkeypatch.setattr(admin_mod, "record_audit", fake_audit)
    monkeypatch.setattr(admin_mod, "invalidate_credential_cache", fake_invalidate)
    return auditorias, invalidadas


# ---------------------------------------------------------------------------
# Claves
# ---------------------------------------------------------------------------


async def test_borrar_una_clave_la_quita_invalida_cache_y_deja_rastro(espias):
    from app.api.admin import delete_credential

    auditorias, invalidadas = espias
    cred = _FakeCred("instagram_oauth_client_id")
    db = _FakeDb(cred)

    resp = await delete_credential("instagram_oauth_client_id", db=db, user=_FakeUser())

    assert resp.ok is True
    assert db.borrados == [cred], "la fila de la clave no se borró"
    assert invalidadas == ["instagram_oauth_client_id"], (
        "sin invalidar la caché, el backend sigue usando la clave borrada"
    )
    assert [a["action"] for a in auditorias] == ["credential.deleted"]
    assert auditorias[0]["after"] == {"key": "instagram_oauth_client_id"}


async def test_borrar_una_clave_que_no_esta_no_es_un_error(espias):
    """Idempotente: el panel lo pide sin comprobar antes si estaba puesta."""
    from app.api.admin import delete_credential

    _auditorias, invalidadas = espias
    db = _FakeDb(None)

    resp = await delete_credential("instagram_oauth_client_secret", db=db, user=_FakeUser())

    assert resp.ok is True
    assert db.borrados == []
    assert invalidadas == ["instagram_oauth_client_secret"], (
        "una clave cacheada pero ya no en la tabla se quedaría viva"
    )


async def test_no_se_puede_borrar_una_clave_inventada(espias):
    """Misma puerta que el guardado: solo las claves del catálogo."""
    from fastapi import HTTPException

    from app.api.admin import delete_credential

    _auditorias, invalidadas = espias
    db = _FakeDb(_FakeCred("loquesea"))

    with pytest.raises(HTTPException) as e:
        await delete_credential("loquesea", db=db, user=_FakeUser())

    assert e.value.status_code == 400
    assert db.borrados == []
    assert invalidadas == []


async def test_la_clave_del_secreto_de_instagram_esta_en_el_catalogo():
    """Si dejara de estarlo, el botón de borrar del panel daría 400."""
    from app.api.admin import CREDENTIAL_KEYS

    for key in ("instagram_oauth_client_id", "instagram_oauth_client_secret"):
        assert key in CREDENTIAL_KEYS


# ---------------------------------------------------------------------------
# Autorizaciones (el token que deja un OAuth)
# ---------------------------------------------------------------------------


async def test_borrar_una_autorizacion_la_quita_y_deja_rastro(espias):
    from app.api.admin import delete_external_api

    auditorias, _invalidadas = espias
    api = _FakeApi("instagram_business")
    db = _FakeDb(api)

    resp = await delete_external_api(api.id, db=db, user=_FakeUser())

    assert resp.ok is True
    assert db.borrados == [api], "la autorización no se borró"
    assert [a["action"] for a in auditorias] == ["external_api.deleted"]
    assert auditorias[0]["after"]["provider"] == "instagram_business"


async def test_borrar_una_autorizacion_que_no_existe_da_404(espias):
    from fastapi import HTTPException

    from app.api.admin import delete_external_api

    db = _FakeDb(None)

    with pytest.raises(HTTPException) as e:
        await delete_external_api(uuid.uuid4(), db=db, user=_FakeUser())

    assert e.value.status_code == 404
    assert db.borrados == []
