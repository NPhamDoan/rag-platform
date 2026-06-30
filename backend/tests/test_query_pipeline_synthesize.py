"""Unit tests for synthesize + verifyAnswer + fallback (task 10.10).

Coverage (R7.1-7.6, R8.1-8.4):
- `synthesize`: maps `[n]` markers <-> TrichDan in the range 1..N (parallel); ignores
  out-of-range markers; the system prompt always includes INVARIANT_SAFETY_CONSTRAINTS
  + supports MauPrompt override.
- `verifyAnswer`: maps the LLM result to exactly ONE of the three NhanXacMinh; a provider
  error -> CHUA_XAC_MINH (safe degradation); a missing provider -> CHUA_XAC_MINH.
- `answerDetail`: synthesis error/timeout -> fallback to the source chunks + laFallback=True
  + CHUA_XAC_MINH; success -> attach the verification label, laFallback=False.

Uses a FAKE LLM_Provider (injected via the constructor) per project conventions — no
network calls.
"""

from __future__ import annotations

from app.db.models import NhanXacMinh
from app.pipelines.query_pipeline import QueryPipeline
from app.prompts.system_prompts import INVARIANT_SAFETY_CONSTRAINTS
from app.storage.vector_store import META_TAI_LIEU_ID, SearchResult


# --- Fakes ------------------------------------------------------------------
class FakeLLM:
    """Fake LLM_Provider: returns the fixed `phanHoi`; records the system/user prompt received."""

    ten = "fake-llm"

    def __init__(self, phanHoi: str) -> None:
        self.phanHoi = phanHoi
        self.systemPromptDaNhan: str | None = None
        self.userPromptDaNhan: str | None = None
        self.soLanGoi = 0

    def generate(self, systemPrompt: str, userPrompt: str) -> str:
        self.soLanGoi += 1
        self.systemPromptDaNhan = systemPrompt
        self.userPromptDaNhan = userPrompt
        return self.phanHoi


class RaisingLLM:
    """Fake LLM_Provider that always raises (simulates an error/timeout)."""

    ten = "raising-llm"

    def __init__(self) -> None:
        self.soLanGoi = 0

    def generate(self, systemPrompt: str, userPrompt: str) -> str:
        self.soLanGoi += 1
        raise TimeoutError("mo phong timeout goi LLM")


def _chunk(i: str, doc: str, taiLieuId: str = "tl-1", score: float = 0.9) -> SearchResult:
    return SearchResult(id=i, document=doc, metadata={META_TAI_LIEU_ID: taiLieuId}, score=score)


def _chunks() -> list[SearchResult]:
    return [
        _chunk("c1", "Noi dung doan mot", taiLieuId="tl-a"),
        _chunk("c2", "Noi dung doan hai", taiLieuId="tl-b"),
        _chunk("c3", "Noi dung doan ba", taiLieuId="tl-c"),
    ]


# --- synthesize: marker <-> TrichDan in 1..N, parallel (R7.4/7.5) -------
class TestSynthesize:
    def test_marker_hop_le_song_anh_voi_trich_dan(self):
        # The answer uses markers [1] and [3] -> exactly the two corresponding TrichDan.
        synthesis = FakeLLM("Khang dinh A [1]. Khang dinh B [3].")
        pipeline = QueryPipeline(synthesisProvider=synthesis)
        traLoi, trichDan = pipeline.synthesize("cau hoi?", _chunks())

        assert traLoi == "Khang dinh A [1]. Khang dinh B [3]."
        markers = [td.marker for td in trichDan]
        assert markers == [1, 3]  # parallels the markers that actually appear, ascending
        # Marker n -> the n-th chunk (chunkId/taiLieuId/noiDung).
        assert trichDan[0].chunkId == "c1"
        assert trichDan[0].taiLieuId == "tl-a"
        assert trichDan[0].noiDung == "Noi dung doan mot"
        assert trichDan[1].chunkId == "c3"
        assert trichDan[1].taiLieuId == "tl-c"

    def test_bo_qua_marker_ngoai_khoang(self):
        # N=3; markers [4] and [0] are out of range -> ignored; only [2] is valid.
        synthesis = FakeLLM("Sai [4]. Sai [0]. Dung [2].")
        pipeline = QueryPipeline(synthesisProvider=synthesis)
        _traLoi, trichDan = pipeline.synthesize("cau hoi?", _chunks())
        assert [td.marker for td in trichDan] == [2]
        assert trichDan[0].chunkId == "c2"

    def test_marker_lap_lai_chi_mot_trich_dan(self):
        synthesis = FakeLLM("A [1]. B [1]. C [1].")
        pipeline = QueryPipeline(synthesisProvider=synthesis)
        _traLoi, trichDan = pipeline.synthesize("cau hoi?", _chunks())
        assert [td.marker for td in trichDan] == [1]

    def test_khong_marker_thi_khong_trich_dan(self):
        synthesis = FakeLLM("Cau tra loi khong co marker nao.")
        pipeline = QueryPipeline(synthesisProvider=synthesis)
        _traLoi, trichDan = pipeline.synthesize("cau hoi?", _chunks())
        assert trichDan == []

    def test_system_prompt_kem_invariant_va_mau_prompt_override(self):
        synthesis = FakeLLM("Tra loi [1].")
        pipeline = QueryPipeline(synthesisProvider=synthesis)
        pipeline.synthesize("cau hoi?", _chunks(), mauPrompt="MAU PROMPT TUY BIEN")
        # The MauPrompt override is used as the base but STILL keeps the immutable INVARIANT.
        assert "MAU PROMPT TUY BIEN" in synthesis.systemPromptDaNhan
        assert INVARIANT_SAFETY_CONSTRAINTS in synthesis.systemPromptDaNhan

    def test_thieu_provider_tong_hop_nem_loi(self):
        import pytest

        from app.errors import InternalError

        pipeline = QueryPipeline(synthesisProvider=None)
        with pytest.raises(InternalError):
            pipeline.synthesize("cau hoi?", _chunks())


