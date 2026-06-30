"""Auth_Service — account authentication logic (task 3.5: `register`).

`AuthService` gathers registration / login / session management logic (other
methods are added in later tasks). This class holds a SQLAlchemy `Session` and
runs transactional operations on it, following the exact signature in design.md
(Auth_Service section):

    class AuthService:
        def register(self, email, tenDangNhap, matKhau) -> TaiKhoan: ...

`register` (R1):
- Validates input through the `RegisterInput` DTO (a single source of truth for the
  rules: email <=254 + valid format, tenDangNhap 3-30, matKhau 8-64, required
  fields) — pydantic errors are translated into a domain-level `ValidationError`
  that names the offending field.
- Checks for duplicate email / tenDangNhap → `ConflictError` naming the duplicated
  field (R1.3).
- Hashes the password with `hashPassword` (bcrypt) — NEVER stores/logs plaintext (R1.2).
- `VaiTro` defaults to NGUOI_DUNG, `trangThai` to HOAT_DONG (ORM defaults).
- Creates a default `HanMuc` record alongside the account (1-1 relationship) so that
  quota checks in later tasks have a configuration ready.
- Commits the transaction; a DB-level UNIQUE error (race condition) → `ConflictError`
  (rollback).

The module logs through the centralized logger. NEVER logs plaintext passwords.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.password import hashPassword, verifyPassword
from app.auth.tokens import (
    createResetToken,
    createToken,
    getTokenJti,
    revokeAllSessions,
    revokeToken,
    verifyResetToken,
    verifyToken,
)
from app.config import get_settings
from app.db.models import HanMuc, TaiKhoan, TrangThaiTaiKhoan, VaiTro
from app.errors import AuthenticationError, ConflictError, LockedError, ValidationError
from app.models.schemas import RegisterInput

logger = logging.getLogger(__name__)

# Generic login message — does NOT reveal whether the account exists / wrong
# password / disabled (R2.2). Every authentication failure uses exactly this message.
_GENERIC_LOGIN_ERROR = "Ten dang nhap hoac mat khau khong dung."
# Message when the account is temporarily locked (R2.4) — the lock state is tracked
# separately per the design, but this does NOT reveal authentication details.
_LOCKED_ERROR = "Tai khoan tam thoi bi khoa do dang nhap sai nhieu lan. Vui long thu lai sau."

# Message when changing password and the current password is wrong (R25.1).
_WRONG_CURRENT_PASSWORD = "Mat khau hien tai khong dung."

# Per-field error messages (R1.4, R1.5, R1.7) — used when the DTO reports an error.
_FIELD_ERROR_MESSAGES = {
    "email": "email khong dung dinh dang hoac dai qua 254 ky tu.",
    "tenDangNhap": "tenDangNhap phai tu 3 den 30 ky tu.",
    "matKhau": "matKhau phai tu 8 den 64 ky tu.",
}


def _translateValidationError(exc: PydanticValidationError) -> ValidationError:
    """Translate a RegisterInput pydantic error into a domain ValidationError.

    Prefers the first error; a missing field (R1.6) → names the missing field, the
    rest → uses the per-field message (R1.4/1.5/1.7).
    """
    for loi in exc.errors():
        truong = loi["loc"][0] if loi["loc"] else ""
        if loi["type"] == "missing":
            return ValidationError(f"Thieu truong bat buoc: {truong}.")
        thongDiep = _FIELD_ERROR_MESSAGES.get(str(truong))
        if thongDiep:
            return ValidationError(thongDiep)
    return ValidationError("Du lieu dang ky khong hop le.")


class AuthService:
    """Authentication service that operates on a single SQLAlchemy `Session`."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def register(self, email: str, tenDangNhap: str, matKhau: str) -> TaiKhoan:
        """Register a new account (R1).

        Returns the created `TaiKhoan` (VaiTro=NGUOI_DUNG, trangThai=HOAT_DONG).
        Raises `ValidationError` when the input has an invalid format/length/missing
        field, or `ConflictError` when email/tenDangNhap already exists (naming the
        duplicated field).
        """
        # 1) Validate input through the DTO (single source of truth for the R1 rules).
        try:
            dto = RegisterInput(email=email, tenDangNhap=tenDangNhap, matKhau=matKhau)
        except PydanticValidationError as exc:
            loi = _translateValidationError(exc)
            logger.info("Tu choi dang ky: %s", loi.message)
            raise loi from exc

        # 2) Check for duplicate email / tenDangNhap → name the duplicated field (R1.3).
        if self.db.query(TaiKhoan).filter(TaiKhoan.email == dto.email).first() is not None:
            logger.info("Tu choi dang ky: email da ton tai.")
            raise ConflictError("email da ton tai.", details={"truong": "email"})
        if (
            self.db.query(TaiKhoan)
            .filter(TaiKhoan.tenDangNhap == dto.tenDangNhap)
            .first()
            is not None
        ):
            logger.info("Tu choi dang ky: tenDangNhap da ton tai.")
            raise ConflictError(
                "tenDangNhap da ton tai.", details={"truong": "tenDangNhap"}
            )

        # 3) Hash the password (does NOT store plaintext) — R1.2.
        matKhauHash = hashPassword(dto.matKhau)

        # 4) Create the TaiKhoan (VaiTro/trangThai use ORM defaults) + default HanMuc.
        taiKhoan = TaiKhoan(
            email=dto.email,
            tenDangNhap=dto.tenDangNhap,
            matKhauHash=matKhauHash,
            vaiTro=VaiTro.NGUOI_DUNG,
        )
        taiKhoan.hanMuc = HanMuc()

        self.db.add(taiKhoan)
        try:
            self.db.commit()
        except IntegrityError as exc:
            # Race condition: the DB-level UNIQUE constraint is the final safety net.
            self.db.rollback()
            logger.info("Tu choi dang ky: vi pham rang buoc duy nhat (email/tenDangNhap).")
            raise ConflictError("email hoac tenDangNhap da ton tai.") from exc

        self.db.refresh(taiKhoan)
        logger.info(
            "Dang ky tai khoan thanh cong: id=%s, tenDangNhap=%s, vaiTro=%s",
            taiKhoan.id,
            taiKhoan.tenDangNhap,
            taiKhoan.vaiTro.value,
        )
        return taiKhoan

    def login(self, tenDangNhap: str, matKhau: str) -> tuple[str, VaiTro]:
        """Log in (R2.1-4, R10.7).

        Returns `(token, vaiTro)` on success: creates a `PhienXacThuc` (TTL =
        `session_ttl_minutes`) + an HMAC token carrying the role. Steps:

        1. Look up the account by `tenDangNhap`. Not found → generic auth error.
        2. Account VO_HIEU_HOA → generic auth error (R10.7) — does NOT reveal whether
           it is disabled or the credentials are wrong (R2.2).
        3. Temporarily locked (`khoaDenThoiDiem` in the future) → `LockedError`,
           rejected EVEN when the password is correct (R2.4). Expired lock → reset
           the counter.
        4. Wrong password → increment `soLanDangNhapThatBai`; on reaching
           `login_max_fails` → set `khoaDenThoiDiem = now + login_lock_minutes`;
           raise the generic auth error.
        5. Correct password → reset the counter + clear the lock, create session +
           token, return `(token, vaiTro)`.

        Every authentication failure uses THE SAME generic message (R2.2). NEVER logs
        plaintext passwords or tokens.
        """
        settings = get_settings()

        taiKhoan = (
            self.db.query(TaiKhoan)
            .filter(TaiKhoan.tenDangNhap == tenDangNhap)
            .first()
        )

        # 1) Account does not exist → generic error (no disclosure).
        if taiKhoan is None:
            logger.info("Dang nhap that bai: tai khoan khong ton tai.")
            raise AuthenticationError(_GENERIC_LOGIN_ERROR)

        # 2) Account disabled → generic error (R10.7, kept generic per R2.2).
        if taiKhoan.trangThai != TrangThaiTaiKhoan.HOAT_DONG:
            logger.info(
                "Dang nhap that bai: tai khoan vo hieu hoa (id=%s).", taiKhoan.id
            )
            raise AuthenticationError(_GENERIC_LOGIN_ERROR)

        # 3) Check the temporary lock (R2.4). An active lock → reject even when the
        #    password is correct. An expired lock → reset the counter to grant 5 fresh
        #    attempts.
        if taiKhoan.khoaDenThoiDiem is not None:
            if _ensureAware(taiKhoan.khoaDenThoiDiem) > _now():
                logger.info(
                    "Dang nhap bi tu choi: tai khoan dang bi khoa (id=%s).",
                    taiKhoan.id,
                )
                raise LockedError(_LOCKED_ERROR)
            # Lock expired → clear the lock, reset the counter before handling this attempt.
            taiKhoan.khoaDenThoiDiem = None
            taiKhoan.soLanDangNhapThatBai = 0

        # 4) Verify the password.
        if not verifyPassword(matKhau, taiKhoan.matKhauHash):
            taiKhoan.soLanDangNhapThatBai += 1
            if taiKhoan.soLanDangNhapThatBai >= settings.login_max_fails:
                taiKhoan.khoaDenThoiDiem = _now() + timedelta(
                    minutes=settings.login_lock_minutes
                )
                logger.info(
                    "Khoa tai khoan sau %d lan dang nhap that bai (id=%s).",
                    taiKhoan.soLanDangNhapThatBai,
                    taiKhoan.id,
                )
            else:
                logger.info(
                    "Dang nhap that bai: sai mat khau (id=%s, soLan=%d).",
                    taiKhoan.id,
                    taiKhoan.soLanDangNhapThatBai,
                )
            self.db.commit()
            raise AuthenticationError(_GENERIC_LOGIN_ERROR)

        # 5) Success → reset the counter + clear the lock, create session + token.
        taiKhoan.soLanDangNhapThatBai = 0
        taiKhoan.khoaDenThoiDiem = None
        # createToken commits both the session record and the changes on taiKhoan.
        token = createToken(self.db, taiKhoan)

        logger.info(
            "Dang nhap thanh cong: id=%s, tenDangNhap=%s, vaiTro=%s",
            taiKhoan.id,
            taiKhoan.tenDangNhap,
            taiKhoan.vaiTro.value,
        )
        return token, taiKhoan.vaiTro

    def logout(self, jti: str) -> None:
        """Log out: revoke the current PhienXacThuc (R2.8, R2.9).

        Sets `revokedAt` for the `jti` session → that session's token is rejected on
        the next verification. Idempotent: a non-existent / already-revoked session
        is skipped.
        """
        revokeToken(self.db, jti)
        logger.info("Dang xuat: thu hoi phien jti=%s", jti)

    def refreshSession(self, token: str) -> str:
        """Refresh the session: issue a new token and revoke the old one (R25.5).

        Verifies that `token` is still valid (not expired, not revoked, account
        HOAT_DONG); creates a new PhienXacThuc + token then revokes the old session
        (token rotation within the safety limits, without forcing a re-login). An
        invalid token → AuthenticationError.
        """
        taiKhoan = verifyToken(self.db, token)
        jtiCu = getTokenJti(token)

        tokenMoi = createToken(self.db, taiKhoan)
        if jtiCu is not None:
            revokeToken(self.db, jtiCu)

        logger.info(
            "Lam moi phien: tai khoan id=%s, thu hoi jti cu=%s", taiKhoan.id, jtiCu
        )
        return tokenMoi

    def changePassword(
        self,
        taiKhoan: TaiKhoan,
        matKhauCu: str,
        matKhauMoi: str,
        jtiHienTai: str | None = None,
    ) -> None:
        """Change the password and revoke OTHER sessions (R25.1).

        Requires the current password to be correct (`AuthenticationError` if wrong);
        the new password must be 8-64 characters (`hashPassword` raises
        `ValidationError` if out of range). After updating the hash, revokes every
        other session of the account; the current session (`jtiHienTai`, if passed)
        is kept. NEVER logs plaintext passwords.
        """
        # 1) Verify the current password.
        if not verifyPassword(matKhauCu, taiKhoan.matKhauHash):
            logger.info("Tu choi doi mat khau: sai mat khau hien tai (id=%s).", taiKhoan.id)
            raise AuthenticationError(_WRONG_CURRENT_PASSWORD)

        # 2) Validate + hash the new password (hashPassword checks the 8-64 length).
        matKhauHashMoi = hashPassword(matKhauMoi)

        # 3) Update the hash + revoke other sessions (keep the current one if present).
        taiKhoan.matKhauHash = matKhauHashMoi
        self.db.commit()
        revokeAllSessions(self.db, taiKhoan.id, exceptJti=jtiHienTai)

        logger.info("Doi mat khau thanh cong: id=%s", taiKhoan.id)

    def requestPasswordReset(self, email: str) -> None:
        """Request a password reset — generic response (R25.2, R25.3).

        If the email matches a HOAT_DONG account, create a time-limited reset token
        (to enable sending the link / usage testing). Whether or not the email
        exists, the method raises nothing and returns nothing → does NOT reveal
        whether the email exists. Does NOT log the token value / email.
        """
        taiKhoan = self.db.query(TaiKhoan).filter(TaiKhoan.email == email).first()
        if taiKhoan is not None and taiKhoan.trangThai == TrangThaiTaiKhoan.HOAT_DONG:
            createResetToken(taiKhoan)
            logger.info("Da tao lien ket dat lai mat khau cho tai khoan id=%s.", taiKhoan.id)
        else:
            logger.info("Yeu cau dat lai mat khau cho email khong khop tai khoan hoat dong.")
        # Generic response: always return None regardless of the lookup result (R25.3).
        return None

    def resetPassword(self, tokenReset: str, matKhauMoi: str) -> None:
        """Reset the password using a reset token (R25.4).

        Verifies the token (not expired + single-use); the new password must be 8-64
        characters. After updating the hash: the token is automatically invalidated
        (the signing key depends on matKhauHash) and every session of the account is
        revoked. An invalid/expired/already-used token → AuthenticationError.
        """
        taiKhoan = verifyResetToken(self.db, tokenReset)

        # Validate + hash the new password before changing the hash (the token is not
        # consumed if the new password is invalid).
        matKhauHashMoi = hashPassword(matKhauMoi)

        taiKhoan.matKhauHash = matKhauHashMoi
        self.db.commit()
        # The reset token is now invalid (the hash changed); revoke every open session (R25.4).
        revokeAllSessions(self.db, taiKhoan.id)

        logger.info("Dat lai mat khau thanh cong: id=%s", taiKhoan.id)

    def deleteOwnAccount(self, taiKhoan: TaiKhoan) -> None:
        """Self-delete the account: remove all data + revoke all sessions (R25.6).

        Deletes the TaiKhoan record; the ORM cascade also deletes
        KhongGianTaiLieu → TaiLieu → Chunk, LichSuTroChuyen → TrichDan, ChiaSe,
        HanMuc, KhoaApiNguoiDung and PhienXacThuc (ending every session). Logging in /
        using an old token afterward is no longer possible.
        """
        taiKhoanId = taiKhoan.id
        self.db.delete(taiKhoan)
        self.db.commit()
        logger.info("Tu xoa tai khoan thanh cong: id=%s (cascade xoa du lieu + phien).", taiKhoanId)


def _now() -> datetime:
    """Current time (UTC, tz-aware) — consistent with models._now."""
    return datetime.now(timezone.utc)


def _ensureAware(dt: datetime) -> datetime:
    """Ensure a datetime is tz-aware (UTC) for safe comparison.

    SQLite (via SQLAlchemy DateTime) returns naive datetimes; treat them as UTC to
    avoid errors comparing tz-aware and naive datetimes.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
