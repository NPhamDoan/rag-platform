"""Encryption-at-rest for user API keys using Fernet (R22.2).

Provides two symmetric functions shared by `ApiKeyService` (and possibly by reset
keys if needed):

    encryptSecret(plaintext: str) -> bytes      # encrypt, returns ciphertext (Fernet token)
    decryptSecret(ciphertext: bytes) -> str     # deterministic round-trip decrypt

## Source of the Fernet key (derived from `settings.secret_key_encrypt`)

Fernet requires a 32-byte urlsafe-base64-encoded key (a 44-character string). The
key derivation rules from the configuration:

1. If `secret_key_encrypt` is already a valid Fernet key (correct format) → use it
   directly. This is the recommended approach for production (generated with
   `Fernet.generate_key()`).
2. Otherwise → derive deterministically: `urlsafe_b64encode(sha256(secret).digest())`.
   SHA-256 always yields 32 bytes so the result is a valid Fernet key; the same
   `secret` always yields the same key → stable round-trip decryption across runs.
3. If `secret_key_encrypt` is empty → still derive a dev key (per rule 2 over the
   empty string) BUT log a WARNING once: production MUST configure a real key,
   otherwise the key is guessable and the data is not properly protected.

NEVER logs the plaintext or ciphertext value of a key (R22.3, R14.4).
"""

from __future__ import annotations

import base64
import logging
from hashlib import sha256

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.errors import InternalError

logger = logging.getLogger(__name__)

# Flag to ensure the "missing real key" WARNING is logged only once (avoid log spam).
_da_canh_bao_khoa_dev = False


def _laKhoaFernetHopLe(secret: str) -> bool:
    """Check whether `secret` is a valid Fernet key (correct format)."""
    try:
        Fernet(secret.encode("utf-8"))
        return True
    except (ValueError, TypeError):
        return False


def _deriveFernetKey(secret: str) -> bytes:
    """Derive a valid Fernet key from the configured `secret` (see module docstring)."""
    global _da_canh_bao_khoa_dev

    if secret and _laKhoaFernetHopLe(secret):
        return secret.encode("utf-8")

    if not secret and not _da_canh_bao_khoa_dev:
        logger.warning(
            "secret_key_encrypt rong — dang dung khoa Fernet suy ra cho moi truong "
            "phat trien. PRODUCTION BAT BUOC cau hinh SECRET_KEY_ENCRYPT bang mot "
            "khoa that (Fernet.generate_key())."
        )
        _da_canh_bao_khoa_dev = True

    # Deterministic derivation: SHA-256 yields 32 bytes → urlsafe-base64 → valid Fernet key.
    return base64.urlsafe_b64encode(sha256(secret.encode("utf-8")).digest())


def _getFernet() -> Fernet:
    """Create a Fernet object from the current configuration (reads settings each call).

    Re-reads settings on every call so tests can override `secret_key_encrypt`
    through the configuration; the cost of creating a Fernet is negligible.
    """
    return Fernet(_deriveFernetKey(get_settings().secret_key_encrypt))


def encryptSecret(plaintext: str) -> bytes:
    """Encrypt `plaintext` into a Fernet ciphertext (R22.2).

    Returns bytes (a Fernet token) to store in the `KhoaApiNguoiDung.khoaMaHoa`
    column. NEVER stores/logs plaintext.
    """
    if not isinstance(plaintext, str):
        raise InternalError("encryptSecret yeu cau plaintext dang chuoi.")
    return _getFernet().encrypt(plaintext.encode("utf-8"))


def decryptSecret(ciphertext: bytes) -> str:
    """Decrypt a Fernet ciphertext back to plaintext (deterministic round-trip, R22.2).

    An invalid token / wrong key → `InternalError` (does not leak key details).
    """
    try:
        return _getFernet().decrypt(bytes(ciphertext)).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        # Do not put the ciphertext/key value into the log (R22.3, R14.4).
        logger.error("Giai ma khoa API that bai: token khong hop le hoac sai khoa.")
        raise InternalError("Khong the giai ma khoa API.")
