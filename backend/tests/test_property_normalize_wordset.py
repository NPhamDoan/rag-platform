"""Property test for Property 32 — normalizing a question preserves the word set (task 10.3).

Under test: `QueryPipeline.normalizeQuestion` with a fake LLM_Provider (injected).
The production guard: accept the LLM result only if its word set (after removing diacritics
and lowercasing) MATCHES the original sentence; otherwise keep the original sentence. A sentence
that ALREADY has diacritics -> kept unchanged, with NO LLM call.

INVARIANT to check: regardless of what the fake LLM returns, the output of `normalizeQuestion`
ALWAYS has the SAME word set (after removing diacritics) as the original input — either it
accepts a normalized version with the same word set, or it keeps the original sentence.

The oracle uses the module's own helpers (`_wordSet`, `_removeDiacritics`, `_hasDiacritics`)
to stay independent of the implementation details of `normalizeQuestion`.

bcrypt is not involved here (pure logic + fake LLM), so it is fast -> max_examples=150.
"""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.pipelines.query_pipeline import (
    QueryPipeline,
    _hasDiacritics,
    _removeDiacritics,
    _wordSet,
)


class FakeLLMProvider:
    """Fake LLM_Provider: returns `ketQuaCoDinh` + counts the number of calls (style of test 10.1)."""

    ten = "fake-llm"

    def __init__(self, ketQuaCoDinh: str):
        self.ketQuaCoDinh = ketQuaCoDinh
        self.soLanGoi = 0

    def generate(self, systemPrompt: str, userPrompt: str) -> str:
        self.soLanGoi += 1
        return self.ketQuaCoDinh


# Maps ASCII vowels -> accented forms that `_removeDiacritics` reverses back to the original.
_BANG_DAU = {"a": "\u00e1", "e": "\u00e9", "i": "\u00ed", "o": "\u00f3", "u": "\u00fa", "y": "\u00fd"}


def _themDau(text: str) -> str:
    """Add an acute accent to each ASCII vowel; words without vowels stay unchanged.

    `_removeDiacritics(_themDau(x)) == x` for x consisting only of a-z + spaces -> this
    'accented' version has the SAME word set as `x`.
    """
    return "".join(_BANG_DAU.get(ch, ch) for ch in text)


def _tuLa(boTu: frozenset[str]) -> str:
    """Generate a token guaranteed NOT to be in `boTu` (to break the word set)."""
    tu = "z"
    while tu in boTu:
        tu += "z"
    return tu


# Un-accented words: a-z only, length 1..8; a list of UNIQUE tokens, 1..6 words.
_tu = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8)
_danhSachTu = st.lists(_tu, min_size=1, max_size=6, unique=True)


# Feature: multi-user-rag-platform, Property 32: Chuan hoa cau hoi khong dau giu nguyen bo tu.
# Validates: Requirements 6.7
@settings(max_examples=150, deadline=None)
@given(
    cacTu=_danhSachTu,
    daCoDauInput=st.booleans(),
    nhanKetQua=st.booleans(),
    kieuLamHong=st.sampled_from(["them-tu", "doi-tu", "bot-tu"]),
)
def test_chuan_hoa_giu_nguyen_bo_tu(cacTu, daCoDauInput, nhanKetQua, kieuLamHong):
    cauKhongDau = " ".join(cacTu)
    boTuGoc = _wordSet(cauKhongDau)

    if daCoDauInput:
        # (b) Input ALREADY has diacritics -> kept unchanged, NO LLM call.
        cauGoc = _themDau(cauKhongDau)
        assume(_hasDiacritics(cauGoc))  # ensure it really has diacritics (at least 1 vowel)
        fake = FakeLLMProvider(ketQuaCoDinh="hoan toan khac han")
        pipeline = QueryPipeline(normalizeProvider=fake)

        ketQua = pipeline.normalizeQuestion(cauGoc)

        assert ketQua == cauGoc  # unchanged
        assert fake.soLanGoi == 0  # no LLM call
        # General invariant: the word set after removing diacritics is unchanged.
        assert _wordSet(ketQua) == _wordSet(cauGoc)
        return

    # (a) Un-accented input -> call the LLM; result accepted/rejected by the same-word-set guard.
    coDau = _themDau(cauKhongDau)  # 'accented' version with the SAME word set as the original

    if nhanKetQua:
        # The LLM returns a valid version (diacritics only) -> must be ACCEPTED.
        ketQuaLLM = coDau
    else:
        # The LLM returns a version that BREAKS the word set -> must be REJECTED, keep original.
        tokens = coDau.split(" ")
        if kieuLamHong == "them-tu":
            tokens = tokens + [_tuLa(boTuGoc)]
        elif kieuLamHong == "doi-tu":
            tokens[0] = _tuLa(boTuGoc)
        else:  # bot-tu (cacTu are unique -> removing 1 word definitely changes the word set)
            tokens = tokens[1:]
        ketQuaLLM = " ".join(tokens)

    fake = FakeLLMProvider(ketQuaCoDinh=ketQuaLLM)
    pipeline = QueryPipeline(normalizeProvider=fake)

    ketQua = pipeline.normalizeQuestion(cauKhongDau)

    # The LLM was called (un-accented sentence + a provider present).
    assert fake.soLanGoi == 1

    # MAIN INVARIANT: the output ALWAYS has the same word set (after removing diacritics) as the original.
    assert _wordSet(ketQua) == boTuGoc

    if nhanKetQua:
        # Same word set -> accept the accented version.
        assert ketQua == coDau
        assert _removeDiacritics(ketQua) == cauKhongDau
    else:
        # Word set mismatch -> reject, keep the original sentence intact.
        assert ketQua == cauKhongDau
