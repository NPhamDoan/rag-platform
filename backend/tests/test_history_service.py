"""Unit tests for HistoryService (task 11.1, R3.5/R3.7/R9.1-9.8).

Coverage:
- saveTurn: stores the question-answer pair + TrichDan (round-trip); ATOMIC — on a commit
  error → no partial item is created, returns None (R9.1, R9.2).
- listHistory: isolated (only one's own), sorted descending, limited to <=50
  (R3.5, R3.7, R9.3, R9.6).
- deleteTurn: only deletes one's own item; another user's item → NotFoundError, unchanged
  (R9.6, R9.7).
- markStaleCitations: marks history items that point to a rechunked TaiLieu (R9.8).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
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
from app.errors import NotFoundError
from app.models.schemas import KetQuaTraLoi
from app.models.schemas import TrichDan as TrichDanDTO
from app.services.history_service import HistoryService


@pytest.fixture()
def session():
    """In-memory SQLite session with schema created from Base.metadata."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def service(session):
    return HistoryService(session)


def _tao_tai_khoan(session, email="u@x.com", ten="u") -> TaiKhoan:
    tk = TaiKhoan(email=email, tenDangNhap=ten, matKhauHash="h")
    session.add(tk)
    session.commit()
    return tk


def _tao_khong_gian(session, chuSoHuu, ten="KG") -> KhongGianTaiLieu:
    kg = KhongGianTaiLieu(
        ten=ten,
        chuSoHuuId=chuSoHuu.id,
        embeddingProvider="e5",
        collectionName="ws_x",
    )
    session.add(kg)
    session.commit()
    return kg


def _tao_tai_lieu_chunk(session, khongGian):
    """Create 1 DA_EMBED TaiLieu + 1 Chunk for TrichDan to reference."""
    taiLieu = TaiLieu(
        khongGianId=khongGian.id,
        tenFile="a.pdf",
        dinhDang="pdf",
        kichThuoc=10,
        trangThai=TrangThaiTaiLieu.DA_EMBED,
        chienLuocChunk="auto",
        soChunk=1,
    )
    session.add(taiLieu)
    session.flush()
    chunk = Chunk(
        taiLieuId=taiLieu.id,
        thuTu=0,
        viTriBatDau=0,
        viTriKetThuc=5,
        noiDung="noi dung",
    )
    session.add(chunk)
    session.commit()
    return taiLieu, chunk


def _ket_qua(chunk=None, taiLieu=None) -> KetQuaTraLoi:
    trichDan = []
    if chunk is not None and taiLieu is not None:
        trichDan = [
            TrichDanDTO(
                marker=1,
                chunkId=chunk.id,
                taiLieuId=taiLieu.id,
                noiDung="noi dung",
            )
        ]
    return KetQuaTraLoi(
        traLoi="cau tra loi [1]",
        trichDan=trichDan,
        nhanXacMinh=NhanXacMinh.DA_XAC_MINH,
    )


