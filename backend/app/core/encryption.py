"""Cifrado simétrico Fernet para credenciales en DB."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Para que el aviso salga una vez por proceso y no en cada credencial que se
# cifra: con una clave derivada, esto se llamaría cientos de veces al día.
_derivacion_avisada = False


def clave_fernet(valor: str) -> bytes:
    """Devuelve una clave Fernet a partir de lo que haya en ENCRYPTION_KEY.

    Si el valor ya es una clave Fernet (32 bytes en base64 urlsafe), se usa tal
    cual. Si no, se DERIVA con sha256 y se avisa en los logs.

    Por qué existe la derivación: Coolify genera sus secretos con las variables
    mágicas `SERVICE_BASE64_64_*`, que son 64 bytes en base64 estándar — no
    valen como clave Fernet. Sin esto, la instalación guiada tendría que pedir
    la clave a mano justo en el paso donde más se equivoca la gente, y un
    formato incorrecto tira el arranque entero.

    Es determinista: la misma cadena da siempre la misma clave, así que lo ya
    cifrado se sigue leyendo mientras la variable no cambie. Cambiarla deja
    ilegibles las credenciales, las notas internas, el NIF y las
    transcripciones, igual que con una clave Fernet de verdad.
    """
    global _derivacion_avisada
    bruto = valor.encode()
    try:
        Fernet(bruto)
        return bruto
    except Exception:
        pass
    derivada = base64.urlsafe_b64encode(hashlib.sha256(bruto).digest())
    if not _derivacion_avisada:
        _derivacion_avisada = True
        logger.warning(
            "encryption.key.derivada",
            detalle=(
                "ENCRYPTION_KEY no tiene formato Fernet; se deriva con sha256. "
                "Funciona, pero NO la cambies: lo cifrado quedaría ilegible."
            ),
        )
    return derivada


class EncryptionService:
    def __init__(self, key: str | None = None) -> None:
        valor = key or settings.ENCRYPTION_KEY
        if not valor:
            raise RuntimeError("ENCRYPTION_KEY no configurada")
        self._fernet = Fernet(clave_fernet(valor))

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode()
        except InvalidToken as e:
            raise ValueError("No se pudo descifrar la credencial") from e


def get_encryption_service() -> EncryptionService:
    return EncryptionService()
