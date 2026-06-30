"""Property test for WorkspaceService.deleteWorkspace (task 5.3, R4.6/R4.7/R4.8).

# Feature: multi-user-rag-platform, Property 19: Deleting a workspace is complete and
# rollback-safe.
#
# Meaning: for EVERY KhongGianTaiLieu carrying a varied amount of related data
# (varying counts of TaiLieu / Chunks per document / LichSuTroChuyen entries +
# TrichDan), the `deleteWorkspace` operation runs in ONE transaction and:
#   (a) on SUCCESS: deletes the workspace along with ALL data belonging to it
#       (TaiLieu, Chunk, TrichDan, LichSuTroChuyen, CauHinhTruyXuat, ChiaSe) and
#       does NOT touch the data of another workspace/account (R4.6, R4.8).
#   (b) on FAILURE midway (commit or delete raises): rolls back to keep the workspace
#       and all of its data intact — atomic / rollback-safe (R4.7).
# Validates: Requirements 4.6, 4.7, 4.8

Each example uses ONE separate in-memory SQLite (mirroring the other property tests).
Accounts are created directly with a fake matKhauHash (no bcrypt) so it is fast →
max_examples=100.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import (
    CauHinhTruyXuat,
    ChiaSe,
    Chunk,
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
from app.services.workspace_service import WorkspaceService


@contextmanager
def _fresh_session():
    """A fresh in-memory SQLite session (schema from Base.metadata), cleaned up after each round."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _tao_tai_khoan(db, email, ten) -> TaiKhoan:
    """Create a TaiKhoan directly with a fake matKhauHash + a generous HanMuc (no bcrypt)."""
    tk = TaiKhoan(email=email, tenDangNhap=ten, matKhauHash="h")
    tk.hanMuc = HanMuc(soKhongGianToiDa=50)
    db.add(tk)
    db.commit()
    return tk


def _do_du_lieu_khong_gian(
    db, khongGian: KhongGianTaiLieu, chuSoHuu: TaiKhoan, soTaiLieu, chunkMoiTaiLieu, soLichSu
):
    """Load varied data into the workspace: TaiLieu + Chunk + LichSuTroChuyen + TrichDan.

    Creates a TrichDan linking a history entry to the first chunk (if any), to cover R4.8.
    """
    chunks: list[Chunk] = []
    for i in range(soTaiLieu):
        taiLieu = TaiLieu(
            khongGianId=khongGian.id,
            tenFile=f"tai_lieu_{i}.pdf",
            dinhDang="pdf",
            kichThuoc=100,
            trangThai=TrangThaiTaiLieu.DA_EMBED,
            chienLuocChunk="auto",
            soChunk=chunkMoiTaiLieu,
        )
        db.add(taiLieu)
        db.flush()
        for j in range(chunkMoiTaiLieu):
            chunk = Chunk(
                taiLieuId=taiLieu.id,
                thuTu=j,
                viTriBatDau=j * 10,
                viTriKetThuc=j * 10 + 5,
                noiDung=f"noi dung {i}-{j}",
            )
            db.add(chunk)
            db.flush()
            chunks.append(chunk)

    for h in range(soLichSu):
        lichSu = LichSuTroChuyen(
            taiKhoanId=chuSoHuu.id,
            khongGianId=khongGian.id,
            cauHoi=f"hoi {h}",
            traLoi=f"dap {h} [1]",
            nhanXacMinh=NhanXacMinh.CHUA_XAC_MINH,
        )
        db.add(lichSu)
        db.flush()
        if chunks:
            chunk = chunks[h % len(chunks)]
            db.add(
                TrichDan(
                    lichSuId=lichSu.id,
                    marker=1,
                    chunkId=chunk.id,
                    taiLieuId=chunk.taiLieuId,
                    noiDung=chunk.noiDung,
                )
            )
    db.commit()


