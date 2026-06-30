"""Unit tests for centralized logging + correlationId (R14.1, R14.5, R14.6, R14.7)."""

from __future__ import annotations

import logging

from app.config import Settings
from app.logging_config import (
    CorrelationIdFilter,
    _LOG_FORMAT,
    _resolve_level,
    get_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)


def test_log_format_includes_required_fields():
    # R14.5: timestamp + level + source + message; R14.6: correlationId.
    for field in ("asctime", "levelname", "name", "message", "correlationId"):
        assert field in _LOG_FORMAT


def test_level_by_environment_prod_info_dev_debug():
    # R14.7: prod=INFO, dev=DEBUG.
    assert _resolve_level(Settings(environment="production")) == logging.INFO
    assert _resolve_level(Settings(environment="development")) == logging.DEBUG


def test_correlation_id_set_and_reset():
    token = set_correlation_id("cid-123")
    try:
        assert get_correlation_id() == "cid-123"
    finally:
        reset_correlation_id(token)
    # After reset it returns to the default value.
    assert get_correlation_id() == "-"


def test_correlation_filter_injects_field():
    token = set_correlation_id("cid-xyz")
    try:
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        assert CorrelationIdFilter().filter(record) is True
        assert record.correlationId == "cid-xyz"
    finally:
        reset_correlation_id(token)
