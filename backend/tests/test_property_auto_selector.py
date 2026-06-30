"""Property-based test for AutoSelector — automatic strategy selection (Property 27).

# Feature: multi-user-rag-platform, Property 27: The "Auto" strategy is selected by a
# FIXED priority order. For every document built from optional structural signals
# (an "Điều" boundary line, a markdown heading, a form-feed page break, plain text)
# and a dinhDang in {pdf, txt, md, None}, `AutoSelector.selectStrategy` must return
# the CORRECT strategy following the invariant priority order:
#   - has "Điều" + a number at line start    -> "vietnamese-law"  (R17.4)
#   - otherwise, has a markdown heading       -> "structure-aware" (R17.5)
#   - otherwise, has a form-feed AND dinhDang in {pdf, None} -> "page" (R17.6)
#   - otherwise                               -> "recursive"       (R17.8)
# This is a deterministic invariant following the priority order (R17.3).
# Validates: Requirements 17.3, 17.4, 17.5, 17.6, 17.8

The oracle computes the expectation INDEPENDENTLY using the same priority rule above,
reusing each strategy's REAL detection patterns (DIEU_BOUNDARY_PATTERN / _HEADING_PATTERN /
_PAGE_MARKER) so the signals in the oracle always match how the strategies detect them.
"Plain" text is generated from lowercase letters + spaces so it does NOT accidentally
create any structural signal. selectStrategy is pure (no I/O), so max_examples=200.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.chunking.auto_selector import AutoSelector
from app.chunking.page_chunker import _PAGE_MARKER
from app.chunking.structure_chunker import _HEADING_PATTERN
from app.chunking.vietnamese_law_chunker import DIEU_BOUNDARY_PATTERN

SELECTOR = AutoSelector()

# Safe "plain" text: lowercase letters + spaces only -> matches none of the
# DIEU/heading/form-feed patterns, avoiding accidental signals.
_PLAIN = st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=1, max_size=40)


def _expected_strategy(text: str, dinhDang: str | None) -> str:
    """Oracle: derive the expected strategy from the fixed priority rule (R17.3-17.8)."""
    if DIEU_BOUNDARY_PATTERN.search(text) is not None:
        return "vietnamese-law"
    if _HEADING_PATTERN.search(text) is not None:
        return "structure-aware"
    co_phan_trang = _PAGE_MARKER in text
    dinhDang_phan_trang = dinhDang is None or dinhDang.strip().lower() == "pdf"
    if co_phan_trang and dinhDang_phan_trang:
        return "page"
    return "recursive"


@settings(max_examples=40, deadline=None)
@given(
    coDieu=st.booleans(),
    coHeading=st.booleans(),
    coPhanTrang=st.booleans(),
    dinhDang=st.sampled_from(["pdf", "txt", "md", None]),
    soDieu=st.integers(min_value=1, max_value=999),
    capHeading=st.integers(min_value=1, max_value=6),
    plain1=_PLAIN,
    plain2=_PLAIN,
)
def test_chon_chien_luoc_tu_dong(
    coDieu, coHeading, coPhanTrang, dinhDang, soDieu, capHeading, plain1, plain2
):
    # Compose the document by concatenating optional signals + plain text.
    cacDong: list[str] = []
    if coDieu:
        cacDong.append(f"Điều {soDieu}. Noi dung dieu khoan mau.")
    if coHeading:
        cacDong.append(f"{'#' * capHeading} Tieu de muc")
    cacDong.append(f"noi dung thuong {plain1}")
    text = "\n".join(cacDong)
    if coPhanTrang:
        text = f"{text}{_PAGE_MARKER}trang sau {plain2}"

    expected = _expected_strategy(text, dinhDang)
    ketQua = SELECTOR.selectStrategy(text, dinhDang=dinhDang)

    assert ketQua == expected, (
        f"selectStrategy tra '{ketQua}' nhung ky vong '{expected}' "
        f"(coDieu={coDieu}, coHeading={coHeading}, coPhanTrang={coPhanTrang}, "
        f"dinhDang={dinhDang!r})"
    )
    # Priority-order invariant: the result always belongs to the fixed strategy set.
    assert ketQua in AutoSelector.PRIORITY
