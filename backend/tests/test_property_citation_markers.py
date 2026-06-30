"""Property test (task 10.11): citation markers [n] pair up with TrichDan (R7.4/7.5).

Under test: `QueryPipeline.synthesize` — from a synthesized answer containing `[n]`
markers inline over N chunks, builds the `TrichDan` list such that:
- every marker lies within 1..N;
- the marker set == the set of VALID markers (1..N) that DISTINCTLY actually appear in
  the answer (paired up, no duplicates, out-of-range markers dropped);
- markers are sorted ascending;
- marker n maps to the n-th chunk (chunkId/taiLieuId/noiDung match chunks[n-1]).

Uses FakeLLM (same convention as tests/test_query_pipeline_synthesize.py) returning an
answer assembled from marker tokens + filler text — no network call.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.pipelines.query_pipeline import QueryPipeline
from app.storage.vector_store import META_TAI_LIEU_ID, SearchResult


class FakeLLM:
    """Fake LLM_Provider: returns a fixed `phanHoi` (a pre-composed synthesized answer)."""

    ten = "fake-llm"

    def __init__(self, phanHoi: str) -> None:
        self.phanHoi = phanHoi

    def generate(self, systemPrompt: str, userPrompt: str) -> str:
        return self.phanHoi


def _makeChunks(n: int) -> list[SearchResult]:
    """N clearly distinct chunks (id/taiLieuId/noiDung indexed by position)."""
    return [
        SearchResult(
            id=f"c{i}",
            document=f"Noi dung doan {i}",
            metadata={META_TAI_LIEU_ID: f"tl-{i}"},
            score=1.0 - i * 0.01,
        )
        for i in range(1, n + 1)
    ]


# Filler text contains no '[' or ']' so it does not create unintended markers.
_filler = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz ABC 0123456789.,",
    min_size=0,
    max_size=8,
)


@st.composite
def _answerAndChunks(draw):
    """Generate (cauTraLoi, chunks, markerHopLeMongDoi).

    - N chunks: N in 1..8.
    - A series of `[m]` markers where m is MIXED: within 1..N, and out of range
      (0, N+1, large values) — duplicates allowed. Interleaved with filler text.
    - markerHopLeMongDoi = the set of distinct markers within 1..N that actually appear.
    """
    n = draw(st.integers(min_value=1, max_value=8))
    inRange = st.integers(min_value=1, max_value=n)
    outOfRange = st.sampled_from([0, n + 1, n + 5, 99, 1000])
    markerValues = draw(
        st.lists(st.one_of(inRange, outOfRange), min_size=0, max_size=20)
    )

    phan: list[str] = []
    for m in markerValues:
        phan.append(draw(_filler))
        phan.append(f"[{m}]")
    phan.append(draw(_filler))
    cauTraLoi = " ".join(p for p in phan if p)

    markerHopLeMongDoi = sorted({m for m in markerValues if 1 <= m <= n})
    return cauTraLoi, _makeChunks(n), markerHopLeMongDoi


# Feature: multi-user-rag-platform, Property 33: Citation markers lie within 1..N and
# pair up with the TrichDan list (R7.4/7.5).
@settings(max_examples=150)
@given(_answerAndChunks())
def test_marker_trich_dan_song_anh(data):
    """Validates: Requirements 7.4, 7.5."""
    cauTraLoi, chunks, markerHopLeMongDoi = data
    n = len(chunks)

    pipeline = QueryPipeline(synthesisProvider=FakeLLM(cauTraLoi))
    _traLoi, trichDan = pipeline.synthesize("cau hoi?", chunks)

    markers = [td.marker for td in trichDan]

    # Every marker lies within 1..N (R7.4).
    assert all(1 <= m <= n for m in markers)
    # Pairing: the marker set == the set of distinct valid markers that appear; sorted ascending;
    # no duplicates (R7.5).
    assert markers == markerHopLeMongDoi
    assert len(markers) == len(set(markers))
    # Marker n -> the n-th chunk (chunkId/taiLieuId/noiDung match chunks[n-1]).
    for td in trichDan:
        nguon = chunks[td.marker - 1]
        assert td.chunkId == nguon.id
        assert td.taiLieuId == nguon.metadata[META_TAI_LIEU_ID]
        assert td.noiDung == nguon.document
