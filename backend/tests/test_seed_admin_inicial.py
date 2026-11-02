"""El seed no crea un administrador con el que luego no se pueda entrar.

Dos fallos reales, los dos silenciosos, los dos con el mismo final: el
despliegue termina en verde, el log dice "seed.admin.created" y el panel no
deja pasar a nadie.

  (a) `INITIAL_ADMIN_PASSWORD=abc` → el usuario se creaba con esa contraseña y
      después el panel exigía la política de verdad (12 caracteres,
      app/core/security.py).
  (b) `INITIAL_ADMIN_EMAIL=admin@local` —que además es el valor POR DEFECTO de
      config.py— → el usuario se creaba y el login devolvía 422, porque
      schemas/auth.LoginRequest valida el correo con `EmailStr`.

Ahora se comprueban las dos cosas ANTES de abrir la sesión: o el .env está
bien, o no se escribe nada. El fallo es ruidoso (SeedConfigError → salida 1),
que es lo que el entrypoint espera para reintentar y, si no hay ningún admin en
la base, abortar el arranque.
"""
from __future__ import annotations

import pytest

from app.scripts.seed import SeedConfigError, validar_admin_inicial


def _config(monkeypatch, *, email: str, password: str) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "INITIAL_ADMIN_EMAIL", email, raising=False)
    monkeypatch.setattr(settings, "INITIAL_ADMIN_PASSWORD", password, raising=False)


def test_un_env_correcto_pasa(monkeypatch):
    _config(monkeypatch, email="ana@tuempresa.com", password="MiClaveSegura123")
    validar_admin_inicial()  # no lanza


def test_password_corta_aborta(monkeypatch):
    """El caso `abc`: se creaba el admin y luego el panel pedía 12 caracteres."""
    _config(monkeypatch, email="ana@tuempresa.com", password="abc")

    with pytest.raises(SeedConfigError) as exc:
        validar_admin_inicial()

    assert "INITIAL_ADMIN_PASSWORD" in str(exc.value)


def test_el_mensaje_de_la_password_usa_la_constante(monkeypatch):
    """El mínimo sale de PASSWORD_MIN_LENGTH, no de un número escrito a mano."""
    from app.core.security import PASSWORD_MIN_LENGTH

    _config(monkeypatch, email="ana@tuempresa.com", password="abc")

    with pytest.raises(SeedConfigError) as exc:
        validar_admin_inicial()

    assert f"{PASSWORD_MIN_LENGTH} caracteres" in str(exc.value)


def test_correo_sin_dominio_valido_aborta(monkeypatch):
    """`admin@local` crea el usuario y el login devuelve 422. Aquí ya no."""
    _config(monkeypatch, email="admin@local", password="MiClaveSegura123")

    with pytest.raises(SeedConfigError) as exc:
        validar_admin_inicial()

    assert "INITIAL_ADMIN_EMAIL" in str(exc.value)


def test_el_defecto_de_config_no_pasa_por_bueno():
    """Los valores de ejemplo de config.py no pueden dar un admin "bueno".

    Son un marcador para que se cambien; si el seed los aceptase, la
    instalación arrancaría con un admin inservible y sin un solo aviso.
    """
    from app.core.config import Settings

    defaults = Settings.model_fields
    assert defaults["INITIAL_ADMIN_EMAIL"].default == "admin@local"

    from app.scripts.seed import _error_email_admin, _error_password_admin

    assert _error_email_admin("admin@local") is not None
    assert _error_password_admin("ChangeMe123!", "admin@local") is not None


def test_lo_que_rechaza_el_seed_es_lo_que_rechaza_el_login():
    """Mismo validador que `LoginRequest.email`: ni más estricto ni más laxo."""
    import pydantic

    from app.schemas.auth import LoginRequest
    from app.scripts.seed import _error_email_admin

    for correo in ("admin@local", "admin@localhost", "sin-arroba", "a@b"):
        rechaza_login = False
        try:
            LoginRequest(email=correo, password="x")
        except pydantic.ValidationError:
            rechaza_login = True
        assert (_error_email_admin(correo) is not None) is rechaza_login, correo


def test_espacios_de_sobra_en_el_correo(monkeypatch):
    """Un `INITIAL_ADMIN_EMAIL= ana@tuempresa.com ` copiado del manual."""
    from app.scripts.seed import _error_email_admin

    assert _error_email_admin(" ana@tuempresa.com ") is not None


async def test_el_seed_no_escribe_nada_si_el_env_esta_mal(monkeypatch):
    """No deja la instalación a medias: se aborta ANTES de abrir la sesión."""
    from app.scripts import seed as mod

    _config(monkeypatch, email="admin@local", password="abc")

    def _explota(*a, **k):  # pragma: no cover - solo salta si hay regresión
        raise AssertionError("el seed NO debe tocar la base con un .env inválido")

    monkeypatch.setattr(mod, "db_session", _explota)

    with pytest.raises(SeedConfigError):
        await mod.seed()


async def test_env_vacio_sigue_saltandose_el_seed_sin_reventar(monkeypatch):
    """Comportamiento de siempre: sin variables, se avisa y se sale. No es un fallo."""
    from app.scripts import seed as mod

    _config(monkeypatch, email="", password="")

    def _explota(*a, **k):  # pragma: no cover - solo salta si hay regresión
        raise AssertionError("sin variables no se toca la base")

    monkeypatch.setattr(mod, "db_session", _explota)

    await mod.seed()  # ni lanza ni escribe
