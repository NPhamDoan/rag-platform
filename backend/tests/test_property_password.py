"""Property-based test for password hashing/verification (R1.2).

# Feature: multi-user-rag-platform, Property 1: Bam mat khau khong luu plaintext
# va xac minh dung — voi moi mat khau hop le (8..64 ky tu, ke ca ky tu tieng Viet
# da byte): (1) hash khong chua plaintext va khac plaintext, (2) verifyPassword voi
# mat khau goc tra True, (3) verifyPassword voi mat khau KHAC tra False, (4) bam cung
# mot mat khau hai lan ra hai hash khac nhau (salt) nhung ca hai deu xac minh dung.

bcrypt is slow, so limit max_examples=100 and disable the deadline (each round performs many
bcrypt hash/verify operations).
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.auth.password import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hashPassword,
    verifyPassword,
)

# Character set of printable ASCII + accented Vietnamese characters (multi-byte UTF-8) to cover
# the case where 64 characters exceed 72 bytes (bcrypt-sha256 prehash, R1.2 note).
_VIETNAMESE = "ăâđêôơưĂÂĐÊÔƠƯáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ"
_PASSWORD_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
    + _VIETNAMESE
)

# Valid password: length in [MIN, MAX] characters (character count, not bytes).
_valid_password = st.text(
    alphabet=_PASSWORD_ALPHABET,
    min_size=MIN_PASSWORD_LENGTH,
    max_size=MAX_PASSWORD_LENGTH,
)


@settings(max_examples=15, deadline=None)
@given(data=st.data())
def test_bam_mat_khau_khong_luu_plaintext_va_xac_minh_dung(data):
    matKhau = data.draw(_valid_password)
    # A DIFFERENT password: valid and different from the original (filter ensures it really differs).
    matKhauKhac = data.draw(
        _valid_password.filter(lambda khac: khac != matKhau)
    )

    matKhauHash = hashPassword(matKhau)

    # (1) The hash does not contain the plaintext and differs from the plaintext.
    assert matKhau not in matKhauHash
    assert matKhauHash != matKhau

    # (2) Verifying the original password → True.
    assert verifyPassword(matKhau, matKhauHash) is True

    # (3) Verifying with a different password → False.
    assert verifyPassword(matKhauKhac, matKhauHash) is False

    # (4) Hashing the same password twice gives two different hashes (salt) but both verify correctly.
    matKhauHashLan2 = hashPassword(matKhau)
    assert matKhauHash != matKhauHashLan2
    assert verifyPassword(matKhau, matKhauHash) is True
    assert verifyPassword(matKhau, matKhauHashLan2) is True
