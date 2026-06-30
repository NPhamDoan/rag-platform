"""Unit tests for the early QueryPipeline steps (task 10.1).

Coverage:
- `validateQuestion`: length boundaries 1..1000 (R6.3).
- `normalizeQuestion` (R6.7): a question with diacritics is left unchanged; a question
  without diacritics gets diacritics added when the guard confirms the same word set;
  keep the original when the LLM changes/adds/drops words; no provider -> keep original.
- `classifyIntent` (R16.6): deterministic (same input -> same output) plus keyword cases.
- `resolveMode` (R16.7/16.8): a forced mode overrides classification; None -> classify.

LLM_Provider is injected via a fake (no network calls).
"""

from __future__ import annotations

import pytest

from app.errors import ValidationError
from app.pipelines.query_pipeline import (
    CHE_DO_CHI_TIET,
    CHE_DO_TONG_QUAN,
    QueryPipeline,
)


class FakeLLMProvider:
    """Fake LLM_Provider: returns `ketQuaCoDinh` (or a map keyed by userPrompt) + counts calls."""

    ten = "fake-llm"

    def __init__(self, ketQuaCoDinh: str | None = None, mapKetQua: dict[str, str] | None = None):
        self.ketQuaCoDinh = ketQuaCoDinh
        self.mapKetQua = mapKetQua or {}
        self.soLanGoi = 0
        self.lanGoiCuoi: tuple[str, str] | None = None

    def generate(self, systemPrompt: str, userPrompt: str) -> str:
        self.soLanGoi += 1
        self.lanGoiCuoi = (systemPrompt, userPrompt)
        if userPrompt in self.mapKetQua:
            return self.mapKetQua[userPrompt]
        return self.ketQuaCoDinh if self.ketQuaCoDinh is not None else userPrompt


# --- validateQuestion (R6.3) ----------------------------------------------
class TestValidateQuestion:
    def test_cau_hoi_hop_le_tra_ve_da_cat(self):
        pipeline = QueryPipeline()
        assert pipeline.validateQuestion("  toc do toi da  ") == "toc do toi da"

    def test_do_dai_bien_1_va_1000_hop_le(self):
        pipeline = QueryPipeline()
        assert pipeline.validateQuestion("a") == "a"
        cau1000 = "x" * 1000
        assert pipeline.validateQuestion(cau1000) == cau1000

    def test_rong_sau_khi_cat_bi_tu_choi(self):
        pipeline = QueryPipeline()
        with pytest.raises(ValidationError):
            pipeline.validateQuestion("   ")
        with pytest.raises(ValidationError):
            pipeline.validateQuestion("")

    def test_dai_qua_1000_bi_tu_choi(self):
        pipeline = QueryPipeline()
        with pytest.raises(ValidationError):
            pipeline.validateQuestion("x" * 1001)


