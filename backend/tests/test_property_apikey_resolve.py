"""Property test for ApiKeyService.resolveKey (task 9.5, R22.7).

# Feature: multi-user-rag-platform, Property 55: System-key fallback or an error
# without calling the provider.

Meaning: the key resolution policy for a role is a deterministic priority cascade:
  1. A user key exists  → return that exact user key (R22.4).
  2. No user key but a system key exists (environment variable
     SYSTEM_API_KEY_<PROVIDER>) → return the system key (R22.7).
  3. Neither exists → a clear `ValidationError` REQUESTING key configuration.

On "without calling the provider": `resolveKey` takes no provider parameter and only
returns a key string (or raises) — no provider object is involved. So the "no provider
call" requirement is structural; on the missing-key branch we simply assert that the
exception is raised.

Generates two independent boolean variables (hasUserKey, hasSystemKey) plus a varying
provider (which affects the environment variable name via `_systemKeyEnvName`). Each
example uses its own in-memory SQLite session; the account is created directly (fake
matKhauHash, avoiding bcrypt). The environment variable is set/removed via monkeypatch
based on `_systemKeyEnvName`.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings, strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import TaiKhoan
from app.errors import ValidationError
from app.services.api_key_service import ApiKeyService, _systemKeyEnvName

_VAI_TRO = "synthesis"
_KHOA_NGUOI_DUNG = "sk-nguoi-dung-0001"
_KHOA_HE_THONG = "sk-he-thong-0002"


def _make_session():
    """A brand-new in-memory SQLite + schema from Base.metadata (fresh for each example)."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine, Session()


@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    hasUserKey=st.booleans(),
    hasSystemKey=st.booleans(),
    providerTen=st.sampled_from(["groq", "gemini", "ollama", "open-ai", "x.ai"]),
)
def test_resolve_key_du_phong_he_thong_hoac_bao_loi(
    hasUserKey, hasSystemKey, providerTen, monkeypatch
):
    engine, session = _make_session()
    try:
        service = ApiKeyService(session)
        taiKhoan = TaiKhoan(email="chu@x.com", tenDangNhap="chu", matKhauHash="h")
        session.add(taiKhoan)
        session.commit()

        # System key state: clear it first, set it only when hasSystemKey.
        envName = _systemKeyEnvName(providerTen)
        monkeypatch.delenv(envName, raising=False)
        if hasSystemKey:
            monkeypatch.setenv(envName, _KHOA_HE_THONG)

        # User key state.
        if hasUserKey:
            service.setApiKey(taiKhoan, providerTen, _VAI_TRO, _KHOA_NGUOI_DUNG)

        if hasUserKey:
            # The user key takes absolute priority, whether or not a system key exists.
            assert service.resolveKey(taiKhoan, providerTen, _VAI_TRO) == _KHOA_NGUOI_DUNG
        elif hasSystemKey:
            # Falls back to the system key from the environment variable.
            assert service.resolveKey(taiKhoan, providerTen, _VAI_TRO) == _KHOA_HE_THONG
        else:
            # Both missing → a clear error; no provider is called
            # (resolveKey does not reference any provider — a structural property).
            try:
                service.resolveKey(taiKhoan, providerTen, _VAI_TRO)
                raise AssertionError(
                    "resolveKey phai nem ValidationError khi thieu ca hai khoa"
                )
            except ValidationError:
                pass
    finally:
        session.close()
        engine.dispose()
