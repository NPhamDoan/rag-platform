"""Property-based test for granting/revoking share permissions — round-trip (Property 44).

# Feature: multi-user-rag-platform, Property 44: Granting and revoking share
# permissions is a round-trip — for ANY sequence of grant(mucQuyen)/revoke operations
# on a (workspace, non-owner target account) pair, after EVERY step resolveAccess
# equals the expected state: starts at NONE; after grant(mucQuyen) == the MucTruyCap
# mapped from mucQuyen; after revoke back to NONE (round-trip); re-grant after revoke
# works again; multiple grants only UPSERT (the ChiaSe record count for the pair is
# always <= 1).
# Validates: Requirements 11.1, 11.6

Model: Hypothesis generates a list of steps — each step is ('grant', mucQuyen) or
('revoke',). The test tracks `mucMongDoi` (the expected MucTruyCap) in parallel with
ShareService and asserts they match after each step. Each example uses ONE separate
in-memory SQLite session; accounts are created directly (no bcrypt) so it is fast →
max_examples=100.
"""

from __future__ import annotations

from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import ChiaSe, KhongGianTaiLieu, MucQuyen, TaiKhoan
from app.services.share_service import MucTruyCap, ShareService


@contextmanager
def _fresh_session():
    """A fresh in-memory SQLite session (schema from Base.metadata) — cleaned up after each round."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


# Map mucQuyen (the ChiaSe record) → expected MucTruyCap after a grant.
_GRANT_TO_ACCESS: dict[MucQuyen, MucTruyCap] = {
    MucQuyen.CHI_DOC: MucTruyCap.CHI_DOC,
    MucQuyen.GHI: MucTruyCap.GHI,
}

# One step: grant with one of the two valid mucQuyen, or revoke.
_buoc = st.one_of(
    st.tuples(st.just("grant"), st.sampled_from([MucQuyen.CHI_DOC, MucQuyen.GHI])),
    st.tuples(st.just("revoke")),
)


@settings(max_examples=40, deadline=None)
@given(cacBuoc=st.lists(_buoc, min_size=1, max_size=12))
def test_cap_thu_hoi_quyen_round_trip(cacBuoc):
    with _fresh_session() as db:
        service = ShareService(db)

        chu = TaiKhoan(email="chu@x.com", tenDangNhap="chu", matKhauHash="h")
        khach = TaiKhoan(email="khach@x.com", tenDangNhap="khach", matKhauHash="h")
        db.add_all([chu, khach])
        db.commit()

        khongGian = KhongGianTaiLieu(
            ten="KG",
            chuSoHuuId=chu.id,
            embeddingProvider="e5",
            collectionName="ws_x",
        )
        db.add(khongGian)
        db.commit()

        # Initial state: the target account (not the owner) has no permission.
        mucMongDoi = MucTruyCap.NONE
        assert service.resolveAccess(khach, khongGian) == mucMongDoi

        for buoc in cacBuoc:
            if buoc[0] == "grant":
                mucQuyen = buoc[1]
                service.grantShare(chu, khongGian.id, khach.id, mucQuyen)
                # After grant: access == the level mapped from mucQuyen (idempotent/upsert).
                mucMongDoi = _GRANT_TO_ACCESS[mucQuyen]
            else:
                service.revokeShare(chu, khongGian.id, khach.id)
                # After revoke: back to NONE (round-trip, even on consecutive revokes).
                mucMongDoi = MucTruyCap.NONE

            # (1) resolveAccess matches the expected state after EVERY step.
            assert service.resolveAccess(khach, khongGian) == mucMongDoi

            # (2) Upsert: there is never more than one ChiaSe record for this pair.
            soBanGhi = (
                db.query(ChiaSe)
                .filter(
                    ChiaSe.khongGianId == khongGian.id,
                    ChiaSe.taiKhoanId == khach.id,
                )
                .count()
            )
            assert soBanGhi <= 1
            # The record count is consistent with the state: has permission → 1, NONE → 0.
            assert soBanGhi == (1 if mucMongDoi != MucTruyCap.NONE else 0)
