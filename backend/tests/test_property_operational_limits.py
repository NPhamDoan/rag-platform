"""Property test (task 12.4) for ConfigService.updateOperationalLimits.

# Feature: multi-user-rag-platform, Property 49: Hop le hoa va ap dung gioi han van hanh
# Validates: Requirements 23.1, 23.2, 23.3

Property under test (R23.1/23.2/23.3): for arbitrary (llmTimeout, sessionTtl,
maxFileSize) values configured by the ADMIN, the update SUCCEEDS if and only if EVERY value
falls within its valid range (predicate computed independently from the config constants);
valid values are APPLIED at runtime (the `Settings` source that the runtime reads reflects the
new values, including `max_file_size_bytes`); out-of-range values are rejected by the
`LimitsInput` DTO (`ValidationError`) and the runtime Settings stay UNCHANGED (atomic, no
partial update).

The generator spans BEYOND the valid range across all 3 dimensions so both branches (valid /
invalid) are exercised.

CRITICAL: snapshot the original Settings values at the start of each example and restore them in
`finally` (Hypothesis runs many examples; mutating the global Settings singleton must not leak
into other tests).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from app.config import (
    LLM_TIMEOUT_MAX,
    LLM_TIMEOUT_MIN,
    MAX_FILE_SIZE_MB_MAX,
    MAX_FILE_SIZE_MB_MIN,
    MB,
    SESSION_TTL_MAX,
    SESSION_TTL_MIN,
    get_settings,
)
from app.db.models import TaiKhoan, VaiTro
from app.models.schemas import LimitsInput
from app.services.config_service import ConfigService


def _trong_khoang_hop_le(llmTimeout: int, sessionTtl: int, maxFileSize: int) -> bool:
    """Independent predicate: EVERY value must fall within the range declared in config."""
    return (
        LLM_TIMEOUT_MIN <= llmTimeout <= LLM_TIMEOUT_MAX
        and SESSION_TTL_MIN <= sessionTtl <= SESSION_TTL_MAX
        and MAX_FILE_SIZE_MB_MIN <= maxFileSize <= MAX_FILE_SIZE_MB_MAX
    )


# Span BEYOND the valid range on both ends to exercise both branches.
_llm_timeout = st.integers(min_value=LLM_TIMEOUT_MIN - 10, max_value=LLM_TIMEOUT_MAX + 50)
_session_ttl = st.integers(min_value=SESSION_TTL_MIN - 10, max_value=SESSION_TTL_MAX + 100)
_max_file_size = st.integers(
    min_value=MAX_FILE_SIZE_MB_MIN - 5, max_value=MAX_FILE_SIZE_MB_MAX + 100
)


@given(llmTimeout=_llm_timeout, sessionTtl=_session_ttl, maxFileSize=_max_file_size)
@settings(max_examples=200, deadline=None)
def test_property_hop_le_hoa_va_ap_dung_gioi_han_van_hanh(
    llmTimeout: int, sessionTtl: int, maxFileSize: int
):
    # Feature: multi-user-rag-platform, Property 49: Hop le hoa va ap dung gioi han van hanh
    # Validates: Requirements 23.1, 23.2, 23.3
    admin = TaiKhoan(
        email="ad@x.com", tenDangNhap="admin", matKhauHash="h", vaiTro=VaiTro.QUAN_TRI
    )
    service = ConfigService(db=None)

    settings_runtime = get_settings()
    goc = (
        settings_runtime.llm_timeout_seconds,
        settings_runtime.session_ttl_minutes,
        settings_runtime.max_file_size_mb,
    )
    try:
        hopLe = _trong_khoang_hop_le(llmTimeout, sessionTtl, maxFileSize)

        if hopLe:
            # VALID branch: the DTO accepts, the service applies at runtime (R23.1, R23.3).
            limits = LimitsInput(
                llmTimeout=llmTimeout, sessionTtl=sessionTtl, maxFileSize=maxFileSize
            )
            ketQua = service.updateOperationalLimits(admin, limits)

            assert ketQua.llmTimeout == llmTimeout
            assert ketQua.sessionTtl == sessionTtl
            assert ketQua.maxFileSize == maxFileSize

            # The runtime source (Settings) reflects the new values — including the derived property.
            assert settings_runtime.llm_timeout_seconds == llmTimeout
            assert settings_runtime.session_ttl_minutes == sessionTtl
            assert settings_runtime.max_file_size_mb == maxFileSize
            assert settings_runtime.max_file_size_bytes == maxFileSize * MB
        else:
            # INVALID branch: the DTO rejects, Settings stay UNCHANGED (R23.2, atomic).
            with pytest.raises(PydanticValidationError):
                LimitsInput(
                    llmTimeout=llmTimeout,
                    sessionTtl=sessionTtl,
                    maxFileSize=maxFileSize,
                )
            assert (
                settings_runtime.llm_timeout_seconds,
                settings_runtime.session_ttl_minutes,
                settings_runtime.max_file_size_mb,
            ) == goc
    finally:
        # Restore the Settings singleton to its original values (no leakage to other tests).
        (
            settings_runtime.llm_timeout_seconds,
            settings_runtime.session_ttl_minutes,
            settings_runtime.max_file_size_mb,
        ) = goc
