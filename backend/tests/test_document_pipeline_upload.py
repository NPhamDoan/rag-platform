"""Unit tests for DocumentPipeline.uploadDocument (task 8.1, R5.1-5.5, R5.13-14, R12.2-3).

Coverage:
- Valid txt/md upload -> DA_PARSE_CHO_DUYET state + a Chunk preview, does NOT embed
  (R5.1, R5.13, R5.14).
- Unsupported format -> ValidationError, no document created (R5.4).
- Exceeds the configured size -> ValidationError, no document created (R5.3).
- No GHI permission -> AuthorizationError, no document created (R5.2).
- Exceeds quota (storage / document count) -> QuotaExceededError (R12.2, R12.3).
- Empty / whitespace-only file -> 0 chunks -> ValidationError (R5.5).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chunking.registry import discover_chunkers
from app.config import get_settings
from app.db.database import Base
from app.db.models import (
    Chunk,
    ChiaSe,
    HanMuc,
    KhongGianTaiLieu,
    MucQuyen,
    TaiKhoan,
    TaiLieu,
    TrangThaiTaiLieu,
)
from app.errors import AuthorizationError, QuotaExceededError, ValidationError
from app.pipelines.document_pipeline import DocumentPipeline


@pytest.fixture(scope="module", autouse=True)
def _nap_chunker():
    """Load the chunk-strategy registry (AutoSelector resolves via get_chunker)."""
    discover_chunkers()


@pytest.fixture()
def session():
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
def pipeline(session):
    return DocumentPipeline(session)


def _tao_tai_khoan(
    session,
    email="chu@x.com",
    ten="chu",
    *,
    dungLuongToiDa=5 * 1024**3,
    soTaiLieuToiDaMoiKhongGian=1000,
) -> TaiKhoan:
    tk = TaiKhoan(email=email, tenDangNhap=ten, matKhauHash="h")
    tk.hanMuc = HanMuc(
        dungLuongToiDa=dungLuongToiDa,
        soTaiLieuToiDaMoiKhongGian=soTaiLieuToiDaMoiKhongGian,
    )
    session.add(tk)
    session.commit()
    return tk


def _tao_khong_gian(session, chuSoHuu, ten="KG") -> KhongGianTaiLieu:
    kg = KhongGianTaiLieu(
        ten=ten,
        moTa="",
        chuSoHuuId=chuSoHuu.id,
        embeddingProvider="huggingface",
        collectionName="ws_tmp",
    )
    session.add(kg)
    session.flush()
    kg.collectionName = f"ws_{kg.id}"
    session.commit()
    return kg


# --- Valid upload -----------------------------------------------------------
def test_upload_txt_tao_preview_chua_embed(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    noiDung = "Dong mot.\n\nDong hai.\n\nDong ba."

    ketQua = pipeline.uploadDocument(
        chu, kg, noiDung.encode("utf-8"), "ghi-chu.txt", "txt"
    )

    assert ketQua.soChunk >= 1
    assert len(ketQua.chunks) == ketQua.soChunk
    # Preview in ascending order, non-empty noiDung.
    assert [c.thuTu for c in ketQua.chunks] == list(range(ketQua.soChunk))
    assert all(c.noiDung.strip() for c in ketQua.chunks)

    # TaiLieu in the DA_PARSE_CHO_DUYET state (NOT yet embedded) — R5.1/5.13.
    taiLieu = session.query(TaiLieu).one()
    assert taiLieu.trangThai == TrangThaiTaiLieu.DA_PARSE_CHO_DUYET
    assert taiLieu.soChunk == ketQua.soChunk
    assert taiLieu.chienLuocChunk  # resolved (not the empty "auto")
    assert taiLieu.kichThuoc == len(noiDung.encode("utf-8"))
    # The Chunk preview has been persisted to the RDB.
    assert session.query(Chunk).count() == ketQua.soChunk


def test_upload_md_chon_structure_aware(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    noiDung = "# Tieu de\n\nNoi dung phan mot.\n\n## Muc nho\n\nNoi dung phan hai."

    ketQua = pipeline.uploadDocument(
        chu, kg, noiDung.encode("utf-8"), "tai-lieu.md", "md"
    )

    assert ketQua.soChunk >= 1
    taiLieu = session.query(TaiLieu).one()
    # AutoSelector: markdown headings present -> structure-aware (R17.5).
    assert taiLieu.chienLuocChunk == "structure-aware"


def test_upload_chien_luoc_chi_dinh(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    noiDung = "# Heading\n\nNoi dung."

    # Specify recursive (not auto) -> must use recursive exactly.
    ketQua = pipeline.uploadDocument(
        chu, kg, noiDung.encode("utf-8"), "a.md", "md", chienLuocChunk="recursive"
    )

    assert ketQua.soChunk >= 1
    assert session.query(TaiLieu).one().chienLuocChunk == "recursive"


def test_upload_dinh_dang_co_dau_cham_va_hoa(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    ketQua = pipeline.uploadDocument(
        chu, kg, b"noi dung text", "a.TXT", ".TXT"
    )
    assert ketQua.soChunk >= 1


# --- Rejections -------------------------------------------------------------
def test_upload_dinh_dang_khong_ho_tro_bi_tu_choi(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    with pytest.raises(ValidationError):
        pipeline.uploadDocument(chu, kg, b"noi dung", "a.docx", "docx")
    assert session.query(TaiLieu).count() == 0
    assert session.query(Chunk).count() == 0


def test_upload_vuot_kich_thuoc_bi_tu_choi(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    quaLon = b"x" * (get_settings().max_file_size_bytes + 1)
    with pytest.raises(ValidationError):
        pipeline.uploadDocument(chu, kg, quaLon, "to.txt", "txt")
    assert session.query(TaiLieu).count() == 0


def test_upload_khong_co_quyen_ghi_bi_tu_choi(pipeline, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)
    # Shared as CHI_DOC -> insufficient write permission (R5.2).
    session.add(ChiaSe(khongGianId=kg.id, taiKhoanId=khach.id, mucQuyen=MucQuyen.CHI_DOC))
    session.commit()

    with pytest.raises(AuthorizationError):
        pipeline.uploadDocument(khach, kg, b"noi dung", "a.txt", "txt")
    assert session.query(TaiLieu).count() == 0


def test_upload_nguoi_la_khong_quyen_bi_tu_choi(pipeline, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    nguoiLa = _tao_tai_khoan(session, "la@x.com", "la")
    kg = _tao_khong_gian(session, chu)
    with pytest.raises(AuthorizationError):
        pipeline.uploadDocument(nguoiLa, kg, b"noi dung", "a.txt", "txt")
    assert session.query(TaiLieu).count() == 0


def test_upload_vuot_han_muc_dung_luong_bi_tu_choi(pipeline, session):
    chu = _tao_tai_khoan(session, dungLuongToiDa=10)
    kg = _tao_khong_gian(session, chu)
    with pytest.raises(QuotaExceededError):
        pipeline.uploadDocument(chu, kg, b"x" * 11, "a.txt", "txt")
    assert session.query(TaiLieu).count() == 0


def test_upload_vuot_han_muc_so_tai_lieu_bi_tu_choi(pipeline, session):
    chu = _tao_tai_khoan(session, soTaiLieuToiDaMoiKhongGian=1)
    kg = _tao_khong_gian(session, chu)
    # The first document succeeds.
    pipeline.uploadDocument(chu, kg, b"noi dung mot", "a.txt", "txt")
    # The second document exceeds the per-workspace document quota (R12.3).
    with pytest.raises(QuotaExceededError):
        pipeline.uploadDocument(chu, kg, b"noi dung hai", "b.txt", "txt")
    assert session.query(TaiLieu).count() == 1


@pytest.mark.parametrize("noiDung", [b"", b"   ", b"\n\t  \n"])
def test_upload_tep_rong_0_chunk_bi_tu_choi(pipeline, session, noiDung):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    with pytest.raises(ValidationError):
        pipeline.uploadDocument(chu, kg, noiDung, "rong.txt", "txt")
    assert session.query(TaiLieu).count() == 0
    assert session.query(Chunk).count() == 0
