"""Property test (task 8.9) for DocumentPipeline.setBoundaryRules + rechunk.

# Feature: multi-user-rag-platform, Property 26: QuyTacRanhGioi is applied when rechunking
# Validates: Requirements 18.4

Property under test (R18.4):
- After `setBoundaryRules` + `rechunk`, the chunking strategy (chunker) receives EXACTLY
  the most recently declared rule set: the same `tuKhoaHoacMau` set (compared as a
  multiset) and the same count.
- `setBoundaryRules` REPLACES the entire previous rule set (declaring a new set leaves
  only the newest set in the RDB, and that is the set passed down to the chunker).

Uses a SpyChunker (following the pattern in test_document_pipeline_rechunk.py): a fake
strategy that records the `rules` it receives, monkeypatching `get_chunker` in the
document_pipeline module. Each Hypothesis example uses its own in-memory SQLite session;
the account/space/document are created directly.
"""

from __future__ import annotations

import dataclasses
from collections import Counter

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.pipelines.document_pipeline as dp
from app.chunking.base import ChunkData, ChunkParams
from app.db.database import Base
from app.db.models import (
    HanMuc,
    KhongGianTaiLieu,
    QuyTacRanhGioi,
    TaiKhoan,
    TaiLieu,
    TrangThaiTaiLieu,
)
from app.pipelines.document_pipeline import DocumentPipeline


# --- Spy chunker: records the rules it receives ----------------------------
class _SpyChunker:
    """Fake strategy: records `rules` into a shared list; returns a single chunk covering everything."""

    captured: list = []

    def chunk(self, text, thamSo=None, rules=None):
        _SpyChunker.captured.append(rules)
        return [ChunkData(thuTu=0, viTriBatDau=0, viTriKetThuc=len(text), noiDung=text)]


# --- Strategies -------------------------------------------------------------
# tuKhoaHoacMau: non-empty string, avoiding surrogates (SQLite rejects them).
_keyword = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), min_codepoint=32, max_codepoint=0x2FFF),
    min_size=1,
    max_size=40,
)
# dieuKien: optional dict (may be absent).
_dieuKien = st.dictionaries(
    keys=st.text(min_size=1, max_size=10),
    values=st.one_of(st.text(max_size=10), st.integers()),
    max_size=3,
)


def _rule_strategy() -> st.SearchStrategy[dict]:
    def _make(kw: str, dk):
        r = {"tuKhoaHoacMau": kw}
        if dk is not None:
            r["dieuKien"] = dk
        return r

    return st.builds(_make, _keyword, st.one_of(st.none(), _dieuKien))


_rule_list = st.lists(_rule_strategy(), min_size=0, max_size=6)


# --- Helpers ----------------------------------------------------------------
def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine, Session()


def _seed_doc(db):
    """Create the account (owner) + space + document directly, ready to rechunk."""
    chu = TaiKhoan(email="chu@x.com", tenDangNhap="chu", matKhauHash="h")
    chu.hanMuc = HanMuc()
    db.add(chu)
    db.commit()

    kg = KhongGianTaiLieu(
        ten="KG",
        moTa="",
        chuSoHuuId=chu.id,
        embeddingProvider="huggingface",
        collectionName="ws_tmp",
    )
    db.add(kg)
    db.flush()
    kg.collectionName = f"ws_{kg.id}"

    taiLieu = TaiLieu(
        khongGianId=kg.id,
        tenFile="a.txt",
        dinhDang="txt",
        kichThuoc=10,
        vanBanGoc="Noi dung tai lieu de cat lai.",
        trangThai=TrangThaiTaiLieu.DA_PARSE_CHO_DUYET,
        chienLuocChunk="spy",
        thamSoChunk=dataclasses.asdict(ChunkParams()),
        soChunk=0,
    )
    db.add(taiLieu)
    db.commit()
    return chu, taiLieu


# --- Property 26 ------------------------------------------------------------
@given(priorRules=_rule_list, latestRules=_rule_list)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property_quy_tac_ranh_gioi_ap_dung_khi_cat_lai(priorRules, latestRules):
    # Feature: multi-user-rag-platform, Property 26: QuyTacRanhGioi is applied when rechunking
    # Validates: Requirements 18.4
    engine, db = _make_session()
    goc_get_chunker = dp.get_chunker
    try:
        chu, taiLieu = _seed_doc(db)
        pipeline = DocumentPipeline(db, embeddingProvider=None)

        # Declare the old set then the new set -> the new set must REPLACE the old one.
        pipeline.setBoundaryRules(chu, "document", taiLieu.id, priorRules)
        pipeline.setBoundaryRules(chu, "document", taiLieu.id, latestRules)

        # The RDB keeps only the newest set (old rules fully replaced).
        daLuu = db.query(QuyTacRanhGioi).filter(
            QuyTacRanhGioi.phamViId == taiLieu.id
        ).all()
        assert len(daLuu) == len(latestRules)
        assert Counter(q.tuKhoaHoacMau for q in daLuu) == Counter(
            r["tuKhoaHoacMau"] for r in latestRules
        )

        # Rechunk with the SpyChunker -> the chunker receives EXACTLY the newest rule set.
        _SpyChunker.captured = []
        dp.get_chunker = lambda ten: _SpyChunker
        pipeline.rechunk(chu, taiLieu.id, chienLuocChunk="spy")

        assert len(_SpyChunker.captured) == 1
        received = _SpyChunker.captured[0]
        assert received is not None
        # Correct count + correct tuKhoaHoacMau multiset (order independent).
        assert len(received) == len(latestRules)
        assert Counter(q.tuKhoaHoacMau for q in received) == Counter(
            r["tuKhoaHoacMau"] for r in latestRules
        )
    finally:
        dp.get_chunker = goc_get_chunker
        db.close()
        engine.dispose()
