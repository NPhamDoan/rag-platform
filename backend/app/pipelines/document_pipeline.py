"""Document_Pipeline — document lifecycle: upload -> parse -> chunk -> preview (task 8.1).

Implements the first steps of the document state machine in design.md:

    NAP -> PARSE -> DA_PARSE_CHO_DUYET (-> CHOT -> DA_EMBED)

`uploadDocument` only goes as far as DA_PARSE_CHO_DUYET: validate permission/format/
size/quota, parse the text, chunk it, and save the preview Chunks into the RDB. It does
**NOT** create embeddings and does **NOT** write anything to the Vector_Store — per the
core invariant R5.13/R5.14: vectors exist if and only if trangThai = DA_EMBED. The
commit-embed step is in task 8.4 (`commitEmbedding`).

Naming conventions: entity/field in Vietnamese without diacritics; verb/method in
English; logging goes through the central logger, errors are never swallowed silently;
runs in a transaction (rollback on a mid-way error — R5.6).

Notes on PDF (R5.4/R5.5):
- Supported format set: txt, md, pdf.
- txt/md: decode UTF-8 text directly.
- pdf: extract text via PyMuPDF (`fitz`) if available. In this environment, PyMuPDF's
  native library may be blocked by Application Control (cannot load the DLL) — in that
  case `_parsePdf` raises `ValidationError` when it clearly cannot extract text. A
  scanned PDF / PDF with no text layer also leads to 0 characters -> 0 chunks -> reject
  (R5.5).
"""

from __future__ import annotations

import dataclasses
import logging

from sqlalchemy.orm import Session

from app.chunking.auto_selector import AutoSelector
from app.chunking.base import ChunkData, ChunkParams
from app.chunking.page_chunker import _PAGE_MARKER
from app.chunking.registry import get_chunker
from app.chunking.structure_chunker import _HEADING_PATTERN
from app.chunking.vietnamese_law_chunker import DIEU_BOUNDARY_PATTERN
from app.config import get_settings
from app.db.models import (
    Chunk,
    KhongGianTaiLieu,
    QuyTacRanhGioi,
    TaiKhoan,
    TaiLieu,
    TomTatTaiLieu,
    TrangThaiTaiLieu,
)
from app.errors import AuthorizationError, NotFoundError, ValidationError
from app.models.schemas import (
    ChunkEditOp,
    ChunkPreview,
    DocumentSummary,
    IndexingResult,
    PaginatedDocumentResponse,
    PreviewResult,
)
from app.providers.embedding_provider import EmbeddingProvider
from app.providers.registry import get_embedding_provider
from app.services.quota_service import LoaiTaiNguyen, QuotaService
from app.services.share_service import MucTruyCap, resolveAccess
from app.storage.vector_store import META_TAI_LIEU_ID, VectorItem, VectorStore

logger = logging.getLogger(__name__)

#: Set of file formats supported on upload (R5.4). Normalized: lower + strip leading ".".
DINH_DANG_HO_TRO = frozenset({"txt", "md", "pdf"})

# Shared error messages (safe to return to the client).
_KHONG_DU_QUYEN_GHI = "Ban khong co quyen ghi tren khong gian tai lieu nay."
_DINH_DANG_KHONG_HO_TRO = (
    "Dinh dang tep khong duoc ho tro. Chi chap nhan: txt, md, pdf."
)
_VUOT_KICH_THUOC = "Kich thuoc tep vuot qua gioi han cho phep."
_KHONG_TRICH_XUAT_DUOC = (
    "Khong trich xuat duoc noi dung van ban tu tep (tep rong hoac PDF scan)."
)
_PDF_KHONG_KHA_DUNG = (
    "Khong the trich xuat van ban tu PDF tren moi truong nay (thieu cong cu trich "
    "xuat PDF)."
)
_TAI_LIEU_KHONG_TON_TAI = "Khong tim thay tai lieu."
_KHONG_THE_CHOT = (
    "Chi co the chot embed tai lieu o trang thai DA_PARSE_CHO_DUYET (cho duyet)."
)
_RECHUNK_0_CHUNK = (
    "Cat lai tao ra 0 chunk; giu nguyen cac chunk cu cua tai lieu."
)
_CHUNK_RONG = "Thao tac sua tay tao ra chunk rong; giu nguyen cac chunk cu."
_OP_VI_TRI_KHONG_HOP_LE = "Vi tri chunk trong thao tac sua tay khong hop le."
_SPLIT_THIEU_VI_TRI_CAT = "Thao tac split thieu viTriCat hop le trong noi dung chunk."
_ADJUST_THIEU_RANH_GIOI = (
    "Thao tac adjust thieu viTriBatDauMoi/viTriKetThucMoi hop le."
)

# Document list pagination (R5.7): page >= 1, pageSize 1..100 (default 20).
PAGE_SIZE_MAC_DINH = 20
PAGE_SIZE_MIN = 1
PAGE_SIZE_MAX = 100
_PAGE_KHONG_HOP_LE = "page phai la so nguyen >= 1."
_PAGE_SIZE_KHONG_HOP_LE = "pageSize phai trong khoang 1..100."
_KHONG_GIAN_KHONG_TON_TAI = "Khong tim thay khong gian tai lieu."

# Document summary (R5.10): maximum length of the summary part (characters).
TOM_TAT_TOI_DA = 500
#: Maximum length of an outline section title (characters).
_OUTLINE_TIEU_DE_TOI_DA = 120


def _chuanHoaDinhDang(dinhDang: str) -> str:
    """Normalize a format: strip + lower + remove leading "." (e.g. ".PDF" -> "pdf")."""
    return dinhDang.strip().lower().lstrip(".")


