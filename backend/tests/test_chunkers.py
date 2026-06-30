"""Unit tests for ChunkerBase + the 5 ChienLuocChunk strategies (task 7.1, R15.1-15.2, R17.1).

Coverage for EACH strategy (recursive, structure-aware, page, semantic,
vietnamese-law):
- Non-empty cross-domain text → produces >= 1 ChunkData, each with non-empty noiDung
  (R15.1, R15.2).
- Empty / whitespace-only text → 0 chunks.
- vietnamese-law splits on Điều boundaries; structure-aware splits on headings.
- Positions (viTriBatDau/viTriKetThuc) match noiDung against the original text.
- discover_chunkers loads all 5 strategies into the registry (R17.1).
"""

from __future__ import annotations

import pytest

from app.chunking import registry as chunk_reg
from app.chunking.base import ChunkData, ChunkParams
from app.chunking.recursive_chunker import RecursiveChunker
from app.chunking.structure_chunker import StructureChunker
from app.chunking.page_chunker import PageChunker
from app.chunking.semantic_chunker import SemanticChunker
from app.chunking.vietnamese_law_chunker import VietnameseLawChunker

# Small parameters so the sample text still gets split into multiple chunks in tests.
THAM_SO_NHO = ChunkParams(kichThuocMucTieu=120, doChongLan=20)

# Cross-domain text (not law) to test general behavior.
VAN_BAN_DA_LINH_VUC = (
    "Huong dan nau pho bo. Buoc mot la ninh xuong trong nhieu gio de lay nuoc dung "
    "ngot thanh.\n\n"
    "Buoc hai la chan banh pho va xep thit bo tai len tren.\n\n"
    "Buoc ba la chan nuoc dung dang soi va them hanh ngo cho thom."
)

MOI_CHIEN_LUOC = [
    RecursiveChunker(),
    StructureChunker(),
    PageChunker(),
    SemanticChunker(),
    VietnameseLawChunker(),
]


def _kiem_bat_bien(chunks: list[ChunkData], text: str) -> None:
    """Each chunk has non-empty noiDung and a position matching the original text."""
    assert len(chunks) >= 1
    for i, c in enumerate(chunks):
        assert c.noiDung.strip(), "noiDung khong duoc rong (R15.2)"
        assert c.thuTu == i, "thuTu phai lien tuc tu 0"
        assert text[c.viTriBatDau:c.viTriKetThuc] == c.noiDung, "vi tri phai khop"


@pytest.mark.parametrize("chunker", MOI_CHIEN_LUOC, ids=lambda c: c.ten)
def test_van_ban_khong_rong_tao_it_nhat_mot_chunk(chunker):
    # R15.1: each strategy applied to a cross-domain document → >= 1 non-empty chunk.
    chunks = chunker.chunk(VAN_BAN_DA_LINH_VUC, THAM_SO_NHO)
    _kiem_bat_bien(chunks, VAN_BAN_DA_LINH_VUC)


@pytest.mark.parametrize("chunker", MOI_CHIEN_LUOC, ids=lambda c: c.ten)
@pytest.mark.parametrize("text", ["", "   ", "\n\n\t  \n"])
def test_van_ban_rong_tra_ve_khong_chunk(chunker, text):
    # R15.2 / pipeline: empty or whitespace-only text → 0 chunks.
    assert chunker.chunk(text, THAM_SO_NHO) == []


def test_recursive_cat_van_ban_dai_thanh_nhieu_chunk():
    text = "Cau van mau so mot. " * 50
    chunks = RecursiveChunker().chunk(text, THAM_SO_NHO)
    assert len(chunks) >= 2
    _kiem_bat_bien(chunks, text)


def test_structure_aware_cat_theo_heading():
    text = (
        "# Gioi thieu\nNoi dung phan gioi thieu ve san pham.\n\n"
        "## Tinh nang\nMo ta cac tinh nang chinh cua san pham.\n\n"
        "## Gia ban\nThong tin ve gia va chinh sach khuyen mai."
    )
    chunks = StructureChunker().chunk(text)
    _kiem_bat_bien(chunks, text)
    # 3 headings → 3 sections.
    assert len(chunks) == 3
    tieu_de = [c.metadata.get("tieuDe") for c in chunks]
    assert tieu_de == ["Gioi thieu", "Tinh nang", "Gia ban"]


def test_structure_aware_khong_heading_van_co_chunk():
    text = "Doan van thuong khong co heading markdown nao ca."
    chunks = StructureChunker().chunk(text)
    _kiem_bat_bien(chunks, text)


def test_page_cat_theo_dau_phan_trang():
    text = "Noi dung trang mot.\fNoi dung trang hai.\fNoi dung trang ba."
    chunks = PageChunker().chunk(text)
    _kiem_bat_bien(chunks, text)
    assert len(chunks) == 3
    assert [c.metadata.get("trang") for c in chunks] == [1, 2, 3]


def test_page_khong_phan_trang_van_co_chunk():
    text = "Van ban khong he co dau phan trang form-feed nao."
    chunks = PageChunker().chunk(text)
    _kiem_bat_bien(chunks, text)


def test_semantic_gom_doan_van():
    chunks = SemanticChunker().chunk(VAN_BAN_DA_LINH_VUC, THAM_SO_NHO)
    _kiem_bat_bien(chunks, VAN_BAN_DA_LINH_VUC)


def test_vietnamese_law_cat_theo_dieu():
    text = (
        "LUAT MAU\n"
        "Điều 1. Pham vi dieu chinh cua van ban nay.\n"
        "Quy dinh chung ve doi tuong ap dung.\n"
        "Điều 2. Giai thich tu ngu su dung trong luat.\n"
        "Cac thuat ngu duoc dinh nghia ro rang.\n"
        "Điều 3: Nguyen tac thuc hien va to chuc.\n"
        "Noi dung chi tiet cua dieu ba."
    )
    chunks = VietnameseLawChunker().chunk(text)
    _kiem_bat_bien(chunks, text)
    so_dieu = [c.metadata.get("dieu") for c in chunks if "dieu" in c.metadata]
    assert so_dieu == [1, 2, 3]


def test_vietnamese_law_khong_cat_nham_tham_chieu():
    # "Điều 5" without a ./: after the number → not a boundary → 1 chunk.
    text = "Theo quy dinh tai Điều 5 cua nghi dinh, hanh vi nay bi xu phat."
    chunks = VietnameseLawChunker().chunk(text)
    assert len(chunks) == 1
    assert "dieu" not in chunks[0].metadata


def test_vietnamese_law_khong_co_dieu_fallback_size():
    text = "Thong tu huong dan thi hanh, khong co dieu khoan danh so. " * 5
    chunks = VietnameseLawChunker().chunk(text, THAM_SO_NHO)
    _kiem_bat_bien(chunks, text)


def test_discover_nap_day_du_nam_chien_luoc():
    # R17.1: discover_chunkers loads every `*_chunker.py` into the registry.
    chunk_reg.discover_chunkers()
    for ten in ["recursive", "structure-aware", "page", "semantic", "vietnamese-law"]:
        assert ten in chunk_reg.CHUNKER_REGISTRY, f"thieu chien luoc '{ten}'"


def test_chunk_params_tu_dict():
    p = ChunkParams.from_any({"kichThuocMucTieu": 500, "doChongLan": 50})
    assert p.kichThuocMucTieu == 500 and p.doChongLan == 50


def test_chunk_params_overlap_khong_hop_le_bi_tu_choi():
    with pytest.raises(ValueError):
        ChunkParams(kichThuocMucTieu=100, doChongLan=100)
