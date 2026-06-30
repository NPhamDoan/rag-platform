"""Unit tests for selecting a ChienLuocChunk by CONFIGURATION (task 7.5, R17.1 + R17.7).

Distinct from the "auto" flow (AutoSelector, already tested in `test_auto_selector.py`):
here the user EXPLICITLY specifies a strategy name via configuration (e.g. "recursive",
"vietnamese-law"), and `get_chunker(ten)` returns exactly the registered strategy class →
ADDING/CHANGING a strategy only requires editing configuration, NOT code (R17.1, R17.2).

Additional coverage (not overlapping `test_registry.py`/`test_chunkers.py`/`test_auto_selector.py`):
- get_chunker(config name) → the CORRECT registered class for EACH real strategy, and the
  instance can chunk text into >= 1 non-empty chunk (R17.1).
- A mistyped / non-existent config name → ValidationError that names the wrong strategy,
  along with the list of valid strategies in the details (R17.7).
"""

from __future__ import annotations

import pytest

from app.chunking import registry as chunk_reg
from app.chunking.base import ChunkerBase
from app.chunking.recursive_chunker import RecursiveChunker
from app.chunking.structure_chunker import StructureChunker
from app.chunking.page_chunker import PageChunker
from app.chunking.semantic_chunker import SemanticChunker
from app.chunking.vietnamese_law_chunker import VietnameseLawChunker
from app.errors import ValidationError

# Load the full registry before looking up by configuration (R17.2).
chunk_reg.discover_chunkers()

# Map config name -> registered class (the source of truth for cross-checking get_chunker).
TEN_CAU_HINH_DEN_CLASS = {
    "recursive": RecursiveChunker,
    "structure-aware": StructureChunker,
    "page": PageChunker,
    "semantic": SemanticChunker,
    "vietnamese-law": VietnameseLawChunker,
}

# Cross-domain text (not law) so every strategy chunks it into >= 1 chunk.
VAN_BAN = (
    "Huong dan su dung san pham. Buoc mot la cam nguon va bat cong tac.\n\n"
    "Buoc hai la chon che do phu hop voi nhu cau su dung.\n\n"
    "Buoc ba la ve sinh thiet bi sau khi dung xong."
)


@pytest.mark.parametrize("ten, expected_cls", list(TEN_CAU_HINH_DEN_CLASS.items()))
def test_ten_cau_hinh_phan_giai_dung_class_da_dang_ky(ten, expected_cls):
    # R17.1: configuration specifies a strategy name -> get_chunker returns the CORRECT registered class.
    cls = chunk_reg.get_chunker(ten)
    assert cls is expected_cls
    assert cls.ten == ten


@pytest.mark.parametrize("ten", list(TEN_CAU_HINH_DEN_CLASS))
def test_ten_cau_hinh_tao_chunker_cat_duoc_van_ban(ten):
    # R17.1: the configured strategy must chunk text into >= 1 non-empty chunk.
    chunker = chunk_reg.get_chunker(ten)()
    assert isinstance(chunker, ChunkerBase)
    chunks = chunker.chunk(VAN_BAN)
    assert len(chunks) >= 1
    assert all(c.noiDung.strip() for c in chunks)


@pytest.mark.parametrize("ten_co_khoang_trang", [" recursive", "vietnamese-law "])
def test_ten_cau_hinh_duoc_chuan_hoa_khoang_trang(ten_co_khoang_trang):
    # Configuration may include surrounding whitespace -> get_chunker normalizes before lookup.
    expected = TEN_CAU_HINH_DEN_CLASS[ten_co_khoang_trang.strip()]
    assert chunk_reg.get_chunker(ten_co_khoang_trang) is expected


@pytest.mark.parametrize(
    "ten_sai",
    ["recursiv", "vietnamese_law", "structure", "auto", "khong-ton-tai"],
)
def test_ten_cau_hinh_sai_bi_tu_choi_neu_ro_ten(ten_sai):
    # R17.7: a mistyped / non-existent config name -> ValidationError that names the wrong strategy.
    with pytest.raises(ValidationError) as exc:
        chunk_reg.get_chunker(ten_sai)
    assert ten_sai in exc.value.message


def test_loi_cau_hinh_sai_kem_danh_sach_chien_luoc_hop_le():
    # R17.7: the error message includes the list of valid strategies so the user can fix the config.
    with pytest.raises(ValidationError) as exc:
        chunk_reg.get_chunker("typo-strategy")
    available = exc.value.details.get("available")
    assert available is not None
    assert set(TEN_CAU_HINH_DEN_CLASS).issubset(set(available))
