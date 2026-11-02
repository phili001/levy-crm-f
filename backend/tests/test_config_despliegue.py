"""Lo que tiene que fallar (o no fallar) ANTES de que la instalación arranque.

Los dos casos de aquí salieron de una prueba de despliegue de cero: son fallos
que no dan la cara al instalar, sino semanas después y con un síntoma que no
menciona la variable que los causó.
"""

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings

CLAVE_BUENA = Fernet.generate_key().decode()


def _ajustes_produccion(**extra) -> Settings:
    base = dict(
        APP_ENV="production",
        JWT_SECRET="x" * 40,
        ENCRYPTION_KEY=CLAVE_BUENA,
        INITIAL_ADMIN_EMAIL="admin@ejemplo.com",
        INITIAL_ADMIN_PASSWORD="UnaContraseñaLarga123!",
        APP_BASE_URL="https://api.ejemplo.com",
        FRONTEND_BASE_URL="https://panel.ejemplo.com",
    )
    base.update(extra)
    return Settings(**base)


def test_produccion_valida_arranca():
    _ajustes_produccion().validate_production()


@pytest.mark.parametrize(
    "clave",
    [
        "no-es-base64-ni-de-lejos",
        # base64 correcto pero de 16 bytes, no de 32.
        Fernet.generate_key().decode()[:20],
    ],
)
def test_encryption_key_mal_formada_para_el_arranque(clave):
    with pytest.raises(RuntimeError) as e:
        _ajustes_produccion(ENCRYPTION_KEY=clave).validate_production()
    assert "ENCRYPTION_KEY" in str(e.value)
    # El mensaje tiene que traer el comando que la genera bien: quien despliega
    # está mirando esta línea en los registros, no la documentación.
    assert "openssl rand -base64 32" in str(e.value)


def test_una_clave_larga_que_no_es_fernet_arranca_y_se_deriva():
    """32 bytes en hex (el `openssl rand -hex 32` de toda la vida) o un secreto
    de Coolify ya no tiran el arranque: se derivan con sha256 y se avisa en los
    registros (core/encryption.py). Lo que sigue sin pasar es una clave corta,
    vacía o de ejemplo, que daría cifrado de mentira.
    """
    _ajustes_produccion(ENCRYPTION_KEY="a" * 64).validate_production()


def test_encryption_key_vacia_sigue_abortando():
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        _ajustes_produccion(ENCRYPTION_KEY="").validate_production()


def test_cors_ignora_la_barra_final():
    """El navegador manda Origin SIN barra final; con ella no casa nunca.

    El síntoma es "el panel no carga nada" y ningún error apunta a la variable.
    """
    s = Settings(CORS_ALLOWED_ORIGINS="https://panel.ejemplo.com/, https://otro.com")
    assert s.cors_origins == ["https://panel.ejemplo.com", "https://otro.com"]


def test_cors_no_se_come_los_origenes_normales():
    s = Settings(CORS_ALLOWED_ORIGINS="https://panel.ejemplo.com,https://otro.com")
    assert s.cors_origins == ["https://panel.ejemplo.com", "https://otro.com"]
