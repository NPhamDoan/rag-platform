"""Property test (task 10.16) — Property 41: overview answer with citations.

# Feature: multi-user-rag-platform, Property 41: Tra loi tong quan dua tren
# tom tat/outline kem trich dan (R16.1, R16.2)

Invariant (R16.1/16.2): when a space has >=1 document with a TomTatTaiLieu,
`QueryPipeline.answerOverview` ALWAYS returns `laTongQuan=True` and ALWAYS has >=1 TrichDan —
REGARDLESS of whether the (fake) synthesis LLM emits `[n]` markers or not (emits valid markers,
emits no markers, or emits markers out of range). Every returned TrichDan has a `marker` in
1..N (N = number of summary sources) and points to a REAL document in the space.

Reuses the in-memory DB seeding helpers + FakeLLM from `test_query_pipeline_overview.py`.
Each example uses its OWN (fresh) in-memory SQLite so examples are independent. The verify
LLM_Provider is not injected -> verifyAnswer degrades safely, with NO network call.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.pipelines.query_pipeline import QueryPipeline
from tests.test_query_pipeline_overview import (
    FakeLLM,
    _tao_khong_gian,
    _them_tai_lieu,
)


def _newSession():
    """Create a brand-new (session, engine) on in-memory SQLite for each Hypothesis example."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return Session(), engine


# --- Generators -------------------------------------------------------------
# Outline titles: short non-empty text (preserving the "table of contents" meaning).
_titleStrategy = st.text(min_size=1, max_size=20).filter(lambda s: s.strip())
_outlineStrategy = st.lists(
    st.builds(lambda t: {"tieuDe": t}, _titleStrategy), max_size=4
)
# Document summary: arbitrary text (may be empty) — does not affect the invariant.
_summaryStrategy = st.text(max_size=40)
# 1..6 documents, each with one (tomTat, outline).
_docsStrategy = st.lists(
    st.tuples(_summaryStrategy, _outlineStrategy), min_size=1, max_size=6
)
# Markers emitted by the fake LLM: includes valid (1..N with N<=6), 0/out-of-range (>6),
# and an empty list (no markers). -1 does not match the `[\d+]` pattern -> treated as absent.
_markersStrategy = st.lists(st.integers(min_value=-1, max_value=12), max_size=8)


def _buildAnswer(markers: list[int]) -> str:
    """Assemble the fake synthesis LLM's answer with the generated `[n]` markers."""
    phan = ["Tong quan noi dung"]
    for m in markers:
        phan.append(f"chi tiet [{m}]")
    return " ".join(phan)


# --- Property 41 ------------------------------------------------------------
@settings(max_examples=100, deadline=None)
@given(docs=_docsStrategy, markers=_markersStrategy)
def test_overview_luon_co_trich_dan_va_la_tong_quan(docs, markers):
    """answerOverview always returns laTongQuan=True + >=1 TrichDan pointing to a real document.

    **Validates: Requirements 16.1, 16.2**
    """
    db, engine = _newSession()
    try:
        kg = _tao_khong_gian(db)
        idsThuc: set[str] = set()
        for i, (tomTat, outline) in enumerate(docs):
            taiLieu = _them_tai_lieu(db, kg, f"doc-{i}.txt", tomTat, outline)
            idsThuc.add(taiLieu.id)

        synthesis = FakeLLM(_buildAnswer(markers))
        pipeline = QueryPipeline(db=db, synthesisProvider=synthesis)
        kq = pipeline.answerOverview(kg, taiKhoan=None, cauHoi="tong quan tai lieu?")

        soNguon = len(docs)
        # R16.1: the result is an overview answer.
        assert kq.laTongQuan is True
        # R16.2: ALWAYS has >=1 citation whether or not the LLM emits markers.
        assert len(kq.trichDan) >= 1
        for td in kq.trichDan:
            # Marker is in 1..N (N = number of summary sources).
            assert 1 <= td.marker <= soNguon
            # The citation points to a REAL document in the space.
            assert td.taiLieuId in idsThuc
    finally:
        db.close()
        engine.dispose()