# --- verifyAnswer: label mapping + safe degradation (R8.1-8.3) ----------------
class TestVerifyAnswer:
    def test_anh_xa_da_xac_minh(self):
        pipeline = QueryPipeline(verifyProvider=FakeLLM("đã xác minh"))
        assert pipeline.verifyAnswer("tra loi", _chunks()) == NhanXacMinh.DA_XAC_MINH

    def test_anh_xa_co_mau_thuan(self):
        pipeline = QueryPipeline(verifyProvider=FakeLLM("có mâu thuẫn rõ ràng"))
        assert pipeline.verifyAnswer("tra loi", _chunks()) == NhanXacMinh.CO_MAU_THUAN

    def test_anh_xa_chua_xac_minh(self):
        pipeline = QueryPipeline(verifyProvider=FakeLLM("chưa xác minh được"))
        assert pipeline.verifyAnswer("tra loi", _chunks()) == NhanXacMinh.CHUA_XAC_MINH

    def test_phan_hoi_la_suy_bien_chua_xac_minh(self):
        # A response matching no label -> safe default CHUA_XAC_MINH.
        pipeline = QueryPipeline(verifyProvider=FakeLLM("blah blah khong ro"))
        assert pipeline.verifyAnswer("tra loi", _chunks()) == NhanXacMinh.CHUA_XAC_MINH

    def test_loi_provider_suy_bien_chua_xac_minh(self):
        verify = RaisingLLM()
        pipeline = QueryPipeline(verifyProvider=verify)
        assert pipeline.verifyAnswer("tra loi", _chunks()) == NhanXacMinh.CHUA_XAC_MINH
        assert verify.soLanGoi == 1  # called and swallowed the error safely

    def test_thieu_provider_suy_bien_chua_xac_minh(self):
        pipeline = QueryPipeline(verifyProvider=None)
        assert pipeline.verifyAnswer("tra loi", _chunks()) == NhanXacMinh.CHUA_XAC_MINH


# --- answerDetail: fallback when synthesis fails (R7.6/R8.4) --------------------
class TestAnswerDetailFallback:
    def test_tong_hop_loi_tra_chunk_goc_fallback(self):
        synthesis = RaisingLLM()
        verify = FakeLLM("đã xác minh")
        pipeline = QueryPipeline(synthesisProvider=synthesis, verifyProvider=verify)
        kq = pipeline.answerDetail("cau hoi?", _chunks())

        assert kq.laFallback is True
        assert kq.nhanXacMinh == NhanXacMinh.CHUA_XAC_MINH
        # TrichDan derived from all the source chunks.
        assert [td.marker for td in kq.trichDan] == [1, 2, 3]
        # The answer is assembled from the source chunks.
        assert "Noi dung doan mot" in kq.traLoi
        # Fallback does NOT call verification (the answer is the raw source chunks).
        assert verify.soLanGoi == 0

    def test_thieu_synthesis_provider_fallback(self):
        pipeline = QueryPipeline(synthesisProvider=None)
        kq = pipeline.answerDetail("cau hoi?", _chunks())
        assert kq.laFallback is True
        assert kq.nhanXacMinh == NhanXacMinh.CHUA_XAC_MINH

    def test_tong_hop_thanh_cong_gan_nhan_xac_minh(self):
        synthesis = FakeLLM("Tra loi [1] va [2].")
        verify = FakeLLM("đã xác minh")
        pipeline = QueryPipeline(synthesisProvider=synthesis, verifyProvider=verify)
        kq = pipeline.answerDetail("cau hoi?", _chunks())

        assert kq.laFallback is False
        assert kq.nhanXacMinh == NhanXacMinh.DA_XAC_MINH
        assert [td.marker for td in kq.trichDan] == [1, 2]
        assert verify.soLanGoi == 1
