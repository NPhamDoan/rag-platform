"""Property test for Property 51 — the normalize role falls back to the verify provider (task 10.19).

Under test: `QueryPipeline.normalizeQuestion` with an un-accented question.
Per R13.4, when the normalize role (`normalizeProvider`) is empty (None), the pipeline
MUST fall back to the verify provider (`verifyProvider`) to add diacritics.

INVARIANTS to check (3 branches):
- normalizeProvider=None, verifyProvider=set -> `verifyProvider.generate` IS called
  (the normalize role falls back to the verify provider).
- normalizeProvider=set (possibly with verifyProvider) -> `normalizeProvider` IS used;
  `verifyProvider` is NOT called for normalization.
- both None -> return the original question intact, calling NO provider.

REUSES `FakeLLMProvider` from `tests/test_query_pipeline_normalize_intent.py` (counts the number
of calls). Pure logic + fake LLM -> fast, max_examples=150.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.pipelines.query_pipeline import QueryPipeline

from tests.test_query_pipeline_normalize_intent import FakeLLMProvider

# Diacritics table (acute accent) for ASCII vowels: produce an "accented" version with the
# SAME word set as the original sentence, so the fake LLM returns a result the guard accepts
# (only adding diacritics, not changing/adding/removing words).
_BANG_DAU = {"a": "\u00e1", "e": "\u00e9", "i": "\u00ed", "o": "\u00f3", "u": "\u00fa", "y": "\u00fd"}


def _themDau(text: str) -> str:
    """Add an acute accent to each ASCII vowel; other characters stay unchanged (SAME word set)."""
    return "".join(_BANG_DAU.get(ch, ch) for ch in text)


# Un-accented sentence: a-z tokens of length 1..8; 1..6 words => at least one vowel is not
# guaranteed, but the guard only affects the return value, NOT whether the provider is called.
_tu = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8)
_danhSachTu = st.lists(_tu, min_size=1, max_size=6)


# Feature: multi-user-rag-platform, Property 51: Vai tro chuan hoa trong dung provider xac minh.
# Validates: Requirements 13.4
@settings(max_examples=150, deadline=None)
@given(cacTu=_danhSachTu, coVerifyKemNormalize=st.booleans())
def test_vai_tro_chuan_hoa_trong_dung_provider_xac_minh(cacTu, coVerifyKemNormalize):
    cauKhongDau = " ".join(cacTu)
    coDau = _themDau(cauKhongDau)  # accented version with the same word set -> guard accepts

    # --- Branch 1: normalizeProvider=None, verifyProvider=set -> use verify (R13.4) ---
    verify = FakeLLMProvider(ketQuaCoDinh=coDau)
    pipeline = QueryPipeline(verifyProvider=verify)
    pipeline.normalizeQuestion(cauKhongDau)
    assert verify.soLanGoi == 1  # normalize role empty -> use the verify provider

    # --- Branch 2: normalizeProvider=set -> use normalize; verify is NOT called ---
    normalize = FakeLLMProvider(ketQuaCoDinh=coDau)
    verifyKhongDung = FakeLLMProvider(ketQuaCoDinh=coDau) if coVerifyKemNormalize else None
    pipeline = QueryPipeline(normalizeProvider=normalize, verifyProvider=verifyKhongDung)
    pipeline.normalizeQuestion(cauKhongDau)
    assert normalize.soLanGoi == 1
    if verifyKhongDung is not None:
        assert verifyKhongDung.soLanGoi == 0  # verify not used for normalization

    # --- Branch 3: both None -> keep the original question, call no provider ---
    pipeline = QueryPipeline()
    assert pipeline.normalizeQuestion(cauKhongDau) == cauKhongDau
