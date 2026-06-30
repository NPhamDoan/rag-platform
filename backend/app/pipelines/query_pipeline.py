"""Query_Pipeline — per-workspace Q&A query processing (task 10.1, 10.6, 10.10).

The query flow steps from design.md:

    validate question -> classify intent (or force mode) -> normalize (add diacritics)
    -> retrieve (embed + hybrid RRF + gating) -> synthesize ([n] + TrichDan)
    -> verify (safe degradation) / fallback to original chunks

- Task 10.1: validate / classifyIntent / resolveMode / normalizeQuestion.
- Task 10.6: `retrieve` (embed + hybrid search RRF limited to the collection + threshold gating).
- Task 10.10: `synthesize` (insert marker [n] <-> TrichDan, strict grounding),
  `verifyAnswer` (asynchronous cross-verification, safe degradation to CHUA_XAC_MINH),
  `answerDetail` (assemble KetQuaTraLoi + fallback to original chunks when synthesis fails).
- Task 10.15: `answerOverview` (synthesize from the TomTatTaiLieu + outline of the
  accessible TaiLieu, with >=1 TrichDan, `laTongQuan=True`; no documents -> reports "no
  documents yet" WITHOUT calling the LLM), `buildSuggestions` (suggestions generated from
  real document outlines; no documents -> empty list, WITHOUT calling the LLM), and
  applying the MauPrompt by role (taken from the db as base) but ALWAYS appending
  `INVARIANT_SAFETY_CONSTRAINTS` (R20.1/20.2/20.3).

The task 10.1 methods:
- `validateQuestion(cauHoi)` (R6.3): strip whitespace, length 1..1000, otherwise
  `ValidationError`.
- `classifyIntent(cauHoi)` (R16.6): DETERMINISTIC classification "tong-quan" vs
  "chi-tiet" by a configurable keyword set (module constant). Same input -> same result.
- `resolveMode(cauHoi, cheDo)` (R16.7/16.8): a forced mode overrides automatic
  classification; when `cheDo` is None -> use `classifyIntent`.
- `normalizeQuestion(cauHoi)` (R6.7): a question with diacritics -> kept as-is; a
  question without diacritics -> call the LLM_Provider to add diacritics, accepting the
  result ONLY if it is the SAME word set after diacritic removal (only adds diacritics,
  does not change words), otherwise keep the original.

Naming conventions: entity/field in Vietnamese without diacritics; verb/method in
English; logging goes through the central logger, errors are never swallowed silently;
the full question content is NOT logged when unnecessary (only the length / decisions).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.orm import Session

from app.db.models import (
    CauHinhTruyXuat,
    KhongGianTaiLieu,
    MauPrompt,
    NhanXacMinh,
    TaiLieu,
    TomTatTaiLieu,
)
from app.errors import InternalError, ValidationError
from app.models.schemas import KetQuaTraLoi, TrichDan
from app.prompts.system_prompts import (
    INVARIANT_SAFETY_CONSTRAINTS,
    SYNTHESIS_SYSTEM_PROMPT,
    VERIFICATION_SYSTEM_PROMPT,
)
from app.providers.embedding_provider import EmbeddingProvider
from app.providers.llm_provider import LLMProvider
from app.providers.registry import get_embedding_provider
from app.storage.vector_store import META_TAI_LIEU_ID, SearchResult, VectorStore

logger = logging.getLogger(__name__)

# --- Question length constraints (R6.3) ------------------------------------
CAU_HOI_MIN = 1
CAU_HOI_MAX = 1000

_CAU_HOI_RONG = "Cau hoi khong duoc rong sau khi loai bo khoang trang."
_CAU_HOI_QUA_DAI = f"Cau hoi khong duoc dai qua {CAU_HOI_MAX} ky tu."

# --- Answer mode (R16) -----------------------------------------------------
CHE_DO_TONG_QUAN = "tong-quan"
CHE_DO_CHI_TIET = "chi-tiet"

# --- MauPrompt roles (R20.1/20.2/20.3) -------------------------------------
# Match `MauPrompt.vaiTro` (synthesis | verify | normalize). When a per-role MauPrompt
# exists in the db, its noiDung is used as the BASE prompt; INVARIANT_SAFETY_CONSTRAINTS
# is ALWAYS appended (no MauPrompt can override it). The overview path reuses the
# synthesis role as the base.
VAI_TRO_SYNTHESIS = "synthesis"
VAI_TRO_VERIFY = "verify"

# Message when the workspace has no documents yet (empty TomTatTaiLieu) — R16.3/16.5.
# Returned WITHOUT calling the LLM.
KHONG_CO_TAI_LIEU = (
    "Khong gian nay chua co tai lieu nao. Hay tai len tai lieu de bat dau hoi dap."
)

# Cap on the number of suggestions generated so the list does not get too long (deterministic).
MAX_SUGGESTIONS = 8

# --- Overview intent classification keywords (R16.6) -----------------------
# Module constant -> configurable / extensible. The phrases are DIACRITIC-FREE + lowercase
# (matched after `_removeDiacritics(cauHoi).lower()`), so classification works whether the
# user types with or without diacritics.
OVERVIEW_KEYWORDS: tuple[str, ...] = (
    "tong quan",
    "tom tat",
    "khai quat",
    "tong the",
    "noi dung chung",
    "noi dung tong the",
    "gom nhung muc nao",
    "gom nhung gi",
    "co nhung gi",
    "co nhung muc nao",
    "nhung muc nao",
    "muc luc",
    "liet ke",
    "co the hoi gi",
    "hoi duoc gi",
    "gioi thieu chung",
)

# Brief instruction for the normalize role (R6.7). The per-role MauPrompt + INVARIANT
# SAFETY CONSTRAINTS are applied in task 10.15; here a minimal instruction is used.
_NORMALIZE_SYSTEM_PROMPT = (
    "Ban la cong cu them dau tieng Viet. Them dau cho cau sau, GIU NGUYEN tung tu "
    "(khong them, khong bot, khong doi tu), chi bo sung dau. Chi tra ve cau da them dau."
)

# Alphanumeric token pattern used to compare word sets (after diacritic removal to ASCII a-z0-9).
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

# Inline citation marker pattern [n] in the synthesized answer (R7.4/7.5).
_MARKER_PATTERN = re.compile(r"\[(\d+)\]")

# Error message when the synthesis provider is missing (the caller catches it for fallback).
_THIEU_SYNTHESIS_PROVIDER = "Khong co LLM_Provider tong hop de tong hop cau tra loi."


def _removeDiacritics(text: str) -> str:
    """Remove Vietnamese diacritics from `text` (NFD + drop combining marks + 'd'/'D' for 'đ'/'Đ').

    Used to: (1) detect whether a sentence has diacritics, (2) compare word sets in the
    normalize guard, (3) match intent-classification keywords independently of diacritics.
    """
    # 'đ'/'Đ' are not decomposed by NFD -> replace them first.
    text = text.replace("\u0111", "d").replace("\u0110", "D")
    nfd = unicodedata.normalize("NFD", text)
    khong_dau = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    return unicodedata.normalize("NFC", khong_dau)


def _hasDiacritics(text: str) -> bool:
    """True if `text` contains Vietnamese diacritics (removing them changes the string)."""
    return _removeDiacritics(text) != text


def _wordSet(text: str) -> frozenset[str]:
    """Word set of `text` after diacritic removal + lowercasing + alphanumeric tokenization.

    Used as the normalize guard: accept the LLM result only if it is the SAME word set
    after diacritic removal."""
    return frozenset(_TOKEN_PATTERN.findall(_removeDiacritics(text).lower()))


# --- Retrieval result after threshold gating (R6.5/6.6) --------------------
class TrangThaiTruyXuat(str, Enum):
    """Gating state after retrieval (comparing the top score against the configured thresholds)."""

    KHONG_TIM_THAY = "KHONG_TIM_THAY"        # top score < nguongKhongTimThay
    CHUA_DU_LIEN_QUAN = "CHUA_DU_LIEN_QUAN"  # nguongKhongTimThay <= top < nguongDuLienQuan
    DU_LIEN_QUAN = "DU_LIEN_QUAN"            # top >= nguongDuLienQuan


@dataclass
class KetQuaTruyXuat:
    """Result of the retrieve step: gating state + list of chunks (if relevant enough).

    Only the DU_LIEN_QUAN state carries chunks for synthesis (task 10.10); the other two
    states (KHONG_TIM_THAY / CHUA_DU_LIEN_QUAN) have an empty `chunks` and do NOT call the
    LLM for synthesis (R6.5/6.6).
    """

    trangThai: TrangThaiTruyXuat
    chunks: list[SearchResult] = field(default_factory=list)


class QueryPipeline:
    """Query processing pipeline operating on a single SQLAlchemy `Session`.

    The LLM_Providers (normalize / verify / synthesize) are INJECTED into the constructor;
    default `None` to be resolved from the registry in later tasks (tests inject fakes).
    The normalize role reuses the verify provider (R13.4): when `normalizeProvider` is
    None, `normalizeQuestion` uses `verifyProvider`.
    """

    def __init__(
        self,
        db: Session | None = None,
        normalizeProvider: LLMProvider | None = None,
        verifyProvider: LLMProvider | None = None,
        synthesisProvider: LLMProvider | None = None,
        embeddingProvider: EmbeddingProvider | None = None,
        vectorStore: VectorStore | None = None,
    ) -> None:
        self.db = db
        self.normalizeProvider = normalizeProvider
        self.verifyProvider = verifyProvider
        self.synthesisProvider = synthesisProvider
        # Override Embedding_Provider for tests (inject a fake). When None -> resolve per
        # workspace via the registry (`khongGian.embeddingProvider`) in retrieve (R21.1).
        self.embeddingProvider = embeddingProvider
        # VectorStore() is import-safe (chromadb is loaded lazily only when used).
        self.vectorStore = vectorStore or VectorStore()

    # --- Validate question (R6.3) -----------------------------------------
    def validateQuestion(self, cauHoi: str) -> str:
        """Strip whitespace + check length 1..1000; return the trimmed question.

        Empty after trimming or longer than 1000 characters -> `ValidationError` (R6.3).
        """
        daCat = cauHoi.strip()
        if len(daCat) < CAU_HOI_MIN:
            logger.info("Tu choi truy van: cau hoi rong sau khi cat khoang trang")
            raise ValidationError(_CAU_HOI_RONG)
        if len(daCat) > CAU_HOI_MAX:
            logger.info(
                "Tu choi truy van: cau hoi dai %d ky tu (> %d)", len(daCat), CAU_HOI_MAX
            )
            raise ValidationError(_CAU_HOI_QUA_DAI)
        return daCat

    # --- Intent classification (R16.6) ------------------------------------
    def classifyIntent(self, cauHoi: str) -> str:
        """DETERMINISTIC classification "tong-quan" vs "chi-tiet" by configurable keywords.

        Matches independently of diacritics: remove diacritics + lowercase, then search
        for a phrase in `OVERVIEW_KEYWORDS`. Any phrase present -> "tong-quan"; otherwise
        -> "chi-tiet". Same input -> same result (R16.6).
        """
        chuanHoa = _removeDiacritics(cauHoi).lower()
        for tuKhoa in OVERVIEW_KEYWORDS:
            if tuKhoa in chuanHoa:
                logger.debug("classifyIntent -> tong-quan (khop '%s')", tuKhoa)
                return CHE_DO_TONG_QUAN
        logger.debug("classifyIntent -> chi-tiet")
        return CHE_DO_CHI_TIET

    # --- Force answer mode (R16.7, R16.8) ---------------------------------
    def resolveMode(self, cauHoi: str, cheDo: str | None = None) -> str:
        """Resolve the final answer mode.

        `cheDo` is "tong-quan"/"chi-tiet" -> force it, overriding automatic classification
        (R16.7/16.8); `cheDo` is None -> use `classifyIntent` (R16.6).
        """
        if cheDo in (CHE_DO_TONG_QUAN, CHE_DO_CHI_TIET):
            logger.debug("resolveMode -> ep che do '%s'", cheDo)
            return cheDo
        if cheDo is not None:
            # A forced value outside the valid set -> treat as no force (DTO already blocks; defensive).
            logger.warning("resolveMode: che do ep '%s' khong hop le, dung phan loai", cheDo)
        return self.classifyIntent(cauHoi)

    # --- Normalize a diacritic-less question (R6.7) -----------------------
    def normalizeQuestion(self, cauHoi: str) -> str:
        """Add diacritics to a diacritic-less question, guarding the SAME word set (R6.7).

        - A question that already has diacritics -> kept as-is (no LLM call).
        - A diacritic-less question -> call the LLM_Provider to normalize (normalize role;
          if empty -> use verify, R13.4). Accept the result ONLY if `_wordSet(ketQua) ==
          _wordSet(cauHoi)` (only adds diacritics, does not change/add/remove words);
          otherwise keep the original.
        - No provider or a provider error -> keep the original (safe fallback, logged).
        """
        if _hasDiacritics(cauHoi):
            logger.debug("normalizeQuestion: cau da co dau, giu nguyen")
            return cauHoi

        provider = self.normalizeProvider or self.verifyProvider
        if provider is None:
            logger.warning(
                "normalizeQuestion: khong co provider chuan hoa/xac minh, giu cau goc"
            )
            return cauHoi

        try:
            ketQua = provider.generate(_NORMALIZE_SYSTEM_PROMPT, cauHoi)
        except Exception:
            # Do not swallow the error silently: log with context then fall back to the original.
            logger.exception(
                "normalizeQuestion: loi goi provider chuan hoa, giu cau goc"
            )
            return cauHoi

        daCat = (ketQua or "").strip()
        if daCat and _wordSet(daCat) == _wordSet(cauHoi):
            logger.debug("normalizeQuestion: nhan ket qua chuan hoa (cung bo tu)")
            return daCat

        logger.info(
            "normalizeQuestion: ket qua chuan hoa lam doi bo tu, giu cau goc"
        )
        return cauHoi

    # --- Retrieve hybrid RRF + threshold gating (R3.4/6.1/6.4/6.5/6.6) ----
    def retrieve(
        self,
        khongGian: KhongGianTaiLieu,
        cauHoi: str,
        cfg: CauHinhTruyXuat,
    ) -> KetQuaTruyXuat:
        """Embed the question -> hybrid search (LIMITED to the workspace collection) -> gating.

        Sequence:
        1. Embed `cauHoi` with the WORKSPACE's Embedding_Provider (a fake is injected in
           tests; otherwise resolved via the registry by `khongGian.embeddingProvider`).
        2. Hybrid RRF search over the EXACT `khongGian.collectionName` (isolation R3.4/6.1),
           with `k` + vector/BM25 weights taken from `cfg` (R6.4).
        3. Gate by the top score (R6.5/6.6):
           - top < `nguongKhongTimThay`  -> KHONG_TIM_THAY  (do NOT call the LLM for synthesis)
           - top < `nguongDuLienQuan`    -> CHUA_DU_LIEN_QUAN (do NOT call the LLM for synthesis)
           - otherwise                    -> DU_LIEN_QUAN with the list of chunks.

        Does NOT call the LLM for synthesis here (synthesis is task 10.10) — only returns
        the gated result.
        """
        provider = self._resolveEmbeddingProvider(khongGian)
        queryVector = provider.embed([cauHoi])[0]
        chunks = self.vectorStore.search(
            khongGian.collectionName,
            queryVector,
            cauHoi,
            cfg.k,
            trongSoVector=cfg.trongSoVector,
            trongSoBm25=cfg.trongSoBm25,
        )

        diemTop = chunks[0].score if chunks else 0.0
        if diemTop < cfg.nguongKhongTimThay:
            logger.info(
                "retrieve khong gian id=%s: diem top %.4f < nguong khong tim thay %.4f -> KHONG_TIM_THAY",
                khongGian.id,
                diemTop,
                cfg.nguongKhongTimThay,
            )
            return KetQuaTruyXuat(TrangThaiTruyXuat.KHONG_TIM_THAY, [])
        if diemTop < cfg.nguongDuLienQuan:
            logger.info(
                "retrieve khong gian id=%s: diem top %.4f < nguong du lien quan %.4f -> CHUA_DU_LIEN_QUAN",
                khongGian.id,
                diemTop,
                cfg.nguongDuLienQuan,
            )
            return KetQuaTruyXuat(TrangThaiTruyXuat.CHUA_DU_LIEN_QUAN, [])

        logger.info(
            "retrieve khong gian id=%s: diem top %.4f >= nguong du lien quan -> DU_LIEN_QUAN (%d chunk)",
            khongGian.id,
            diemTop,
            len(chunks),
        )
        return KetQuaTruyXuat(TrangThaiTruyXuat.DU_LIEN_QUAN, chunks)

    def _resolveEmbeddingProvider(self, khongGian: KhongGianTaiLieu) -> EmbeddingProvider:
        """Return the workspace's Embedding_Provider.

        Prefers the injected one (test). Otherwise resolves the class via the registry
        by `khongGian.embeddingProvider` and instantiates it (R21.1).
        """
        if self.embeddingProvider is not None:
            return self.embeddingProvider
        providerCls = get_embedding_provider(khongGian.embeddingProvider)
        return providerCls()

    # --- Strict-grounding synthesis + [n] citations (R7) ------------------
    def synthesize(
        self,
        cauHoi: str,
        chunks: list[SearchResult],
        mauPrompt: str | None = None,
    ) -> tuple[str, list[TrichDan]]:
        """Synthesize an answer with `[n]` markers + the corresponding TrichDan list (R7).

        Sequence:
        1. Build the numbered context [1..N] from `chunks` (in the exact order passed in).
        2. Call the synthesis LLM_Provider with the system prompt = (MauPrompt override or
           `SYNTHESIS_SYSTEM_PROMPT`) + `INVARIANT_SAFETY_CONSTRAINTS` (invariant, cannot
           be overridden - R20.3), instructing it to insert `[n]` markers inline.
        3. Parse the `[n]` markers in the answer: keep only markers in the range 1..N
           (ignore out-of-range markers - R7.4); for each valid marker USED in the answer,
           create a TrichDan mapping marker n -> the n-th chunk (chunkId/taiLieuId/noiDung).
           Ensure a bijection between the markers that actually appear and the TrichDan
           list (R7.5).

        Provider errors/timeouts are RAISED (not swallowed) so the caller (`answerDetail`)
        can decide on a fallback (R7.6/R8.4). A missing synthesis provider -> `InternalError`.
        """
        provider = self.synthesisProvider
        if provider is None:
            logger.error("synthesize: thieu LLM_Provider tong hop")
            raise InternalError(_THIEU_SYNTHESIS_PROVIDER)

        soChunk = len(chunks)
        systemPrompt = self._buildSynthesisSystemPrompt(mauPrompt)
        userPrompt = self._buildUserPrompt(cauHoi, chunks)

        # Provider errors (network/timeout/quota) MUST propagate for fallback; just log + raise.
        try:
            traLoi = provider.generate(systemPrompt, userPrompt)
        except Exception:
            logger.exception("synthesize: loi goi LLM_Provider tong hop")
            raise

        traLoi = (traLoi or "").strip()
        trichDan = self._buildTrichDan(traLoi, chunks)
        logger.info(
            "synthesize: %d chunk -> cau tra loi (%d ky tu), %d trich dan (marker 1..%d)",
            soChunk,
            len(traLoi),
            len(trichDan),
            soChunk,
        )
        return traLoi, trichDan

    def _buildSynthesisSystemPrompt(self, mauPrompt: str | None) -> str:
        """Compose the synthesis system prompt: (override | MauPrompt db | default) + INVARIANT.

        A hand-passed `mauPrompt` is preferred; if None, look up the synthesis-role
        MauPrompt from the db (if any). INVARIANT_SAFETY_CONSTRAINTS is ALWAYS appended
        (invariant, cannot be overridden - R20.1/20.2/20.3).
        """
        return self._composeSystemPrompt(VAI_TRO_SYNTHESIS, mauPrompt, SYNTHESIS_SYSTEM_PROMPT)

    def _composeSystemPrompt(
        self, vaiTro: str, override: str | None, default: str
    ) -> str:
        """Build the system prompt for `vaiTro`: base + invariant INVARIANT_SAFETY_CONSTRAINTS.

        Base selection order: a hand-passed `override` -> the per-role MauPrompt from the
        db -> `default`. Wherever the base comes from, INVARIANT_SAFETY_CONSTRAINTS is
        ALWAYS appended at the end (R20.1/20.2/20.3 — a MauPrompt cannot override the
        safety constraint).
        """
        base = (override or "").strip() or self._resolveBasePrompt(vaiTro) or default
        return f"{base}\n\n{INVARIANT_SAFETY_CONSTRAINTS}"

    def _resolveBasePrompt(self, vaiTro: str) -> str | None:
        """Return `MauPrompt.noiDung` for `vaiTro` from the db (if any), otherwise None.

        No db / no record / empty noiDung -> None (so the default is used). A query error
        is logged (not swallowed silently) then returns None as a fallback.
        """
        if self.db is None:
            return None
        try:
            mau = self.db.get(MauPrompt, vaiTro)
        except Exception:
            logger.exception(
                "_resolveBasePrompt: loi truy van MauPrompt vai tro '%s'", vaiTro
            )
            return None
        if mau is None:
            return None
        return (mau.noiDung or "").strip() or None

    @staticmethod
    def _buildUserPrompt(cauHoi: str, chunks: list[SearchResult]) -> str:
        """Compose the user prompt: question + the numbered excerpts [1..N]."""
        cacDoan = "\n".join(
            f"[{i}] {chunk.document}" for i, chunk in enumerate(chunks, start=1)
        )
        return f"Cau hoi: {cauHoi}\n\nCac doan trich (danh so):\n{cacDoan}"

    @staticmethod
    def _buildTrichDan(traLoi: str, chunks: list[SearchResult]) -> list[TrichDan]:
        """Create TrichDan for the valid markers `[n]` (1..N) that ACTUALLY appear in `traLoi`.

        Ignore out-of-range markers 1..N (R7.4). Each valid marker -> one TrichDan mapping
        n -> the n-th chunk (bijection - R7.5). Sorted by marker ascending.
        """
        soChunk = len(chunks)
        markerDung: set[int] = set()
        for raw in _MARKER_PATTERN.findall(traLoi):
            n = int(raw)
            if 1 <= n <= soChunk:
                markerDung.add(n)
            else:
                logger.debug("_buildTrichDan: bo qua marker ngoai khoang [%d] (N=%d)", n, soChunk)
        return [QueryPipeline._trichDanTuChunk(n, chunks[n - 1]) for n in sorted(markerDung)]

    @staticmethod
    def _trichDanTuChunk(marker: int, chunk: SearchResult) -> TrichDan:
        """Create one TrichDan from the `marker`-th chunk (chunkId/taiLieuId/noiDung)."""
        return TrichDan(
            marker=marker,
            chunkId=chunk.id,
            taiLieuId=str(chunk.metadata.get(META_TAI_LIEU_ID, "")),
            noiDung=chunk.document,
        )

    # --- Asynchronous cross-verification (R8.1-8.3) -----------------------
    def verifyAnswer(self, traLoi: str, chunks: list[SearchResult]) -> NhanXacMinh:
        """Cross-verify the answer -> one of three `NhanXacMinh` (R8.1).

        This is a SEPARATE step, run AFTER the answer is ready ("asynchronous" in the
        sense: does not block/break the answer). Safe degradation: ANY error/timeout/
        exception from the verify provider -> `CHUA_XAC_MINH` (R8.2/8.3) — never raised.
        A missing verify provider also -> `CHUA_XAC_MINH`.

        Maps the LLM result (text) to a label by keyword matching (diacritic-free):
        contains "mau thuan" -> CO_MAU_THUAN; contains "chua xac minh"/"khong" ->
        CHUA_XAC_MINH; contains "da xac minh"/"xac minh" -> DA_XAC_MINH; otherwise ->
        CHUA_XAC_MINH.
        """
        provider = self.verifyProvider
        if provider is None:
            logger.warning("verifyAnswer: khong co provider xac minh, suy bien CHUA_XAC_MINH")
            return NhanXacMinh.CHUA_XAC_MINH

        systemPrompt = self._composeSystemPrompt(
            VAI_TRO_VERIFY, None, VERIFICATION_SYSTEM_PROMPT
        )
        userPrompt = self._buildUserPrompt(traLoi, chunks)
        try:
            ketQua = provider.generate(systemPrompt, userPrompt)
        except Exception:
            # Do not swallow the error silently: log with context then degrade safely.
            logger.exception("verifyAnswer: loi goi provider xac minh, suy bien CHUA_XAC_MINH")
            return NhanXacMinh.CHUA_XAC_MINH

        nhan = self._mapNhanXacMinh(ketQua)
        logger.info("verifyAnswer: nhan xac minh = %s", nhan.value)
        return nhan

    @staticmethod
    def _mapNhanXacMinh(ketQua: str | None) -> NhanXacMinh:
        """Map the verify LLM text to a `NhanXacMinh` (default CHUA_XAC_MINH)."""
        chuanHoa = _removeDiacritics(ketQua or "").lower()
        if "mau thuan" in chuanHoa:
            return NhanXacMinh.CO_MAU_THUAN
        if "chua xac minh" in chuanHoa or "khong xac minh" in chuanHoa:
            return NhanXacMinh.CHUA_XAC_MINH
        if "da xac minh" in chuanHoa:
            return NhanXacMinh.DA_XAC_MINH
        return NhanXacMinh.CHUA_XAC_MINH

    # --- Assemble KetQuaTraLoi for the detail path (R7.6/R8.4) ------------
    def answerDetail(
        self,
        cauHoi: str,
        chunks: list[SearchResult],
        mauPrompt: str | None = None,
    ) -> KetQuaTraLoi:
        """Assemble `KetQuaTraLoi` for the detail path: synthesize -> verify / fallback.

        - Synthesis succeeds: attach the label from `verifyAnswer` (run afterwards, safe
          degradation), `laFallback=False`.
        - Synthesis fails/times out (provider raises, or provider missing): FALLBACK
          (R7.6/R8.4) — return an answer assembled from the original chunks (`chunksGoc`),
          TrichDan derived from all chunks, `nhanXacMinh=CHUA_XAC_MINH`, `laFallback=True`.
        """
        try:
            traLoi, trichDan = self.synthesize(cauHoi, chunks, mauPrompt)
        except Exception:
            # Detail already logged in synthesize; here log the fallback decision.
            logger.warning("answerDetail: tong hop that bai -> fallback tra chunk goc")
            return self._fallbackKetQua(chunks)

        nhanXacMinh = self.verifyAnswer(traLoi, chunks)
        return KetQuaTraLoi(
            traLoi=traLoi,
            trichDan=trichDan,
            nhanXacMinh=nhanXacMinh,
            laFallback=False,
            laTongQuan=False,
        )

    @staticmethod
    def _fallbackKetQua(chunks: list[SearchResult]) -> KetQuaTraLoi:
        """Build a fallback `KetQuaTraLoi` from the original chunks (R7.6/R8.4).

        The answer is assembled from the numbered original chunks [1..N]; each chunk
        produces a TrichDan; the label is `CHUA_XAC_MINH`; `laFallback=True`.
        """
        traLoi = "\n\n".join(
            f"[{i}] {chunk.document}" for i, chunk in enumerate(chunks, start=1)
        )
        trichDan = [
            QueryPipeline._trichDanTuChunk(i, chunk)
            for i, chunk in enumerate(chunks, start=1)
        ]
        return KetQuaTraLoi(
            traLoi=traLoi,
            trichDan=trichDan,
            nhanXacMinh=NhanXacMinh.CHUA_XAC_MINH,
            laFallback=True,
            laTongQuan=False,
        )

    # --- Overview answer from TomTatTaiLieu + outline (R16.1/16.2/16.3) ----
    def answerOverview(
        self,
        khongGian: KhongGianTaiLieu,
        taiKhoan,
        cauHoi: str,
    ) -> KetQuaTraLoi:
        """Answer an overview based on the TomTatTaiLieu + outline of the workspace's TaiLieu.

        - No documents (no TomTatTaiLieu) -> return the "no documents yet" message,
          `laTongQuan=True`, WITHOUT calling the LLM (R16.3).
        - Has documents -> use the summaries/outlines as numbered context [1..N], call the
          synthesis LLM_Provider (synthesis-role MauPrompt from db as base + invariant
          INVARIANT) via `synthesize`; parse the `[n]` markers -> TrichDan pointing at the
          summary sources (R16.1). Ensure >=1 TrichDan (R16.2): if the LLM produces no
          marker, attach the first source's citation. `laTongQuan=True`.
        - Synthesis fails/times out / provider missing -> fallback: return the summary
          sources with TrichDan, `laFallback=True`, `laTongQuan=True` (R16.4).

        Access is at the workspace level (resolveAccess) — every TaiLieu in the workspace
        is within query scope, so every TomTatTaiLieu in the workspace is valid.
        """
        nguon = self._buildOverviewSources(khongGian)
        if not nguon:
            logger.info(
                "answerOverview khong gian id=%s: chua co tai lieu, khong goi LLM",
                khongGian.id,
            )
            return KetQuaTraLoi(
                traLoi=KHONG_CO_TAI_LIEU,
                trichDan=[],
                nhanXacMinh=NhanXacMinh.CHUA_XAC_MINH,
                laFallback=False,
                laTongQuan=True,
            )

        try:
            traLoi, trichDan = self.synthesize(cauHoi, nguon)
        except Exception:
            logger.warning(
                "answerOverview khong gian id=%s: tong hop tong quan that bai -> fallback nguon tom tat",
                khongGian.id,
            )
            return self._fallbackOverview(nguon)

        if not trichDan:
            # Has sources but the LLM produced no marker -> ensure >=1 citation (R16.2).
            logger.info(
                "answerOverview: LLM khong tao marker, dinh kem trich dan nguon dau (R16.2)"
            )
            trichDan = [self._trichDanTuChunk(1, nguon[0])]

        nhanXacMinh = self.verifyAnswer(traLoi, nguon)
        logger.info(
            "answerOverview khong gian id=%s: tong quan tu %d nguon, %d trich dan",
            khongGian.id,
            len(nguon),
            len(trichDan),
        )
        return KetQuaTraLoi(
            traLoi=traLoi,
            trichDan=trichDan,
            nhanXacMinh=nhanXacMinh,
            laFallback=False,
            laTongQuan=True,
        )

    # --- Question suggestions from real documents (R15.4/15.5/16.5) -------
    def buildSuggestions(self, khongGian: KhongGianTaiLieu, taiKhoan) -> list[str]:
        """Generate question suggestions from the outline (headings) of real TaiLieu in the workspace.

        - No documents (no TomTatTaiLieu) -> EMPTY list (only reports emptiness), WITHOUT
          calling the LLM (R16.5). This method is fully deterministic, not LLM-dependent —
          suggestions are extracted from real document outline titles, not fabricated
          (R15.4/15.5).
        - Has documents -> use each document's outline titles as suggestions; a document
          with no outline -> use its file name as a suggestion. Deduplicate, preserve
          order, cap at `MAX_SUGGESTIONS`.
        """
        rows = self._loadSummaries(khongGian)
        if not rows:
            logger.info(
                "buildSuggestions khong gian id=%s: chua co tai lieu, tra danh sach rong",
                khongGian.id,
            )
            return []

        goiY: list[str] = []
        for taiLieu, tomTat in rows:
            tieuDe = self._outlineTitles(tomTat.outline)
            if tieuDe:
                goiY.extend(tieuDe)
            else:
                # Document with no outline -> use the file name as a suggestion (still a real document).
                goiY.append(taiLieu.tenFile)

        # Deduplicate preserving order + cap the count (deterministic).
        daThay: set[str] = set()
        ketQua: list[str] = []
        for g in goiY:
            g = (g or "").strip()
            if g and g not in daThay:
                daThay.add(g)
                ketQua.append(g)
            if len(ketQua) >= MAX_SUGGESTIONS:
                break
        logger.info(
            "buildSuggestions khong gian id=%s: %d goi y tu %d tai lieu",
            khongGian.id,
            len(ketQua),
            len(rows),
        )
        return ketQua

    # --- Summary/outline utilities for the overview path -------------------
    def _loadSummaries(
        self, khongGian: KhongGianTaiLieu
    ) -> list[tuple[TaiLieu, TomTatTaiLieu]]:
        """Load (TaiLieu, TomTatTaiLieu) for every document that has a summary in the workspace.

        Deterministic order by `createdAt` then `id`. No db -> empty.
        """
        if self.db is None:
            return []
        return (
            self.db.query(TaiLieu, TomTatTaiLieu)
            .join(TomTatTaiLieu, TomTatTaiLieu.taiLieuId == TaiLieu.id)
            .filter(TaiLieu.khongGianId == khongGian.id)
            .order_by(TaiLieu.createdAt, TaiLieu.id)
            .all()
        )

    def _buildOverviewSources(self, khongGian: KhongGianTaiLieu) -> list[SearchResult]:
        """Build the overview source list (SearchResult) from each document's summary + outline.

        Each document -> one source: `id=f"tomtat-{taiLieuId}"`, `document` joins the file
        name + summary + outline titles, `metadata[taiLieuId]`. Reuses the synthesis path's
        `_buildUserPrompt`/`_buildTrichDan` (no duplicated numbering/marker logic).
        """
        nguon: list[SearchResult] = []
        for taiLieu, tomTat in self._loadSummaries(khongGian):
            nguon.append(
                SearchResult(
                    id=f"tomtat-{taiLieu.id}",
                    document=self._composeSummaryText(taiLieu, tomTat),
                    metadata={META_TAI_LIEU_ID: taiLieu.id},
                    score=1.0,
                )
            )
        return nguon

    @staticmethod
    def _composeSummaryText(taiLieu: TaiLieu, tomTat: TomTatTaiLieu) -> str:
        """Join the overview source text: file name + summary + table of contents (if any)."""
        phan = [f"{taiLieu.tenFile}: {tomTat.tomTat}"]
        tieuDe = QueryPipeline._outlineTitles(tomTat.outline)
        if tieuDe:
            phan.append("Muc luc: " + "; ".join(tieuDe))
        return "\n".join(phan)

    @staticmethod
    def _outlineTitles(outline) -> list[str]:
        """Extract the list of titles from an outline (JSON list[{tieuDe, viTri}] or list[str]).

        Skip entries with no title; preserve the original order. Field-name tolerant —
        accepts the keys `tieuDe`/`tieu_de`/`title` for robustness.
        """
        titles: list[str] = []
        for muc in outline or []:
            if isinstance(muc, dict):
                t = muc.get("tieuDe") or muc.get("tieu_de") or muc.get("title")
                if t:
                    titles.append(str(t))
            elif isinstance(muc, str) and muc.strip():
                titles.append(muc.strip())
        return titles

    @staticmethod
    def _fallbackOverview(nguon: list[SearchResult]) -> KetQuaTraLoi:
        """Overview fallback: join the summary sources [1..N] + TrichDan (R16.4).

        Used when synthesis fails/times out / the provider is missing — still ensures >=1
        TrichDan and `laTongQuan=True`.
        """
        traLoi = "\n\n".join(
            f"[{i}] {s.document}" for i, s in enumerate(nguon, start=1)
        )
        trichDan = [
            QueryPipeline._trichDanTuChunk(i, s) for i, s in enumerate(nguon, start=1)
        ]
        return KetQuaTraLoi(
            traLoi=traLoi,
            trichDan=trichDan,
            nhanXacMinh=NhanXacMinh.CHUA_XAC_MINH,
            laFallback=True,
            laTongQuan=True,
        )
