"""Unit tests for hybrid search + retrieve gating (task 10.6).

Coverage (R3.4, R6.1, R6.4, R6.5, R6.6):
- `VectorStore.search`: returns at most `k`, sorted by the merged score descending, and
  ONLY touches the workspace's own collection (data isolation between two workspaces).
- `QueryPipeline.retrieve`: embeds the question with the workspace's Embedding_Provider,
  calls search restricted to the workspace's collection, then gates on the top score:
    * top < nguongKhongTimThay        -> KHONG_TIM_THAY
    * nguongKhongTimThay <= top < ...  -> CHUA_DU_LIEN_QUAN
    * top >= nguongDuLienQuan          -> DU_LIEN_QUAN with chunks
  Both below-threshold cases do NOT call the synthesis LLM (synthesis spy soLanGoi == 0).

Uses a FAKE Embedding_Provider + a FAKE/in-memory Vector_Store (injected via the
constructor) per project conventions — no network calls, no real ChromaDB needed.
"""

from __future__ import annotations

import math

from app.db.models import CauHinhTruyXuat, KhongGianTaiLieu
from app.pipelines.query_pipeline import (
    KetQuaTruyXuat,
    QueryPipeline,
    TrangThaiTruyXuat,
)
from app.storage.vector_store import (
    META_TAI_LIEU_ID,
    SearchResult,
    VectorItem,
    VectorStore,
)


# --- Fakes ------------------------------------------------------------------
class FakeEmbeddingProvider:
    """Fake Embedding_Provider: a fixed vector based on text length (deterministic)."""

    ten = "fake"

    def __init__(self) -> None:
        self.soLanGoi = 0

    def embed(self, texts):
        self.soLanGoi += 1
        return [[float(len(t)), 1.0, 0.0] for t in texts]


class SynthesisSpy:
    """Fake synthesis LLM_Provider: counts calls (MUST be 0 when gated)."""

    ten = "synthesis-spy"

    def __init__(self) -> None:
        self.soLanGoi = 0

    def generate(self, systemPrompt: str, userPrompt: str) -> str:
        self.soLanGoi += 1
        return "khong duoc goi"


