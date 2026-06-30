"""Unit tests for WorkspaceService (task 5.1, R3.1/R4.1-4.8/R12.1).

Coverage:
- createWorkspace: validates ten/moTa (trim, 1-100 / <=1000) + applies the workspace
  count quota (R12.1); sets embeddingProvider + collectionName + CauHinhTruyXuat.
- renameWorkspace / updateDescription: require CHU_SO_HUU (R4.5), check constraints,
  404 when not found.
- deleteWorkspace: runs in a transaction, cascading TaiLieu/Chunk/TrichDan/LichSu;
  requires CHU_SO_HUU; 404 when not found.
- listWorkspaces: returns only owned + shared workspaces (R3.1), does not reveal
  other people's.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import (
    Chunk,
    ChiaSe,
    CauHinhTruyXuat,
    HanMuc,
    KhongGianTaiLieu,
    LichSuTroChuyen,
    MucQuyen,
    NhanXacMinh,
    TaiKhoan,
    TaiLieu,
    TrangThaiTaiLieu,
    TrichDan,
)
from app.errors import (
    AuthorizationError,
    NotFoundError,
    QuotaExceededError,
    ValidationError,
)
from app.services.workspace_service import WorkspaceService


@pytest.fixture()
def session():
    """In-memory SQLite session with the schema created from Base.metadata."""
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
    return WorkspaceService(session)


def _tao_tai_khoan(session, email="chu@x.com", ten="chu", soKhongGianToiDa=50) -> TaiKhoan:
    tk = TaiKhoan(email=email, tenDangNhap=ten, matKhauHash="h")
    tk.hanMuc = HanMuc(soKhongGianToiDa=soKhongGianToiDa)
    session.add(tk)
    session.commit()
    return tk


# --- createWorkspace --------------------------------------------------------
def test_create_workspace_hop_le(service, session):
    chu = _tao_tai_khoan(session)

    kg = service.createWorkspace(chu, "  Du an A  ", "  mo ta  ")

    assert kg.id is not None
    assert kg.ten == "Du an A"  # trimmed
    assert kg.moTa == "mo ta"
    assert kg.chuSoHuuId == chu.id
    assert kg.embeddingProvider  # taken from the default config
    assert kg.collectionName == f"ws_{kg.id}"
    # Created with a default CauHinhTruyXuat (1-1 relationship).
    cauHinh = session.get(CauHinhTruyXuat, kg.id)
    assert cauHinh is not None
    assert cauHinh.k == 8


def test_create_workspace_moTa_mac_dinh_rong(service, session):
    chu = _tao_tai_khoan(session)
    kg = service.createWorkspace(chu, "Du an A")
    assert kg.moTa == ""


@pytest.mark.parametrize(
    "ten",
    [
        "",            # empty (R4.2)
        "   ",          # whitespace only → empty after trim (R4.1, R4.2)
        "x" * 101,      # over 100 characters (R4.2)
    ],
)
def test_create_workspace_ten_khong_hop_le_bi_tu_choi(service, session, ten):
    chu = _tao_tai_khoan(session)
    with pytest.raises(ValidationError):
        service.createWorkspace(chu, ten)
    assert session.query(KhongGianTaiLieu).count() == 0


def test_create_workspace_moTa_qua_dai_bi_tu_choi(service, session):
    chu = _tao_tai_khoan(session)
    with pytest.raises(ValidationError):
        service.createWorkspace(chu, "Du an A", "x" * 1001)
    assert session.query(KhongGianTaiLieu).count() == 0


def test_create_workspace_ap_han_muc_so_khong_gian(service, session):
    # Quota = 2 → 2 succeed, the 3rd is rejected (R12.1).
    chu = _tao_tai_khoan(session, soKhongGianToiDa=2)
    service.createWorkspace(chu, "KG1")
    service.createWorkspace(chu, "KG2")

    with pytest.raises(QuotaExceededError):
        service.createWorkspace(chu, "KG3")

    assert (
        session.query(KhongGianTaiLieu)
        .filter(KhongGianTaiLieu.chuSoHuuId == chu.id)
        .count()
        == 2
    )


def test_create_workspace_han_muc_doc_lap_theo_tai_khoan(service, session):
    # Another person's workspaces do not count toward one's own quota.
    chuA = _tao_tai_khoan(session, "a@x.com", "a", soKhongGianToiDa=1)
    chuB = _tao_tai_khoan(session, "b@x.com", "b", soKhongGianToiDa=1)
    service.createWorkspace(chuA, "KG-A")
    # B can still create even though A has hit the quota.
    kgB = service.createWorkspace(chuB, "KG-B")
    assert kgB.chuSoHuuId == chuB.id


# --- renameWorkspace / updateDescription -----------------------------------
def test_rename_workspace_chu_so_huu(service, session):
    chu = _tao_tai_khoan(session)
    kg = service.createWorkspace(chu, "Ten cu")

    daDoi = service.renameWorkspace(chu, kg.id, "  Ten moi  ")
    assert daDoi.ten == "Ten moi"


def test_rename_workspace_khong_phai_chu_so_huu_bi_tu_choi(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    nguoiLa = _tao_tai_khoan(session, "la@x.com", "la")
    kg = service.createWorkspace(chu, "Ten cu")

    with pytest.raises(AuthorizationError):
        service.renameWorkspace(nguoiLa, kg.id, "Ten moi")
    # Unchanged.
    assert session.get(KhongGianTaiLieu, kg.id).ten == "Ten cu"


def test_rename_workspace_khong_ton_tai_404(service, session):
    chu = _tao_tai_khoan(session)
    with pytest.raises(NotFoundError):
        service.renameWorkspace(chu, "khong-co", "Ten moi")


def test_rename_workspace_ten_khong_hop_le_bi_tu_choi(service, session):
    chu = _tao_tai_khoan(session)
    kg = service.createWorkspace(chu, "Ten cu")
    with pytest.raises(ValidationError):
        service.renameWorkspace(chu, kg.id, "x" * 101)
    assert session.get(KhongGianTaiLieu, kg.id).ten == "Ten cu"


def test_update_description_chu_so_huu(service, session):
    chu = _tao_tai_khoan(session)
    kg = service.createWorkspace(chu, "Du an A")

    daDoi = service.updateDescription(chu, kg.id, "  mo ta moi  ")
    assert daDoi.moTa == "mo ta moi"


def test_update_description_qua_dai_bi_tu_choi(service, session):
    chu = _tao_tai_khoan(session)
    kg = service.createWorkspace(chu, "Du an A", "mo ta cu")
    with pytest.raises(ValidationError):
        service.updateDescription(chu, kg.id, "x" * 1001)
    assert session.get(KhongGianTaiLieu, kg.id).moTa == "mo ta cu"


def test_update_description_khong_phai_chu_so_huu_bi_tu_choi(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    nguoiLa = _tao_tai_khoan(session, "la@x.com", "la")
    kg = service.createWorkspace(chu, "Du an A", "mo ta cu")
    with pytest.raises(AuthorizationError):
        service.updateDescription(nguoiLa, kg.id, "moi")
    assert session.get(KhongGianTaiLieu, kg.id).moTa == "mo ta cu"


# --- deleteWorkspace --------------------------------------------------------
def _tao_tai_lieu_co_chunk_va_lich_su(session, khongGian, taiKhoan):
    """Create 1 TaiLieu + Chunk + LichSuTroChuyen + TrichDan in the workspace."""
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
    session.flush()
    lichSu = LichSuTroChuyen(
        taiKhoanId=taiKhoan.id,
        khongGianId=khongGian.id,
        cauHoi="hoi",
        traLoi="dap [1]",
        nhanXacMinh=NhanXacMinh.DA_XAC_MINH,
    )
    session.add(lichSu)
    session.flush()
    session.add(
        TrichDan(
            lichSuId=lichSu.id,
            marker=1,
            chunkId=chunk.id,
            taiLieuId=taiLieu.id,
            noiDung="noi dung",
        )
    )
    session.commit()
    return taiLieu, chunk, lichSu


def test_delete_workspace_xoa_tai_lieu_chunk_trich_dan_lich_su(service, session):
    chu = _tao_tai_khoan(session)
    kg = service.createWorkspace(chu, "Du an A")
    _tao_tai_lieu_co_chunk_va_lich_su(session, kg, chu)

    service.deleteWorkspace(chu, kg.id)

    assert session.get(KhongGianTaiLieu, kg.id) is None
    assert session.query(TaiLieu).count() == 0
    assert session.query(Chunk).count() == 0
    assert session.query(TrichDan).count() == 0
    assert session.query(LichSuTroChuyen).count() == 0
    assert session.query(CauHinhTruyXuat).count() == 0


def test_delete_workspace_khong_phai_chu_so_huu_bi_tu_choi(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    nguoiLa = _tao_tai_khoan(session, "la@x.com", "la")
    kg = service.createWorkspace(chu, "Du an A")

    with pytest.raises(AuthorizationError):
        service.deleteWorkspace(nguoiLa, kg.id)
    assert session.get(KhongGianTaiLieu, kg.id) is not None


def test_delete_workspace_khong_ton_tai_404(service, session):
    chu = _tao_tai_khoan(session)
    with pytest.raises(NotFoundError):
        service.deleteWorkspace(chu, "khong-co")


def test_delete_workspace_chia_se_khong_du_quyen(service, session):
    # A person shared with GHI still cannot delete (only CHU_SO_HUU, R4.5).
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = service.createWorkspace(chu, "Du an A")
    session.add(ChiaSe(khongGianId=kg.id, taiKhoanId=khach.id, mucQuyen=MucQuyen.GHI))
    session.commit()

    with pytest.raises(AuthorizationError):
        service.deleteWorkspace(khach, kg.id)
    assert session.get(KhongGianTaiLieu, kg.id) is not None


# --- listWorkspaces ---------------------------------------------------------
def test_list_workspaces_chi_so_huu_va_duoc_chia_se(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    nguoiKhac = _tao_tai_khoan(session, "khac@x.com", "khac")

    kg1 = service.createWorkspace(chu, "KG1")  # owned
    kgKhac = service.createWorkspace(nguoiKhac, "KG-khac")  # someone else's
    kgChiaSe = service.createWorkspace(nguoiKhac, "KG-chia-se")
    session.add(
        ChiaSe(khongGianId=kgChiaSe.id, taiKhoanId=chu.id, mucQuyen=MucQuyen.CHI_DOC)
    )
    session.commit()

    ketQua = service.listWorkspaces(chu)
    ids = {kg.id for kg in ketQua}

    assert kg1.id in ids          # owned
    assert kgChiaSe.id in ids     # shared
    assert kgKhac.id not in ids   # does not reveal another person's
    assert len(ketQua) == 2


def test_list_workspaces_khong_trung_lap(service, session):
    # A workspace both owned and having a ChiaSe record (edge case) → no duplicate.
    chu = _tao_tai_khoan(session)
    kg = service.createWorkspace(chu, "KG1")
    session.add(
        ChiaSe(khongGianId=kg.id, taiKhoanId=chu.id, mucQuyen=MucQuyen.CHI_DOC)
    )
    session.commit()

    ketQua = service.listWorkspaces(chu)
    assert len(ketQua) == 1


def test_list_workspaces_rong_khi_khong_co(service, session):
    chu = _tao_tai_khoan(session)
    assert service.listWorkspaces(chu) == []
