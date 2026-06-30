"""Property-based test for ApiKeyService — uses the right user key + isolation (Property 54).

# Feature: multi-user-rag-platform, Property 54: Uses the right user's key and is
# isolated between users — for a population of multiple accounts, where each account
# setApiKey's an arbitrary set of (provider, vaiTro) pairs with its own key value
# (two accounts MAY use the same (provider, vaiTro) but with different values),
# getApiKey(taiKhoan, provider, vaiTro) ALWAYS returns that account's OWN correct key
# (R22.4) at every pair that was set, and returns None at any pair not set — NEVER
# returning another account's key (isolation, R22.5).
# Validates: Requirements 22.4, 22.5

Model: Hypothesis generates a number of accounts + a list of assignments
(chiSoTaiKhoan, provider, vaiTro, khoaGoc). The key value actually stored is prefixed
with the account index (`"{i}|{khoaGoc}"`) so each account gets a DISTINCT value —
which means any cross-account leak would be detectable. A later assignment overwrites
an earlier one for the same (taiKhoan, provider, vaiTro) (upsert).

After setting, we walk the ENTIRE space (every account) x (every provider) x (every
vaiTro) and assert that getApiKey returns that account's own expected value (or None
if not set), never crossing over to another account. Each example uses its OWN
in-memory SQLite session; accounts are created directly (no bcrypt) → fast,
max_examples=100.
"""

from __future__ import annotations

from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import TaiKhoan
from app.services.api_key_service import ApiKeyService


@contextmanager
def _fresh_session():
    """A fresh in-memory SQLite session (schema from Base.metadata) — cleaned up after each round."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


# Fixed (provider, vaiTro) space, kept small so we can walk it fully and hit duplicate pairs easily.
_PROVIDERS = ["groq", "gemini", "openai", "ollama"]
_VAI_TRO = ["synthesis", "verify", "normalize"]


@st.composite
def _quanThe(draw):
    """Generate (number of accounts, list of key assignments)."""
    soTaiKhoan = draw(st.integers(min_value=2, max_value=4))
    phepGan = draw(
        st.lists(
            st.tuples(
                st.integers(min_value=0, max_value=soTaiKhoan - 1),
                st.sampled_from(_PROVIDERS),
                st.sampled_from(_VAI_TRO),
                st.text(min_size=1, max_size=16),
            ),
            min_size=0,
            max_size=24,
        )
    )
    return soTaiKhoan, phepGan


@settings(max_examples=100, deadline=None)
@given(quanThe=_quanThe())
def test_dung_dung_khoa_nguoi_dung_va_co_lap(quanThe):
    soTaiKhoan, phepGan = quanThe
    with _fresh_session() as db:
        service = ApiKeyService(db)

        # Create the population of accounts directly (no bcrypt).
        taiKhoanList = [
            TaiKhoan(
                email=f"u{i}@x.com",
                tenDangNhap=f"u{i}",
                matKhauHash="h",
            )
            for i in range(soTaiKhoan)
        ]
        db.add_all(taiKhoanList)
        db.commit()

        # Expected model: (chiSo, provider, vaiTro) -> the key actually set.
        # A later assignment overwrites an earlier one (upsert).
        kyVong: dict[tuple[int, str, str], str] = {}
        for chiSo, provider, vaiTro, khoaGoc in phepGan:
            # Prefix with the account index → each account gets a DISTINCT value,
            # even when khoaGoc collides across accounts.
            khoaThuc = f"{chiSo}|{khoaGoc}"
            service.setApiKey(taiKhoanList[chiSo], provider, vaiTro, khoaThuc)
            kyVong[(chiSo, provider, vaiTro)] = khoaThuc

        # Walk the ENTIRE space (account x provider x vaiTro) and check.
        for chiSo in range(soTaiKhoan):
            tienToCuaToi = f"{chiSo}|"
            for provider in _PROVIDERS:
                for vaiTro in _VAI_TRO:
                    ketQua = service.getApiKey(taiKhoanList[chiSo], provider, vaiTro)
                    mongDoi = kyVong.get((chiSo, provider, vaiTro))

                    # (R22.4) Returns the account's own key at a pair that was set;
                    # None at a pair not set.
                    assert ketQua == mongDoi

                    # (R22.5) Isolation: the returned value (if any) MUST carry the
                    # calling account's own prefix — never another account's key.
                    if ketQua is not None:
                        assert ketQua.startswith(tienToCuaToi)
