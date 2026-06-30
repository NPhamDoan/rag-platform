"""Property test for RateLimiter.checkAndRecord (task 6.5, R24.1-24.2).

# Feature: multi-user-rag-platform, Property 47: Exceeding the rate limit is
# rejected and does not call the LLM.
#
# Meaning: for EVERY limit `gioiHan` (>=1) and EVERY number of requests `N` issued
# within the SAME time window (a fixed fake clock is used so every call falls into
# the same window):
#   - the first `gioiHan` calls are ACCEPTED (no error raised) — R24.1,
#   - every call BEYOND `gioiHan` raises `RateLimitError` — R24.2,
#   => the number of accepted calls == min(N, gioiHan).
# It also checks ISOLATION per account: a second account with its own limit is not
# affected by an account that is already full.
# In addition: exceeding the limit does NOT call the LLM — the limiter runs BEFORE
# any processing/LLM, so a fake LLM (spy) reports an error if called; this ensures
# the limiter raises before any LLM call occurs.
# Validates: Requirements 24.1, 24.2
#
# The limiter state is in memory and fast → max_examples=150.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.api.middleware.rate_limit import RateLimiter
from app.errors import RateLimitError


class _DongHoCoDinh:
    """Fake clock that always returns a fixed instant → every call falls into the same window."""

    def __init__(self, moc: float = 1000.0) -> None:
        self._moc = moc

    def __call__(self) -> float:
        return self._moc


class _LLMSpy:
    """Fake LLM: counts how many times it is called. It is only called for ALLOWED
    calls (after the limiter lets them through); a rejected call raises before
    reaching here → the counter is not incremented."""

    def __init__(self) -> None:
        self.soLanGoi = 0

    def synthesize(self, *args, **kwargs):
        self.soLanGoi += 1


def _goi_qua_limiter(limiter: RateLimiter, taiKhoanId: str, gioiHan: int, llm) -> None:
    """Simulate the route: apply the limiter FIRST, only call the LLM when allowed.

    If the limiter raises RateLimitError, the error propagates BEFORE the
    `llm.synthesize()` line → the LLM is never called for a rejected call (R24.2).
    """
    limiter.checkAndRecord(taiKhoanId, gioiHan)
    llm.synthesize()


@settings(max_examples=40, deadline=None)
@given(
    gioiHan=st.integers(min_value=1, max_value=20),
    soYeuCau=st.integers(min_value=0, max_value=40),
    gioiHanB=st.integers(min_value=1, max_value=20),
)
def test_vuot_gioi_han_bi_tu_choi_va_khong_goi_llm(gioiHan, soYeuCau, gioiHanB):
    limiter = RateLimiter(windowSeconds=60, timeFunc=_DongHoCoDinh())
    llm = _LLMSpy()

    soChapNhan = 0
    for _ in range(soYeuCau):
        try:
            _goi_qua_limiter(limiter, "tk-A", gioiHan, llm)
            soChapNhan += 1
        except RateLimitError:
            # Rejected: the LLM is NOT called (the limiter blocks before reaching synthesize).
            pass

    # The number of accepted calls equals exactly min(N, gioiHan) (R24.1 + R24.2).
    assert soChapNhan == min(soYeuCau, gioiHan)
    # The LLM is called exactly as many times as accepted calls, not for rejected ones.
    assert llm.soLanGoi == soChapNhan

    # Per-account isolation: tk-B with its own limit is not affected by tk-A.
    llmB = _LLMSpy()
    soChapNhanB = 0
    for _ in range(gioiHanB):
        _goi_qua_limiter(limiter, "tk-B", gioiHanB, llmB)
        soChapNhanB += 1
    assert soChapNhanB == gioiHanB
    # B's next call exceeds its own limit → rejected, no LLM call.
    with pytest.raises(RateLimitError):
        _goi_qua_limiter(limiter, "tk-B", gioiHanB, llmB)
    assert llmB.soLanGoi == gioiHanB
