"""Property-based test for intent classification (R16.6).

# Feature: multi-user-rag-platform, Property 39: Intent classification is deterministic
# — for every question (arbitrary text), QueryPipeline.classifyIntent: (1) is
# DETERMINISTIC (calling it multiple times with the same input -> the same result),
# (2) the result always belongs to the valid set {CHE_DO_TONG_QUAN, CHE_DO_CHI_TIET},
# (3) independent oracle: the result == "tong-quan" IF AND ONLY IF at least one phrase
# in OVERVIEW_KEYWORDS is a substring of _removeDiacritics(cauHoi).lower().

Text is generated from two sources to cover both branches: (a) random concatenation
with overview keywords inserted (both accented and unaccented variants) and (b)
arbitrary text.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.pipelines.query_pipeline import (
    CHE_DO_CHI_TIET,
    CHE_DO_TONG_QUAN,
    OVERVIEW_KEYWORDS,
    QueryPipeline,
    _removeDiacritics,
)

# ACCENTED variants of some keyword phrases (stored unaccented in OVERVIEW_KEYWORDS) to
# verify matching regardless of whether the user types with or without diacritics.
_ACCENTED_KEYWORDS = (
    "tổng quan",
    "tóm tắt",
    "khái quát",
    "tổng thể",
    "mục lục",
    "liệt kê",
    "giới thiệu chung",
)

# "Noisy" text patterns: ASCII + accented Vietnamese + whitespace + punctuation, to create
# both non-matching sentences and sentences that happen to match a keyword.
_TEXT_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz "
    "ăâđêôơưáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụ"
    ".,?!-"
)
_arbitrary_text = st.text(alphabet=_TEXT_ALPHABET, min_size=0, max_size=80)

# Keyword phrases (both the unaccented and accented versions) to insert into the text.
_keyword_piece = st.sampled_from(list(OVERVIEW_KEYWORDS) + list(_ACCENTED_KEYWORDS))


@st.composite
def _cau_hoi(draw):
    """Generate a question: sometimes appending a keyword (with/without diacritics), sometimes arbitrary text."""
    prefix = draw(_arbitrary_text)
    suffix = draw(_arbitrary_text)
    if draw(st.booleans()):
        keyword = draw(_keyword_piece)
        return f"{prefix} {keyword} {suffix}"
    return f"{prefix}{suffix}"


def _oracle_la_tong_quan(cauHoi: str) -> bool:
    """Independent oracle: whether any OVERVIEW_KEYWORDS phrase is in the diacritics-stripped + lowercased text."""
    chuanHoa = _removeDiacritics(cauHoi).lower()
    return any(tuKhoa in chuanHoa for tuKhoa in OVERVIEW_KEYWORDS)


@settings(max_examples=200)
@given(cauHoi=_cau_hoi())
def test_phan_loai_y_dinh_la_xac_dinh(cauHoi):
    pipeline = QueryPipeline()

    ketQua = pipeline.classifyIntent(cauHoi)

    # (1) Deterministic: calling multiple times with the same input -> the same result.
    assert pipeline.classifyIntent(cauHoi) == ketQua
    assert pipeline.classifyIntent(cauHoi) == ketQua

    # (2) The result always belongs to the valid set.
    assert ketQua in {CHE_DO_TONG_QUAN, CHE_DO_CHI_TIET}

    # (3) Independent oracle: tong-quan if and only if an overview keyword phrase is present.
    if _oracle_la_tong_quan(cauHoi):
        assert ketQua == CHE_DO_TONG_QUAN
    else:
        assert ketQua == CHE_DO_CHI_TIET
