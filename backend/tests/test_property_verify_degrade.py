"""Property test (task 10.13) — verification error/timeout degrades safely to CHUA_XAC_MINH.

# Feature: multi-user-rag-platform, Property 35: Verification error/timeout degrades
# safely to "not verified"
# Validates: Requirements 8.2, 8.3

Property 35: when the verification provider RAISES ANY error (any Exception type,
including `TimeoutError`) OR the verification provider is missing (`None`),
`QueryPipeline.verifyAnswer` ALWAYS returns `NhanXacMinh.CHUA_XAC_MINH` and NEVER lets
the error propagate (safe degradation). Randomly generates the exception type
(TimeoutError, RuntimeError, ValueError, ConnectionError, ...) and an arbitrary
message; the fake provider's `generate()` raises that error.

Uses a FAKE LLM_Provider — no network calls.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.db.models import NhanXacMinh
from app.pipelines.query_pipeline import QueryPipeline
from app.storage.vector_store import META_TAI_LIEU_ID, SearchResult


class RaisingVerifyLLM:
    """Fake LLM_Provider: `generate()` always raises an instance of `excClass` with `message`.

    Simulates an error/timeout when calling the verification provider; records the call
    count to confirm it actually called (and swallowed the error) rather than skipping it.
    """

    ten = "raising-verify-llm"

    def __init__(self, excClass: type[BaseException], message: str) -> None:
        self.excClass = excClass
        self.message = message
        self.soLanGoi = 0

    def generate(self, systemPrompt: str, userPrompt: str) -> str:
        self.soLanGoi += 1
        raise self.excClass(self.message)


def _chunks() -> list[SearchResult]:
    """Two minimal chunks as verification context (content does not affect the label)."""
    return [
        SearchResult(id="c1", document="Noi dung doan mot", metadata={META_TAI_LIEU_ID: "tl-a"}, score=0.9),
        SearchResult(id="c2", document="Noi dung doan hai", metadata={META_TAI_LIEU_ID: "tl-b"}, score=0.8),
    ]


# Representative exception types (including timeout, network errors, value/run-time errors...).
_EXC_CLASSES = (
    TimeoutError,
    RuntimeError,
    ValueError,
    ConnectionError,
    OSError,
    KeyError,
    Exception,
)
_exc_class = st.sampled_from(_EXC_CLASSES)
_exc_message = st.text(max_size=80)


# Feature: multi-user-rag-platform, Property 35: Verification error/timeout degrades
# safely to "not verified" (R8.2/8.3).
@settings(max_examples=150)
@given(excClass=_exc_class, message=_exc_message)
def test_loi_xac_minh_suy_bien_chua_xac_minh(excClass: type[BaseException], message: str) -> None:
    """The verification provider raises any error -> verifyAnswer returns CHUA_XAC_MINH, does not raise."""
    provider = RaisingVerifyLLM(excClass, message)
    pipeline = QueryPipeline(verifyProvider=provider)

    nhan = pipeline.verifyAnswer("cau tra loi bat ky", _chunks())

    assert nhan == NhanXacMinh.CHUA_XAC_MINH
    assert provider.soLanGoi == 1  # actually called and swallowed the error safely


def test_thieu_provider_suy_bien_chua_xac_minh() -> None:
    """Missing verification provider (None) -> verifyAnswer returns CHUA_XAC_MINH, does not raise."""
    pipeline = QueryPipeline(verifyProvider=None)
    assert pipeline.verifyAnswer("cau tra loi bat ky", _chunks()) == NhanXacMinh.CHUA_XAC_MINH
