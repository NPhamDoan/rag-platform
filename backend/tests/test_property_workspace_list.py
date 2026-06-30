"""Property-based test for `WorkspaceService.listWorkspaces` — isolation (Property 13).

# Feature: multi-user-rag-platform, Property 13: Workspace listing returns only owned
# and shared spaces — given a population of accounts and workspaces with diverse
# ownership and ChiaSe (share) relationships, `listWorkspaces(taiKhoan)` returns
# EXACTLY the set of workspaces the account OWNS unioned with the set SHARED with the
# account (no duplicates, no leaking into other people's spaces it has no ChiaSe for).
# Validates: Requirements 3.1

Each round uses ONE separate in-memory SQLite session (schema from Base.metadata).
Accounts are created directly with a fake matKhauHash ("h", no bcrypt) for speed →
max_examples=200. Hypothesis generates: the number of accounts, the owner of each
workspace, and the ChiaSe records (unique (khongGian, taiKhoan) pairs per the UNIQUE
constraint), then picks one account to check. The expected set is computed
independently of the implementation: owned unioned with shared.
"""

from __future__ import annotations

from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import ChiaSe, KhongGianTaiLieu, MucQuyen, TaiKhoan
from app.services.workspace_service import WorkspaceService


@contextmanager
def _fresh_session():
    """Fresh in-memory SQLite session — cleaned up after each round."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@st.composite
def _quan_the(draw):
    """Generate a population (number of accounts, number of workspaces, owner of each
    workspace, the share records, and the index of the account under test).

    Returns a purely descriptive dict (no ORM yet) so the test can build the expected
    set itself.
    """
    soTaiKhoan = draw(st.integers(min_value=1, max_value=5))
    soKhongGian = draw(st.integers(min_value=0, max_value=8))

    # Owner of each workspace: an account index in [0, soTaiKhoan).
    chuSoHuuTheoKhongGian = draw(
        st.lists(
            st.integers(min_value=0, max_value=soTaiKhoan - 1),
            min_size=soKhongGian,
            max_size=soKhongGian,
        )
    )

    # Share records: a set of unique (khongGianIdx, taiKhoanIdx) pairs (the
    # UNIQUE(khongGianId, taiKhoanId) constraint) together with a mucQuyen. Sharing
    # with the owner is allowed too, to exercise dedup (edge case).
    if soKhongGian > 0:
        capChiaSe = draw(
            st.lists(
                st.tuples(
                    st.integers(min_value=0, max_value=soKhongGian - 1),
                    st.integers(min_value=0, max_value=soTaiKhoan - 1),
                    st.sampled_from([MucQuyen.CHI_DOC, MucQuyen.GHI]),
                ),
                max_size=12,
                unique_by=lambda t: (t[0], t[1]),  # one ChiaSe per pair
            )
        )
    else:
        capChiaSe = []

    taiKhoanKiem = draw(st.integers(min_value=0, max_value=soTaiKhoan - 1))

    return {
        "soTaiKhoan": soTaiKhoan,
        "chuSoHuuTheoKhongGian": chuSoHuuTheoKhongGian,
        "capChiaSe": capChiaSe,
        "taiKhoanKiem": taiKhoanKiem,
    }


@settings(max_examples=40, deadline=None)
@given(quanThe=_quan_the())
def test_liet_ke_khong_gian_co_lap(quanThe):
    soTaiKhoan = quanThe["soTaiKhoan"]
    chuSoHuuTheoKhongGian = quanThe["chuSoHuuTheoKhongGian"]
    capChiaSe = quanThe["capChiaSe"]
    idxKiem = quanThe["taiKhoanKiem"]

    with _fresh_session() as db:
        # Create accounts (fake matKhauHash, no bcrypt).
        taiKhoan = [
            TaiKhoan(email=f"u{i}@x.com", tenDangNhap=f"u{i}", matKhauHash="h")
            for i in range(soTaiKhoan)
        ]
        db.add_all(taiKhoan)
        db.commit()

        # Create workspaces from the generated owners.
        khongGian = []
        for j, chuIdx in enumerate(chuSoHuuTheoKhongGian):
            kg = KhongGianTaiLieu(
                ten=f"KG{j}",
                chuSoHuuId=taiKhoan[chuIdx].id,
                embeddingProvider="e5",
                collectionName=f"ws_{j}",
            )
            khongGian.append(kg)
        db.add_all(khongGian)
        db.commit()

        # Create share records.
        for kgIdx, tkIdx, mucQuyen in capChiaSe:
            db.add(
                ChiaSe(
                    khongGianId=khongGian[kgIdx].id,
                    taiKhoanId=taiKhoan[tkIdx].id,
                    mucQuyen=mucQuyen,
                )
            )
        db.commit()

        # Expected set (independent of the implementation): owned unioned with shared.
        idSoHuu = {
            khongGian[j].id
            for j, chuIdx in enumerate(chuSoHuuTheoKhongGian)
            if chuIdx == idxKiem
        }
        idDuocChiaSe = {
            khongGian[kgIdx].id
            for kgIdx, tkIdx, _ in capChiaSe
            if tkIdx == idxKiem
        }
        idMongDoi = idSoHuu | idDuocChiaSe

        ketQua = WorkspaceService(db).listWorkspaces(taiKhoan[idxKiem])
        idKetQua = [kg.id for kg in ketQua]

        # (1) No duplicates.
        assert len(idKetQua) == len(set(idKetQua))
        # (2) Exactly the owned + shared set, no leaking into other spaces.
        assert set(idKetQua) == idMongDoi
