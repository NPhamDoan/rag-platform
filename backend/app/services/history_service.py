"""HistoryService — LichSuTroChuyen per-user business logic (task 11.1).

Implements the signatures in design.md (services/history_service.py section):

    class HistoryService:
        def saveTurn(self, taiKhoan, khongGian, cauHoi, ketQua) -> LichSuTroChuyen | None: ...
        def listHistory(self, taiKhoan, khongGian, limit=50) -> list[LichSuTroChuyen]: ...
        def deleteTurn(self, taiKhoan, lichSuId) -> None: ...
        def markStaleCitations(self, taiLieuId) -> int: ...

Principles (R3.5, R3.7, R3.8, R9.1-9.8):

- `saveTurn` stores ONE question-answer pair (LichSuTroChuyen) along with its
  TrichDan in a single ATOMIC transaction (R9.1). IMPORTANT CONTRACT (R9.2): if the
  save fails it does NOT raise — it rolls back so NO partial entry is created, then
  returns `None` to signal failure. This lets the query flow (Query_Pipeline) still
  return an answer to the user with a "history not saved" warning. Errors are always
  logged (never swallowed silently).
- `listHistory` returns only `taiKhoan`'s OWN history in that workspace, ordered by
  `createdAt` DESCENDING (newest first), limited to at most `limit` (default 50) —
  strict isolation: another account's history is never returned (R3.5, R3.7, R9.3,
  R9.6, R9.7 on reads; the workspace permission layer is added by
  `require_workspace_access`).
- `deleteTurn` deletes only an entry BELONGING to `taiKhoan`; not found OR not the
  owner → `NotFoundError` (404). 404 (instead of 403) is chosen so as NOT to reveal
  the existence of another user's history entry (R9.7). ORM cascade deletes the
  entry's TrichDan too.
- `markStaleCitations` is called when a TaiLieu is re-chunked (re-indexed): it marks
  any LichSuTroChuyen whose TrichDan point at that TaiLieu as `nguonConKhaDung = False`
  (R9.8) — because after re-chunking, the old Chunk (referenced by the TrichDan) no
  longer exists. Returns the number of history entries marked.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import LichSuTroChuyen, TrichDan, TaiKhoan, KhongGianTaiLieu
from app.errors import NotFoundError
from app.models.schemas import KetQuaTraLoi

logger = logging.getLogger(__name__)

# Default maximum number of history entries returned per call (R9.3).
_GIOI_HAN_MAC_DINH = 50

# Error message when deleting an invalid entry (R9.7) — does not reveal existence.
_MUC_KHONG_HOP_LE = "Khong tim thay muc lich su tro chuyen hop le."


class HistoryService:
    """Chat history service operating on a single SQLAlchemy `Session`."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Save one question-answer turn -------------------------------------
    def saveTurn(
        self,
        taiKhoan: TaiKhoan,
        khongGian: KhongGianTaiLieu,
        cauHoi: str,
        ketQua: KetQuaTraLoi,
    ) -> LichSuTroChuyen | None:
        """Save a question-answer pair + TrichDan in a single transaction (R9.1, R9.2).

        Creates a `LichSuTroChuyen` (with taiKhoan + khongGian + default `_now`
        timestamp, `nguonConKhaDung=True`) along with the `TrichDan` from
        `ketQua.trichDan`. All of it is in one transaction: any error → rollback (NO
        entry created) and return `None` instead of raising, so the caller can still
        return an answer to the user with a "history not saved" warning (R9.2). On
        success → returns the saved record.
        """
        try:
            lichSu = LichSuTroChuyen(
                taiKhoanId=taiKhoan.id,
                khongGianId=khongGian.id,
                cauHoi=cauHoi,
                traLoi=ketQua.traLoi,
                nhanXacMinh=ketQua.nhanXacMinh,
            )
            self.db.add(lichSu)
            self.db.flush()  # generate id to set the foreign key for TrichDan.

            for td in ketQua.trichDan:
                self.db.add(
                    TrichDan(
                        lichSuId=lichSu.id,
                        marker=td.marker,
                        chunkId=td.chunkId,
                        taiLieuId=td.taiLieuId,
                        noiDung=td.noiDung,
                    )
                )

            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi luu lich su tro chuyen (taiKhoan=%s, khongGian=%s) — da rollback, "
                "khong tao muc do",
                taiKhoan.id,
                khongGian.id,
            )
            return None

        self.db.refresh(lichSu)
        logger.info(
            "Luu lich su tro chuyen thanh cong: id=%s, taiKhoan=%s, khongGian=%s, soTrichDan=%d",
            lichSu.id,
            taiKhoan.id,
            khongGian.id,
            len(ketQua.trichDan),
        )
        return lichSu

    # --- List one's own history --------------------------------------------
    def listHistory(
        self,
        taiKhoan: TaiKhoan,
        khongGian: KhongGianTaiLieu,
        limit: int = _GIOI_HAN_MAC_DINH,
    ) -> list[LichSuTroChuyen]:
        """List `taiKhoan`'s OWN history in `khongGian` (R3.5, R9.3, R9.6).

        Filters by BOTH `taiKhoanId` AND `khongGianId` → strict isolation: another
        account's history is never returned (R9.6, R9.7 on reads). Orders by `createdAt`
        DESCENDING (newest first) and limits to at most `limit` (default 50, R9.3). No
        entries yet → returns an empty list (R9.5).
        """
        ketQua = (
            self.db.query(LichSuTroChuyen)
            .filter(
                LichSuTroChuyen.taiKhoanId == taiKhoan.id,
                LichSuTroChuyen.khongGianId == khongGian.id,
            )
            .order_by(LichSuTroChuyen.createdAt.desc())
            .limit(limit)
            .all()
        )
        logger.info(
            "Liet ke lich su: taiKhoan=%s, khongGian=%s, gioiHan=%d, tra ve=%d muc",
            taiKhoan.id,
            khongGian.id,
            limit,
            len(ketQua),
        )
        return ketQua

    # --- Delete one's own entry --------------------------------------------
    def deleteTurn(self, taiKhoan: TaiKhoan, lichSuId: str) -> None:
        """Delete a history entry BELONGING to `taiKhoan` (R9.6, R9.7).

        Not found OR not belonging to `taiKhoan` → `NotFoundError` (404). 404 (instead
        of 403) is chosen so as NOT to reveal the existence of another user's history
        entry. ORM cascade deletes the entry's TrichDan too; other entries are kept
        (R9.6).
        """
        lichSu = self.db.get(LichSuTroChuyen, lichSuId)
        if lichSu is None or lichSu.taiKhoanId != taiKhoan.id:
            logger.info(
                "Tu choi xoa lich su: muc khong hop le hoac khong thuoc tai khoan "
                "(lichSuId=%s, taiKhoan=%s)",
                lichSuId,
                taiKhoan.id,
            )
            raise NotFoundError(_MUC_KHONG_HOP_LE)

        try:
            self.db.delete(lichSu)
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi xoa muc lich su id=%s — da rollback", lichSuId
            )
            raise

        logger.info(
            "Xoa muc lich su thanh cong: id=%s, taiKhoan=%s", lichSuId, taiKhoan.id
        )

    # --- Mark stale citations when a document is re-chunked ----------------
    def markStaleCitations(self, taiLieuId: str) -> int:
        """Mark the stale source of history entries pointing at `taiLieuId` (R9.8).

        When a TaiLieu is re-chunked (re-indexed), the old Chunks referenced by the
        TrichDan no longer exist. Find every `LichSuTroChuyen` that has at least one
        TrichDan pointing at `taiLieuId` and is currently `nguonConKhaDung=True`, and
        set it to `False` (no longer available) instead of leaving it pointing at the
        wrong Chunk. Returns the number of history entries marked.
        """
        lichSuCanDanhDau = (
            self.db.query(LichSuTroChuyen)
            .join(TrichDan, TrichDan.lichSuId == LichSuTroChuyen.id)
            .filter(
                TrichDan.taiLieuId == taiLieuId,
                LichSuTroChuyen.nguonConKhaDung.is_(True),
            )
            .distinct()
            .all()
        )

        for lichSu in lichSuCanDanhDau:
            lichSu.nguonConKhaDung = False

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi danh dau trich dan cu cho taiLieuId=%s — da rollback", taiLieuId
            )
            raise

        soMuc = len(lichSuCanDanhDau)
        logger.info(
            "Danh dau nguon cu (R9.8): taiLieuId=%s, so muc lich su bi danh dau=%d",
            taiLieuId,
            soMuc,
        )
        return soMuc
