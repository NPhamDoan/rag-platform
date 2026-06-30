"""Unit tests for answerOverview + buildSuggestions + applying MauPrompt (task 10.15).

Coverage (R16.1/16.2/16.3, R15.4/15.5/16.5, R20.1/20.2/20.3):
- `answerOverview`: synthesizes from TomTatTaiLieu + outline, with >=1 TrichDan and
  `laTongQuan=True`; no documents -> "no documents yet" message, NO LLM call, empty
  TrichDan.
- `buildSuggestions`: draws suggestions from the real document outline; no documents
  -> empty list, NO LLM call.
- Applying MauPrompt: the synthesis-role MauPrompt in the db is used as the BASE
  prompt but INVARIANT_SAFETY_CONSTRAINTS is ALWAYS appended (cannot be overridden).

Uses a FAKE LLM_Provider (injected via the constructor) + an in-memory SQLite DB
seeded with TomTat/outline per project conventions — no network calls.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import (
    KhongGianTaiLieu,
    MauPrompt,
    TaiKhoan,
    TaiLieu,
    TomTatTaiLieu,
    TrangThaiTaiLieu,
)
from app.pipelines.query_pipeline import KHONG_CO_TAI_LIEU, QueryPipeline
from app.prompts.system_prompts import INVARIANT_SAFETY_CONSTRAINTS


# --- Fakes ------------------------------------------------------------------
class FakeLLM:
    """Fake LLM_Provider: returns the fixed `phanHoi`; records the system prompt + counts calls."""

    ten = "fake-llm"

    def __init__(self, phanHoi: str) -> None:
        self.phanHoi = phanHoi
        self.systemPromptDaNhan: str | None = None
        self.soLanGoi = 0

    def generate(self, systemPrompt: str, userPrompt: str) -> str:
        self.soLanGoi += 1
        self.systemPromptDaNhan = systemPrompt
        return self.phanHoi


# --- Fixtures DB ------------------------------------------------------------
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


def _tao_khong_gian(session) -> KhongGianTaiLieu:
    chu = TaiKhoan(email="chu@x.com", tenDangNhap="chu", matKhauHash="h")
    session.add(chu)
    session.flush()
    kg = KhongGianTaiLieu(
        ten="KG",
        moTa="",
        chuSoHuuId=chu.id,
        embeddingProvider="fake",
        collectionName="ws_kg",
    )
    session.add(kg)
    session.flush()
    return kg


def _them_tai_lieu(session, kg, tenFile, tomTat, outline) -> TaiLieu:
    tl = TaiLieu(
        khongGianId=kg.id,
        tenFile=tenFile,
        dinhDang="txt",
        kichThuoc=10,
        trangThai=TrangThaiTaiLieu.DA_EMBED,
        chienLuocChunk="recursive",
        soChunk=1,
    )
    session.add(tl)
    session.flush()
    session.add(TomTatTaiLieu(taiLieuId=tl.id, tomTat=tomTat, outline=outline))
    session.commit()
    return tl


# --- answerOverview: with documents -> >=1 citation + laTongQuan (R16.1/16.2) -
class TestAnswerOverviewCoTaiLieu:
    def test_tong_quan_kem_trich_dan_va_la_tong_quan(self, session):
        kg = _tao_khong_gian(session)
        _them_tai_lieu(
            session,
            kg,
            "luat-a.txt",
            "Tom tat tai lieu A",
            [{"tieuDe": "Chuong 1", "viTri": 0}, {"tieuDe": "Chuong 2", "viTri": 10}],
        )
        _them_tai_lieu(session, kg, "luat-b.txt", "Tom tat tai lieu B", [])

        synthesis = FakeLLM("Tong quan: noi dung A [1] va noi dung B [2].")
        pipeline = QueryPipeline(db=session, synthesisProvider=synthesis)
        kq = pipeline.answerOverview(kg, taiKhoan=None, cauHoi="tong quan tai lieu?")

        assert kq.laTongQuan is True
        assert kq.laFallback is False
        assert len(kq.trichDan) >= 1
        assert [td.marker for td in kq.trichDan] == [1, 2]
        assert synthesis.soLanGoi == 1

    def test_llm_khong_marker_van_dam_bao_mot_trich_dan(self, session):
        kg = _tao_khong_gian(session)
        tl = _them_tai_lieu(session, kg, "luat-a.txt", "Tom tat A", [])

        synthesis = FakeLLM("Tong quan khong co marker nao.")
        pipeline = QueryPipeline(db=session, synthesisProvider=synthesis)
        kq = pipeline.answerOverview(kg, taiKhoan=None, cauHoi="tong quan?")

        # R16.2: ensure >=1 citation pointing to a source (the first document).
        assert len(kq.trichDan) == 1
        assert kq.trichDan[0].taiLieuId == tl.id
        assert kq.laTongQuan is True


# --- answerOverview: no documents -> report empty, NO LLM call (R16.3) -----
class TestAnswerOverviewKhongTaiLieu:
    def test_khong_tai_lieu_bao_chua_co_va_khong_goi_llm(self, session):
        kg = _tao_khong_gian(session)
        synthesis = FakeLLM("khong duoc goi")
        pipeline = QueryPipeline(db=session, synthesisProvider=synthesis)
        kq = pipeline.answerOverview(kg, taiKhoan=None, cauHoi="tong quan?")

        assert kq.traLoi == KHONG_CO_TAI_LIEU
        assert kq.trichDan == []
        assert kq.laTongQuan is True
        assert synthesis.soLanGoi == 0  # NO LLM call


# --- buildSuggestions: from real outline / report empty (R15.4/15.5/16.5) -------
class TestBuildSuggestions:
    def test_goi_y_tu_outline_tai_lieu_thuc(self, session):
        kg = _tao_khong_gian(session)
        _them_tai_lieu(
            session,
            kg,
            "luat-a.txt",
            "Tom tat A",
            [{"tieuDe": "Quy dinh chung"}, {"tieuDe": "Xu phat"}],
        )
        _them_tai_lieu(session, kg, "phu-luc.txt", "Tom tat phu luc", [])

        pipeline = QueryPipeline(db=session)
        goiY = pipeline.buildSuggestions(kg, taiKhoan=None)

        # Suggestions drawn from real outline titles + file names (documents without outline).
        assert "Quy dinh chung" in goiY
        assert "Xu phat" in goiY
        assert "phu-luc.txt" in goiY

    def test_khong_tai_lieu_tra_danh_sach_rong(self, session):
        kg = _tao_khong_gian(session)
        pipeline = QueryPipeline(db=session)
        assert pipeline.buildSuggestions(kg, taiKhoan=None) == []

    def test_khu_trung_lap_giu_thu_tu(self, session):
        kg = _tao_khong_gian(session)
        _them_tai_lieu(session, kg, "a.txt", "TT A", [{"tieuDe": "Chung"}])
        _them_tai_lieu(session, kg, "b.txt", "TT B", [{"tieuDe": "Chung"}])

        pipeline = QueryPipeline(db=session)
        goiY = pipeline.buildSuggestions(kg, taiKhoan=None)
        assert goiY.count("Chung") == 1  # duplicate removed


# --- Apply MauPrompt per role + keep the INVARIANT immutable (R20.1/20.2/20.3) --
class TestMauPromptOverride:
    def test_mau_prompt_db_lam_base_va_giu_invariant_overview(self, session):
        kg = _tao_khong_gian(session)
        _them_tai_lieu(session, kg, "a.txt", "Tom tat A", [])
        session.add(
            MauPrompt(vaiTro="synthesis", noiDung="MAU PROMPT QUAN TRI", isDefault=False)
        )
        session.commit()

        synthesis = FakeLLM("Tong quan [1].")
        pipeline = QueryPipeline(db=session, synthesisProvider=synthesis)
        pipeline.answerOverview(kg, taiKhoan=None, cauHoi="tong quan?")

        # The db MauPrompt is the base but the INVARIANT is still appended (cannot be overridden).
        assert "MAU PROMPT QUAN TRI" in synthesis.systemPromptDaNhan
        assert INVARIANT_SAFETY_CONSTRAINTS in synthesis.systemPromptDaNhan

    def test_mau_prompt_db_dung_cho_synthesize(self, session):
        from app.storage.vector_store import META_TAI_LIEU_ID, SearchResult

        session.add(
            MauPrompt(vaiTro="synthesis", noiDung="BASE TU DB", isDefault=False)
        )
        session.commit()

        synthesis = FakeLLM("Tra loi [1].")
        pipeline = QueryPipeline(db=session, synthesisProvider=synthesis)
        chunks = [
            SearchResult("c1", "noi dung", {META_TAI_LIEU_ID: "tl-1"}, score=0.9)
        ]
        pipeline.synthesize("cau hoi?", chunks)

        assert "BASE TU DB" in synthesis.systemPromptDaNhan
        assert INVARIANT_SAFETY_CONSTRAINTS in synthesis.systemPromptDaNhan

    def test_khong_mau_prompt_db_dung_mac_dinh(self, session):
        from app.prompts.system_prompts import SYNTHESIS_SYSTEM_PROMPT
        from app.storage.vector_store import META_TAI_LIEU_ID, SearchResult

        synthesis = FakeLLM("Tra loi [1].")
        pipeline = QueryPipeline(db=session, synthesisProvider=synthesis)
        chunks = [
            SearchResult("c1", "noi dung", {META_TAI_LIEU_ID: "tl-1"}, score=0.9)
        ]
        pipeline.synthesize("cau hoi?", chunks)

        assert SYNTHESIS_SYSTEM_PROMPT in synthesis.systemPromptDaNhan
        assert INVARIANT_SAFETY_CONSTRAINTS in synthesis.systemPromptDaNhan


# --- answerOverview: synthesis error -> fallback to sources (R16.4) ----------------
class TestAnswerOverviewFallback:
    def test_thieu_synthesis_provider_fallback_van_la_tong_quan(self, session):
        kg = _tao_khong_gian(session)
        _them_tai_lieu(session, kg, "a.txt", "Tom tat A", [])

        pipeline = QueryPipeline(db=session, synthesisProvider=None)
        kq = pipeline.answerOverview(kg, taiKhoan=None, cauHoi="tong quan?")

        assert kq.laFallback is True
        assert kq.laTongQuan is True
        assert len(kq.trichDan) >= 1
