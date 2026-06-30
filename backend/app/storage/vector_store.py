"""Vector_Store — ChromaDB wrapper: 1 collection per KhongGianTaiLieu (R21).

Data isolation at the storage layer (R3.4): each workspace has exactly one
collection (`khongGian.collectionName`), allowing each workspace to use its own
Embedding_Provider.

IMPORTANT — import-safe + dependency injection (DI):
- The file does NOT import `chromadb` at module load time. The real client is created
  LAZILY on first access (`self.client`). This keeps the module import-safe in
  environments without chromadb installed, and lets tests inject a fake (in-memory)
  client via the `client` argument.
- `VectorStore(client=fake)` → uses `fake` (tests); `VectorStore()` → creates a real
  `chromadb.PersistentClient` lazily when needed.

Atomic write-temp-then-swap (R21.4): `commitDocument` first writes a document's
vectors into a TEMP collection, then "swaps" them into the main collection by
deleting that document's old vectors and writing the new ones. An error midway →
clean up the temp collection + remove the document's local vectors from the main
collection (no partial vectors left behind — invariant R5.13: vectors exist if and
only if DA_EMBED).

Naming convention: entity/field names in Vietnamese without diacritics; verb/method
names in English; logging via the centralized logger, never swallowing errors silently.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.config import get_settings
from app.errors import InitializationError

logger = logging.getLogger(__name__)

#: Metadata key marking which document a chunk belongs to (used to delete/swap per document).
META_TAI_LIEU_ID = "taiLieuId"

# --- Hybrid search parameters (R6.4) ---------------------------------------
#: Standard RRF constant (larger flattens the rank-difference more).
_RRF_K = 60
#: Minimum candidate window taken from EACH ranking (vector/BM25) before merging —
#: wide enough for a meaningful merge without scanning the entire large collection.
_CANDIDATE_WINDOW = 50
#: BM25 Okapi parameters (standard).
_BM25_K1 = 1.5
_BM25_B = 0.75
#: Letter/number tokens after stripping diacritics + lowercasing (keep numbers per legacy design).
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def _removeDiacritics(text: str) -> str:
    """Strip Vietnamese diacritics (NFD + drop combining marks; 'đ'/'Đ' -> 'd'/'D')."""
    text = text.replace("\u0111", "d").replace("\u0110", "D")
    nfd = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in nfd if not unicodedata.combining(ch))


def _tokenize(text: str) -> list[str]:
    """Tokenize for BM25: strip diacritics + lowercase + keep letter/number groups (R6.4)."""
    return _TOKEN_PATTERN.findall(_removeDiacritics(text or "").lower())


def _bm25Scores(
    corpus: list[list[str]], queryTokens: list[str]
) -> list[float]:
    """Compute BM25 Okapi scores for `queryTokens` over `corpus` (already tokenized).

    Returns a list of scores with the same length as `corpus`. A document matching no
    query term -> 0.
    """
    soDoc = len(corpus)
    if soDoc == 0 or not queryTokens:
        return [0.0] * soDoc
    doDai = [len(tokens) for tokens in corpus]
    doDaiTb = sum(doDai) / soDoc if soDoc else 0.0
    # Document frequency (df) for each term.
    df: dict[str, int] = {}
    for tokens in corpus:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1
    qset = set(queryTokens)
    diem = [0.0] * soDoc
    for i, tokens in enumerate(corpus):
        if not tokens:
            continue
        tanSuat: dict[str, int] = {}
        for term in tokens:
            tanSuat[term] = tanSuat.get(term, 0) + 1
        s = 0.0
        for term in qset:
            f = tanSuat.get(term, 0)
            if f == 0:
                continue
            n_qi = df.get(term, 0)
            idf = math.log(1 + (soDoc - n_qi + 0.5) / (n_qi + 0.5))
            mauSo = f + _BM25_K1 * (
                1 - _BM25_B + _BM25_B * (doDai[i] / doDaiTb if doDaiTb else 0.0)
            )
            if mauSo:
                s += idf * (f * (_BM25_K1 + 1)) / mauSo
        diem[i] = s
    return diem


def _rrfFuse(
    vectorOrder: list[str],
    bm25Order: list[str],
    trongSoVector: float,
    trongSoBm25: float,
) -> dict[str, float]:
    """Merge two rankings using weighted RRF; normalize to [0, 1].

    `vectorOrder`/`bm25Order` are lists of ids ordered best-first. An id's raw RRF
    score = wv/(K+rankV) + wb/(K+rankB) (1-based rank; absent -> skip that ranking).
    Normalize by dividing by the maximum raw score (rank 1 in BOTH rankings) so the
    top can reach 1.0 — suitable for comparison against the gating thresholds in [0, 1].
    """
    rankVector = {docId: i + 1 for i, docId in enumerate(vectorOrder)}
    rankBm25 = {docId: i + 1 for i, docId in enumerate(bm25Order)}
    diemToiDa = (trongSoVector + trongSoBm25) / (_RRF_K + 1)
    fused: dict[str, float] = {}
    for docId in set(rankVector) | set(rankBm25):
        tho = 0.0
        if docId in rankVector:
            tho += trongSoVector / (_RRF_K + rankVector[docId])
        if docId in rankBm25:
            tho += trongSoBm25 / (_RRF_K + rankBm25[docId])
        # Clamp to [0, 1]: when a doc is #1 in BOTH rankings, floating-point rounding
        # can make tho/diemToiDa > 1.0 (e.g. 1.0000000000000002) — clamp to keep the
        # [0, 1] contract of SearchResult.score (gating threshold comparison R6.4/6.5).
        fused[docId] = max(0.0, min(1.0, tho / diemToiDa)) if diemToiDa > 0 else 0.0
    return fused

_THIEU_CHROMADB = (
    "Khong nap duoc thu vien vector store (chromadb chua duoc cai dat)."
)


@dataclass
class VectorItem:
    """A vector record written to a collection: id + vector + original text + metadata."""

    id: str
    vector: list[float]
    document: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """A hybrid search result: chunk id + original text + metadata + fused score.

    `score` is the RRF score normalized to [0, 1] (higher is more relevant) — used to
    sort (R6.4) and filter by the gating thresholds (R6.5/6.6).
    """

    id: str
    document: str
    metadata: dict[str, Any]
    score: float


# --- Client interface (to inject the real ChromaDB or a fake) --------------
@runtime_checkable
class CollectionLike(Protocol):
    """Minimal contract of a collection (matches the ChromaDB Collection API).

    Besides write/delete/count, hybrid search also uses:
    - `query(query_embeddings, n_results)`: rank by vector (best-first), returns a dict
      with key "ids" (list-of-list, one list per query embedding).
    - `get()`: returns every record in the collection (dict "ids"/"documents"/"metadatas")
      to compute BM25 + return the text/metadata alongside the result. An in-memory fake
      only needs these two methods for `search` to run on the fake (no real ChromaDB needed).
    """

    def add(self, *, ids, embeddings, documents, metadatas) -> None: ...
    def delete(self, *, where=None, ids=None) -> None: ...
    def count(self) -> int: ...
    def query(self, *, query_embeddings, n_results) -> dict: ...
    def get(self) -> dict: ...


@runtime_checkable
class ClientLike(Protocol):
    """Minimal contract of a client (matches the ChromaDB Client/PersistentClient API)."""

    def get_or_create_collection(self, name: str) -> CollectionLike: ...
    def delete_collection(self, name: str) -> None: ...


class VectorStore:
    """ChromaDB wrapper; operates by each workspace's `collectionName`.

    `client` can be injected (tests use an in-memory fake). Otherwise the real
    `chromadb.PersistentClient` is created LAZILY from `chroma_persist_path` (config).
    """

    def __init__(self, client: ClientLike | None = None, persistPath: str | None = None) -> None:
        self._client = client
        self._persistPath = persistPath

    # --- Lazy client -------------------------------------------------------
    @property
    def client(self) -> ClientLike:
        """Return the client; create a `chromadb.PersistentClient` lazily if missing."""
        if self._client is None:
            self._client = self._buildChromaClient()
        return self._client

    def _buildChromaClient(self) -> ClientLike:
        """Create the ChromaDB PersistentClient (lazy). Missing chromadb → InitializationError."""
        try:
            import chromadb
        except Exception as exc:  # noqa: BLE001 — missing library
            logger.error("Khong nap duoc chromadb: %s", exc)
            raise InitializationError(_THIEU_CHROMADB) from exc
        path = self._persistPath or get_settings().chroma_persist_path
        logger.info("Khoi tao ChromaDB PersistentClient tai '%s'", path)
        return chromadb.PersistentClient(path=path)

    # --- Basic operations --------------------------------------------------
    def addChunks(self, collectionName: str, items: list[VectorItem]) -> None:
        """Write the vectors into the workspace's collection (skip if empty)."""
        if not items:
            return
        col = self.client.get_or_create_collection(collectionName)
        self._addItems(col, items)
        logger.info("Ghi %d vector vao collection '%s'", len(items), collectionName)

    def deleteByTaiLieu(self, collectionName: str, taiLieuId: str) -> None:
        """Delete all of a document's vectors from the workspace's collection."""
        col = self.client.get_or_create_collection(collectionName)
        col.delete(where={META_TAI_LIEU_ID: taiLieuId})
        logger.info(
            "Xoa vector cua tai lieu id=%s khoi collection '%s'", taiLieuId, collectionName
        )

    def deleteCollection(self, collectionName: str) -> None:
        """Delete the workspace's entire collection (e.g. when deleting the workspace)."""
        self._safeDeleteCollection(collectionName)
        logger.info("Xoa collection '%s'", collectionName)

    # --- Hybrid search (vector + BM25 fused via RRF) — R3.4/R6.1/R6.4 ------
    def search(
        self,
        collectionName: str,
        queryVector: list[float],
        queryText: str,
        k: int,
        *,
        trongSoVector: float = 0.5,
        trongSoBm25: float = 0.5,
    ) -> list[SearchResult]:
        """Hybrid search LIMITED to exactly one workspace's collection (R3.4/6.1).

        Sequence:
        1. Get the VECTOR ranking from `col.query` (best-first).
        2. Get all records (`col.get`) -> compute the BM25 ranking over the text.
        3. Merge the two rankings via weighted RRF (`trongSoVector`/`trongSoBm25`),
           normalizing scores to [0, 1].
        4. Sort descending by score (tie-break by id for determinism), return at most `k`.

        Returns `[]` when `k <= 0` or the collection is empty. Only accesses the
        collection named `collectionName` — never queries another collection (data
        isolation R3.4).
        """
        if k <= 0:
            return []
        col = self.client.get_or_create_collection(collectionName)
        if col.count() == 0:
            logger.debug("search: collection '%s' rong", collectionName)
            return []

        cuaSo = max(k, _CANDIDATE_WINDOW)
        vectorOrder = self._vectorRank(col, queryVector, cuaSo)
        banGhi = col.get()
        idToDoc = self._buildDocLookup(banGhi)
        bm25Order = self._bm25Rank(banGhi, queryText, cuaSo)

        fused = _rrfFuse(vectorOrder, bm25Order, trongSoVector, trongSoBm25)
        xepHang = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        ketQua = [
            SearchResult(
                id=docId,
                document=idToDoc.get(docId, ("", {}))[0],
                metadata=idToDoc.get(docId, ("", {}))[1],
                score=score,
            )
            for docId, score in xepHang
        ]
        logger.info(
            "search collection '%s': %d ung vien -> tra %d ket qua",
            collectionName,
            len(fused),
            len(ketQua),
        )
        return ketQua

    @staticmethod
    def _vectorRank(col: CollectionLike, queryVector: list[float], n: int) -> list[str]:
        """Vector ranking (best-first) from `col.query`, at most `n`."""
        res = col.query(query_embeddings=[queryVector], n_results=n)
        ids = (res.get("ids") or [[]])[0]
        return list(ids)

    @staticmethod
    def _buildDocLookup(banGhi: dict) -> dict[str, tuple[str, dict]]:
        """Map id -> (document, metadata) from the `col.get` result."""
        ids = banGhi.get("ids") or []
        documents = banGhi.get("documents") or []
        metadatas = banGhi.get("metadatas") or []
        lookup: dict[str, tuple[str, dict]] = {}
        for i, docId in enumerate(ids):
            doc = documents[i] if i < len(documents) else ""
            meta = metadatas[i] if i < len(metadatas) else {}
            lookup[docId] = (doc or "", dict(meta or {}))
        return lookup

    @staticmethod
    def _bm25Rank(banGhi: dict, queryText: str, n: int) -> list[str]:
        """BM25 ranking (best-first), at most `n`; drop documents scoring 0."""
        ids = banGhi.get("ids") or []
        documents = banGhi.get("documents") or []
        queryTokens = _tokenize(queryText)
        if not ids or not queryTokens:
            return []
        corpus = [_tokenize(documents[i] if i < len(documents) else "") for i in range(len(ids))]
        diem = _bm25Scores(corpus, queryTokens)
        xepHang = sorted(zip(ids, diem), key=lambda p: (-p[1], p[0]))
        return [docId for docId, s in xepHang[:n] if s > 0]

    # --- Atomic write-temp-then-swap (R21.4) -------------------------------
    def commitDocument(
        self, collectionName: str, taiLieuId: str, items: list[VectorItem]
    ) -> None:
        """Commit a document's vectors into the main collection using temp-then-swap.

        1. Write `items` into a TEMP collection (delete a leftover temp first).
        2. Swap into the main collection: delete the old vectors of exactly `taiLieuId`
           then write the new vectors.
        3. Clean up the temp collection.

        An error midway → clean up the temp + remove the document's local vectors for
        `taiLieuId` in the main collection (no partial vectors left behind). The error
        is re-raised to the upper layer (a failed embed commit → keep the document's
        state unchanged).
        """
        tmpName = self._tempName(collectionName, taiLieuId)
        self._safeDeleteCollection(tmpName)  # clean leftover temp (if any)
        try:
            tmp = self.client.get_or_create_collection(tmpName)
            self._addItems(tmp, items)

            main = self.client.get_or_create_collection(collectionName)
            main.delete(where={META_TAI_LIEU_ID: taiLieuId})  # swap: delete old
            self._addItems(main, items)                        # swap: write new
        except Exception:
            logger.exception(
                "Loi khi chot vector tai lieu id=%s vao collection '%s' — rollback",
                taiLieuId,
                collectionName,
            )
            # Clean up the document's local vectors in the main collection.
            self._safeDeleteByTaiLieu(collectionName, taiLieuId)
            raise
        finally:
            self._safeDeleteCollection(tmpName)
        logger.info(
            "Chot %d vector tai lieu id=%s vao collection '%s' (atomic swap)",
            len(items),
            taiLieuId,
            collectionName,
        )

    # --- Internal helpers --------------------------------------------------
    @staticmethod
    def _tempName(collectionName: str, taiLieuId: str) -> str:
        """Temp collection name used during the swap (per document)."""
        return f"{collectionName}__tmp_{taiLieuId}"

    @staticmethod
    def _addItems(col: CollectionLike, items: list[VectorItem]) -> None:
        """Write the list of `items` into the collection (skip if empty)."""
        if not items:
            return
        col.add(
            ids=[i.id for i in items],
            embeddings=[i.vector for i in items],
            documents=[i.document for i in items],
            metadatas=[i.metadata for i in items],
        )

    def _safeDeleteCollection(self, collectionName: str) -> None:
        """Delete a collection, ignoring "does not exist" errors (idempotent)."""
        try:
            self.client.delete_collection(collectionName)
        except Exception as exc:  # noqa: BLE001 — collection does not exist yet
            logger.debug("Bo qua xoa collection '%s' (khong ton tai?): %s", collectionName, exc)

    def _safeDeleteByTaiLieu(self, collectionName: str, taiLieuId: str) -> None:
        """Delete a document's vectors, ignoring errors (used in the rollback path)."""
        try:
            col = self.client.get_or_create_collection(collectionName)
            col.delete(where={META_TAI_LIEU_ID: taiLieuId})
        except Exception as exc:  # noqa: BLE001 — rollback best-effort
            logger.debug(
                "Bo qua don vector tai lieu id=%s khoi '%s': %s",
                taiLieuId,
                collectionName,
                exc,
            )
