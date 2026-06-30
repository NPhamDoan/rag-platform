"""Time-limited HMAC tokens + PhienXacThuc management (R2.5-7, R2.9, R10.8).

Inherits the legacy system's HMAC token mechanism but adds **session revocation**:
each token carries a `jti` corresponding to a `PhienXacThuc` record. Thanks to this,
logout / password change / account disable only need to set `revokedAt` for the token
to lose effect even before it expires (R2.8, R10.8, R25.1).

Token format (ASCII string, URL-safe):

    base64url(payload_json) + "." + base64url(hmac_sha256(secret_key, payload_part))

`payload_json` contains `{jti, taiKhoanId, expiresAt}` (expiresAt in tz-aware
ISO-8601). The signature is compared in constant time (`hmac.compare_digest`) to
prevent timing attacks. The token expiry is taken from the signed payload itself
(tamper-proof), not from a value read back from the DB (SQLite does not preserve
timezone information).

`verifyToken` checks in the right order but **always raises the same generic error**
(`AuthenticationError`) so as NOT to reveal which check failed:
  1. The HMAC signature is valid.
  2. The token is not expired (expiresAt > now).
  3. The PhienXacThuc exists and `revokedAt is None` (not revoked).
  4. The account exists and `trangThai == HOAT_DONG`.

The module logs through the centralized logger. NEVER logs token or signature values.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import PhienXacThuc, TaiKhoan, TrangThaiTaiKhoan, _uuid
from app.errors import AuthenticationError

logger = logging.getLogger(__name__)

# Generic message — does not reveal which check failed (R2.2 generic spirit).
_GENERIC_AUTH_ERROR = "Token khong hop le hoac da het hieu luc."
# Generic message for the password reset token (R25.3 generic spirit) — does not
# reveal whether the token is expired / wrong / already used.
_GENERIC_RESET_ERROR = "Lien ket dat lai mat khau khong hop le hoac da het han."


def _now() -> datetime:
    """Current time (UTC, tz-aware) — consistent with models._now."""
    return datetime.now(timezone.utc)


def _b64url_encode(raw: bytes) -> str:
    """URL-safe base64, stripping '=' padding to keep the token compact."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    """Decode base64url, re-adding the stripped padding."""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(payloadPart: str, secretKey: str) -> str:
    """Sign the payload part with HMAC-SHA256 → a base64url string."""
    chuKy = hmac.new(secretKey.encode("utf-8"), payloadPart.encode("ascii"), sha256).digest()
    return _b64url_encode(chuKy)


def createToken(db: Session, taiKhoan: TaiKhoan, ttlMinutes: int | None = None) -> str:
    """Create a new PhienXacThuc and return the signed HMAC token (R2.5).

    `ttlMinutes` defaults to `settings.session_ttl_minutes`. The session record is
    added and committed before returning the token, so that `jti` exists at verify
    time.
    """
    settings = get_settings()
    ttl = ttlMinutes if ttlMinutes is not None else settings.session_ttl_minutes

    jti = _uuid()
    issuedAt = _now()
    expiresAt = issuedAt + timedelta(minutes=ttl)

    phien = PhienXacThuc(
        id=jti,
        taiKhoanId=taiKhoan.id,
        issuedAt=issuedAt,
        expiresAt=expiresAt,
    )
    db.add(phien)
    db.commit()

    payload = {
        "jti": jti,
        "taiKhoanId": taiKhoan.id,
        "expiresAt": expiresAt.isoformat(),
    }
    payloadPart = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    token = f"{payloadPart}.{_sign(payloadPart, settings.secret_key)}"

    logger.info("Tao phien xac thuc moi: jti=%s, taiKhoanId=%s", jti, taiKhoan.id)
    return token


def _decodeVerified(token: str, secretKey: str) -> dict:
    """Split the token, verify the HMAC signature, return the decoded payload.

    Raises the generic `AuthenticationError` if the format is wrong or the signature
    does not match.
    """
    try:
        payloadPart, chuKy = token.split(".", 1)
    except (ValueError, AttributeError):
        raise AuthenticationError(_GENERIC_AUTH_ERROR)

    chuKyMongDoi = _sign(payloadPart, secretKey)
    # Constant-time comparison to prevent timing attacks.
    if not hmac.compare_digest(chuKy, chuKyMongDoi):
        raise AuthenticationError(_GENERIC_AUTH_ERROR)

    try:
        return json.loads(_b64url_decode(payloadPart))
    except (ValueError, json.JSONDecodeError):
        raise AuthenticationError(_GENERIC_AUTH_ERROR)


def verifyToken(db: Session, token: str) -> TaiKhoan:
    """Verify the token and return the corresponding TaiKhoan (R2.5-7, R2.9, R10.8).

    Checks: valid signature → not expired → session not revoked → account HOAT_DONG.
    Every failure raises `AuthenticationError` with the same generic message so as
    not to reveal which check failed.
    """
    if not isinstance(token, str) or not token:
        raise AuthenticationError(_GENERIC_AUTH_ERROR)

    settings = get_settings()
    payload = _decodeVerified(token, settings.secret_key)

    jti = payload.get("jti")
    expiresAtRaw = payload.get("expiresAt")
    if not jti or not expiresAtRaw:
        raise AuthenticationError(_GENERIC_AUTH_ERROR)

    # Expiry check based on the signed payload (tamper-proof, avoids the DB's tz issue).
    try:
        expiresAt = datetime.fromisoformat(expiresAtRaw)
    except ValueError:
        raise AuthenticationError(_GENERIC_AUTH_ERROR)
    if expiresAt <= _now():
        logger.info("Tu choi token het han: jti=%s", jti)
        raise AuthenticationError(_GENERIC_AUTH_ERROR)

    # The session must exist and not be revoked.
    phien = db.get(PhienXacThuc, jti)
    if phien is None or phien.revokedAt is not None:
        logger.info("Tu choi token: phien khong ton tai hoac da thu hoi (jti=%s)", jti)
        raise AuthenticationError(_GENERIC_AUTH_ERROR)

    # The account must exist and be active.
    taiKhoan = db.get(TaiKhoan, phien.taiKhoanId)
    if taiKhoan is None or taiKhoan.trangThai != TrangThaiTaiKhoan.HOAT_DONG:
        logger.info("Tu choi token: tai khoan khong hop le/vo hieu hoa (jti=%s)", jti)
        raise AuthenticationError(_GENERIC_AUTH_ERROR)

    logger.debug("Xac minh token thanh cong: jti=%s, taiKhoanId=%s", jti, taiKhoan.id)
    return taiKhoan


def revokeToken(db: Session, jti: str) -> None:
    """Revoke an authentication session by setting `revokedAt` (R2.8, R10.8).

    A non-existent session → skip (idempotent). An already-revoked session → keep
    the previous `revokedAt` timestamp.
    """
    phien = db.get(PhienXacThuc, jti)
    if phien is None:
        logger.info("revokeToken: phien khong ton tai (jti=%s), bo qua.", jti)
        return
    if phien.revokedAt is None:
        phien.revokedAt = _now()
        db.commit()
        logger.info("Thu hoi phien xac thuc: jti=%s", jti)


def getTokenJti(token: str) -> str | None:
    """Read the `jti` from the token payload (does NOT verify the signature).

    Only use after a successful `verifyToken` (e.g. `refreshSession` needs the old
    jti to revoke it). Returns None if the token is malformed.
    """
    if not isinstance(token, str) or not token:
        return None
    try:
        payloadPart, _chuKy = token.split(".", 1)
        payload = json.loads(_b64url_decode(payloadPart))
    except (ValueError, AttributeError, json.JSONDecodeError):
        return None
    return payload.get("jti")


def revokeAllSessions(db: Session, taiKhoanId: str, exceptJti: str | None = None) -> int:
    """Revoke every active session of an account (R25.1, R25.4, R10.8).

    Sets `revokedAt` for every non-revoked `PhienXacThuc` of `taiKhoanId`, except the
    `exceptJti` session (if provided — used for password change: keep the current
    session, revoke the others). Returns the number of sessions just revoked.
    Idempotent: an already-revoked session keeps its previous timestamp.
    """
    moc = _now()
    query = (
        db.query(PhienXacThuc)
        .filter(PhienXacThuc.taiKhoanId == taiKhoanId)
        .filter(PhienXacThuc.revokedAt.is_(None))
    )
    if exceptJti is not None:
        query = query.filter(PhienXacThuc.id != exceptJti)

    soThuHoi = 0
    for phien in query.all():
        phien.revokedAt = moc
        soThuHoi += 1
    if soThuHoi:
        db.commit()
        logger.info(
            "Thu hoi %d phien xac thuc cua tai khoan id=%s (giu lai jti=%s).",
            soThuHoi,
            taiKhoanId,
            exceptJti,
        )
    return soThuHoi


def _resetSigningKey(secretKey: str, matKhauHash: str) -> str:
    """Reset-token signing key = secret_key bound to the current matKhauHash.

    Because the signing key depends on `matKhauHash`, once the password changes (the
    hash changes) the old token can no longer be verified → automatically invalidated
    (single-use, R25.4) WITHOUT storing any token state in the DB.
    """
    return f"{secretKey}:{matKhauHash}"


def createResetToken(taiKhoan: TaiKhoan, ttlMinutes: int | None = None) -> str:
    """Create a time-limited, single-use password reset token (R25.2-4).

    The token is stateless: signed with `secret_key + matKhauHash` (the account's
    current hash), with `expiresAt` in the signed payload. The TTL defaults to
    `settings.password_reset_ttl_minutes`. When the password changes, the token is
    automatically invalidated (see `_resetSigningKey`). NEVER logs the token value.
    """
    settings = get_settings()
    ttl = ttlMinutes if ttlMinutes is not None else settings.password_reset_ttl_minutes
    expiresAt = _now() + timedelta(minutes=ttl)

    payload = {"taiKhoanId": taiKhoan.id, "expiresAt": expiresAt.isoformat()}
    payloadPart = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    chuKy = _sign(payloadPart, _resetSigningKey(settings.secret_key, taiKhoan.matKhauHash))

    logger.info("Tao token dat lai mat khau: taiKhoanId=%s", taiKhoan.id)
    return f"{payloadPart}.{chuKy}"


def verifyResetToken(db: Session, tokenReset: str) -> TaiKhoan:
    """Verify a password reset token, return the corresponding TaiKhoan (R25.4).

    Checks: format → account exists and HOAT_DONG → valid signature (signed with the
    current matKhauHash → single-use) → not expired. Every failure raises
    `AuthenticationError` with the same generic message so as not to reveal which
    check failed.
    """
    if not isinstance(tokenReset, str) or not tokenReset:
        raise AuthenticationError(_GENERIC_RESET_ERROR)

    try:
        payloadPart, chuKy = tokenReset.split(".", 1)
    except (ValueError, AttributeError):
        raise AuthenticationError(_GENERIC_RESET_ERROR)

    try:
        payload = json.loads(_b64url_decode(payloadPart))
    except (ValueError, json.JSONDecodeError):
        raise AuthenticationError(_GENERIC_RESET_ERROR)

    taiKhoanId = payload.get("taiKhoanId")
    expiresAtRaw = payload.get("expiresAt")
    if not taiKhoanId or not expiresAtRaw:
        raise AuthenticationError(_GENERIC_RESET_ERROR)

    # Look up the account to get the current matKhauHash (the signing key). A disabled
    # account is not allowed to reset its password.
    taiKhoan = db.get(TaiKhoan, taiKhoanId)
    if taiKhoan is None or taiKhoan.trangThai != TrangThaiTaiKhoan.HOAT_DONG:
        logger.info("Tu choi token reset: tai khoan khong hop le/vo hieu hoa.")
        raise AuthenticationError(_GENERIC_RESET_ERROR)

    settings = get_settings()
    chuKyMongDoi = _sign(
        payloadPart, _resetSigningKey(settings.secret_key, taiKhoan.matKhauHash)
    )
    if not hmac.compare_digest(chuKy, chuKyMongDoi):
        # Signature mismatch: tampering OR the token was already used (matKhauHash changed).
        logger.info("Tu choi token reset: chu ky khong hop le hoac da dung.")
        raise AuthenticationError(_GENERIC_RESET_ERROR)

    try:
        expiresAt = datetime.fromisoformat(expiresAtRaw)
    except ValueError:
        raise AuthenticationError(_GENERIC_RESET_ERROR)
    if expiresAt <= _now():
        logger.info("Tu choi token reset: da het han (taiKhoanId=%s).", taiKhoanId)
        raise AuthenticationError(_GENERIC_RESET_ERROR)

    return taiKhoan
