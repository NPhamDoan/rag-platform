"""Property test for HistoryService.markStaleCitations (task 11.4, R9.8).

# Feature: multi-user-rag-platform, Property 38: Cat lai tai lieu danh dau TrichDan
# cu la khong con kha dung.
#
# Meaning: for ONE re-indexed TaiLieu, EVERY LichSuTroChuyen with a TrichDan pointing to
# an OLD Chunk of that TaiLieu is marked "source no longer available"
# (`nguonConKhaDung = False`), while history turns pointing to OTHER TaiLieu (or with no
# citation) keep `nguonConKhaDung = True`. markStaleCitations does NOT modify a TrichDan's
# `chunkId`/`taiLieuId` (it does not point to the wrong Chunk) — staleness is expressed via
# the `nguonConKhaDung` flag, and the TrichDan record is left intact.
# Validates: Requirements 9.8

Each example uses ITS OWN in-memory SQLite (mirroring the other property tests). Accounts are
created directly with a fake matKhauHash (no bcrypt), so it is fast → max_examples=100.
"""

from __future__ import annotations

from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import (
    Chunk,
    KhongGianTaiLieu,
    LichSuTroChuyen,
    NhanXacMinh,
    TaiKhoan,
    TaiLieu,
    TrangThaiTaiLieu,
    TrichDan,
)
from app.models.schemas import KetQuaTraLoi
from app.models.schemas import TrichDan as TrichDanDTO
from app.services.history_service import HistoryService


@contextmanager
def _fresh_session():
    """Fresh in-memory SQLite session (schema from Base.metadata), cleaned up after each round."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _tao_tai_khoan(db) -> TaiKhoan:
    tk = TaiKhoan(email="u@x.com", tenDangNhap="u", matKhauHash="h")
    db.add(tk)
    db.commit()
    return tk


def _tao_khong_gian(db, chuSoHuu: TaiKhoan) -> KhongGianTaiLieu:
    kg = KhongGianTaiLieu(
        ten="KG",
        chuSoHuuId=chuSoHuu.id,
        embeddingProvider="e5",
        collectionName="ws_x",
    )
    db.add(kg)
    db.commit()
    return kg


def _tao_tai_lieu_chunk(db, khongGian: KhongGianTaiLieu, thuTu: int):
    """Create 1 DA_EMBED TaiLieu + 1 Chunk for TrichDan to reference."""
    taiLieu = TaiLieu(
        khongGianId=khongGian.id,
        tenFile=f"tl_{thuTu}.pdf",
        dinhDang="pdf",
        kichThuoc=10,
        trangThai=TrangThaiTaiLieu.DA_EMBED,
        chienLuocChunk="auto",
        soChunk=1,
    )
    db.add(taiLieu)
    db.flush()
    chunk = Chunk(
        taiLieuId=taiLieu.id,
        thuTu=0,
        viTriBatDau=0,
        viTriKetThuc=5,
        noiDung=f"noi dung {thuTu}",
    )
    db.add(chunk)
    db.commit()
    return taiLieu, chunk


def _ket_qua(chunk: Chunk | None, taiLieu: TaiLieu | None) -> KetQuaTraLoi:
    trichDan = []
    if chunk is not None and taiLieu is not None:
        trichDan = [
            TrichDanDTO(
                marker=1,
                chunkId=chunk.id,
                taiLieuId=taiLieu.id,
                noiDung=chunk.noiDung,
            )
        ]
    return KetQuaTraLoi(
        traLoi="cau tra loi [1]",
        trichDan=trichDan,
        nhanXacMinh=NhanXacMinh.CHUA_XAC_MINH,
    )


@settings(max_examples=100, deadline=None)
@given(
    soTaiLieu=st.integers(min_value=1, max_value=3),
    # Each element = one history turn; value -1 = no citation, otherwise = the document
    # index (taken modulo soTaiLieu) that the turn cites.
    luots=st.lists(st.integers(min_value=-1, max_value=2), min_size=0, max_size=6),
)
def test_cat_lai_danh_dau_trich_dan_cu_khong_con_kha_dung(soTaiLieu, luots):
    with _fresh_session() as db:
        chu = _tao_tai_khoan(db)
        kg = _tao_khong_gian(db, chu)
        service = HistoryService(db)

        # Create soTaiLieu documents + chunks; document[0] is the one to be re-indexed.
        taiLieuChunks = [_tao_tai_lieu_chunk(db, kg, i) for i in range(soTaiLieu)]

        # Create the history turns; remember which turn cites the target document (index 0).
        lichSuIds: list[str] = []
        mongDoiStale: list[bool] = []
        for v in luots:
            if v == -1:
                ls = service.saveTurn(chu, kg, "hoi", _ket_qua(None, None))
                mongDoiStale.append(False)  # no citation → stays available
            else:
                idx = v % soTaiLieu
                taiLieu, chunk = taiLieuChunks[idx]
                ls = service.saveTurn(chu, kg, "hoi", _ket_qua(chunk, taiLieu))
                mongDoiStale.append(idx == 0)  # only turns pointing to document[0] become stale
            assert ls is not None
            lichSuIds.append(ls.id)

        taiLieuMucTieu, chunkMucTieu = taiLieuChunks[0]

        # Save the TrichDan state of the target document before re-indexing.
        trichDanTruoc = {
            td.id: (td.chunkId, td.taiLieuId)
            for td in db.query(TrichDan)
            .filter(TrichDan.taiLieuId == taiLieuMucTieu.id)
            .all()
        }

        soMuc = service.markStaleCitations(taiLieuMucTieu.id)

        # The number of marked entries matches the number of expected stale turns.
        assert soMuc == sum(mongDoiStale)

        # Each turn: stale matches expectation (turns pointing to document[0] → False, others True).
        for lichSuId, mongDoi in zip(lichSuIds, mongDoiStale):
            ls = db.get(LichSuTroChuyen, lichSuId)
            db.refresh(ls)
            assert ls.nguonConKhaDung is (not mongDoi)

        # TrichDan pointing to the target document are NOT modified (do not point to the wrong Chunk).
        trichDanSau = {
            td.id: (td.chunkId, td.taiLieuId)
            for td in db.query(TrichDan)
            .filter(TrichDan.taiLieuId == taiLieuMucTieu.id)
            .all()
        }
        assert trichDanSau == trichDanTruoc