def _dem_du_lieu(db, khongGianId: str) -> dict[str, int]:
    """Count each record type belonging to a workspace (to compare before/after)."""
    taiLieuIds = [
        r[0]
        for r in db.query(TaiLieu.id).filter(TaiLieu.khongGianId == khongGianId).all()
    ]
    soChunk = (
        db.query(Chunk).filter(Chunk.taiLieuId.in_(taiLieuIds)).count()
        if taiLieuIds
        else 0
    )
    soTrichDan = (
        db.query(TrichDan).filter(TrichDan.taiLieuId.in_(taiLieuIds)).count()
        if taiLieuIds
        else 0
    )
    return {
        "khongGian": 1 if db.get(KhongGianTaiLieu, khongGianId) is not None else 0,
        "taiLieu": len(taiLieuIds),
        "chunk": soChunk,
        "trichDan": soTrichDan,
        "lichSu": db.query(LichSuTroChuyen)
        .filter(LichSuTroChuyen.khongGianId == khongGianId)
        .count(),
        "cauHinh": 1 if db.get(CauHinhTruyXuat, khongGianId) is not None else 0,
        "chiaSe": db.query(ChiaSe).filter(ChiaSe.khongGianId == khongGianId).count(),
    }


@settings(max_examples=40, deadline=None)
@given(
    soTaiLieu=st.integers(min_value=0, max_value=3),
    chunkMoiTaiLieu=st.integers(min_value=0, max_value=3),
    soLichSu=st.integers(min_value=0, max_value=3),
    epThatBai=st.booleans(),
    cheDoLoi=st.sampled_from(["commit", "delete"]),
)
def test_xoa_khong_gian_toan_ven_va_co_hoan_tac(
    soTaiLieu, chunkMoiTaiLieu, soLichSu, epThatBai, cheDoLoi
):
    with _fresh_session() as db:
        chuSoHuu = _tao_tai_khoan(db, "chu@x.com", "chu")
        nguoiKhac = _tao_tai_khoan(db, "khac@x.com", "khac")
        service = WorkspaceService(db)

        # The target workspace (to be deleted) + varied data + one ChiaSe record.
        kg = service.createWorkspace(chuSoHuu, "KG muc tieu")
        _do_du_lieu_khong_gian(db, kg, chuSoHuu, soTaiLieu, chunkMoiTaiLieu, soLichSu)
        db.add(ChiaSe(khongGianId=kg.id, taiKhoanId=nguoiKhac.id, mucQuyen=MucQuyen.CHI_DOC))
        db.commit()

        # Another person's workspace (which MUST remain intact in every scenario).
        kgKhac = service.createWorkspace(nguoiKhac, "KG khac")
        _do_du_lieu_khong_gian(db, kgKhac, nguoiKhac, 2, 2, 2)

        truocMucTieu = _dem_du_lieu(db, kg.id)
        truocKhac = _dem_du_lieu(db, kgKhac.id)
        # Precondition: the target workspace really has data as expected.
        assert truocMucTieu["khongGian"] == 1
        assert truocMucTieu["taiLieu"] == soTaiLieu
        assert truocMucTieu["chunk"] == soTaiLieu * chunkMoiTaiLieu
        assert truocMucTieu["cauHinh"] == 1
        assert truocMucTieu["chiaSe"] == 1

        if not epThatBai:
            # (a) SUCCESS: completely delete the target workspace.
            service.deleteWorkspace(chuSoHuu, kg.id)

            sauMucTieu = _dem_du_lieu(db, kg.id)
            assert sauMucTieu == {
                "khongGian": 0,
                "taiLieu": 0,
                "chunk": 0,
                "trichDan": 0,
                "lichSu": 0,
                "cauHinh": 0,
                "chiaSe": 0,
            }
            # The other person's data is NOT touched (R4.6 — isolation).
            assert _dem_du_lieu(db, kgKhac.id) == truocKhac
        else:
            # (b) FAILURE midway: force an error then assert a complete rollback (R4.7).
            if cheDoLoi == "commit":
                def _commit_loi():
                    raise RuntimeError("loi commit gia lap khi xoa khong gian")

                db.commit = _commit_loi  # type: ignore[method-assign]
            else:
                def _delete_loi(_obj):
                    raise RuntimeError("loi delete gia lap khi xoa khong gian")

                db.delete = _delete_loi  # type: ignore[method-assign]

            with pytest.raises(RuntimeError):
                service.deleteWorkspace(chuSoHuu, kg.id)

            # Restore the EXACT patched method to run verification queries.
            if cheDoLoi == "commit":
                del db.commit
            else:
                del db.delete

            # The target workspace and ALL of its data remain intact.
            assert _dem_du_lieu(db, kg.id) == truocMucTieu
            # The other person's data is also unchanged.
            assert _dem_du_lieu(db, kgKhac.id) == truocKhac