class FakeCollection:
    """In-memory collection supporting add/get/query/count (matches CollectionLike).

    `query` ranks by Euclidean distance to `queryVector` (ascending = best first) —
    mimicking ChromaDB (l2).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.rows: dict[str, tuple] = {}  # id -> (vector, document, metadata)

    def add(self, *, ids, embeddings, documents, metadatas) -> None:
        for i, vec, doc, meta in zip(ids, embeddings, documents, metadatas):
            self.rows[i] = (list(vec), doc, dict(meta))

    def delete(self, *, where=None, ids=None) -> None:  # pragma: no cover - unused
        if ids is not None:
            for i in ids:
                self.rows.pop(i, None)

    def count(self) -> int:
        return len(self.rows)

    def get(self) -> dict:
        ids = list(self.rows.keys())
        return {
            "ids": ids,
            "documents": [self.rows[i][1] for i in ids],
            "metadatas": [self.rows[i][2] for i in ids],
        }

    def query(self, *, query_embeddings, n_results) -> dict:
        qv = query_embeddings[0]

        def khoangCach(vec):
            return math.sqrt(sum((a - b) ** 2 for a, b in zip(vec, qv)))

        xepHang = sorted(self.rows.items(), key=lambda kv: (khoangCach(kv[1][0]), kv[0]))
        ids = [i for i, _ in xepHang[:n_results]]
        return {"ids": [ids]}


class FakeClient:
    """In-memory client: manages FakeCollections by name."""

    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}
        self.tenDaTruyCap: list[str] = []  # record every collection name touched

    def get_or_create_collection(self, name: str) -> FakeCollection:
        self.tenDaTruyCap.append(name)
        col = self.collections.get(name)
        if col is None:
            col = FakeCollection(name)
            self.collections[name] = col
        return col

    def delete_collection(self, name: str) -> None:  # pragma: no cover - unused
        del self.collections[name]


class FakeVectorStore:
    """Fake Vector_Store for gating tests: returns fixed results, counts search calls."""

    def __init__(self, ketQua: list[SearchResult]) -> None:
        self.ketQua = ketQua
        self.tenDaTruyVan: list[str] = []

    def search(self, collectionName, queryVector, queryText, k, *, trongSoVector=0.5, trongSoBm25=0.5):
        self.tenDaTruyVan.append(collectionName)
        return list(self.ketQua[:k])


def _items(*specs: tuple[str, list[float], str]) -> list[VectorItem]:
    """Helper to build VectorItems: (id, vector, document) + metadata taiLieuId=id."""
    return [
        VectorItem(id=i, vector=v, document=d, metadata={META_TAI_LIEU_ID: i})
        for i, v, d in specs
    ]


def _khong_gian(collectionName: str = "ws_a") -> KhongGianTaiLieu:
    return KhongGianTaiLieu(
        id="kg-1",
        ten="KG",
        moTa="",
        chuSoHuuId="chu-1",
        embeddingProvider="fake",
        collectionName=collectionName,
    )


def _cfg(
    nguongKhongTimThay=0.3, nguongDuLienQuan=0.5, k=8, trongSoVector=0.5, trongSoBm25=0.5
) -> CauHinhTruyXuat:
    return CauHinhTruyXuat(
        khongGianId="kg-1",
        nguongKhongTimThay=nguongKhongTimThay,
        nguongDuLienQuan=nguongDuLienQuan,
        k=k,
        trongSoVector=trongSoVector,
        trongSoBm25=trongSoBm25,
    )


# --- VectorStore.search: <= k + correct order (R6.4) ------------------------
class TestSearchRanking:
    def test_tra_toi_da_k_va_giam_dan_theo_diem(self):
        client = FakeClient()
        store = VectorStore(client=client)
        store.addChunks(
            "ws_a",
            _items(
                ("c1", [1.0, 0.0, 0.0], "toc do toi da khu dan cu"),
                ("c2", [2.0, 0.0, 0.0], "toc do toi da duong cao toc"),
                ("c3", [3.0, 0.0, 0.0], "thua ke di san"),
                ("c4", [4.0, 0.0, 0.0], "hop dong lao dong"),
            ),
        )
        ketQua = store.search("ws_a", [1.0, 0.0, 0.0], "toc do toi da", k=2)
        assert len(ketQua) == 2  # <= k
        # Sorted by score descending.
        assert ketQua[0].score >= ketQua[1].score
        # The chunk matching both vector (near [1,0,0]) and BM25 ("toc do toi da") ranks first.
        assert ketQua[0].id == "c1"
        # Each result carries its document + metadata + a score in [0, 1].
        assert ketQua[0].document == "toc do toi da khu dan cu"
        assert ketQua[0].metadata[META_TAI_LIEU_ID] == "c1"
        assert 0.0 <= ketQua[0].score <= 1.0

    def test_collection_rong_tra_rong(self):
        client = FakeClient()
        store = VectorStore(client=client)
        store.addChunks("ws_a", _items(("c1", [1.0, 0.0, 0.0], "noi dung")))
        assert store.search("ws_a", [1.0, 0.0, 0.0], "cau hoi", k=0) == []
        assert store.search("ws_empty", [1.0, 0.0, 0.0], "cau hoi", k=5) == []


# --- VectorStore.search: isolation per collection (R3.4/6.1) ----------------
class TestSearchIsolation:
    def test_chi_tra_chunk_cua_dung_collection(self):
        client = FakeClient()
        store = VectorStore(client=client)
        store.addChunks("ws_a", _items(("a1", [1.0, 0.0, 0.0], "toc do toi da")))
        store.addChunks("ws_b", _items(("b1", [1.0, 0.0, 0.0], "toc do toi da")))

        client.tenDaTruyCap.clear()  # only track accesses during search
        ketQua = store.search("ws_a", [1.0, 0.0, 0.0], "toc do toi da", k=8)
        ids = {r.id for r in ketQua}
        assert ids == {"a1"}
        assert "b1" not in ids
        # Never touches the ws_b collection while searching ws_a.
        assert set(client.tenDaTruyCap) == {"ws_a"}


# --- QueryPipeline.retrieve: threshold gating (R6.5/6.6) ---------------------
class TestRetrieveGating:
    def _pipeline(self, ketQua, synthesis):
        return QueryPipeline(
            embeddingProvider=FakeEmbeddingProvider(),
            vectorStore=FakeVectorStore(ketQua),
            synthesisProvider=synthesis,
        )

    def test_duoi_nguong_thap_tra_khong_tim_thay_khong_goi_llm(self):
        synthesis = SynthesisSpy()
        ketQua = [SearchResult("c1", "noi dung", {}, score=0.2)]
        pipeline = self._pipeline(ketQua, synthesis)
        kq = pipeline.retrieve(_khong_gian(), "cau hoi", _cfg())
        assert kq.trangThai == TrangThaiTruyXuat.KHONG_TIM_THAY
        assert kq.chunks == []
        assert synthesis.soLanGoi == 0

    def test_giua_hai_nguong_tra_chua_du_lien_quan_khong_goi_llm(self):
        synthesis = SynthesisSpy()
        ketQua = [SearchResult("c1", "noi dung", {}, score=0.4)]
        pipeline = self._pipeline(ketQua, synthesis)
        kq = pipeline.retrieve(_khong_gian(), "cau hoi", _cfg())
        assert kq.trangThai == TrangThaiTruyXuat.CHUA_DU_LIEN_QUAN
        assert kq.chunks == []
        assert synthesis.soLanGoi == 0

    def test_tren_nguong_cao_tra_chunk_du_lien_quan(self):
        synthesis = SynthesisSpy()
        ketQua = [
            SearchResult("c1", "noi dung 1", {}, score=0.7),
            SearchResult("c2", "noi dung 2", {}, score=0.6),
        ]
        pipeline = self._pipeline(ketQua, synthesis)
        kq = pipeline.retrieve(_khong_gian(), "cau hoi", _cfg())
        assert kq.trangThai == TrangThaiTruyXuat.DU_LIEN_QUAN
        assert [c.id for c in kq.chunks] == ["c1", "c2"]
        # retrieve does NOT call synthesis (synthesis is task 10.10).
        assert synthesis.soLanGoi == 0

    def test_rong_tra_khong_tim_thay(self):
        synthesis = SynthesisSpy()
        pipeline = self._pipeline([], synthesis)
        kq = pipeline.retrieve(_khong_gian(), "cau hoi", _cfg())
        assert kq.trangThai == TrangThaiTruyXuat.KHONG_TIM_THAY
        assert synthesis.soLanGoi == 0

    def test_bien_dung_nguong_du_lien_quan_la_du(self):
        # top == nguongDuLienQuan -> NOT < threshold -> DU_LIEN_QUAN (boundary).
        synthesis = SynthesisSpy()
        ketQua = [SearchResult("c1", "noi dung", {}, score=0.5)]
        pipeline = self._pipeline(ketQua, synthesis)
        kq = pipeline.retrieve(_khong_gian(), "cau hoi", _cfg())
        assert kq.trangThai == TrangThaiTruyXuat.DU_LIEN_QUAN

    def test_retrieve_search_dung_collection_cua_khong_gian(self):
        synthesis = SynthesisSpy()
        store = FakeVectorStore([SearchResult("c1", "x", {}, score=0.9)])
        pipeline = QueryPipeline(
            embeddingProvider=FakeEmbeddingProvider(),
            vectorStore=store,
            synthesisProvider=synthesis,
        )
        pipeline.retrieve(_khong_gian("ws_dac_biet"), "cau hoi", _cfg())
        # Only queries the workspace's own collection (isolation R3.4/6.1).
        assert store.tenDaTruyVan == ["ws_dac_biet"]


# --- retrieve uses the workspace's Embedding_Provider (integration) -------
def test_retrieve_dung_embedding_va_search_in_memory():
    """retrieve integration: embed the question -> in-memory RRF search -> gating."""
    client = FakeClient()
    store = VectorStore(client=client)
    store.addChunks(
        "ws_a",
        _items(
            ("c1", [12.0, 1.0, 0.0], "toc do toi da khu dan cu la bao nhieu"),
            ("c2", [40.0, 1.0, 0.0], "thua ke di san theo phap luat"),
        ),
    )
    emb = FakeEmbeddingProvider()  # vector = [len(text), 1, 0]
    synthesis = SynthesisSpy()
    pipeline = QueryPipeline(
        embeddingProvider=emb,
        vectorStore=store,
        synthesisProvider=synthesis,
    )
    # Question of 12 chars -> vector [12,1,0] near c1; also matches BM25 "toc do toi da".
    kq = pipeline.retrieve(_khong_gian("ws_a"), "toc do max?", _cfg())
    assert isinstance(kq, KetQuaTruyXuat)
    assert emb.soLanGoi == 1
    assert synthesis.soLanGoi == 0
    if kq.chunks:
        assert kq.chunks[0].id == "c1"
