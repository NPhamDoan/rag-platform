"""Property test (task 10.12) — the verification label always belongs to the valid set.

# Feature: multi-user-rag-platform, Property 34: The verification label always belongs to the valid set
# Validates: Requirements 8.1

Property 34: for ANY text returned by the verification provider (an arbitrary string —
including ones missing label keywords, garbage, empty, unicode), `QueryPipeline.verifyAnswer`
ALWAYS returns a value that is a member of the `NhanXacMinh` enum (one of three:
DA_XAC_MINH / CO_MAU_THUAN / CHUA_XAC_MINH) — never raises, never returns a value
outside the set.

Uses a FAKE LLM_Provider (REUSE `FakeLLM` from test_query_pipeline_synthesize) — no
network calls.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.db.models import NhanXacMinh
from app.pipelines.query_pipeline import QueryPipeline
from app.storage.vector_store import META_TAI_LIEU_ID, SearchResult

from tests.test_query_pipeline_synthesize import FakeLLM

# The set of three valid labels (per R8.1).
NHAN_HOP_LE = frozenset(NhanXacMinh)

# Mapping keywords (DIACRITICS REMOVED per _mapNhanXacMinh) — inserted into strings to
# cover the mapping branches, alongside arbitrary garbage/unicode strings.
_MAPPING_KEYWORDS = (
    "mau thuan",
    "co mau thuan",
    "chua xac minh",
    "khong xac minh",
    "da xac minh",
    "xac minh",
    "đã xác minh",
    "có mâu thuẫn",
    "chưa xác minh",
)


def _chunks() -> list[SearchResult]:
    """Two minimal chunks as verification context (content does not affect the label)."""
    return [
        SearchResult(id="c1", document="Noi dung doan mot", metadata={META_TAI_LIEU_ID: "tl-a"}, score=0.9),
        SearchResult(id="c2", document="Noi dung doan hai", metadata={META_TAI_LIEU_ID: "tl-b"}, score=0.8),
    ]


# Generate an arbitrary string: any text (including empty/unicode) OR text with a mapping keyword inserted.
_arbitrary_text = st.text()
_keyword_text = st.tuples(st.text(max_size=20), st.sampled_from(_MAPPING_KEYWORDS), st.text(max_size=20)).map(
    lambda parts: parts[0] + parts[1] + parts[2]
)
_verify_output = st.one_of(_arbitrary_text, _keyword_text)


@settings(max_examples=200)
@given(phanHoi=_verify_output)
def test_nhan_xac_minh_luon_thuoc_tap_hop_le(phanHoi: str) -> None:
    """verifyAnswer with arbitrary provider output ALWAYS returns a valid NhanXacMinh."""
    pipeline = QueryPipeline(verifyProvider=FakeLLM(phanHoi))
    nhan = pipeline.verifyAnswer("cau tra loi bat ky", _chunks())
    assert isinstance(nhan, NhanXacMinh)
    assert nhan in NHAN_HOP_LE
