"""Per-account query rate limiting (task 6.4, R24.1-24.2).

Provides `RateLimiter` (in-memory state, one-minute sliding window) and a FastAPI
dependency `rate_limit_query` attached to the query route. When a TaiKhoan exceeds
its own rate quota within the window → raises `RateLimitError` (→ 429) BEFORE any
processing/LLM runs (R24.2).

The per-account quota is taken from `HanMuc.tanSuatTruyVanMoiPhut`; when there is no
HanMuc record → use the configured default `settings.quota_tan_suat_truy_van` (R24.1).

Counting mechanism (sliding window):
- Each account keeps a queue (`deque`) of timestamps for the queries accepted within
  the most recent `windowSeconds` (default 60s).
- On each check: drop timestamps older than `now - windowSeconds`, count the
  remaining ones; if >= the limit → reject (does NOT record this attempt). Otherwise
  → record `now`.
- The window "self-resets" as old timestamps expire over time.

State is kept IN-PROCESS (per-process): sufficient for a single-service deployment.
For multiple instances/shared state, it must move to an external store (e.g. Redis) —
this note is intentional, not an oversight.

The time function (`timeFunc`) can be injected for deterministic testing (defaults to
`time.monotonic`). Logs through the centralized logger; does NOT swallow errors silently.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.config import get_settings
from app.db.models import HanMuc, TaiKhoan
from app.errors import RateLimitError

logger = logging.getLogger(__name__)

# Default rate-counting window: 1 minute (R24 — "queries per minute").
_DEFAULT_WINDOW_SECONDS = 60
_VUOT_GIOI_HAN_MESSAGE = (
    "Vuot gioi han tan suat truy van. Vui long thu lai sau giay lat."
)


class RateLimiter:
    """In-memory sliding-window rate counter, thread-safe.

    Per-process state: each `taiKhoanId` has a `deque` of timestamps for the attempts
    accepted within the most recent `windowSeconds`. `timeFunc` can be injected for
    testing.
    """

    def __init__(
        self,
        *,
        windowSeconds: float = _DEFAULT_WINDOW_SECONDS,
        timeFunc: Callable[[], float] = time.monotonic,
    ) -> None:
        self._windowSeconds = windowSeconds
        self._timeFunc = timeFunc
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def checkAndRecord(self, taiKhoanId: str, gioiHan: int) -> None:
        """Check + record a query attempt for `taiKhoanId` (R24.2).

        Drops timestamps older than the window, counts the remaining ones; if >=
        `gioiHan` → raise `RateLimitError` (does NOT record the rejected attempt).
        Otherwise → record the current timestamp and allow.
        """
        now = self._timeFunc()
        bienDuoi = now - self._windowSeconds
        with self._lock:
            hits = self._hits.setdefault(taiKhoanId, deque())
            # Sliding window: drop expired attempts from the front of the queue.
            while hits and hits[0] <= bienDuoi:
                hits.popleft()

            if len(hits) >= gioiHan:
                logger.info(
                    "Tu choi truy van: vuot gioi han tan suat (taiKhoanId=%s, "
                    "soLuotTrongCuaSo=%d, gioiHan=%d).",
                    taiKhoanId,
                    len(hits),
                    gioiHan,
                )
                raise RateLimitError(_VUOT_GIOI_HAN_MESSAGE)

            hits.append(now)

    def reset(self) -> None:
        """Clear all counter state (used on re-initialization / testing)."""
        with self._lock:
            self._hits.clear()


# Shared rate limiter for the dependency (state lives for the process lifetime).
_rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Return the process-wide shared `RateLimiter`."""
    return _rate_limiter


def rate_limit_query(
    taiKhoan: TaiKhoan = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaiKhoan:
    """Dependency: apply per-account query rate limiting (R24.1-24.2).

    Depends on `get_current_user` (requires a valid token). Takes the limit from the
    account's `HanMuc.tanSuatTruyVanMoiPhut`; missing HanMuc → the configured default
    `quota_tan_suat_truy_van`. Exceeding the limit → `RateLimitError` (429) BEFORE the
    route runs any processing/LLM. Sufficient → return the `TaiKhoan`.
    """
    hanMuc = db.get(HanMuc, taiKhoan.id)
    gioiHan = (
        hanMuc.tanSuatTruyVanMoiPhut
        if hanMuc is not None
        else get_settings().quota_tan_suat_truy_van
    )
    _rate_limiter.checkAndRecord(taiKhoan.id, gioiHan)
    return taiKhoan
