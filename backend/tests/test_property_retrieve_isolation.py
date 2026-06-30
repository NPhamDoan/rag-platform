"""Property test (task 10.9) — retrieval ISOLATED per workspace (collection).

Property 16: Retrieval only returns Chunks belonging to the queried workspace.
Validates: Requirements 3.4, 6.1

`VectorStore.search` is restricted to exactly ONE collection of the workspace: the
results only contain chunk ids added to that collection (never leaking ids of other
workspaces), and during the search it does NOT touch any other collection name
(isolation at the storage layer).

Reuses the in-memory FakeClient/FakeCollection from `tests/test_query_pipeline_retrieve.py`
(no network calls, no real ChromaDB needed). LLM/Embedding are irrelevant for this
property (search receives a ready-made queryVector + queryText).
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.storage.vector_store import META_TAI_LIEU_ID, VectorItem, VectorStore

from tests.test_query_pipeline_retrieve import FakeClient

# --- Generators -------------------------------------------------------------
# Varied text tokens (English + Vietnamese without diacritics) so BM25 has a varied signal.
_TU = st.sampled_from(
    ["toc", "do", "toi", "da", "thua", "ke", "di", "san", "hop", "dong",
     "lao", "khu", "dan", "cu", "duong", "cao", "luat", "dieu", "phap", "ly"]
)
_DOC = st.lists(_TU, min_size=1, max_size=8).map(" ".join)
# 3-dimensional vector (matching FakeCollection.query which uses Euclidean distance).
_VEC = st.lists(st.floats(min_value=-50.0, max_value=50.0), min_size=3, max_size=3)


@st.composite
def khongGianStrategy(draw):
    """Generate >=2 collections with DISJOINT id sets + varied content; pick one to search.

    Returns:
      - collections: list[(collectionName, list[VectorItem])]  (globally disjoint ids)
      - chiSoSearch: index of the collection to be searched
      - queryVector / queryText: search parameters
      - k: maximum number of results
    """
    soCol = draw(st.integers(min_value=2, max_value=4))
    # A globally unique id set -> split disjointly across the collections.
    tongChunk = draw(st.integers(min_value=soCol, max_value=soCol * 5))
    idDuyNhat = draw(
        st.lists(
            st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8),
            min_size=tongChunk,
            max_size=tongChunk,
            unique=True,
        )
    )
    tenCol = draw(
        st.lists(
            st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=2, max_size=10),
            min_size=soCol,
            max_size=soCol,
            unique=True,
        )
    )

    # Split the ids into soCol non-empty, contiguous groups (simple + guarantees disjointness).
    collections: list[tuple[str, list[VectorItem]]] = []
    viTri = 0
    for ci in range(soCol):
        conLai = soCol - ci
        # Ensure each remaining group has at least 1 id.
        toiDa = len(idDuyNhat) - viTri - (conLai - 1)
        soId = draw(st.integers(min_value=1, max_value=toiDa))
        items: list[VectorItem] = []
        for cid in idDuyNhat[viTri : viTri + soId]:
            items.append(
                VectorItem(
                    id=cid,
                    vector=draw(_VEC),
                    document=draw(_DOC),
                    metadata={META_TAI_LIEU_ID: cid},
                )
            )
        viTri += soId
        collections.append((tenCol[ci], items))

    chiSoSearch = draw(st.integers(min_value=0, max_value=soCol - 1))
    queryVector = draw(_VEC)
    queryText = draw(_DOC)
    k = draw(st.integers(min_value=1, max_value=10))
    return collections, chiSoSearch, queryVector, queryText, k


# --- Property 16 ------------------------------------------------------------
# Feature: multi-user-rag-platform, Property 16: Retrieval only returns Chunks
# belonging to the queried workspace (isolation per collection) — Validates: Requirements 3.4, 6.1
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(data=khongGianStrategy())
def test_search_chi_tra_chunk_cua_khong_gian_duoc_truy_van(data):
    collections, chiSoSearch, queryVector, queryText, k = data

    client = FakeClient()
    store = VectorStore(client=client)
    for tenCol, items in collections:
        store.addChunks(tenCol, items)

    tenSearch, itemsSearch = collections[chiSoSearch]
    idCuaA = {it.id for it in itemsSearch}
    idCacKhongGianKhac = {
        it.id for i, (_, items) in enumerate(collections) if i != chiSoSearch for it in items
    }

    # Only track collection accesses during the search (ignore the adds above).
    client.tenDaTruyCap.clear()
    ketQua = store.search(tenSearch, queryVector, queryText, k)

    idTra = {r.id for r in ketQua}
    # 1) Only returns chunks belonging to the queried workspace.
    assert idTra <= idCuaA
    # 2) Never leaks ids of other workspaces.
    assert idTra.isdisjoint(idCacKhongGianKhac)
    # 3) At most k results (R6.4 — accompanying invariant).
    assert len(ketQua) <= k
    # 4) Search does NOT touch any other collection name (isolation at the storage layer).
    assert set(client.tenDaTruyCap) == {tenSearch}
