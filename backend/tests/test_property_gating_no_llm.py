"""Property test for threshold gating during retrieve (task 10.8).

# Feature: multi-user-rag-platform, Property 31: Loc nguong khong goi LLM tong hop
# Validates: Requirements 6.5, 6.6

Property: for an arbitrary top score `s` and two configured thresholds
(`nguongKhongTimThay` <= `nguongDuLienQuan`, both in [0, 1]),
`QueryPipeline.retrieve` (gating) returns a status of:
  * KHONG_TIM_THAY      <=> s < nguongKhongTimThay
  * CHUA_DU_LIEN_QUAN   <=> nguongKhongTimThay <= s < nguongDuLienQuan
  * DU_LIEN_QUAN        <=> s >= nguongDuLienQuan
AND in the two below-threshold cases, the synthesis provider (SynthesisSpy) is NEVER
called (soLanGoi == 0) and `chunks` is empty; in DU_LIEN_QUAN it returns chunks (and
retrieve also does NOT call synthesis, since that is task 10.10).

Uses FakeEmbeddingProvider + FakeVectorStore + SynthesisSpy as in
`tests/test_query_pipeline_retrieve.py`; both LLM and Embedding are fakes (no network calls).
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.pipelines.query_pipeline import QueryPipeline, TrangThaiTruyXuat
from app.storage.vector_store import SearchResult

from tests.test_query_pipeline_retrieve import (
    FakeEmbeddingProvider,
    FakeVectorStore,
    SynthesisSpy,
    _cfg,
    _khong_gian,
)

# Generate a score and two thresholds in [0, 1] (rounded to avoid floating-point noise).
_DIEM = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@settings(max_examples=200)
@given(s=_DIEM, a=_DIEM, b=_DIEM)
def test_gating_nguong_khong_goi_llm(s: float, a: float, b: float) -> None:
    # Ensure the lower threshold <= the upper threshold.
    nguongKhongTimThay, nguongDuLienQuan = sorted((a, b))

    synthesis = SynthesisSpy()
    store = FakeVectorStore([SearchResult("c1", "noi dung", {}, score=s)])
    pipeline = QueryPipeline(
        embeddingProvider=FakeEmbeddingProvider(),
        vectorStore=store,
        synthesisProvider=synthesis,
    )
    cfg = _cfg(nguongKhongTimThay=nguongKhongTimThay, nguongDuLienQuan=nguongDuLienQuan)

    kq = pipeline.retrieve(_khong_gian(), "cau hoi", cfg)

    if s < nguongKhongTimThay:
        assert kq.trangThai == TrangThaiTruyXuat.KHONG_TIM_THAY
        assert kq.chunks == []
        assert synthesis.soLanGoi == 0  # do not call synthesis LLM when below threshold (R6.5)
    elif s < nguongDuLienQuan:
        assert kq.trangThai == TrangThaiTruyXuat.CHUA_DU_LIEN_QUAN
        assert kq.chunks == []
        assert synthesis.soLanGoi == 0  # do not call synthesis LLM when not relevant enough (R6.6)
    else:
        assert kq.trangThai == TrangThaiTruyXuat.DU_LIEN_QUAN
        assert [c.id for c in kq.chunks] == ["c1"]  # return chunks when relevant enough
        # retrieve also does not call synthesis (synthesis is task 10.10).
        assert synthesis.soLanGoi == 0
