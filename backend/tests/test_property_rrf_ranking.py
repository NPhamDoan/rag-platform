"""Property test (task 10.7) for VectorStore.search: hybrid RRF returns at most k in the right order.

# Feature: multi-user-rag-platform, Property 30: RRF fusion returns at most k and
# in the correct order.
# Validates: Requirements 6.4

A single Hypothesis test generates a collection of N documents (vector + varied text)
along with a query (queryVector + queryText) and an arbitrary k, then checks the
invariants of `VectorStore.search` (hybrid vector + BM25 fused via RRF):

(1) Count: returns at most min(k, N) results (k <= 0 -> empty).
(2) Order: results sorted by `score` descending (non-increasing).
(3) Score domain: every `score` lies within [0, 1].
(4) Uniqueness + collection membership: the result ids are pairwise distinct and all
    lie within the set of ids loaded into the collection.

Uses the FAKE/in-memory Vector_Store (FakeClient/FakeCollection) from
test_query_pipeline_retrieve.py — no network calls, no real ChromaDB needed.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.storage.vector_store import META_TAI_LIEU_ID, VectorItem, VectorStore
from tests.test_query_pipeline_retrieve import FakeClient

# An English + Vietnamese (no diacritics) vocabulary so the text can match BM25.
_TU_VUNG = [
    "toc",
    "do",
    "toi",
    "da",
    "khu",
    "dan",
    "cu",
    "duong",
    "cao",
    "hop",
    "dong",
    "lao",
    "thua",
    "ke",
    "di",
    "san",
    "phap",
    "luat",
]

# 3-dimensional vector (matching the 3-dimensional FakeEmbeddingProvider in this test suite).
_chieuVector = st.floats(
    min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False
)
_vector = st.lists(_chieuVector, min_size=3, max_size=3)
_vanBan = st.lists(st.sampled_from(_TU_VUNG), min_size=0, max_size=8).map(" ".join)


@settings(max_examples=100, deadline=None)
@given(
    docs=st.lists(st.tuples(_vector, _vanBan), min_size=0, max_size=12),
    queryVector=_vector,
    queryText=st.lists(st.sampled_from(_TU_VUNG), min_size=0, max_size=5).map(" ".join),
    k=st.integers(min_value=-2, max_value=15),
    trongSoVector=st.floats(min_value=0.0, max_value=1.0),
    trongSoBm25=st.floats(min_value=0.0, max_value=1.0),
)
def test_property_rrf_toi_da_k_va_dung_thu_tu(
    docs, queryVector, queryText, k, trongSoVector, trongSoBm25
):
    store = VectorStore(client=FakeClient())
    # Load N documents into one collection; ids are unique by index.
    items = [
        VectorItem(id=f"c{i}", vector=vec, document=doc, metadata={META_TAI_LIEU_ID: f"c{i}"})
        for i, (vec, doc) in enumerate(docs)
    ]
    store.addChunks("ws_a", items)
    idsTrongCollection = {it.id for it in items}
    N = len(items)

    ketQua = store.search(
        "ws_a",
        queryVector,
        queryText,
        k=k,
        trongSoVector=trongSoVector,
        trongSoBm25=trongSoBm25,
    )

    # (1) Count: at most min(k, N); k <= 0 -> empty.
    gioiHan = min(k, N) if k > 0 else 0
    assert len(ketQua) <= gioiHan

    # (2) Order: score descending (non-increasing).
    diem = [r.score for r in ketQua]
    assert all(diem[i] >= diem[i + 1] for i in range(len(diem) - 1))

    # (3) Score domain: every score in [0, 1].
    assert all(0.0 <= s <= 1.0 for s in diem)

    # (4) Unique ids + all belong to the collection.
    ids = [r.id for r in ketQua]
    assert len(ids) == len(set(ids))
    assert set(ids) <= idsTrongCollection
