"""Unit tests for RateLimiter + the rate_limit_query dependency (task 6.4, R24.1-24.2).

Coverage:
- Below the limit → every request allowed (R24.1).
- Hitting exactly the limit then exceeding it → RateLimitError (R24.2); the rejected
  request is NOT recorded (does not further fill the window).
- The sliding window resets as time passes (uses an injected clock for determinism).
- Isolation per account (A's quota does not affect B).
- Dependency: reads the limit from HanMuc; no HanMuc → default config; exceeding → 429.

Uses an injected time function (`timeFunc`) so each test is deterministic and does not
depend on the real clock.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.middleware.rate_limit import RateLimiter, rate_limit_query
from app.config import get_settings
from app.db.database import Base
from app.db.models import HanMuc, TaiKhoan
from app.errors import RateLimitError


class _DongHo:
    """Fake clock, manually controlled to test the sliding window deterministically."""

    def __init__(self, batDau: float = 1000.0) -> None:
        self.hienTai = batDau

    def __call__(self) -> float:
        return self.hienTai

    def tien(self, giay: float) -> None:
        self.hienTai += giay


# --- RateLimiter -----------------------------------------------------------
def test_duoi_gioi_han_cho_phep():
    dongHo = _DongHo()
    limiter = RateLimiter(windowSeconds=60, timeFunc=dongHo)
    # Limit 3 → the first 3 requests are allowed (no error raised).
    for _ in range(3):
        limiter.checkAndRecord("tk-A", 3)


def test_vuot_gioi_han_bi_tu_choi():
    dongHo = _DongHo()
    limiter = RateLimiter(windowSeconds=60, timeFunc=dongHo)
    for _ in range(3):
        limiter.checkAndRecord("tk-A", 3)
    # The 4th request within the same window → rejected (R24.2).
    with pytest.raises(RateLimitError):
        limiter.checkAndRecord("tk-A", 3)


def test_luot_bi_tu_choi_khong_duoc_ghi_nhan():
    dongHo = _DongHo()
    limiter = RateLimiter(windowSeconds=60, timeFunc=dongHo)
    limiter.checkAndRecord("tk-A", 1)
    # Over the limit → rejected several times, but no request is recorded.
    for _ in range(3):
        with pytest.raises(RateLimitError):
            limiter.checkAndRecord("tk-A", 1)
    # After the first request expires, a new request is accepted again.
    dongHo.tien(61)
    limiter.checkAndRecord("tk-A", 1)


def test_cua_so_truot_reset_theo_thoi_gian():
    dongHo = _DongHo()
    limiter = RateLimiter(windowSeconds=60, timeFunc=dongHo)
    limiter.checkAndRecord("tk-A", 1)
    with pytest.raises(RateLimitError):
        limiter.checkAndRecord("tk-A", 1)
    # Advance past the window → the old entry expires → allowed again.
    dongHo.tien(60.1)
    limiter.checkAndRecord("tk-A", 1)


def test_moc_chua_het_han_van_bi_chan():
    dongHo = _DongHo()
    limiter = RateLimiter(windowSeconds=60, timeFunc=dongHo)
    limiter.checkAndRecord("tk-A", 1)
    # Not yet a full window → still blocked.
    dongHo.tien(59)
    with pytest.raises(RateLimitError):
        limiter.checkAndRecord("tk-A", 1)


def test_co_lap_theo_tung_tai_khoan():
    dongHo = _DongHo()
    limiter = RateLimiter(windowSeconds=60, timeFunc=dongHo)
    limiter.checkAndRecord("tk-A", 1)
    # A is full, but B still has its own quota.
    limiter.checkAndRecord("tk-B", 1)
    with pytest.raises(RateLimitError):
        limiter.checkAndRecord("tk-A", 1)
    with pytest.raises(RateLimitError):
        limiter.checkAndRecord("tk-B", 1)


def test_reset_xoa_trang_thai():
    dongHo = _DongHo()
    limiter = RateLimiter(windowSeconds=60, timeFunc=dongHo)
    limiter.checkAndRecord("tk-A", 1)
    limiter.reset()
    # After reset, the quota is full again.
    limiter.checkAndRecord("tk-A", 1)


# --- Dependency rate_limit_query -------------------------------------------
@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _tao_tai_khoan(session, email="a@x.com", ten="a", *, tanSuat=None) -> TaiKhoan:
    tk = TaiKhoan(email=email, tenDangNhap=ten, matKhauHash="h")
    if tanSuat is not None:
        tk.hanMuc = HanMuc(tanSuatTruyVanMoiPhut=tanSuat)
    session.add(tk)
    session.commit()
    return tk


@pytest.fixture(autouse=True)
def _reset_shared_limiter():
    # Isolate the shared rate-limiter state between dependency tests.
    from app.api.middleware.rate_limit import get_rate_limiter

    get_rate_limiter().reset()
    yield
    get_rate_limiter().reset()


def test_dependency_lay_gioi_han_tu_han_muc(session):
    tk = _tao_tai_khoan(session, tanSuat=2)
    # The first 2 requests are allowed, the 3rd → 429.
    rate_limit_query(taiKhoan=tk, db=session)
    rate_limit_query(taiKhoan=tk, db=session)
    with pytest.raises(RateLimitError):
        rate_limit_query(taiKhoan=tk, db=session)


def test_dependency_thieu_han_muc_dung_mac_dinh_cau_hinh(session):
    tk = _tao_tai_khoan(session, tanSuat=None)
    gioiHanMacDinh = get_settings().quota_tan_suat_truy_van
    # Consume the entire default quota → the next request gets a 429.
    for _ in range(gioiHanMacDinh):
        rate_limit_query(taiKhoan=tk, db=session)
    with pytest.raises(RateLimitError):
        rate_limit_query(taiKhoan=tk, db=session)


def test_dependency_co_lap_giua_tai_khoan(session):
    tkA = _tao_tai_khoan(session, "a@x.com", "a", tanSuat=1)
    tkB = _tao_tai_khoan(session, "b@x.com", "b", tanSuat=1)
    rate_limit_query(taiKhoan=tkA, db=session)
    # A is full but B is still allowed.
    rate_limit_query(taiKhoan=tkB, db=session)
    with pytest.raises(RateLimitError):
        rate_limit_query(taiKhoan=tkA, db=session)
