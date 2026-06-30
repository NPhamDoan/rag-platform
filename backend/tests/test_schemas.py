"""Unit tests for the Pydantic DTO schemas (task 2.2).

Covers the key validation constraints from design.md + requirements:
- email <=254 + valid format, tenDangNhap 3-30, matKhau 8-64 (R1).
- cauHoi 1-1000 (R6.3); cheDo restricted to the valid set (R16.7-8).
- thresholds in [0,1] with lower<=upper, k + weights valid (R19).
- mucQuyen in {CHI_DOC, GHI} (R11); the verification label uses the NhanXacMinh set (R8.1).
- TrichDan marker >=1; operational limits within the config range (R23).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.models import MucQuyen, NhanXacMinh, TrangThaiTaiLieu
from app.models.schemas import (
    IndexingResult,
    KetQuaTraLoi,
    KhoaApiMasked,
    LimitsInput,
    QueryInput,
    RegisterInput,
    RetrievalConfigInput,
    ShareInput,
    TrichDan,
)


# --- RegisterInput (R1) ----------------------------------------------------
def test_register_hop_le():
    dto = RegisterInput(email="a@x.com", tenDangNhap="userA", matKhau="matkhau123")
    assert dto.email == "a@x.com"
    assert dto.tenDangNhap == "userA"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"email": "khong-co-at", "tenDangNhap": "userA", "matKhau": "matkhau123"},
        {"email": "a@x.com", "tenDangNhap": "ab", "matKhau": "matkhau123"},  # ten < 3
        {"email": "a@x.com", "tenDangNhap": "u" * 31, "matKhau": "matkhau123"},  # ten > 30
        {"email": "a@x.com", "tenDangNhap": "userA", "matKhau": "short"},  # password < 8
        {"email": "a@x.com", "tenDangNhap": "userA", "matKhau": "x" * 65},  # password > 64
        {"email": "x" * 250 + "@y.com", "tenDangNhap": "userA", "matKhau": "matkhau123"},  # email > 254
    ],
)
def test_register_khong_hop_le_bi_tu_choi(kwargs):
    with pytest.raises(ValidationError):
        RegisterInput(**kwargs)


def test_register_cam_field_la():
    with pytest.raises(ValidationError):
        RegisterInput(
            email="a@x.com", tenDangNhap="userA", matKhau="matkhau123", vaiTro="QUAN_TRI"
        )


# --- QueryInput (R6.3, R16.7-8) --------------------------------------------
def test_query_hop_le_mac_dinh_che_do_none():
    dto = QueryInput(cauHoi="Toi muon hoi mot cau")
    assert dto.cheDo is None


@pytest.mark.parametrize("cauHoi", ["", "x" * 1001])
def test_query_do_dai_ngoai_khoang_bi_tu_choi(cauHoi):
    with pytest.raises(ValidationError):
        QueryInput(cauHoi=cauHoi)


def test_query_che_do_ngoai_tap_bi_tu_choi():
    with pytest.raises(ValidationError):
        QueryInput(cauHoi="hoi", cheDo="khong-hop-le")


# --- RetrievalConfigInput (R19) --------------------------------------------
def test_retrieval_config_hop_le():
    dto = RetrievalConfigInput(
        nguongKhongTimThay=0.3,
        nguongDuLienQuan=0.5,
        k=8,
        trongSoVector=0.5,
        trongSoBm25=0.5,
    )
    assert dto.k == 8


def test_retrieval_config_duoi_lon_hon_tren_bi_tu_choi():
    with pytest.raises(ValidationError):
        RetrievalConfigInput(
            nguongKhongTimThay=0.8,
            nguongDuLienQuan=0.5,
            k=8,
            trongSoVector=0.5,
            trongSoBm25=0.5,
        )


@pytest.mark.parametrize(
    "field,gia_tri",
    [
        ("nguongKhongTimThay", -0.1),
        ("nguongDuLienQuan", 1.1),
        ("k", 0),
        ("k", 101),
        ("trongSoVector", 1.5),
    ],
)
def test_retrieval_config_ngoai_range_bi_tu_choi(field, gia_tri):
    base = dict(
        nguongKhongTimThay=0.3,
        nguongDuLienQuan=0.5,
        k=8,
        trongSoVector=0.5,
        trongSoBm25=0.5,
    )
    base[field] = gia_tri
    with pytest.raises(ValidationError):
        RetrievalConfigInput(**base)


# --- ShareInput (R11) ------------------------------------------------------
def test_share_input_muc_quyen_hop_le():
    dto = ShareInput(taiKhoanMucTieuId="id-1", mucQuyen=MucQuyen.GHI)
    assert dto.mucQuyen is MucQuyen.GHI


def test_share_input_muc_quyen_ngoai_tap_bi_tu_choi():
    with pytest.raises(ValidationError):
        ShareInput(taiKhoanMucTieuId="id-1", mucQuyen="CHU_SO_HUU")


# --- KetQuaTraLoi / TrichDan (R7.5, R8.1) ----------------------------------
def test_ket_qua_tra_loi_hop_le():
    dto = KetQuaTraLoi(
        traLoi="Cau tra loi [1]",
        trichDan=[TrichDan(marker=1, chunkId="c1", taiLieuId="t1", noiDung="noi dung")],
        nhanXacMinh=NhanXacMinh.DA_XAC_MINH,
    )
    assert dto.trichDan[0].marker == 1
    assert dto.laFallback is False
    assert dto.laTongQuan is False


def test_trich_dan_marker_phai_lon_hon_0():
    with pytest.raises(ValidationError):
        TrichDan(marker=0, chunkId="c1", taiLieuId="t1", noiDung="x")


def test_ket_qua_tra_loi_nhan_xac_minh_ngoai_tap_bi_tu_choi():
    with pytest.raises(ValidationError):
        KetQuaTraLoi(traLoi="x", nhanXacMinh="khong-ro")


# --- IndexingResult / KhoaApiMasked / LimitsInput --------------------------
def test_indexing_result_trang_thai_enum():
    dto = IndexingResult(taiLieuId="t1", soChunk=3, trangThai=TrangThaiTaiLieu.DA_EMBED)
    assert dto.trangThai is TrangThaiTaiLieu.DA_EMBED


def test_khoa_api_masked_chi_co_truong_che():
    dto = KhoaApiMasked(providerTen="groq", vaiTro="synthesis", khoaChe="sk-***")
    assert dto.khoaChe == "sk-***"


def test_limits_input_hop_le():
    dto = LimitsInput(llmTimeout=30, sessionTtl=60, maxFileSize=50)
    assert dto.maxFileSize == 50


@pytest.mark.parametrize(
    "field,gia_tri",
    [
        ("llmTimeout", 1),     # < LLM_TIMEOUT_MIN (5)
        ("llmTimeout", 1000),  # > LLM_TIMEOUT_MAX (300)
        ("sessionTtl", 1),     # < SESSION_TTL_MIN (5)
        ("maxFileSize", 0),    # < MAX_FILE_SIZE_MB_MIN (1)
        ("maxFileSize", 2048), # > MAX_FILE_SIZE_MB_MAX (1024)
    ],
)
def test_limits_input_ngoai_range_bi_tu_choi(field, gia_tri):
    base = dict(llmTimeout=30, sessionTtl=60, maxFileSize=50)
    base[field] = gia_tri
    with pytest.raises(ValidationError):
        LimitsInput(**base)
