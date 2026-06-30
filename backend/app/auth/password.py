"""Hash and verify passwords (R1.2).

Uses **bcrypt** to hash passwords — never stores plaintext. `hashPassword` validates
that the password length is within 8..64 characters (per R1.2) before hashing;
`verifyPassword` compares in constant time (bcrypt) and does NOT raise on mismatch —
it simply returns False.

About bcrypt's 72-byte limit: the password is at most 64 characters, but Vietnamese
(UTF-8) characters can take 2-4 bytes, so 64 characters may exceed 72 bytes and cause
bcrypt to raise. So that every 8..64-character password works reliably regardless of
byte length, we pre-hash with SHA-256 then base64-encode (44 ASCII bytes, always < 72
bytes) as the input to bcrypt (the common "bcrypt-sha256" technique). This does not
weaken bcrypt and handles UTF-8 safely.

The module logs through the centralized logger (`logging.getLogger(__name__)` →
propagates up to "app"). NEVER logs plaintext password values.
"""

from __future__ import annotations

import base64
import hashlib
import logging

import bcrypt

from app.errors import ValidationError

logger = logging.getLogger(__name__)

# Valid password length range (number of characters), per R1.2.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 64


def _prehash(matKhau: str) -> bytes:
    """Reduce the password to a stable 44 ASCII bytes before feeding it to bcrypt.

    SHA-256 digest (32 bytes) → base64 (44 bytes) is always < 72 bytes, avoiding
    bcrypt's 72-byte limit for long UTF-8 passwords (R1.2 note).
    """
    digest = hashlib.sha256(matKhau.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hashPassword(matKhau: str) -> str:
    """Validate the 8..64 character length then hash the password with bcrypt.

    Returns the bcrypt hash string (safe to store in the DB). Raises `ValidationError`
    when the length is out of the valid range. NEVER stores/returns plaintext.
    """
    if not isinstance(matKhau, str):
        raise ValidationError("Mat khau khong hop le.")

    soKyTu = len(matKhau)
    if soKyTu < MIN_PASSWORD_LENGTH or soKyTu > MAX_PASSWORD_LENGTH:
        # Do not put the password value into the message/log — only the length.
        logger.info(
            "Tu choi bam mat khau: do dai %d ngoai khoang [%d, %d]",
            soKyTu,
            MIN_PASSWORD_LENGTH,
            MAX_PASSWORD_LENGTH,
        )
        raise ValidationError(
            f"Mat khau phai tu {MIN_PASSWORD_LENGTH} den {MAX_PASSWORD_LENGTH} ky tu."
        )

    matKhauHash = bcrypt.hashpw(_prehash(matKhau), bcrypt.gensalt()).decode("utf-8")
    logger.debug("Bam mat khau thanh cong (do dai=%d ky tu)", soKyTu)
    return matKhauHash


def verifyPassword(matKhau: str, matKhauHash: str) -> bool:
    """Compare the password against the hash in constant time (bcrypt).

    Returns True when it matches, False when it does not. NEVER raises — every error
    (wrong type, corrupted/malformed bcrypt hash) degrades safely to False.
    """
    if not isinstance(matKhau, str) or not isinstance(matKhauHash, str):
        logger.warning("verifyPassword: dau vao khong phai chuoi, tra ve False.")
        return False

    try:
        return bcrypt.checkpw(_prehash(matKhau), matKhauHash.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        # Hash is not in bcrypt format / decode error — treat as no match, do not raise.
        logger.warning("verifyPassword: khong xac minh duoc hash (%s); tra ve False.", exc)
        return False
