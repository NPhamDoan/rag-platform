"""Property test for QueryPipeline.buildSuggestions (task 10.17).

# Feature: multi-user-rag-platform, Property 42: Suggestions are generated from real
documents; with no documents it reports empty.
Validates: Requirements 15.4, 15.5, 16.5.

Property 42 (two branches):
  (a) No documents in the workspace  -> buildSuggestions returns an EMPTY LIST
      (reports empty) and is fully DETERMINISTIC (called multiple times -> same
      result). The pipeline is initialized with NO LLM_Provider (buildSuggestions
      takes no provider — the structure guarantees no LLM call, R16.5).
  (b) >=1 document -> every suggestion is drawn from REAL DOCUMENT DATA: equal to one
      of the document's outline titles OR the file name (never fabricated text —
      R15.4/15.5). No duplicates, and the count <= MAX_SUGGESTIONS.

Generates a varied document population (with/without outline titles). Oracle = the
union of every outline title + every file name (after strip, dropping empties);
assert the result is a SUBSET of the oracle. Each example uses a NEW in-memory SQLite
(complete isolation).

Reuses the DB-seeding convention from tests/test_query_pipeline_overview.py
(Base.metadata, KhongGianTaiLieu/TaiLieu/TomTatTaiLieu). No network calls, no LLM.
"""

from __future__ import annotations

import string

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import (
    KhongGianTaiLieu,
    TaiKhoan,
    TaiLieu,
    TomTatTaiLieu,
    TrangThaiTaiLieu,
)
from app.pipelines.query_pipeline import MAX_SUGGESTIONS, QueryPipeline


# --- Strategies -------------------------------------------------------------
# File name: always non-empty after strip (with a .txt suffix) — reflects a real document.
_ten_file = st.text(
    alphabet=string.ascii_letters + string.digits + "-_ ", min_size=1, max_size=12
).map(lambda s: s.strip() + ".txt")

# Outline title: varied text (including empty/whitespace/Vietnamese diacritics) to
# cover deduplication + dropping empty titles in buildSuggestions.
_tieu_de = st.text(max_size=20)

# One document: file name + a list of outline titles (may be empty -> use file name).
_tai_lieu = st.fixed_dictionaries(
    {"tenFile": _ten_file, "titles": st.lists(_tieu_de, max_size=5)}
)

# Document population: may be empty (the "reports empty" branch) or many documents.
_quan_the = st.lists(_tai_lieu, max_size=6)


# --- Seed a NEW in-memory DB for each example -------------------------------
def _seed(docs: list[dict]) -> tuple[object, KhongGianTaiLieu, set[str]]:
    """Create a new in-memory SQLite, seed workspace + documents; return (db, kg, oracle).

    Oracle = union {outline title after strip, dropping empties} U {file name after
    strip}. This is the SUPERSET of valid texts buildSuggestions is allowed to return.
    """
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()

    chu = TaiKhoan(email="chu@x.com", tenDangNhap="chu", matKhauHash="h")
    db.add(chu)
    db.flush()
    kg = KhongGianTaiLieu(
        ten="KG",
        moTa="",
        chuSoHuuId=chu.id,
        embeddingProvider="fake",
        collectionName="ws_kg",
    )
    db.add(kg)
    db.flush()

    oracle: set[str] = set()
    for doc in docs:
        tenFile = doc["tenFile"]
        outline = [{"tieuDe": t} for t in doc["titles"]]
        tl = TaiLieu(
            khongGianId=kg.id,
            tenFile=tenFile,
            dinhDang="txt",
            kichThuoc=10,
            trangThai=TrangThaiTaiLieu.DA_EMBED,
            chienLuocChunk="recursive",
            soChunk=1,
        )
        db.add(tl)
        db.flush()
        db.add(TomTatTaiLieu(taiLieuId=tl.id, tomTat="tt", outline=outline))
        # Oracle (superset): file name + every outline title, all after strip, dropping empties.
        if tenFile.strip():
            oracle.add(tenFile.strip())
        for t in doc["titles"]:
            if t.strip():
                oracle.add(t.strip())
    db.commit()
    return db, kg, oracle


@settings(max_examples=120)
@given(docs=_quan_the)
def test_property_42_suggestions_tu_tai_lieu_thuc_hoac_bao_trong(docs):
    db, kg, oracle = _seed(docs)
    try:
        # The pipeline is injected with NO LLM_Provider (R16.5: no LLM call for suggestions).
        pipeline = QueryPipeline(db=db)
        goiY = pipeline.buildSuggestions(kg, taiKhoan=None)

        # Deterministic: calling again must give the same result.
        goiYLai = pipeline.buildSuggestions(kg, taiKhoan=None)
        assert goiY == goiYLai

        if not docs:
            # (a) No documents -> reports empty.
            assert goiY == []
            return

        # (b) With documents: every suggestion is drawn from real document data (a subset
        # of the oracle), no duplicates, count <= MAX_SUGGESTIONS.
        assert set(goiY) <= oracle
        assert len(goiY) == len(set(goiY))
        assert len(goiY) <= MAX_SUGGESTIONS
    finally:
        db.close()
