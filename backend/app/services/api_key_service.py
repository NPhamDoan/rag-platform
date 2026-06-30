"""ApiKeyService — user API key management (BYOK) and key resolution (R22).

Implements the signatures in design.md (ApiKeyService section):

    class ApiKeyService:
        def setApiKey(self, taiKhoan, providerTen, vaiTro, khoa) -> None: ...
        def getApiKey(self, taiKhoan, providerTen, vaiTro) -> str | None: ...
        def getMaskedKeys(self, taiKhoan) -> list[KhoaApiMasked]: ...
        def deleteApiKey(self, taiKhoan, providerTen, vaiTro) -> None: ...

Adds `resolveKey` for the key-resolution policy (R22.7).

## Per-user key isolation (R22.5)

Every key query/update/delete ALWAYS filters by the calling account's `taiKhoanId`.
As a result, account A's keys are never returned to / deleted by account B.

## Encryption at rest (R22.2)

Keys are encrypted with Fernet (`auth/crypto.py`) before being stored in
`KhoaApiNguoiDung.khoaMaHoa`. Plaintext is NEVER stored/logged (R22.2, R22.3).

## Masking on display (R22.3)

`getMaskedKeys` returns only the masked form (`****` + last 4 characters), never the
plaintext over the API.

## System key source (R22.7)

`resolveKey` prefers the user's own key; if absent it falls back to the **default
system key** read from environment variables following the convention
`SYSTEM_API_KEY_<PROVIDER>` (provider name uppercased, non-alphanumeric characters →
`_`). E.g. provider "groq" → `SYSTEM_API_KEY_GROQ`. If both the user key and the
system key are missing → `ValidationError` with a clear message REQUIRING a key to be
configured, and the provider is NOT called (R22.6, R22.7).

Logging goes through the central logger; errors are NEVER swallowed silently; key
values are NEVER logged.
"""

from __future__ import annotations

import logging
import os
import re

from sqlalchemy.orm import Session

from app.auth.crypto import decryptSecret, encryptSecret
from app.db.models import KhoaApiNguoiDung, TaiKhoan
from app.errors import ValidationError
from app.models.schemas import KhoaApiMasked

logger = logging.getLogger(__name__)

# Environment variable prefix for the default system key (R22.7).
_SYSTEM_KEY_PREFIX = "SYSTEM_API_KEY_"
# Non-alphanumeric characters in the provider name → "_" when building the env var name.
_KHONG_PHAI_CHU_SO = re.compile(r"[^A-Z0-9]")


def _maskKey(plaintext: str) -> str:
    """Mask a key on display (R22.3): `****` + last 4 characters.

    Key <= 4 characters → fully masked (fixed number of leading stars, length not
    leaked). Never exposes more than the last 4 characters.
    """
    if len(plaintext) <= 4:
        return "****"
    return "****" + plaintext[-4:]


def _systemKeyEnvName(providerTen: str) -> str:
    """Environment variable name of the system key for a provider (R22.7)."""
    chuanHoa = _KHONG_PHAI_CHU_SO.sub("_", providerTen.upper())
    return f"{_SYSTEM_KEY_PREFIX}{chuanHoa}"