class DocumentPipeline:
    """Document processing pipeline operating on a single SQLAlchemy `Session`.

    Collaborators (`QuotaService`, `AutoSelector`, `VectorStore`, Embedding_Provider)
    are initialized from `db`/defaults if not passed in — convenient for tests injecting
    fakes (fake embedding provider + fake/in-memory vector store).
    """

    def __init__(
        self,
        db: Session,
        quotaService: QuotaService | None = None,
        autoSelector: AutoSelector | None = None,
        vectorStore: VectorStore | None = None,
        embeddingProvider: EmbeddingProvider | None = None,
    ) -> None:
        self.db = db
        self.quotaService = quotaService or QuotaService(db)
        self.autoSelector = autoSelector or AutoSelector()
        # VectorStore() is import-safe (chromadb is loaded lazily only when used).
        self.vectorStore = vectorStore or VectorStore()
        # Override Embedding_Provider for tests (inject a fake). When None → resolve per
        # workspace via the registry (`khongGian.embeddingProvider`) in commitEmbedding.
        self.embeddingProvider = embeddingProvider

    # --- Upload -> parse -> chunk -> preview -------------------------------
    def uploadDocument(
        self,
        taiKhoan: TaiKhoan,
        khongGian: KhongGianTaiLieu,
        fileBytes: bytes,
        tenFile: str,
        dinhDang: str,
        chienLuocChunk: str = "auto",
    ) -> PreviewResult:
        """Upload + parse + chunk a document, stopping at state DA_PARSE_CHO_DUYET.

        Sequence (R5.1-5.5, R5.11, R5.13-14, R12.2-3):
        1. WRITE permission on the workspace (resolveAccess >= GHI) — otherwise
           `AuthorizationError` (403, R5.2).
        2. Format is in the supported set — otherwise `ValidationError` (R5.4).
        3. Size <= the config limit (`max_file_size_bytes`) — over →
           `ValidationError` (R5.3).
        4. Atomic quota check before saving (R12.2 capacity per account; R12.3 document
           count per workspace) via `QuotaService.checkAndReserve`.
        5. Parse the text from the file by format.
        6. Chunk by strategy: "auto" -> AutoSelector picks + resolves; otherwise ->
           `get_chunker(ten)`.
        7. 0 chunks (empty file / scanned PDF) -> `ValidationError`, do NOT create the
           document (R5.5).
        8. Save `TaiLieu` (DA_PARSE_CHO_DUYET) + the preview `Chunk`s; do NOT embed, do
           NOT write the Vector_Store (R5.13/5.14). Return `PreviewResult` ordered by
           thuTu.

        A mid-way error -> rollback the transaction (R5.6).
        """
        # 1) WRITE permission.
        mucTruyCap = resolveAccess(self.db, taiKhoan, khongGian)
        if mucTruyCap < MucTruyCap.GHI:
            logger.info(
                "Tu choi upload: tai khoan id=%s khong co quyen ghi khong gian id=%s",
                taiKhoan.id,
                khongGian.id,
            )
            raise AuthorizationError(_KHONG_DU_QUYEN_GHI)

        # 2) Format.
        dinhDangChuan = _chuanHoaDinhDang(dinhDang)
        if dinhDangChuan not in DINH_DANG_HO_TRO:
            logger.info(
                "Tu choi upload: dinh dang khong ho tro (tenFile=%s, dinhDang=%r)",
                tenFile,
                dinhDang,
            )
            raise ValidationError(_DINH_DANG_KHONG_HO_TRO)

        # 3) Size.
        kichThuoc = len(fileBytes)
        gioiHanByte = get_settings().max_file_size_bytes
        if kichThuoc > gioiHanByte:
            logger.info(
                "Tu choi upload: vuot kich thuoc (tenFile=%s, kichThuoc=%d, gioiHan=%d)",
                tenFile,
                kichThuoc,
                gioiHanByte,
            )
            raise ValidationError(_VUOT_KICH_THUOC)

        # 4) Atomic quota check before saving (R12.2 capacity; R12.3 document count).
        self.quotaService.checkAndReserve(
            taiKhoan.id, LoaiTaiNguyen.DUNG_LUONG, kichThuoc
        )
        self.quotaService.checkAndReserve(
            taiKhoan.id,
            LoaiTaiNguyen.SO_TAI_LIEU,
            1,
            khongGianId=khongGian.id,
        )

        # 5) Parse the text.
        text = self._parse(fileBytes, dinhDangChuan)

        # 6) Pick the strategy + chunk.
        thamSo = ChunkParams()
        chienLuocResolved, chunkData = self._chunk(
            text, dinhDangChuan, chienLuocChunk, thamSo
        )

        # 7) 0 chunks -> reject, do NOT create the document.
        if not chunkData:
            logger.info(
                "Tu choi upload: 0 chunk sau khi parse (tenFile=%s, dinhDang=%s)",
                tenFile,
                dinhDangChuan,
            )
            raise ValidationError(_KHONG_TRICH_XUAT_DUOC)

        # 8) Save TaiLieu + preview Chunks (DA_PARSE_CHO_DUYET), do NOT embed.
        taiLieu = self._persist(
            khongGian=khongGian,
            tenFile=tenFile,
            dinhDang=dinhDangChuan,
            kichThuoc=kichThuoc,
            vanBanGoc=text,
            chienLuocResolved=chienLuocResolved,
            thamSo=thamSo,
            chunkData=chunkData,
        )

        chunksPreview = [
            ChunkPreview(
                id=c.id,
                thuTu=c.thuTu,
                viTriBatDau=c.viTriBatDau,
                viTriKetThuc=c.viTriKetThuc,
                noiDung=c.noiDung,
            )
            for c in sorted(taiLieu.chunks, key=lambda c: c.thuTu)
        ]
        logger.info(
            "Upload thanh cong (preview): taiLieuId=%s, khongGianId=%s, soChunk=%d, "
            "chienLuoc=%s, trangThai=%s",
            taiLieu.id,
            khongGian.id,
            taiLieu.soChunk,
            chienLuocResolved,
            taiLieu.trangThai.value,
        )
        return PreviewResult(soChunk=taiLieu.soChunk, chunks=chunksPreview)

    # --- Commit embed: DA_PARSE_CHO_DUYET -> DA_EMBED ----------------------
    def commitEmbedding(self, taiKhoan: TaiKhoan, taiLieuId: str) -> IndexingResult:
        """Commit-embed a document: embed Chunks -> write Vector_Store -> DA_EMBED (R5.13, R21).

        Sequence:
        1. Fetch the TaiLieu; does not exist → `NotFoundError` (404).
        2. WRITE permission on the workspace holding the document (resolveAccess >= GHI)
           — otherwise `AuthorizationError` (403, R5.2).
        3. State must be DA_PARSE_CHO_DUYET (pending review); otherwise → `ValidationError`
           (e.g. already DA_EMBED, or still loading) — nothing is changed.
        4. Resolve the WORKSPACE's Embedding_Provider via the registry
           (`get_embedding_provider(khongGian.embeddingProvider)`); if a provider is
           injected (test) use the injected one.
        5. Embed the Chunk content (by thuTu) — an embed error → keep the state, do NOT
           write the Vector_Store (invariant R5.13: vector ⇔ DA_EMBED).
        6. Write the Vector_Store using atomic write-temp-then-swap (R21.4): the
           workspace's collection (`collectionName`).
        7. Set trangThai = DA_EMBED + commit. Return `IndexingResult`.

        An error at step 5/6 → no state change, no partial vectors left behind
        (VectorStore.commitDocument cleans up on error).
        """
        # 1) Fetch the document.
        taiLieu = self.db.get(TaiLieu, taiLieuId)
        if taiLieu is None:
            logger.info("Tu choi chot embed: tai lieu khong ton tai (id=%s)", taiLieuId)
            raise NotFoundError(_TAI_LIEU_KHONG_TON_TAI)

        khongGian = self.db.get(KhongGianTaiLieu, taiLieu.khongGianId)
        if khongGian is None:  # foreign-key invariant — defensive
            logger.error(
                "Tai lieu id=%s tham chieu khong gian id=%s khong ton tai",
                taiLieuId,
                taiLieu.khongGianId,
            )
            raise NotFoundError(_TAI_LIEU_KHONG_TON_TAI)

        # 2) WRITE permission.
        if resolveAccess(self.db, taiKhoan, khongGian) < MucTruyCap.GHI:
            logger.info(
                "Tu choi chot embed: tai khoan id=%s khong co quyen ghi khong gian id=%s",
                taiKhoan.id,
                khongGian.id,
            )
            raise AuthorizationError(_KHONG_DU_QUYEN_GHI)

        # 3) Valid state.
        if taiLieu.trangThai != TrangThaiTaiLieu.DA_PARSE_CHO_DUYET:
            logger.info(
                "Tu choi chot embed: tai lieu id=%s o trang thai %s (can DA_PARSE_CHO_DUYET)",
                taiLieuId,
                taiLieu.trangThai.value,
            )
            raise ValidationError(_KHONG_THE_CHOT)

        # 4) Resolve the workspace's Embedding_Provider.
        provider = self._resolveEmbeddingProvider(khongGian)

        # 5) Embed the Chunk content by thuTu (error → no state change).
        chunks = sorted(taiLieu.chunks, key=lambda c: c.thuTu)
        vectors = provider.embed([c.noiDung for c in chunks])
        if len(vectors) != len(chunks):
            logger.error(
                "Embedding tra ve %d vector cho %d chunk (tai lieu id=%s)",
                len(vectors),
                len(chunks),
                taiLieuId,
            )
            raise ValidationError("So vector embedding khong khop so chunk.")

        # 6) Write the Vector_Store (atomic write-temp-then-swap, R21.4).
        items = [
            VectorItem(
                id=c.id,
                vector=vec,
                document=c.noiDung,
                metadata={
                    META_TAI_LIEU_ID: taiLieuId,
                    "khongGianId": khongGian.id,
                    "thuTu": c.thuTu,
                    "viTriBatDau": c.viTriBatDau,
                    "viTriKetThuc": c.viTriKetThuc,
                },
            )
            for c, vec in zip(chunks, vectors)
        ]
        self.vectorStore.commitDocument(khongGian.collectionName, taiLieuId, items)

        # 7) Set state DA_EMBED.
        taiLieu.trangThai = TrangThaiTaiLieu.DA_EMBED
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            # DB state unchanged → clean up the vectors just written to keep invariant R5.13.
            self.vectorStore.deleteByTaiLieu(khongGian.collectionName, taiLieuId)
            logger.exception(
                "Loi khi luu trang thai DA_EMBED (tai lieu id=%s) — da rollback + don vector",
                taiLieuId,
            )
            raise
        self.db.refresh(taiLieu)

        # R5.10: document loaded successfully (DA_EMBED) → generate TomTatTaiLieu + outline.
        # The embedding is safely committed; a summary-generation error (non-critical)
        # only logs WARN and does NOT break the commit-embed result.
        try:
            self.buildSummary(taiLieu)
        except Exception:
            logger.warning(
                "Khong sinh duoc TomTatTaiLieu sau khi chot embed (taiLieuId=%s); "
                "embedding van hop le",
                taiLieuId,
                exc_info=True,
            )

        logger.info(
            "Chot embed thanh cong: taiLieuId=%s, khongGianId=%s, soChunk=%d, trangThai=%s",
            taiLieuId,
            khongGian.id,
            taiLieu.soChunk,
            taiLieu.trangThai.value,
        )
        return IndexingResult(
            taiLieuId=taiLieuId,
            soChunk=taiLieu.soChunk,
            trangThai=taiLieu.trangThai,
        )

    def _resolveEmbeddingProvider(self, khongGian: KhongGianTaiLieu) -> EmbeddingProvider:
        """Return the workspace's Embedding_Provider.

        Prefers the injected one (test). Otherwise resolves the class via the registry
        by `khongGian.embeddingProvider` and instantiates it (R21.1).
        """
        if self.embeddingProvider is not None:
            return self.embeddingProvider
        providerCls = get_embedding_provider(khongGian.embeddingProvider)
        return providerCls()

    # --- Re-chunk / manual edit / boundary rules / reset (task 8.5) --------
    def _loadDocWithWriteAccess(
        self, taiKhoan: TaiKhoan, taiLieuId: str, hanhDong: str
    ) -> tuple[TaiLieu, KhongGianTaiLieu]:
        """Fetch TaiLieu + KhongGian, requiring WRITE permission. Shared by task 8.5.

        - Document does not exist -> `NotFoundError` (404).
        - Insufficient WRITE permission on the workspace -> `AuthorizationError` (403, R18.5).

        Requires only WRITE (no role restriction): a NGUOI_DUNG with write permission can
        perform every chunk-editing operation WITHOUT any code change (R18.5/R18.8/R18.9).
        """
        taiLieu = self.db.get(TaiLieu, taiLieuId)
        if taiLieu is None:
            logger.info("Tu choi %s: tai lieu khong ton tai (id=%s)", hanhDong, taiLieuId)
            raise NotFoundError(_TAI_LIEU_KHONG_TON_TAI)

        khongGian = self.db.get(KhongGianTaiLieu, taiLieu.khongGianId)
        if khongGian is None:  # foreign-key invariant — defensive
            logger.error(
                "Tai lieu id=%s tham chieu khong gian id=%s khong ton tai",
                taiLieuId,
                taiLieu.khongGianId,
            )
            raise NotFoundError(_TAI_LIEU_KHONG_TON_TAI)

        if resolveAccess(self.db, taiKhoan, khongGian) < MucTruyCap.GHI:
            logger.info(
                "Tu choi %s: tai khoan id=%s khong co quyen ghi khong gian id=%s",
                hanhDong,
                taiKhoan.id,
                khongGian.id,
            )
            raise AuthorizationError(_KHONG_DU_QUYEN_GHI)
        return taiLieu, khongGian

    def rechunk(
        self,
        taiKhoan: TaiKhoan,
        taiLieuId: str,
        chienLuocChunk: str | None = None,
        thamSo: ChunkParams | dict | None = None,
    ) -> PreviewResult:
        """Re-chunk a document (idempotent, replaces all old Chunks) — R5.12, R18.7, R21.4.

        Sequence:
        1. Fetch TaiLieu + WRITE permission (R18.5).
        2. Re-chunk from `vanBanGoc` using the new strategy (or keep the old strategy) +
           new params (or keep the old), APPLYING the QuyTacRanhGioi declared for the
           document (R18.4). Computed in memory first — NOT yet committed to the DB.
        3. 0 chunks -> `ValidationError`, KEEP the old Chunks (R18.10).
        4. Full replace: delete all old Chunks then write the new ones in one transaction
           (build-then-replace). A mid-way error -> rollback, keep the old Chunks (R5.6,
           R18.10).
        5. If the document was DA_EMBED: re-chunking INVALIDATES the embedding — revert to
           DA_PARSE_CHO_DUYET + delete the vectors from the Vector_Store (invariant R5.13:
           vectors exist if and only if DA_EMBED).

        Idempotent: re-chunking multiple times with the same input yields the same set of
        Chunks (same content/positions/order) — all old ones are cleared before writing
        the new ones.
        """
        taiLieu, khongGian = self._loadDocWithWriteAccess(taiKhoan, taiLieuId, "cat lai")

        chienLuocYeuCau = chienLuocChunk if chienLuocChunk is not None else taiLieu.chienLuocChunk
        thamSoMoi = ChunkParams.from_any(thamSo if thamSo is not None else taiLieu.thamSoChunk)
        rules = list(taiLieu.quyTac)

        # Compute first (not committed yet) — an error here keeps the old Chunks.
        chienLuocResolved, chunkData = self._chunk(
            taiLieu.vanBanGoc, taiLieu.dinhDang, chienLuocYeuCau, thamSoMoi, rules
        )
        if not chunkData:
            logger.info(
                "Tu choi cat lai: 0 chunk (taiLieuId=%s, chienLuoc=%s)",
                taiLieuId,
                chienLuocYeuCau,
            )
            raise ValidationError(_RECHUNK_0_CHUNK)

        wasEmbedded = taiLieu.trangThai == TrangThaiTaiLieu.DA_EMBED
        self._replaceChunks(
            taiLieu, chunkData, chienLuocResolved=chienLuocResolved, thamSo=thamSoMoi,
            wasEmbedded=wasEmbedded,
        )
        # After the DB is committed: re-chunking invalidated the embedding -> clean up vectors.
        if wasEmbedded:
            self.vectorStore.deleteByTaiLieu(khongGian.collectionName, taiLieuId)

        logger.info(
            "Cat lai thanh cong: taiLieuId=%s, soChunk=%d, chienLuoc=%s, trangThai=%s",
            taiLieuId,
            taiLieu.soChunk,
            chienLuocResolved,
            taiLieu.trangThai.value,
        )
        return self._previewResult(taiLieuId)

    def editChunks(
        self, taiKhoan: TaiKhoan, taiLieuId: str, ops: list[ChunkEditOp]
    ) -> list[ChunkPreview]:
        """Manually edit Chunks: merge / split / adjust — R18.3, R18.11.

        Applies each operation to a COPY of the current chunk list (not committed to the
        DB). An operation producing an empty chunk (only whitespace) -> `ValidationError`,
        KEEPING the old chunks (R18.11). After applying all: recompute `thuTu` contiguously
        + overwrite.

        If the document was DA_EMBED: a manual edit INVALIDATES the embedding -> revert to
        DA_PARSE_CHO_DUYET + delete the vectors (invariant R5.13).
        """
        taiLieu, khongGian = self._loadDocWithWriteAccess(taiKhoan, taiLieuId, "sua tay chunk")

        # Working copy: each element = [viTriBatDau, viTriKetThuc, noiDung].
        chunksHienTai = self.db.query(Chunk).filter(
            Chunk.taiLieuId == taiLieuId
        ).order_by(Chunk.thuTu).all()
        lamViec: list[list] = [
            [c.viTriBatDau, c.viTriKetThuc, c.noiDung] for c in chunksHienTai
        ]

        for op in ops:
            lamViec = self._applyEditOp(lamViec, op, taiLieu.vanBanGoc)

        # Convert the copy -> ChunkData with contiguous thuTu.
        chunkData = [
            ChunkData(thuTu=i, viTriBatDau=batDau, viTriKetThuc=ketThuc, noiDung=noiDung)
            for i, (batDau, ketThuc, noiDung) in enumerate(lamViec)
        ]
        if not chunkData:
            logger.info("Tu choi sua tay: 0 chunk con lai (taiLieuId=%s)", taiLieuId)
            raise ValidationError(_CHUNK_RONG)

        wasEmbedded = taiLieu.trangThai == TrangThaiTaiLieu.DA_EMBED
        self._replaceChunks(
            taiLieu, chunkData, chienLuocResolved=taiLieu.chienLuocChunk,
            thamSo=ChunkParams.from_any(taiLieu.thamSoChunk), wasEmbedded=wasEmbedded,
        )
        if wasEmbedded:
            self.vectorStore.deleteByTaiLieu(khongGian.collectionName, taiLieuId)

        logger.info(
            "Sua tay chunk thanh cong: taiLieuId=%s, soOp=%d, soChunk=%d, trangThai=%s",
            taiLieuId,
            len(ops),
            taiLieu.soChunk,
            taiLieu.trangThai.value,
        )
        return self._previewResult(taiLieuId).chunks

    def getChunks(self, taiKhoan: TaiKhoan, taiLieuId: str) -> PreviewResult:
        """Read the document's current preview Chunks (R18.1) — requires WRITE permission.

        Document does not exist -> `NotFoundError` (404); insufficient WRITE permission ->
        `AuthorizationError` (403). Returns `PreviewResult` ordered by thuTu (read-only,
        nothing is changed).
        """
        self._loadDocWithWriteAccess(taiKhoan, taiLieuId, "xem chunk")
        logger.info("Doc chunk preview: taiLieuId=%s", taiLieuId)
        return self._previewResult(taiLieuId)

    def setBoundaryRules(
        self,
        taiKhoan: TaiKhoan,
        phamVi: str,
        phamViId: str,
        rules: list[dict],
    ) -> None:
        """Declare QuyTacRanhGioi (stored as DATA, not code) — R18.4.

        `phamViId` is the document id; requires WRITE permission. Each element of `rules`
        is a dict with `tuKhoaHoacMau` (str) + `dieuKien` (dict, optional). Replaces the
        document's entire existing rule set (build-then-replace). These rules are APPLIED
        on the next `rechunk`.
        """
        _, _ = self._loadDocWithWriteAccess(taiKhoan, phamViId, "khai bao quy tac ranh gioi")

        # Clear the document's old rules then write the new ones (in one transaction).
        cu = self.db.query(QuyTacRanhGioi).filter(
            QuyTacRanhGioi.phamViId == phamViId
        ).all()
        for q in cu:
            self.db.delete(q)
        self.db.flush()

        for r in rules:
            self.db.add(
                QuyTacRanhGioi(
                    phamVi=phamVi,
                    phamViId=phamViId,
                    tuKhoaHoacMau=r["tuKhoaHoacMau"],
                    dieuKien=r.get("dieuKien", {}) or {},
                )
            )
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi khai bao quy tac ranh gioi (phamViId=%s) — da rollback", phamViId
            )
            raise
        logger.info(
            "Khai bao %d quy tac ranh gioi (phamVi=%s, phamViId=%s)",
            len(rules),
            phamVi,
            phamViId,
        )

    def resetToDefault(self, taiKhoan: TaiKhoan, taiLieuId: str) -> PreviewResult:
        """Reset strategy/params to default + delete custom rules, then re-chunk — R18.6.

        - Strategy -> "auto"; params -> default `ChunkParams()`.
        - Delete every QuyTacRanhGioi specific to the document.
        - Re-chunk (idempotent) with the default strategy/params and no rules.
        """
        taiLieu, _ = self._loadDocWithWriteAccess(taiKhoan, taiLieuId, "reset mac dinh")

        # Delete the document's custom rules before re-chunking (so rechunk applies no rules).
        cu = self.db.query(QuyTacRanhGioi).filter(
            QuyTacRanhGioi.phamViId == taiLieuId
        ).all()
        for q in cu:
            self.db.delete(q)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi xoa quy tac ranh gioi luc reset (taiLieuId=%s) — da rollback",
                taiLieuId,
            )
            raise

        logger.info("Reset ve mac dinh + cat lai: taiLieuId=%s", taiLieuId)
        return self.rechunk(
            taiKhoan, taiLieuId, chienLuocChunk="auto", thamSo=ChunkParams()
        )

    # --- buildSummary / listDocuments / deleteDocument (task 8.10) ---------
    def buildSummary(self, taiLieu: TaiLieu) -> TomTatTaiLieu:
        """Generate a TomTatTaiLieu (summary + outline) for the document and save it (upsert) — R5.10.

        The summary + outline are derived DETERMINISTICALLY from `vanBanGoc` (no LLM
        involved):
        - Summary: normalize the whitespace of the original text, take at most
          `TOM_TAT_TOI_DA` leading characters (enough to display/introduce the content).
        - Outline (a list of {tieuDe, viTri}): extracted from the structural markers in
          the original text — "Điều N." lines (DIEU_BOUNDARY_PATTERN) and markdown
          headings (_HEADING_PATTERN), ordered by position. No markers → fall back to the
          existing Chunk boundaries (one entry per Chunk).

        1-1 with TaiLieu: if a record already exists, update it (upsert) rather than
        creating a duplicate primary key. Runs in a transaction (rollback on error).
        Returns the `TomTatTaiLieu`.

        Note: this is a deterministic derived summary, NOT an LLM call. Later an LLM
        enricher (generating a richer summary/semantics) could be plugged in at the same
        point without changing the interface.
        """
        tomTatText = self._buildSummaryText(taiLieu.vanBanGoc)
        outline = self._buildOutline(taiLieu)

        tomTat = self.db.get(TomTatTaiLieu, taiLieu.id)
        if tomTat is None:
            tomTat = TomTatTaiLieu(
                taiLieuId=taiLieu.id, tomTat=tomTatText, outline=outline
            )
            self.db.add(tomTat)
            hanhDong = "tao moi"
        else:
            tomTat.tomTat = tomTatText
            tomTat.outline = outline
            hanhDong = "cap nhat"

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi luu TomTatTaiLieu (taiLieuId=%s) — da rollback", taiLieu.id
            )
            raise
        self.db.refresh(tomTat)
        logger.info(
            "Sinh TomTatTaiLieu (%s): taiLieuId=%s, soMuc=%d",
            hanhDong,
            taiLieu.id,
            len(outline),
        )
        return tomTat

    def listDocuments(
        self,
        taiKhoan: TaiKhoan,
        khongGianId: str,
        page: int = 1,
        pageSize: int = PAGE_SIZE_MAC_DINH,
    ) -> PaginatedDocumentResponse:
        """List a workspace's TaiLieu by page, including the total count (R5.7).

        1. Workspace exists + the account has at least CHI_DOC permission (resolveAccess
           >= CHI_DOC). Workspace does not exist OR permission NONE → `NotFoundError`
           (404) — per the project's data-isolation convention (do not reveal a
           workspace's existence to an unauthorized user).
        2. Validate parameters: `page >= 1`, `1 <= pageSize <= 100`; out of range →
           `ValidationError` (400). (Default `pageSize = 20`.)
        3. Return a page of TaiLieu (ordered by createdAt descending, stable by id) +
           the TOTAL number of TaiLieu in the workspace.
        """
        khongGian = self.db.get(KhongGianTaiLieu, khongGianId)
        if khongGian is None or (
            resolveAccess(self.db, taiKhoan, khongGian) < MucTruyCap.CHI_DOC
        ):
            logger.info(
                "Tu choi liet ke tai lieu: khong gian id=%s khong ton tai hoac "
                "tai khoan id=%s khong co quyen doc",
                khongGianId,
                taiKhoan.id,
            )
            raise NotFoundError(_KHONG_GIAN_KHONG_TON_TAI)

        if page < 1:
            raise ValidationError(_PAGE_KHONG_HOP_LE)
        if pageSize < PAGE_SIZE_MIN or pageSize > PAGE_SIZE_MAX:
            raise ValidationError(_PAGE_SIZE_KHONG_HOP_LE)

        tongSo = (
            self.db.query(TaiLieu)
            .filter(TaiLieu.khongGianId == khongGianId)
            .count()
        )
        taiLieuTrang = (
            self.db.query(TaiLieu)
            .filter(TaiLieu.khongGianId == khongGianId)
            .order_by(TaiLieu.createdAt.desc(), TaiLieu.id)
            .offset((page - 1) * pageSize)
            .limit(pageSize)
            .all()
        )

        logger.info(
            "Liet ke tai lieu: khongGianId=%s, page=%d, pageSize=%d, tongSo=%d, traVe=%d",
            khongGianId,
            page,
            pageSize,
            tongSo,
            len(taiLieuTrang),
        )
        return PaginatedDocumentResponse(
            items=[DocumentSummary.model_validate(t) for t in taiLieuTrang],
            tongSo=tongSo,
            page=page,
            pageSize=pageSize,
        )

    def deleteDocument(self, taiKhoan: TaiKhoan, taiLieuId: str) -> None:
        """Delete a TaiLieu along with its Chunks/Summary + vectors in the Vector_Store (R5.8, R5.9).

        1. Document exists + WRITE permission (R5.9: does not exist → `NotFoundError`
           404, the Vector_Store is not changed).
        2. Delete the TaiLieu in a transaction — ORM cascade deletes its Chunks +
           TomTatTaiLieu + QuyTacRanhGioi.
        3. After the DB commits: delete every vector of the document from the workspace's
           collection (`vectorStore.deleteByTaiLieu`) — leaving no orphaned vectors (keeps
           invariant R5.13: vectors exist if and only if the document is DA_EMBED & still
           exists).

        Order (DB first, vectors after) is consistent with `rechunk`/`editChunks`. A
        mid-way error while deleting from the DB → rollback, keeping the document +
        vectors.
        """
        taiLieu, khongGian = self._loadDocWithWriteAccess(
            taiKhoan, taiLieuId, "xoa tai lieu"
        )
        collectionName = khongGian.collectionName

        try:
            self.db.delete(taiLieu)  # cascade: Chunk + TomTatTaiLieu + QuyTacRanhGioi
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi xoa tai lieu id=%s — da rollback, giu nguyen tai lieu", taiLieuId
            )
            raise

        # DB committed: clean up the vectors from the Vector_Store (idempotent).
        self.vectorStore.deleteByTaiLieu(collectionName, taiLieuId)
        logger.info(
            "Xoa tai lieu thanh cong: taiLieuId=%s, khongGianId=%s (Chunk/TomTat + vector da xoa)",
            taiLieuId,
            khongGian.id,
        )

    # --- Summary / outline utilities ---------------------------------------
    @staticmethod
    def _buildSummaryText(vanBanGoc: str) -> str:
        """Deterministic summary: normalize whitespace + truncate to at most `TOM_TAT_TOI_DA` chars."""
        text = " ".join(vanBanGoc.split())
        if len(text) <= TOM_TAT_TOI_DA:
            return text
        return text[:TOM_TAT_TOI_DA].rstrip() + "…"

    def _buildOutline(self, taiLieu: TaiLieu) -> list[dict]:
        """Derive the outline [{tieuDe, viTri}] from the structural markers of the original text.

        Prefers "Điều N." markers + markdown headings (ordered by position). No markers →
        fall back to the existing Chunk boundaries (one entry per Chunk).
        """
        text = taiLieu.vanBanGoc
        muc: list[dict] = []

        for m in DIEU_BOUNDARY_PATTERN.finditer(text):
            muc.append({"tieuDe": self._layDong(text, m.start()), "viTri": m.start()})
        for m in _HEADING_PATTERN.finditer(text):
            tieuDe = self._layDong(text, m.start()).lstrip("#").strip()
            if tieuDe:
                muc.append({"tieuDe": tieuDe, "viTri": m.start()})

        if muc:
            muc.sort(key=lambda x: x["viTri"])
            return muc

        # Fallback: Chunk boundaries (one entry per Chunk, title = first line).
        chunks = sorted(taiLieu.chunks, key=lambda c: c.thuTu)
        return [
            {
                "tieuDe": self._tieuDeTuNoiDung(c.noiDung),
                "viTri": c.viTriBatDau,
            }
            for c in chunks
        ]

    @staticmethod
    def _layDong(text: str, pos: int) -> str:
        """Take the (stripped) line starting at `pos`, truncated to `_OUTLINE_TIEU_DE_TOI_DA`."""
        ketThuc = text.find("\n", pos)
        if ketThuc == -1:
            ketThuc = len(text)
        dong = text[pos:ketThuc].strip()
        return dong[:_OUTLINE_TIEU_DE_TOI_DA]

    @staticmethod
    def _tieuDeTuNoiDung(noiDung: str) -> str:
        """Fallback title for an entry: the first non-empty line of the Chunk content."""
        for dong in noiDung.splitlines():
            dong = dong.strip()
            if dong:
                return dong[:_OUTLINE_TIEU_DE_TOI_DA]
        return noiDung.strip()[:_OUTLINE_TIEU_DE_TOI_DA]

    # --- Re-chunk / manual-edit utilities ----------------------------------
    def _applyEditOp(
        self, lamViec: list[list], op: ChunkEditOp, vanBanGoc: str
    ) -> list[list]:
        """Apply one manual-edit operation to a copy of the chunk list; return the new copy.

        Producing an empty chunk (only whitespace) -> `ValidationError` (R18.11). Does NOT
        change the DB; only operates on the in-memory copy.
        """
        i = op.viTri
        if i < 0 or i >= len(lamViec):
            raise ValidationError(_OP_VI_TRI_KHONG_HOP_LE)

        ketQua = [list(c) for c in lamViec]
        if op.loai == "merge":
            if i + 1 >= len(ketQua):
                raise ValidationError(_OP_VI_TRI_KHONG_HOP_LE)
            batDau = ketQua[i][0]
            ketThuc = ketQua[i + 1][1]
            noiDung = ketQua[i][2] + ketQua[i + 1][2]
            self._kiemNoiDung(noiDung)
            ketQua[i] = [batDau, ketThuc, noiDung]
            del ketQua[i + 1]
        elif op.loai == "split":
            batDau, ketThuc, noiDung = ketQua[i]
            cat = op.viTriCat
            if cat is None or cat <= 0 or cat >= len(noiDung):
                raise ValidationError(_SPLIT_THIEU_VI_TRI_CAT)
            trai = noiDung[:cat]
            phai = noiDung[cat:]
            self._kiemNoiDung(trai)
            self._kiemNoiDung(phai)
            ketQua[i] = [batDau, batDau + cat, trai]
            ketQua.insert(i + 1, [batDau + cat, ketThuc, phai])
        elif op.loai == "adjust":
            batDauMoi = op.viTriBatDauMoi
            ketThucMoi = op.viTriKetThucMoi
            if (
                batDauMoi is None
                or ketThucMoi is None
                or batDauMoi >= ketThucMoi
                or ketThucMoi > len(vanBanGoc)
            ):
                raise ValidationError(_ADJUST_THIEU_RANH_GIOI)
            noiDung = vanBanGoc[batDauMoi:ketThucMoi]
            self._kiemNoiDung(noiDung)
            ketQua[i] = [batDauMoi, ketThucMoi, noiDung]
        else:  # defensive — the schema already restricts to a Literal
            raise ValidationError(_OP_VI_TRI_KHONG_HOP_LE)
        return ketQua

    @staticmethod
    def _kiemNoiDung(noiDung: str) -> None:
        """Reject an empty chunk (only whitespace) — R18.11."""
        if not noiDung.strip():
            raise ValidationError(_CHUNK_RONG)

    def _replaceChunks(
        self,
        taiLieu: TaiLieu,
        chunkData: list[ChunkData],
        *,
        chienLuocResolved: str,
        thamSo: ChunkParams,
        wasEmbedded: bool,
    ) -> None:
        """Replace all old Chunks with new ones in one transaction (build-then-replace).

        A mid-way error -> rollback, keep the old Chunks (R5.6, R18.10). If `wasEmbedded`,
        revert the state to DA_PARSE_CHO_DUYET (the embedding is invalidated); cleaning up
        the vectors is done by the caller after the transaction commits.
        """
        for c in list(taiLieu.chunks):
            self.db.delete(c)
        self.db.flush()

        for cd in chunkData:
            self.db.add(
                Chunk(
                    taiLieuId=taiLieu.id,
                    thuTu=cd.thuTu,
                    viTriBatDau=cd.viTriBatDau,
                    viTriKetThuc=cd.viTriKetThuc,
                    noiDung=cd.noiDung,
                    chunkMetadata=cd.metadata,
                )
            )

        taiLieu.chienLuocChunk = chienLuocResolved
        taiLieu.thamSoChunk = dataclasses.asdict(thamSo)
        taiLieu.soChunk = len(chunkData)
        if wasEmbedded:
            taiLieu.trangThai = TrangThaiTaiLieu.DA_PARSE_CHO_DUYET

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi thay sach Chunk (taiLieuId=%s) — da rollback, giu Chunk cu",
                taiLieu.id,
            )
            raise
        self.db.refresh(taiLieu)

    def _previewResult(self, taiLieuId: str) -> PreviewResult:
        """Read the document's current Chunks from the RDB -> PreviewResult ordered by thuTu."""
        chunks = self.db.query(Chunk).filter(
            Chunk.taiLieuId == taiLieuId
        ).order_by(Chunk.thuTu).all()
        chunksPreview = [
            ChunkPreview(
                id=c.id,
                thuTu=c.thuTu,
                viTriBatDau=c.viTriBatDau,
                viTriKetThuc=c.viTriKetThuc,
                noiDung=c.noiDung,
            )
            for c in chunks
        ]
        return PreviewResult(soChunk=len(chunksPreview), chunks=chunksPreview)

    # --- Parse -------------------------------------------------------------
    def _parse(self, fileBytes: bytes, dinhDang: str) -> str:
        """Extract text from the file by the normalized format.

        - txt/md: decode UTF-8 (invalid characters -> replaced for robustness).
        - pdf: use PyMuPDF if available; otherwise raise `ValidationError`.
        """
        if dinhDang in {"txt", "md"}:
            return fileBytes.decode("utf-8", errors="replace")
        # pdf
        return self._parsePdf(fileBytes)

    def _parsePdf(self, fileBytes: bytes) -> str:
        """Extract text from a PDF (PyMuPDF). Pages are joined with a form-feed marker.

        If the extraction tool cannot be loaded (e.g. blocked by Application Control) ->
        `ValidationError` when clear. A PDF with no text layer -> returns an empty string
        (then rejected at the 0-chunk step, R5.5).
        """
        try:
            import fitz  # PyMuPDF
        except Exception as exc:  # noqa: BLE001 — load failed (missing / blocked DLL)
            logger.error("Khong nap duoc PyMuPDF de trich xuat PDF: %s", exc)
            raise ValidationError(_PDF_KHONG_KHA_DUNG) from exc

        try:
            doc = fitz.open(stream=fileBytes, filetype="pdf")
            try:
                cacTrang = [trang.get_text() for trang in doc]
            finally:
                doc.close()
        except Exception as exc:  # noqa: BLE001 — corrupt / unreadable PDF
            logger.error("Loi khi trich xuat van ban PDF: %s", exc)
            raise ValidationError(_KHONG_TRICH_XUAT_DUOC) from exc

        return _PAGE_MARKER.join(cacTrang)

    # --- Chunk -------------------------------------------------------------
    def _chunk(
        self,
        text: str,
        dinhDang: str,
        chienLuocChunk: str,
        thamSo: ChunkParams,
        rules: list[QuyTacRanhGioi] | None = None,
    ) -> tuple[str, list[ChunkData]]:
        """Pick the strategy + chunk. Returns (resolved strategy name, chunks).

        `chienLuocChunk == "auto"` -> AutoSelector picks by a fixed priority order then
        resolves. Otherwise -> look up `get_chunker(ten)` (a non-existent name →
        `ValidationError`, R17.7). `rules` (QuyTacRanhGioi) are passed down to the
        strategy to apply on re-chunk (R18.4); the strategy may ignore them if unused.
        """
        if chienLuocChunk.strip().lower() == "auto":
            tenChienLuoc = self.autoSelector.selectStrategy(text, dinhDang=dinhDang)
            chunker = self.autoSelector.resolveChunker(tenChienLuoc)
        else:
            tenChienLuoc = chienLuocChunk.strip()
            chunker = get_chunker(tenChienLuoc)()

        chunkData = chunker.chunk(text, thamSo, rules)
        return tenChienLuoc, chunkData

    # --- Persist -----------------------------------------------------------
    def _persist(
        self,
        khongGian: KhongGianTaiLieu,
        tenFile: str,
        dinhDang: str,
        kichThuoc: int,
        vanBanGoc: str,
        chienLuocResolved: str,
        thamSo: ChunkParams,
        chunkData: list[ChunkData],
    ) -> TaiLieu:
        """Save TaiLieu (DA_PARSE_CHO_DUYET) + the preview Chunks in one transaction.

        Does NOT embed, does NOT write the Vector_Store (R5.13/5.14). A mid-way error ->
        rollback (R5.6) so no half-loaded state is left behind.
        """
        taiLieu = TaiLieu(
            khongGianId=khongGian.id,
            tenFile=tenFile,
            dinhDang=dinhDang,
            kichThuoc=kichThuoc,
            vanBanGoc=vanBanGoc,
            trangThai=TrangThaiTaiLieu.DA_PARSE_CHO_DUYET,
            chienLuocChunk=chienLuocResolved,
            thamSoChunk=dataclasses.asdict(thamSo),
            soChunk=len(chunkData),
        )
        self.db.add(taiLieu)
        self.db.flush()  # generate taiLieu.id for the Chunk foreign key.

        for cd in chunkData:
            self.db.add(
                Chunk(
                    taiLieuId=taiLieu.id,
                    thuTu=cd.thuTu,
                    viTriBatDau=cd.viTriBatDau,
                    viTriKetThuc=cd.viTriKetThuc,
                    noiDung=cd.noiDung,
                    chunkMetadata=cd.metadata,
                )
            )

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi luu tai lieu/chunk (tenFile=%s, khongGianId=%s) — da rollback",
                tenFile,
                khongGian.id,
            )
            raise

        self.db.refresh(taiLieu)
        return taiLieu
