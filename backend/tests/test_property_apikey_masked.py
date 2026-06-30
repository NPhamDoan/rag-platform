"""Property test for ApiKeyService.getMaskedKeys (task 9.3, R22.3).

Property 53: API keys are always masked when exposed externally.

For an account with an arbitrary set of API keys (varied provider/vaiTro/key strings),
`getMaskedKeys` returns the entries in masked form: `khoaChe` is NEVER equal to or
contains the full plaintext key string (at most the last 4 characters may be revealed).
At the same time the count and identifiers (providerTen, vaiTro) match exactly what
was set.

API keys are mocked/randomly generated; NO LLM/embedding call. Each example uses its
own in-memory SQLite session, with the account created directly.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import TaiKhoan
from app.services.api_key_service import ApiKeyService

# Character set for provider/vaiTro names: letters/digits → avoid control characters that add noise.
_TEN_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
# Character set for key strings: excludes '*' to avoid clashing with the "****" mask
# placeholder (a coincidence unrelated to leaking the secret).
_KHOA_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.+/= "
)

_ten = st.text(alphabet=_TEN_ALPHABET, min_size=1, max_size=50)
_khoa = st.text(alphabet=_KHOA_ALPHABET, min_size=1, max_size=80)

# A few keys per account: list of (provider, vaiTro, khoa).
_danh_sach_khoa = st.lists(
    st.tuples(_ten, _ten, _khoa),
    min_size=1,
    max_size=8,
)


# Feature: multi-user-rag-platform, Property 53: API keys are always masked when
# exposed externally (getMaskedKeys returns masked form, never leaks plaintext) — R22.3.
@settings(max_examples=100, deadline=None)
@given(danhSachKhoa=_danh_sach_khoa)
def test_khoa_luon_duoc_che_khi_xuat(danhSachKhoa):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        tk = TaiKhoan(email="a@x.com", tenDangNhap="a", matKhauHash="h")
        db.add(tk)
        db.commit()

        service = ApiKeyService(db)

        # setApiKey upserts by (taiKhoanId, providerTen, vaiTro): a later config
        # overwrites an earlier one for the same identifier pair → "last wins".
        kyVong: dict[tuple[str, str], str] = {}
        for providerTen, vaiTro, khoa in danhSachKhoa:
            service.setApiKey(tk, providerTen, vaiTro, khoa)
            kyVong[(providerTen, vaiTro)] = khoa

        masked = service.getMaskedKeys(tk)

        # Count + identifiers match exactly the set of (provider, vaiTro) that was set.
        assert len(masked) == len(kyVong)
        assert {(m.providerTen, m.vaiTro) for m in masked} == set(kyVong.keys())

        for m in masked:
            plaintext = kyVong[(m.providerTen, m.vaiTro)]

            # 1) Never expose the verbatim plaintext.
            assert m.khoaChe != plaintext
            # 2) The full plaintext is not a substring of khoaChe.
            assert plaintext not in m.khoaChe
            # 3) At most the last 4 characters may be revealed. Check by STRUCTURE
            #    (not substring, to avoid a coincidental match between the masked part
            #    and the revealed part): khoaChe = "****" + revealed part, where the
            #    revealed part is at most 4 characters and is exactly a SUFFIX of plaintext.
            assert m.khoaChe.startswith("****")
            phanLo = m.khoaChe[4:]
            assert len(phanLo) <= 4
            assert plaintext.endswith(phanLo)
    finally:
        db.close()
        engine.dispose()
