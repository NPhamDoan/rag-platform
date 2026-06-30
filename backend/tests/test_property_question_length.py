"""Property test (task 10.2) for QueryPipeline.validateQuestion: length validation.

# Feature: multi-user-rag-platform, Property 29: Hop le hoa do dai cau hoi
# Validates: Requirements 6.3

A single Hypothesis test generates an arbitrary string and checks the biconditional invariant:

    validateQuestion(cauHoi) SUCCEEDS  <=>  CAU_HOI_MIN <= len(cauHoi.strip()) <= CAU_HOI_MAX

- When valid: returns the WHITESPACE-TRIMMED string (`cauHoi.strip()`).
- When invalid (empty/whitespace-only after trimming, or longer than CAU_HOI_MAX):
  raises `ValidationError`.

Uses the `CAU_HOI_MIN`/`CAU_HOI_MAX` constants from the module (no hardcoding). No DB/LLM is
needed, so `QueryPipeline()` is instantiated without arguments. The generator deliberately
covers the boundary cases: empty/whitespace-only -> rejected; lengths 1 and CAU_HOI_MAX ->
accepted; CAU_HOI_MAX+1 -> rejected.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.errors import ValidationError
from app.pipelines.query_pipeline import CAU_HOI_MAX, CAU_HOI_MIN, QueryPipeline

_KHOANG_TRANG = " \t\n\r\f\v"


def _strategyCauHoi() -> st.SearchStrategy[str]:
    """Generate an arbitrary string, weighted toward the boundary cases.

    - Arbitrary string (may have leading/trailing/internal whitespace).
    - Whitespace-only string (-> empty after trimming -> rejected).
    - Strings of exact boundary length after trimming: 1, CAU_HOI_MAX, CAU_HOI_MAX+1.
    """
    tuyY = st.text(max_size=CAU_HOI_MAX + 50)
    chiKhoangTrang = st.text(alphabet=_KHOANG_TRANG, max_size=10)
    # Cores (non-whitespace) so the trimmed length is deterministic.
    loiBien = st.sampled_from([
        "a",  # len 1 after trimming -> accepted (lower bound)
        "x" * CAU_HOI_MAX,  # len CAU_HOI_MAX -> accepted (upper bound)
        "x" * (CAU_HOI_MAX + 1),  # len CAU_HOI_MAX+1 -> rejected
    ])
    # Wrap the boundary cores with random whitespace on both ends (trimming preserves core length).
    bienCoDem = st.builds(
        lambda dau, loi, cuoi: dau + loi + cuoi,
        st.text(alphabet=_KHOANG_TRANG, max_size=5),
        loiBien,
        st.text(alphabet=_KHOANG_TRANG, max_size=5),
    )
    return st.one_of(tuyY, chiKhoangTrang, bienCoDem)


@settings(max_examples=200, deadline=None)
@given(cauHoi=_strategyCauHoi())
def test_property_hop_le_hoa_do_dai_cau_hoi(cauHoi: str):
    pipeline = QueryPipeline()
    daCat = cauHoi.strip()
    hopLe = CAU_HOI_MIN <= len(daCat) <= CAU_HOI_MAX

    if hopLe:
        ketQua = pipeline.validateQuestion(cauHoi)
        # Success -> returns the whitespace-trimmed string.
        assert ketQua == daCat
    else:
        # Invalid (empty after trimming or too long) -> ValidationError.
        with pytest.raises(ValidationError):
            pipeline.validateQuestion(cauHoi)


def test_bien_do_dai_truc_tiep():
    """Quick check of the deterministic boundaries (complementing the property test)."""
    pipeline = QueryPipeline()

    # Empty / whitespace-only -> rejected.
    for xau in ["", "   ", "\t\n  "]:
        with pytest.raises(ValidationError):
            pipeline.validateQuestion(xau)

    # Length 1 (lower bound) -> accepted, returns the trimmed value.
    assert pipeline.validateQuestion("  a  ") == "a"

    # Length CAU_HOI_MAX (upper bound) -> accepted.
    bienTren = "x" * CAU_HOI_MAX
    assert pipeline.validateQuestion(bienTren) == bienTren

    # Length CAU_HOI_MAX + 1 -> rejected.
    with pytest.raises(ValidationError):
        pipeline.validateQuestion("x" * (CAU_HOI_MAX + 1))