# --- saveTurn ---------------------------------------------------------------
def test_save_turn_luu_cap_va_trich_dan(service, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu, chunk = _tao_tai_lieu_chunk(session, kg)

    lichSu = service.saveTurn(chu, kg, "cau hoi", _ket_qua(chunk, taiLieu))

    assert lichSu is not None
    assert lichSu.taiKhoanId == chu.id
    assert lichSu.khongGianId == kg.id
    assert lichSu.cauHoi == "cau hoi"
    assert lichSu.traLoi == "cau tra loi [1]"
    assert lichSu.nguonConKhaDung is True
    assert lichSu.createdAt is not None
    # TrichDan is stored along with it, assigned the correct lichSuId.
    trichDan = session.query(TrichDan).filter(TrichDan.lichSuId == lichSu.id).all()
    assert len(trichDan) == 1
    assert trichDan[0].marker == 1
    assert trichDan[0].chunkId == chunk.id


def test_save_turn_khong_trich_dan(service, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)

    lichSu = service.saveTurn(chu, kg, "cau hoi", _ket_qua())

    assert lichSu is not None
    assert session.query(TrichDan).count() == 0


def test_save_turn_loi_khong_tao_muc_do_tra_ve_none(service, session, monkeypatch):
    # R9.2: a save error → no partial item is created, returns None (does not raise).
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu, chunk = _tao_tai_lieu_chunk(session, kg)

    def _commit_loi():
        raise RuntimeError("loi DB gia lap")

    monkeypatch.setattr(session, "commit", _commit_loi)

    ketQua = service.saveTurn(chu, kg, "cau hoi", _ket_qua(chunk, taiLieu))

    assert ketQua is None
    # No partial item created (rollback): no LichSuTroChuyen / TrichDan remains.
    assert session.query(LichSuTroChuyen).count() == 0
    assert session.query(TrichDan).count() == 0


# --- listHistory ------------------------------------------------------------
def test_list_history_co_lap_theo_tai_khoan(service, session):
    # R9.6: returns only one's own history, does not expose another user's.
    chu = _tao_tai_khoan(session, "a@x.com", "a")
    nguoiKhac = _tao_tai_khoan(session, "b@x.com", "b")
    kg = _tao_khong_gian(session, chu)

    service.saveTurn(chu, kg, "cua chu", _ket_qua())
    service.saveTurn(nguoiKhac, kg, "cua nguoi khac", _ket_qua())

    ketQua = service.listHistory(chu, kg)
    assert len(ketQua) == 1
    assert ketQua[0].cauHoi == "cua chu"


def test_list_history_co_lap_theo_khong_gian(service, session):
    # History from another workspace must not be returned.
    chu = _tao_tai_khoan(session)
    kg1 = _tao_khong_gian(session, chu, "KG1")
    kg2 = _tao_khong_gian(session, chu, "KG2")

    service.saveTurn(chu, kg1, "trong kg1", _ket_qua())
    service.saveTurn(chu, kg2, "trong kg2", _ket_qua())

    ketQua = service.listHistory(chu, kg1)
    assert len(ketQua) == 1
    assert ketQua[0].cauHoi == "trong kg1"


def test_list_history_sap_xep_giam_dan(service, session):
    # R9.3: most recent first. Set createdAt manually to check a deterministic order.
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    moc = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(3):
        ls = LichSuTroChuyen(
            taiKhoanId=chu.id,
            khongGianId=kg.id,
            cauHoi=f"hoi {i}",
            traLoi="dap",
            nhanXacMinh=NhanXacMinh.CHUA_XAC_MINH,
            createdAt=moc + timedelta(minutes=i),
        )
        session.add(ls)
    session.commit()

    ketQua = service.listHistory(chu, kg)
    assert [ls.cauHoi for ls in ketQua] == ["hoi 2", "hoi 1", "hoi 0"]


def test_list_history_gioi_han_50(service, session):
    # R9.3: at most 50 items returned per call.
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    moc = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(55):
        session.add(
            LichSuTroChuyen(
                taiKhoanId=chu.id,
                khongGianId=kg.id,
                cauHoi=f"hoi {i}",
                traLoi="dap",
                nhanXacMinh=NhanXacMinh.CHUA_XAC_MINH,
                createdAt=moc + timedelta(minutes=i),
            )
        )
    session.commit()

    ketQua = service.listHistory(chu, kg)
    assert len(ketQua) == 50


def test_list_history_rong_khi_khong_co(service, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    assert service.listHistory(chu, kg) == []


# --- deleteTurn -------------------------------------------------------------
def test_delete_turn_cua_chinh_minh(service, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu, chunk = _tao_tai_lieu_chunk(session, kg)
    lichSu = service.saveTurn(chu, kg, "hoi", _ket_qua(chunk, taiLieu))

    service.deleteTurn(chu, lichSu.id)

    assert session.get(LichSuTroChuyen, lichSu.id) is None
    # Cascade also deletes TrichDan.
    assert session.query(TrichDan).count() == 0


def test_delete_turn_giu_nguyen_cac_muc_khac(service, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    ls1 = service.saveTurn(chu, kg, "hoi 1", _ket_qua())
    ls2 = service.saveTurn(chu, kg, "hoi 2", _ket_qua())

    service.deleteTurn(chu, ls1.id)

    assert session.get(LichSuTroChuyen, ls1.id) is None
    assert session.get(LichSuTroChuyen, ls2.id) is not None


def test_delete_turn_cua_nguoi_khac_bi_tu_choi(service, session):
    # R9.7: deleting an item that is not yours → NotFoundError, the item is kept.
    chu = _tao_tai_khoan(session, "a@x.com", "a")
    nguoiKhac = _tao_tai_khoan(session, "b@x.com", "b")
    kg = _tao_khong_gian(session, chu)
    lichSu = service.saveTurn(chu, kg, "cua chu", _ket_qua())

    with pytest.raises(NotFoundError):
        service.deleteTurn(nguoiKhac, lichSu.id)
    assert session.get(LichSuTroChuyen, lichSu.id) is not None


def test_delete_turn_khong_ton_tai_404(service, session):
    chu = _tao_tai_khoan(session)
    with pytest.raises(NotFoundError):
        service.deleteTurn(chu, "khong-co")


# --- markStaleCitations -----------------------------------------------------
def test_mark_stale_citations_danh_dau_muc_lien_quan(service, session):
    # R9.8: rechunking a document → mark history items that point to that document.
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu, chunk = _tao_tai_lieu_chunk(session, kg)
    lichSu = service.saveTurn(chu, kg, "hoi", _ket_qua(chunk, taiLieu))
    assert lichSu.nguonConKhaDung is True

    soMuc = service.markStaleCitations(taiLieu.id)

    assert soMuc == 1
    session.refresh(lichSu)
    assert lichSu.nguonConKhaDung is False


def test_mark_stale_citations_khong_anh_huong_muc_khac(service, session):
    # An item not referencing the rechunked document is left unchanged.
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu1, chunk1 = _tao_tai_lieu_chunk(session, kg)
    taiLieu2, chunk2 = _tao_tai_lieu_chunk(session, kg)
    ls1 = service.saveTurn(chu, kg, "hoi 1", _ket_qua(chunk1, taiLieu1))
    ls2 = service.saveTurn(chu, kg, "hoi 2", _ket_qua(chunk2, taiLieu2))

    soMuc = service.markStaleCitations(taiLieu1.id)

    assert soMuc == 1
    session.refresh(ls1)
    session.refresh(ls2)
    assert ls1.nguonConKhaDung is False
    assert ls2.nguonConKhaDung is True


def test_mark_stale_citations_khong_co_trich_dan_tra_ve_0(service, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu, _ = _tao_tai_lieu_chunk(session, kg)

    assert service.markStaleCitations(taiLieu.id) == 0
