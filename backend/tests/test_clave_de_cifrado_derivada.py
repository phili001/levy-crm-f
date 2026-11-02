"""La ENCRYPTION_KEY que genera Coolify no es Fernet y aun así tiene que servir.

Coolify crea sus secretos con las variables mágicas `SERVICE_BASE64_64_*`: 64
bytes en base64 estándar, que Fernet rechaza. Sin derivación, la instalación
guiada moría al arrancar justo en el paso donde más se equivoca la gente.
"""
import base64

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.core.encryption import EncryptionService, clave_fernet

# Lo que devuelve una variable mágica de Coolify: 64 bytes, base64 ESTÁNDAR
# (con + y /), sin el formato que espera Fernet.
CLAVE_DE_COOLIFY = base64.b64encode(b"x" * 64).decode()


def test_una_clave_fernet_valida_se_usa_tal_cual():
    clave = Fernet.generate_key().decode()
    assert clave_fernet(clave) == clave.encode()


def test_la_clave_de_coolify_se_deriva_y_cifra():
    enc = EncryptionService(key=CLAVE_DE_COOLIFY)
    assert enc.decrypt(enc.encrypt("sk-secreta")) == "sk-secreta"


def test_la_derivacion_es_estable_entre_arranques():
    """Si cambiara entre reinicios, lo cifrado antes quedaría ilegible."""
    assert clave_fernet(CLAVE_DE_COOLIFY) == clave_fernet(CLAVE_DE_COOLIFY)


def test_dos_claves_distintas_no_descifran_lo_del_otro():
    otra = base64.b64encode(b"y" * 64).decode()
    token = EncryptionService(key=CLAVE_DE_COOLIFY).encrypt("sk-secreta")
    with pytest.raises(ValueError):
        EncryptionService(key=otra).decrypt(token)


def _produccion(**extra) -> Settings:
    base = dict(
        APP_ENV="production",
        JWT_SECRET="j" * 40,
        INITIAL_ADMIN_EMAIL="admin@clinica.mx",
        INITIAL_ADMIN_PASSWORD="ContrasenaLarga123",
        APP_BASE_URL="https://crm.ejemplo.mx",
        FRONTEND_BASE_URL="https://crm.ejemplo.mx",
    )
    base.update(extra)
    return Settings(**base)


def test_produccion_arranca_con_la_clave_de_coolify():
    _produccion(ENCRYPTION_KEY=CLAVE_DE_COOLIFY).validate_production()


def test_produccion_sigue_rechazando_la_clave_vacia():
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        _produccion(ENCRYPTION_KEY="").validate_production()


def test_produccion_rechaza_una_clave_de_ejemplo():
    with pytest.raises(RuntimeError, match="ejemplo"):
        _produccion(ENCRYPTION_KEY="change-me-fernet-key").validate_production()


def test_produccion_rechaza_una_clave_corta_que_no_es_fernet():
    """Derivar de "1234" daría una clave válida y una falsa sensación de cifrado."""
    with pytest.raises(RuntimeError, match="corta"):
        _produccion(ENCRYPTION_KEY="demasiado-corta").validate_production()
