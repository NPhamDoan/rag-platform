"""Property test (task 10.5) for QueryPipeline.resolveMode: forcing a mode overrides classification.

# Feature: multi-user-rag-platform, Property 40: Ep che do tra loi ghi de phan loai tu dong
# Validates: Requirements 16.7, 16.8

A single Hypothesis test generates arbitrary text plus a forced mode and checks two invariants:

  1. Forcing a mode (R16.7/16.8): for cheDo in {CHE_DO_TONG_QUAN, CHE_DO_CHI_TIET},
     resolveMode(text, cheDo) == cheDo, OVERRIDING automatic classification (regardless
     of what classifyIntent(text) returns).
  2. Not forcing (R16.6): when cheDo is None, resolveMode(text, None) == classifyIntent(text).

Uses the CHE_DO_* constants from the module (no hardcoding). No DB/LLM is needed, so
`QueryPipeline()` is instantiated without arguments. The generator deliberately also
produces text containing overview keywords (e.g. "tong quan", "tom tat") -> classifyIntent
= overview, to ensure that forcing the "detail" mode REALLY differs from the automatic
classification result in some examples.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.pipelines.query_pipeline import (
    CHE_DO_CHI_TIET,
    CHE_DO_TONG_QUAN,
    OVERVIEW_KEYWORDS,
    QueryPipeline,
)

# The valid modes that can be forced (overriding classification).
_CHE_DO_HOP_LE = (CHE_DO_TONG_QUAN, CHE_DO_CHI_TIET)


def _strategyText() -> st.SearchStrategy[str]:
    """Generate arbitrary text, weighted toward both overview and detail natures.

    - Arbitrary text (mostly -> detail, since it does not match keywords).
    - Sentences seeded with overview keywords (e.g. "tong quan", "tom tat", "co nhung gi")
      -> classifyIntent = overview; used to ensure forcing "detail" really overrides.
    """
    tuyY = st.text(max_size=200)
    # Take some overview keywords + wrap them with text on both sides -> still matches
    # (since classifyIntent matches regardless of diacritics and by substring).
    cauTongQuan = st.builds(
        lambda dau, tuKhoa, cuoi: f"{dau} {tuKhoa} {cuoi}",
        st.text(max_size=30),
        st.sampled_from(OVERVIEW_KEYWORDS),
        st.text(max_size=30),
    )
    return st.one_of(tuyY, cauTongQuan)


@settings(max_examples=200, deadline=None)
@given(
    text=_strategyText(),
    cheDoEp=st.sampled_from(_CHE_DO_HOP_LE),
)
def test_property_ep_che_do_ghi_de_phan_loai(text: str, cheDoEp: str):
    pipeline = QueryPipeline()

    # (1) Forcing a valid mode -> overrides automatic classification, returns the forced mode.
    assert pipeline.resolveMode(text, cheDoEp) == cheDoEp

    # (2) Not forcing (None) -> use classifyIntent.
    assert pipeline.resolveMode(text, None) == pipeline.classifyIntent(text)


def test_ep_thuc_su_ghi_de_khi_khac_phan_loai():
    """Supplementary: for an overview-natured sentence, forcing detail really overrides (and vice versa)."""
    pipeline = QueryPipeline()

    cauTongQuan = "tóm tắt giúp tôi tài liệu này"
    assert pipeline.classifyIntent(cauTongQuan) == CHE_DO_TONG_QUAN
    # Force detail -> overrides the overview classification.
    assert pipeline.resolveMode(cauTongQuan, CHE_DO_CHI_TIET) == CHE_DO_CHI_TIET

    cauChiTiet = "mức phạt vượt đèn đỏ là bao nhiêu"
    assert pipeline.classifyIntent(cauChiTiet) == CHE_DO_CHI_TIET
    # Force overview -> overrides the detail classification.
    assert pipeline.resolveMode(cauChiTiet, CHE_DO_TONG_QUAN) == CHE_DO_TONG_QUAN
