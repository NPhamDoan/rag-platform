"""Unit tests for password hashing/verification (task 3.1).

Coverage:
- `hashPassword` does not return the plaintext and produces a valid bcrypt hash.
- `verifyPassword` matches the original password and rejects a wrong one.
- Length validation 8..64 characters (boundary and out-of-range -> ValidationError).
- A 64-character Vietnamese password (>72 UTF-8 bytes) still hashes/verifies (R1.2 note).
- `verifyPassword` does not raise on a corrupt hash / wrongly typed input.
- Hashing the same password twice yields two different hashes (salt) yet both verify correctly.
"""

from __future__ import annotations

import pytest

from app.auth.password import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hashPassword,
    verifyPassword,
)
from app.errors import ValidationError


def test_hash_khong_chua_plaintext_va_xac_minh_dung():
    matKhau = "matKhau123"
    matKhauHash = hashPassword(matKhau)

    assert matKhau not in matKhauHash
    assert matKhauHash.startswith("$2")  # bcrypt format prefix
    assert verifyPassword(matKhau, matKhauHash) is True


def test_verify_tu_choi_mat_khau_sai():
    matKhauHash = hashPassword("matKhauDung1")
    assert verifyPassword("matKhauSai99", matKhauHash) is False


def test_hash_cung_mat_khau_ra_hash_khac_nhau_nho_salt():
    matKhau = "trungMatKhau1"
    assert hashPassword(matKhau) != hashPassword(matKhau)
    # Both hashes verify the original password correctly.
    assert verifyPassword(matKhau, hashPassword(matKhau)) is True


@pytest.mark.parametrize(
    "matKhau",
    ["a" * MIN_PASSWORD_LENGTH, "a" * MAX_PASSWORD_LENGTH, "a" * 20],
)
def test_do_dai_hop_le_duoc_chap_nhan(matKhau):
    assert verifyPassword(matKhau, hashPassword(matKhau)) is True


@pytest.mark.parametrize(
    "matKhau",
    ["a" * (MIN_PASSWORD_LENGTH - 1), "", "a" * (MAX_PASSWORD_LENGTH + 1)],
)
def test_do_dai_ngoai_khoang_bi_tu_choi(matKhau):
    with pytest.raises(ValidationError):
        hashPassword(matKhau)


def test_mat_khau_tieng_viet_64_ky_tu_vuot_72_byte_van_hoat_dong():
    # 64 Vietnamese characters with diacritics -> exceeds 72 UTF-8 bytes, checks the bcrypt-sha256 prehash.
    matKhau = "đ" * MAX_PASSWORD_LENGTH
    assert len(matKhau) == MAX_PASSWORD_LENGTH
    assert len(matKhau.encode("utf-8")) > 72
    matKhauHash = hashPassword(matKhau)
    assert verifyPassword(matKhau, matKhauHash) is True
    assert verifyPassword("đ" * (MAX_PASSWORD_LENGTH - 1), matKhauHash) is False


def test_verify_khong_nem_ngoai_le_voi_hash_hong():
    assert verifyPassword("matKhau123", "khong-phai-hash-bcrypt") is False


def test_verify_khong_nem_ngoai_le_voi_dau_vao_sai_kieu():
    assert verifyPassword(None, hashPassword("matKhau123")) is False  # type: ignore[arg-type]
    assert verifyPassword("matKhau123", None) is False  # type: ignore[arg-type]