# --- normalizeQuestion (R6.7) ---------------------------------------------
class TestNormalizeQuestion:
    def test_cau_da_co_dau_giu_nguyen_khong_goi_llm(self):
        fake = FakeLLMProvider(ketQuaCoDinh="khac han")
        pipeline = QueryPipeline(normalizeProvider=fake)
        cauCoDau = "tốc độ tối đa là bao nhiêu"
        assert pipeline.normalizeQuestion(cauCoDau) == cauCoDau
        assert fake.soLanGoi == 0

    def test_them_dau_khi_guard_cung_bo_tu(self):
        # The LLM returns the same word set (only adds diacritics) -> accept the result.
        fake = FakeLLMProvider(ketQuaCoDinh="tốc độ tối đa là bao nhiêu")
        pipeline = QueryPipeline(normalizeProvider=fake)
        ketQua = pipeline.normalizeQuestion("toc do toi da la bao nhieu")
        assert ketQua == "tốc độ tối đa là bao nhiêu"
        assert fake.soLanGoi == 1

    def test_giu_goc_khi_llm_doi_tu(self):
        # The LLM adds a new word ("khac") -> word set differs -> keep the original.
        fake = FakeLLMProvider(ketQuaCoDinh="tốc độ tối đa khác là bao nhiêu")
        pipeline = QueryPipeline(normalizeProvider=fake)
        cauGoc = "toc do toi da la bao nhieu"
        assert pipeline.normalizeQuestion(cauGoc) == cauGoc

    def test_giu_goc_khi_llm_bot_tu(self):
        fake = FakeLLMProvider(ketQuaCoDinh="tốc độ tối đa")
        pipeline = QueryPipeline(normalizeProvider=fake)
        cauGoc = "toc do toi da la bao nhieu"
        assert pipeline.normalizeQuestion(cauGoc) == cauGoc

    def test_khong_provider_giu_goc(self):
        pipeline = QueryPipeline()
        cauGoc = "toc do toi da la bao nhieu"
        assert pipeline.normalizeQuestion(cauGoc) == cauGoc

    def test_dung_verify_provider_khi_normalize_none(self):
        # R13.4: when normalize is empty -> use the verification provider.
        verify = FakeLLMProvider(ketQuaCoDinh="tốc độ tối đa")
        pipeline = QueryPipeline(verifyProvider=verify)
        pipeline.normalizeQuestion("toc do toi da")
        assert verify.soLanGoi == 1

    def test_provider_loi_giu_goc(self):
        class ProviderLoi:
            ten = "loi"

            def generate(self, systemPrompt: str, userPrompt: str) -> str:
                raise RuntimeError("mang loi")

        pipeline = QueryPipeline(normalizeProvider=ProviderLoi())
        cauGoc = "toc do toi da"
        assert pipeline.normalizeQuestion(cauGoc) == cauGoc


# --- classifyIntent (R16.6) -----------------------------------------------
class TestClassifyIntent:
    def test_xac_dinh_cung_input_cung_output(self):
        pipeline = QueryPipeline()
        cau = "tóm tắt giúp tôi tài liệu này"
        ketQua1 = pipeline.classifyIntent(cau)
        ketQua2 = pipeline.classifyIntent(cau)
        assert ketQua1 == ketQua2 == CHE_DO_TONG_QUAN

    @pytest.mark.parametrize(
        "cau",
        [
            "tóm tắt giúp tôi",
            "tom tat giup toi",
            "tài liệu gồm những mục nào",
            "tai lieu gom nhung muc nao",
            "cho tôi tổng quan nội dung",
            "tôi có thể hỏi gì",
            "liệt kê các mục",
            "nội dung chung là gì",
        ],
    )
    def test_cau_tong_quan(self, cau):
        pipeline = QueryPipeline()
        assert pipeline.classifyIntent(cau) == CHE_DO_TONG_QUAN

    @pytest.mark.parametrize(
        "cau",
        [
            "tốc độ tối đa trong khu dân cư là bao nhiêu",
            "điều 650 quy định gì về thừa kế",
            "mức phạt khi vượt đèn đỏ",
        ],
    )
    def test_cau_chi_tiet(self, cau):
        pipeline = QueryPipeline()
        assert pipeline.classifyIntent(cau) == CHE_DO_CHI_TIET


# --- resolveMode (R16.7, R16.8) -------------------------------------------
class TestResolveMode:
    def test_ep_tong_quan_ghi_de_phan_loai(self):
        pipeline = QueryPipeline()
        # An inherently detail question forced to overview -> follow the forced mode.
        assert pipeline.resolveMode("mức phạt vượt đèn đỏ", CHE_DO_TONG_QUAN) == CHE_DO_TONG_QUAN

    def test_ep_chi_tiet_ghi_de_phan_loai(self):
        pipeline = QueryPipeline()
        # An inherently overview question forced to detail -> follow the forced mode.
        assert pipeline.resolveMode("tóm tắt tài liệu", CHE_DO_CHI_TIET) == CHE_DO_CHI_TIET

    def test_none_dung_phan_loai(self):
        pipeline = QueryPipeline()
        assert pipeline.resolveMode("tóm tắt tài liệu", None) == CHE_DO_TONG_QUAN
        assert pipeline.resolveMode("mức phạt vượt đèn đỏ", None) == CHE_DO_CHI_TIET
