"""Property-based test for saving chat history — round-trip + atomicity (Property 37).

# Feature: multi-user-rag-platform, Property 37: Luu lich su la round-trip; luu loi
# khong tao muc do
# Validates: Requirements 9.1, 9.2

Model: Hypothesis generates the content of one question-answer turn (cauHoi, traLoi,
verification label, list of citations) along with a `coLoi` flag that decides the test branch:

- coLoi=False (round-trip, R9.1): `saveTurn` saves successfully → the record is bound to the
  right taiKhoan + khongGian, has a timestamp, and `listHistory` READS BACK the correct
  question / answer / verification label; the saved TrichDan entries match (marker, noiDung,
  chunkId, taiLieuId) — total entry count increases by exactly 1.
- coLoi=True (atomicity, R9.2): commit raises → `saveTurn` returns None (does NOT raise; the
  error is surfaced to the caller), and the count of LichSuTroChuyen / TrichDan stays
  UNCHANGED — no partial entry.

Each example uses ITS OWN in-memory SQLite session (mirroring the other property tests).
Accounts are created directly with a fake matKhauHash (no bcrypt), so it is fast → max_examples=100.
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
    """Fresh in-memory SQLite session (schema from Base.metadata) — cleaned up after each round."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _seed_taikhoan_khonggian_chunk(db):
    """Create owner + space + 1 DA_EMBED TaiLieu + 1 Chunk for TrichDan to reference."""
    chu = TaiKhoan(email="u@x.com", tenDangNhap="u", matKhauHash="h")
    db.add(chu)
    db.commit()

    kg = KhongGianTaiLieu(
        ten="KG",
        chuSoHuuId=chu.id,
        embeddingProvider="e5",
        collectionName="ws_x",
    )
    db.add(kg)
    db.commit()

    taiLieu = TaiLieu(
        khongGianId=kg.id,
        tenFile="a.pdf",
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
        noiDung="noi dung chunk",
    )
    db.add(chunk)
    db.commit()
    return chu, kg, taiLieu, chunk


# One citation: marker >= 1 (schema constraint) + arbitrary noiDung; chunk/taiLieu attached later.
_trich_dan = st.fixed_dictionaries(
    {
        "marker": st.integers(min_value=1, max_value=50),
        "noiDung": st.text(min_size=0, max_size=80),
    }
)


@settings(max_examples=100, deadline=None)
@given(
    cauHoi=st.text(min_size=1, max_size=200),
    traLoi=st.text(min_size=0, max_size=200),
    nhanXacMinh=st.sampled_from(list(NhanXacMinh)),
    cacTrichDan=st.lists(_trich_dan, min_size=0, max_size=4),
    coLoi=st.booleans(),
)
def test_luu_lich_su_round_trip_va_loi_khong_tao_muc_do(
    cauHoi, traLoi, nhanXacMinh, cacTrichDan, coLoi
):
    with _fresh_session() as db:
        chu, kg, taiLieu, chunk = _seed_taikhoan_khonggian_chunk(db)
        service = HistoryService(db)

        ketQua = KetQuaTraLoi(
            traLoi=traLoi,
            trichDan=[
                TrichDanDTO(
                    marker=td["marker"],
                    chunkId=chunk.id,
                    taiLieuId=taiLieu.id,
                    noiDung=td["noiDung"],
                )
                for td in cacTrichDan
            ],
            nhanXacMinh=nhanXacMinh,
        )

        # Snapshot before saving: used to verify "no partial entry" on the error branch.
        soLichSuTruoc = db.query(LichSuTroChuyen).count()
        soTrichDanTruoc = db.query(TrichDan).count()

        if coLoi:
            # R9.2: commit raises → saveTurn returns None, no partial entry created.
            commitGoc = db.commit

            def _commit_loi():
                raise RuntimeError("loi DB gia lap")

            db.commit = _commit_loi
            try:
                ketQuaLuu = service.saveTurn(chu, kg, cauHoi, ketQua)
            finally:
                db.commit = commitGoc

            # The error is surfaced to the caller (returns None), does NOT raise.
            assert ketQuaLuu is None
            # No partial entry: counts stay unchanged.
            assert db.query(LichSuTroChuyen).count() == soLichSuTruoc
            assert db.query(TrichDan).count() == soTrichDanTruoc
        else:
            # R9.1: save succeeds → round-trip reads back the correct content.
            lichSu = service.saveTurn(chu, kg, cauHoi, ketQua)

            assert lichSu is not None
            assert lichSu.taiKhoanId == chu.id
            assert lichSu.khongGianId == kg.id
            assert lichSu.createdAt is not None

            # Read back via listHistory: the just-saved record must appear with the right content.
            danhSach = service.listHistory(chu, kg)
            khopId = [ls for ls in danhSach if ls.id == lichSu.id]
            assert len(khopId) == 1
            docLai = khopId[0]
            assert docLai.cauHoi == cauHoi
            assert docLai.traLoi == traLoi
            assert docLai.nhanXacMinh == nhanXacMinh
            assert docLai.nguonConKhaDung is True

            # Saved TrichDan match as a set (marker, noiDung, chunkId, taiLieuId).
            trichDanLuu = (
                db.query(TrichDan).filter(TrichDan.lichSuId == lichSu.id).all()
            )
            assert sorted(
                (td.marker, td.noiDung, td.chunkId, td.taiLieuId) for td in trichDanLuu
            ) == sorted(
                (td.marker, td.noiDung, td.chunkId, td.taiLieuId)
                for td in ketQua.trichDan
            )

            # Total count increases by exactly 1 (LichSu) and by the right number of citations.
            assert db.query(LichSuTroChuyen).count() == soLichSuTruoc + 1
            assert db.query(TrichDan).count() == soTrichDanTruoc + len(ketQua.trichDan)
