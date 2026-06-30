"""Unit tests for AutoSelector — fixed priority order (task 7.2, R17.3-17.8).

Checks:
- Điều + a leading article number WINS over markdown headings and any other signal (R17.4).
- Markdown headings WIN over plain text when there is no Điều (R17.5).
- Paginated PDF (form-feed) -> "page" when there is no Điều/heading (R17.6).
- Plain text -> "recursive" (the default, R17.8).
- AutoSelector only returns names that exist; resolveChunker resolves to a real chunker.
- A non-existent strategy name -> ValidationError that names it (R17.7).
"""

from __future__ import annotations

import pytest

from app.chunking import registry as chunk_reg
from app.chunking.auto_selector import AutoSelector
from app.chunking.base import ChunkerBase
from app.errors import ValidationError

# Load the full registry before resolving (R17.1/17.2).
chunk_reg.discover_chunkers()

SELECTOR = AutoSelector()

VAN_BAN_DIEU = (
    "LUAT MAU\n"
    "Điều 1. Pham vi dieu chinh cua van ban nay.\n"
    "Điều 2. Giai thich tu ngu su dung trong luat."
)
VAN_BAN_HEADING = (
    "# Gioi thieu\nNoi dung gioi thieu.\n\n## Chi tiet\nMo ta chi tiet san pham."
)
VAN_BAN_THUONG = "Doan van thuong khong co dau hieu cau truc nao dac biet ca."
VAN_BAN_PHAN_TRANG = "Noi dung trang mot.\fNoi dung trang hai.\fNoi dung trang ba."


def test_dieu_thang_moi_dau_hieu_khac():
    # R17.4: if there is a Điều, pick vietnamese-law REGARDLESS of heading/pagination also present.
    text = (
        "# Tieu de markdown\n"
        "Điều 1. Quy dinh chung ve pham vi.\fTrang hai co them noi dung."
    )
    assert SELECTOR.selectStrategy(text, dinhDang="pdf") == "vietnamese-law"
    assert SELECTOR.selectStrategy(VAN_BAN_DIEU) == "vietnamese-law"


def test_heading_thang_van_ban_thuong():
    # R17.5: no Điều but markdown headings present -> structure-aware.
    assert SELECTOR.selectStrategy(VAN_BAN_HEADING) == "structure-aware"
    # Headings win over pagination when there is no Điều.
    text = "# Tieu de\nNoi dung.\fTrang hai."
    assert SELECTOR.selectStrategy(text, dinhDang="pdf") == "structure-aware"


def test_pdf_phan_trang_chon_page():
    # R17.6: no Điều, no heading, paginated PDF -> page.
    assert SELECTOR.selectStrategy(VAN_BAN_PHAN_TRANG, dinhDang="pdf") == "page"
    # dinhDang None but with form-feed is still treated as paginated.
    assert SELECTOR.selectStrategy(VAN_BAN_PHAN_TRANG) == "page"


def test_dinh_dang_khong_phai_pdf_khong_chon_page():
    # Form-feed but dinhDang is not pdf -> falls back to recursive (the default).
    assert SELECTOR.selectStrategy(VAN_BAN_PHAN_TRANG, dinhDang="txt") == "recursive"


def test_van_ban_thuong_chon_recursive():
    # R17.8: no signal at all -> recursive.
    assert SELECTOR.selectStrategy(VAN_BAN_THUONG) == "recursive"


def test_chap_nhan_doi_tuong_co_noiDung():
    # The signature accepts an object with a noiDung attribute (e.g. a preview).
    class _TaiLieuGia:
        noiDung = VAN_BAN_DIEU

    assert SELECTOR.selectStrategy(_TaiLieuGia()) == "vietnamese-law"


def test_moi_ten_duoc_chon_deu_resolve_duoc():
    # AutoSelector only returns real names -> resolveChunker must resolve all of them.
    for ten in AutoSelector.PRIORITY:
        chunker = SELECTOR.resolveChunker(ten)
        assert isinstance(chunker, ChunkerBase)
        assert chunker.ten == ten


def test_auto_roi_resolve_ra_chunker_thuc():
    # Typical flow: select by text then resolve to a real chunker.
    ten = SELECTOR.selectStrategy(VAN_BAN_DIEU)
    chunker = SELECTOR.resolveChunker(ten)
    chunks = chunker.chunk(VAN_BAN_DIEU)
    assert len(chunks) >= 1


def test_ten_chien_luoc_khong_ton_tai_bi_tu_choi_neu_ro_ten():
    # R17.7: a non-existent name -> ValidationError, the message names it.
    with pytest.raises(ValidationError) as exc:
        SELECTOR.resolveChunker("khong-ton-tai")
    assert "khong-ton-tai" in str(exc.value)
