"""Property-based test for the centralized log format (R14.5, R14.6).

# Feature: multi-user-rag-platform, Property 57: Moi log entry du truong bat buoc
# (timestamp, level, ten module/nguon, message) va co dinh danh truy vet
# (correlationId) — gan nhat quan voi gia tri dat trong pham vi, hoac '-' khi chua dat.

Verified by emitting real logs through the system's actual `CorrelationIdFilter` +
`_LOG_FORMAT`, but on a SEPARATE logger (propagate=False) so as NOT to touch the shared
root `app` logger.
"""

from __future__ import annotations

import io
import logging
import re

from hypothesis import given, settings
from hypothesis import strategies as st

from app.logging_config import (
    CorrelationIdFilter,
    _DATE_FORMAT,
    _LOG_FORMAT,
    _NO_CORRELATION_ID,
    reset_correlation_id,
    set_correlation_id,
)

# Dedicated logger for the test, does NOT propagate to the root `app` logger -> fully isolated.
_TEST_LOGGER_NAME = "test_property_log_fields_isolated"

# The four valid log levels of the system (R14.3 / log levels).
_LEVELS = [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR]

# The leading timestamp must match the _DATE_FORMAT format ("%Y-%m-%d %H:%M:%S").
_TIMESTAMP_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| ")

# Safe correlationId: alphanumerics + hyphen (mimics uuid4().hex and the X-Correlation-ID header).
_cid_text = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-",
    min_size=1,
    max_size=64,
)
# Arbitrary message, stripped of control characters for stable single-line substring comparison.
_message_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cc", "Cs"), max_codepoint=0x2FFFF),
    min_size=0,
    max_size=200,
)


def _build_isolated_logger() -> tuple[logging.Logger, io.StringIO]:
    """Build an isolated logger + StringIO capturing output formatted per _LOG_FORMAT."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(stream=buffer)
    handler.setFormatter(logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))
    handler.addFilter(CorrelationIdFilter())

    logger = logging.getLogger(_TEST_LOGGER_NAME)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # do NOT touch the root `app` logger.
    return logger, buffer


# Initialize once; each round truncates the buffer and resets correlationId for isolation.
_LOGGER, _BUFFER = _build_isolated_logger()


@settings(max_examples=40)
@given(message=_message_text, level=st.sampled_from(_LEVELS), correlationId=st.one_of(st.none(), _cid_text))
def test_log_entry_has_required_fields_and_correlation_id(message, level, correlationId):
    # Isolate state between rounds: clear the buffer + set a scoped correlationId.
    _BUFFER.seek(0)
    _BUFFER.truncate(0)

    expected_cid = correlationId if correlationId is not None else _NO_CORRELATION_ID
    token = set_correlation_id(correlationId) if correlationId is not None else None
    try:
        _LOGGER.log(level, "%s", message)
    finally:
        if token is not None:
            reset_correlation_id(token)

    line = _BUFFER.getvalue()

    # Required field 1: a correctly formatted leading timestamp (R14.5).
    assert _TIMESTAMP_PREFIX.match(line), f"thieu/loi timestamp: {line!r}"

    # Required field 2: log level (R14.5).
    assert f" | {logging.getLevelName(level)} | " in line

    # Required field 3: module/source name (R14.5).
    assert f" | {_TEST_LOGGER_NAME} | " in line

    # Trace identifier: exactly the scoped value, or '-' when unset (R14.6).
    assert f" | cid={expected_cid} | " in line

    # Required field 4: the event-describing message at the end of the line (R14.5).
    assert line.rstrip("\n").endswith(message)
