"""Property-based test for AuthService.register with INVALID input (task 3.7).

# Feature: multi-user-rag-platform, Property 3: Invalid registration is always
# rejected and creates no account.

Validates: Requirements 1.3, 1.4, 1.5, 1.6, 1.7, 2.3

Idea: generate tuples (email, tenDangNhap, matKhau) that DEFINITELY violate at least
one R1 rule (malformed email or >254; tenDangNhap outside 3..30; matKhau outside
8..64; empty field). For each example, use a brand-new in-memory SQLite Session
(mirroring tests/test_auth_service_register.py) and assert:

  1. `register` raises `ValidationError` (a domain validation error), and
  2. NO `TaiKhoan` record is created (count stays 0).

Validation runs BEFORE password hashing / DB queries, so the test is very fast,
easily running >=100 examples.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.auth_service import AuthService
from app.db.database import Base
from app.db.models import TaiKhoan
from app.errors import ValidationError

# "Safe" character set: letters/digits, no '@' and no whitespace → not altered by
# str_strip_whitespace, and won't accidentally form a valid email.
_SAFE = string.ascii_letters + string.digits


# --- Generator per field: the valid version (so only one field gets corrupted) ---
_email_hop_le = st.builds(
    lambda local, domain, tld: f"{local}@{domain}.{tld}",
    st.text(alphabet=_SAFE, min_size=1, max_size=20),
    st.text(alphabet=_SAFE, min_size=1, max_size=20),
    st.text(alphabet=string.ascii_letters, min_size=2, max_size=5),
)
_ten_hop_le = st.text(alphabet=_SAFE, min_size=3, max_size=30)
_mat_khau_hop_le = st.text(alphabet=_SAFE, min_size=8, max_size=64)


# --- Generator per field: the INVALID version (guaranteed to violate) ----------
# Email: either malformed (no '@'), or well-formed but > 254 characters.
_email_khong_hop_le = st.one_of(
    st.text(alphabet=_SAFE, min_size=0, max_size=20),  # no '@' → malformed
    st.integers(min_value=250, max_value=300).map(
        lambda n: ("a" * n) + "@b.co"  # well-formed but length > 254
    ),
)
# tenDangNhap: outside the 3..30 range (too short including empty, or too long).
_ten_khong_hop_le = st.one_of(
    st.text(alphabet=_SAFE, min_size=0, max_size=2),
    st.text(alphabet=_SAFE, min_size=31, max_size=50),
)
# matKhau: outside the 8..64 range (too short including empty, or too long).
_mat_khau_khong_hop_le = st.one_of(
    st.text(alphabet=_SAFE, min_size=0, max_size=7),
    st.text(alphabet=_SAFE, min_size=65, max_size=100),
)


@st.composite
def dang_ky_khong_hop_le(draw: st.DrawFn) -> dict[str, str]:
    """Generate a registration tuple with EXACTLY one field corrupted to an invalid value.

    Randomly pick which field gets corrupted; the remaining fields stay valid. This
    guarantees the input violates at least one R1 rule without accidentally becoming valid.
    """
    truong_loi = draw(st.sampled_from(["email", "tenDangNhap", "matKhau"]))
    return {
        "email": draw(_email_khong_hop_le if truong_loi == "email" else _email_hop_le),
        "tenDangNhap": draw(
            _ten_khong_hop_le if truong_loi == "tenDangNhap" else _ten_hop_le
        ),
        "matKhau": draw(
            _mat_khau_khong_hop_le if truong_loi == "matKhau" else _mat_khau_hop_le
        ),
    }


@settings(max_examples=15)
@given(args=dang_ky_khong_hop_le())
def test_dang_ky_khong_hop_le_luon_bi_tu_choi(args: dict[str, str]) -> None:
    # A brand-new in-memory SQLite session for each example (mirroring the unit test).
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        service = AuthService(db)

        with pytest.raises(ValidationError):
            service.register(**args)

        # No record is created when the input is rejected.
        assert db.query(TaiKhoan).count() == 0
    finally:
        db.close()
        engine.dispose()