class ApiKeyService:
    """User API key management service (BYOK), operating on a single `Session`."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Internal helpers --------------------------------------------------
    def _layBanGhi(
        self, taiKhoanId: str, providerTen: str, vaiTro: str
    ) -> KhoaApiNguoiDung | None:
        """Fetch the key record of the EXACT calling account (isolation by taiKhoanId, R22.5)."""
        return (
            self.db.query(KhoaApiNguoiDung)
            .filter(
                KhoaApiNguoiDung.taiKhoanId == taiKhoanId,
                KhoaApiNguoiDung.providerTen == providerTen,
                KhoaApiNguoiDung.vaiTro == vaiTro,
            )
            .first()
        )

    # --- Insert / update ----------------------------------------------------
    def setApiKey(
        self, taiKhoan: TaiKhoan, providerTen: str, vaiTro: str, khoa: str
    ) -> None:
        """Encrypt and upsert the API key for (taiKhoan, providerTen, vaiTro) (R22.1, R22.2).

        If a record already exists → update the ciphertext; otherwise → create a new
        one. The plaintext `khoa` value is NEVER logged (R22.2, R22.3).
        """
        khoaMaHoa = encryptSecret(khoa)

        banGhi = self._layBanGhi(taiKhoan.id, providerTen, vaiTro)
        if banGhi is None:
            banGhi = KhoaApiNguoiDung(
                taiKhoanId=taiKhoan.id,
                providerTen=providerTen,
                vaiTro=vaiTro,
                khoaMaHoa=khoaMaHoa,
            )
            self.db.add(banGhi)
            hanhDong = "tao moi"
        else:
            banGhi.khoaMaHoa = khoaMaHoa
            hanhDong = "cap nhat"

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi luu khoa API: taiKhoanId=%s, provider=%s, vaiTro=%s",
                taiKhoan.id,
                providerTen,
                vaiTro,
            )
            raise

        logger.info(
            "Luu khoa API thanh cong (%s): taiKhoanId=%s, provider=%s, vaiTro=%s",
            hanhDong,
            taiKhoan.id,
            providerTen,
            vaiTro,
        )

    # --- Read (internal use) ------------------------------------------------
    def getApiKey(
        self, taiKhoan: TaiKhoan, providerTen: str, vaiTro: str
    ) -> str | None:
        """Decrypt and return the OWN calling account's key (R22.4, R22.5).

        Not configured → `None`. Filtering by `taiKhoanId` means another account's key
        is never returned (isolation, R22.5). For internal use (e.g. when calling
        LLM/embedding); the plaintext is NOT exposed over the API.
        """
        banGhi = self._layBanGhi(taiKhoan.id, providerTen, vaiTro)
        if banGhi is None:
            return None
        return decryptSecret(banGhi.khoaMaHoa)

    # --- Display (masked) ---------------------------------------------------
    def getMaskedKeys(self, taiKhoan: TaiKhoan) -> list[KhoaApiMasked]:
        """List the account's API keys in MASKED form (R22.3) — never plaintext.

        Isolation by `taiKhoanId`: returns only the calling account's own keys (R22.5).
        """
        banGhiList = (
            self.db.query(KhoaApiNguoiDung)
            .filter(KhoaApiNguoiDung.taiKhoanId == taiKhoan.id)
            .all()
        )
        ketQua = [
            KhoaApiMasked(
                providerTen=banGhi.providerTen,
                vaiTro=banGhi.vaiTro,
                khoaChe=_maskKey(decryptSecret(banGhi.khoaMaHoa)),
            )
            for banGhi in banGhiList
        ]
        logger.info(
            "Liet ke %d khoa API (che) cho taiKhoanId=%s",
            len(ketQua),
            taiKhoan.id,
        )
        return ketQua

    # --- Delete -------------------------------------------------------------
    def deleteApiKey(
        self, taiKhoan: TaiKhoan, providerTen: str, vaiTro: str
    ) -> None:
        """Delete the calling account's API key (R22.1) — IDEMPOTENT.

        No record → skip (idempotent). Isolation by `taiKhoanId` means another
        account's key cannot be deleted (R22.5).
        """
        banGhi = self._layBanGhi(taiKhoan.id, providerTen, vaiTro)
        if banGhi is None:
            logger.info(
                "Xoa khoa API idempotent: khong co ban ghi (taiKhoanId=%s, "
                "provider=%s, vaiTro=%s)",
                taiKhoan.id,
                providerTen,
                vaiTro,
            )
            return

        try:
            self.db.delete(banGhi)
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi xoa khoa API: taiKhoanId=%s, provider=%s, vaiTro=%s",
                taiKhoan.id,
                providerTen,
                vaiTro,
            )
            raise

        logger.info(
            "Xoa khoa API thanh cong: taiKhoanId=%s, provider=%s, vaiTro=%s",
            taiKhoan.id,
            providerTen,
            vaiTro,
        )

    # --- Key resolution (policy R22.7) -------------------------------------
    def resolveKey(self, taiKhoan: TaiKhoan, providerTen: str, vaiTro: str) -> str:
        """Resolve the key for a role: user key → system key (R22.6-7).

        Priority order:
        1. The user's own key (if configured) — R22.4.
        2. The default system key from the env var `SYSTEM_API_KEY_<PROVIDER>` — R22.7.
        3. Neither present → `ValidationError` with a clear message REQUIRING a key to
           be configured, the provider is NOT called, and no key detail is leaked
           (R22.6, R22.7).
        """
        khoaNguoiDung = self.getApiKey(taiKhoan, providerTen, vaiTro)
        if khoaNguoiDung:
            logger.info(
                "Phan giai khoa: dung khoa nguoi dung (taiKhoanId=%s, provider=%s, "
                "vaiTro=%s)",
                taiKhoan.id,
                providerTen,
                vaiTro,
            )
            return khoaNguoiDung

        khoaHeThong = os.environ.get(_systemKeyEnvName(providerTen))
        if khoaHeThong:
            logger.info(
                "Phan giai khoa: dung khoa he thong mac dinh (provider=%s, vaiTro=%s)",
                providerTen,
                vaiTro,
            )
            return khoaHeThong

        logger.info(
            "Phan giai khoa that bai: thieu khoa nguoi dung va khoa he thong "
            "(taiKhoanId=%s, provider=%s, vaiTro=%s) — khong goi provider.",
            taiKhoan.id,
            providerTen,
            vaiTro,
        )
        raise ValidationError(
            f"Chua cau hinh khoa API cho provider '{providerTen}' (vai tro "
            f"'{vaiTro}'). Vui long nhap khoa API de tiep tuc."
        )
