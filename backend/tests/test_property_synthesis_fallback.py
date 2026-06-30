"""Property test for the fallback when synthesis errors/times out (task 10.14).

# Feature: multi-user-rag-platform, Property 36: Synthesis error/timeout returns the
# original chunks as a fallback.
# Validates: Requirements 8.4, 16.4

Property 36 — for N chunks (1..8) and a synthesis LLM_Provider that RAISES any
exception (varied type/message) OR a missing provider (None), `QueryPipeline.answerDetail`
degrades safely to the fallback (R8.4): it returns a `KetQuaTraLoi` with
- `laFallback == True`,
- `nhanXacMinh == CHUA_XAC_MINH`,
- `trichDan` with markers == [1..N], each marker n -> the nth chunk (chunkId/
  taiLieuId/noiDung),
- the text of every original chunk appears in `traLoi`.

Uses a Fake/Raising LLM_Provider (injected via the constructor) per the convention in
tests/test_query_pipeline_synthesize.py — no network calls.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.db.models import NhanXacMinh
from app.pipelines.query_pipeline import QueryPipeline
from app.storage.vector_store import META_TAI_LIEU_ID, SearchResult


# --- Fakes (following the pattern in test_query_pipeline_synthesize.py) ------
class ParamRaisingLLM:
    """Fake LLM_Provider that raises a configurable exception (varied type + message)."""

    ten = "param-raising-llm"

    def __init__(self, excClass: type[BaseException], message: str) -> None:
        self.excClass = excClass
        self.message = message
        self.soLanGoi = 0

    def generate(self, systemPrompt: str, userPrompt: str) -> str:
        self.soLanGoi += 1
        raise self.excClass(self.message)


class CountingLLM:
    """Fake LLM_Provider that counts calls (to assert the fallback does NOT call verification)."""

    ten = "counting-llm"

    def __init__(self) -> None:
        self.soLanGoi = 0

    def generate(self, systemPrompt: str, userPrompt: str) -> str:
        self.soLanGoi += 1
        return "đã xác minh"


# Varied exception types simulating error/timeout/network/quota when calling the synthesis LLM.
_EXC_CLASSES = [
    TimeoutError,
    RuntimeError,
    ValueError,
    ConnectionError,
    KeyError,
    Exception,
]


@st.composite
def _searchResult(draw: st.DrawFn) -> SearchResult:
    """Generate a SearchResult with non-empty id/document/taiLieuId + score >= threshold."""
    cid = draw(st.text(min_size=1, max_size=12))
    doc = draw(st.text(min_size=1, max_size=40))
    tlid = draw(st.text(min_size=1, max_size=12))
    score = draw(st.floats(min_value=0.5, max_value=1.0))
    return SearchResult(id=cid, document=doc, metadata={META_TAI_LIEU_ID: tlid}, score=score)


# Synthesis provider: either None (missing provider) or raises any exception.
_synthesisProvider = st.one_of(
    st.none(),
    st.builds(ParamRaisingLLM, st.sampled_from(_EXC_CLASSES), st.text(max_size=30)),
)


# --- Property 36 -----------------------------------------------------------
@settings(max_examples=150)
@given(
    chunks=st.lists(_searchResult(), min_size=1, max_size=8),
    synthesisProvider=_synthesisProvider,
)
def test_tong_hop_loi_hoac_thieu_provider_tra_chunk_goc(chunks, synthesisProvider):
    """Property 36: synthesis error/timeout/missing provider -> fallback to original chunks.

    Validates: Requirements 8.4, 16.4
    """
    verify = CountingLLM()
    pipeline = QueryPipeline(
        synthesisProvider=synthesisProvider, verifyProvider=verify
    )

    kq = pipeline.answerDetail("cau hoi?", chunks)

    # Safe fallback: the fallback flag is set + label CHUA_XAC_MINH (R8.4).
    assert kq.laFallback is True
    assert kq.nhanXacMinh == NhanXacMinh.CHUA_XAC_MINH

    # TrichDan derived from ALL original chunks: markers 1..N, mapping to the nth chunk.
    n = len(chunks)
    assert [td.marker for td in kq.trichDan] == list(range(1, n + 1))
    for i, (td, ch) in enumerate(zip(kq.trichDan, chunks), start=1):
        assert td.marker == i
        assert td.chunkId == ch.id
        assert td.taiLieuId == ch.metadata[META_TAI_LIEU_ID]
        assert td.noiDung == ch.document

    # The text of every original chunk appears in the fallback answer.
    for ch in chunks:
        assert ch.document in kq.traLoi

    # The fallback does NOT call the verification LLM (the answer is the original chunks).
    assert verify.soLanGoi == 0
