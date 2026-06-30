"""Unit tests for auth/crypto.py + ApiKeyService (task 9.1, R22.1-22.7).

Coverage:
- crypto: encrypt round-trip (decrypt(encrypt(x)) == x) and ciphertext != plaintext.
- setApiKey: create new + upsert (update the ciphertext, do not create a second record).
- getApiKey: returns one's own key; None when not configured; does NOT return another
  account's key (isolation, R22.5).
- getMaskedKeys: only returns the masked form, never exposes plaintext (R22.3); isolated.
- deleteApiKey: idempotent; does not delete another account's key (R22.5).
- resolveKey: prefers the user key → system key (env); missing both → a clear error,
  does NOT call the provider (R22.6, R22.7).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.crypto import decryptSecret, encryptSecret
from app.db.database import Base
from app.db.models import KhoaApiNguoiDung, TaiKhoan
from app.errors import ValidationError
from app.services.api_key_service import ApiKeyService, _systemKeyEnvName


@pytest.fixture()
def session():
    """In-memory SQLite session with schema created from Base.metadata."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def service(session):
    return ApiKeyService(session)


def _tao_tai_khoan(session, email="a@x.com", ten="a") -> TaiKhoan:
    tk = TaiKhoan(email=email, tenDangNhap=ten, matKhauHash="h")
    session.add(tk)
    session.commit()
    return tk


# --- crypto round-trip ------------------------------------------------------
@pytest.mark.parametrize(
    "plaintext",
    ["sk-test-1234567890", "a", "  khoa co khoang trang  ", "Khóa-Việt-é"],
)
def test_crypto_round_trip(plaintext):
    # Deterministic round-trip: decrypting the ciphertext == the original plaintext.
    assert decryptSecret(encryptSecret(plaintext)) == plaintext


def test_crypto_khong_luu_plaintext():
    # The ciphertext of a real key does not contain the plaintext string (not stored raw).
    plaintext = "sk-secret-key-9876543210"
    ciphertext = encryptSecret(plaintext)
    assert plaintext.encode("utf-8") not in ciphertext


# --- setApiKey + getApiKey --------------------------------------------------
def test_set_api_key_tao_moi_va_get_tra_khoa(service, session):
    tk = _tao_tai_khoan(session)
    service.setApiKey(tk, "groq", "synthesis", "sk-abc-123")

    assert service.getApiKey(tk, "groq", "synthesis") == "sk-abc-123"
    # Stored in encrypted form (khoaMaHoa != plaintext).
    banGhi = session.query(KhoaApiNguoiDung).one()
    assert banGhi.khoaMaHoa != b"sk-abc-123"


def test_set_api_key_upsert_cap_nhat_khong_tao_ban_ghi_moi(service, session):
    tk = _tao_tai_khoan(session)
    service.setApiKey(tk, "groq", "synthesis", "sk-cu")
    service.setApiKey(tk, "groq", "synthesis", "sk-moi")

    assert service.getApiKey(tk, "groq", "synthesis") == "sk-moi"
    assert session.query(KhoaApiNguoiDung).count() == 1


def test_get_api_key_chua_cau_hinh_tra_none(service, session):
    tk = _tao_tai_khoan(session)
    assert service.getApiKey(tk, "groq", "synthesis") is None


def test_get_api_key_co_lap_giua_tai_khoan(service, session):
    a = _tao_tai_khoan(session, "a@x.com", "a")
    b = _tao_tai_khoan(session, "b@x.com", "b")
    service.setApiKey(a, "groq", "synthesis", "sk-cua-A")

    # B has nothing configured → None; A's key is never returned to B (R22.5).
    assert service.getApiKey(b, "groq", "synthesis") is None
    assert service.getApiKey(a, "groq", "synthesis") == "sk-cua-A"


# --- getMaskedKeys ----------------------------------------------------------
def test_get_masked_keys_chi_tra_dang_che(service, session):
    tk = _tao_tai_khoan(session)
    service.setApiKey(tk, "groq", "synthesis", "sk-secret-7890")

    masked = service.getMaskedKeys(tk)
    assert len(masked) == 1
    item = masked[0]
    assert item.providerTen == "groq"
    assert item.vaiTro == "synthesis"
    # Does not expose plaintext; only the last 4 characters.
    assert item.khoaChe == "****7890"
    assert "sk-secret" not in item.khoaChe


def test_get_masked_keys_khoa_ngan_che_toan_bo(service, session):
    tk = _tao_tai_khoan(session)
    service.setApiKey(tk, "groq", "synthesis", "abcd")
    masked = service.getMaskedKeys(tk)
    assert masked[0].khoaChe == "****"


def test_get_masked_keys_co_lap_giua_tai_khoan(service, session):
    a = _tao_tai_khoan(session, "a@x.com", "a")
    b = _tao_tai_khoan(session, "b@x.com", "b")
    service.setApiKey(a, "groq", "synthesis", "sk-cua-A-1234")

    # B does not see A's key (R22.5).
    assert service.getMaskedKeys(b) == []
    assert len(service.getMaskedKeys(a)) == 1


# --- deleteApiKey -----------------------------------------------------------
def test_delete_api_key_xoa_ban_ghi(service, session):
    tk = _tao_tai_khoan(session)
    service.setApiKey(tk, "groq", "synthesis", "sk-x")
    service.deleteApiKey(tk, "groq", "synthesis")

    assert service.getApiKey(tk, "groq", "synthesis") is None
    assert session.query(KhoaApiNguoiDung).count() == 0


def test_delete_api_key_idempotent(service, session):
    tk = _tao_tai_khoan(session)
    # Delete when nothing is configured → no error (idempotent).
    service.deleteApiKey(tk, "groq", "synthesis")
    assert session.query(KhoaApiNguoiDung).count() == 0


def test_delete_api_key_khong_xoa_khoa_tai_khoan_khac(service, session):
    a = _tao_tai_khoan(session, "a@x.com", "a")
    b = _tao_tai_khoan(session, "b@x.com", "b")
    service.setApiKey(a, "groq", "synthesis", "sk-cua-A")

    # B tries to delete the same (provider, vaiTro) → does not touch A's key (R22.5).
    service.deleteApiKey(b, "groq", "synthesis")
    assert service.getApiKey(a, "groq", "synthesis") == "sk-cua-A"


# --- resolveKey -------------------------------------------------------------
def test_resolve_key_uu_tien_khoa_nguoi_dung(service, session, monkeypatch):
    tk = _tao_tai_khoan(session)
    service.setApiKey(tk, "groq", "synthesis", "sk-nguoi-dung")
    # System key exists too, but the user key still takes precedence (R22.4).
    monkeypatch.setenv(_systemKeyEnvName("groq"), "sk-he-thong")

    assert service.resolveKey(tk, "groq", "synthesis") == "sk-nguoi-dung"


def test_resolve_key_du_phong_khoa_he_thong(service, session, monkeypatch):
    tk = _tao_tai_khoan(session)
    monkeypatch.setenv(_systemKeyEnvName("groq"), "sk-he-thong")

    # No user key → use the system key (R22.7).
    assert service.resolveKey(tk, "groq", "synthesis") == "sk-he-thong"


def test_resolve_key_thieu_ca_hai_bao_loi_ro_rang(service, session, monkeypatch):
    tk = _tao_tai_khoan(session)
    monkeypatch.delenv(_systemKeyEnvName("groq"), raising=False)

    with pytest.raises(ValidationError) as exc:
        service.resolveKey(tk, "groq", "synthesis")
    # A clear message requesting key configuration, without exposing key details (R22.6).
    assert "khoa api" in str(exc.value).lower()
