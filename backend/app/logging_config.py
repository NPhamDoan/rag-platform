"""Centralized logging configuration for the Multi-User RAG Platform.

R14: a single source of logging configuration (`setup_logging`) shared by every
component — console + file, with a format that includes timestamp/level/source/
message and a correlationId for tracing (R14.1, R14.5, R14.6). Level depends on the
environment: prod=INFO, dev=DEBUG (R14.7).

Every module logs via `logging.getLogger(__name__)`; modules do NOT configure their
own handlers and do NOT use scattered print statements. Sensitive fields are masked
using `logging_redaction`.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import Settings, get_settings

# --- Anchor the log directory path absolutely to backend/logs/ -------------
# logging_config.py lives at backend/app/logging_config.py => parent.parent = backend/
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_LOG_DIR = _BACKEND_DIR / "logs"
_LOG_FILE = _LOG_DIR / "app.log"

# Root logger shared by the whole app. Every child logger (app.*) propagates here.
ROOT_LOGGER_NAME = "app"

# correlationId value when none is set in request scope (e.g. logs during startup).
_NO_CORRELATION_ID = "-"

# ContextVar holding the current request's correlationId; the middleware
# (correlation.py) sets the value at the start of each request, and
# CorrelationIdFilter reads it back when formatting logs (R14.6).
correlation_id_var: ContextVar[str] = ContextVar(
    "correlation_id", default=_NO_CORRELATION_ID
)

# Log format: timestamp | level | source (module name) | correlationId | message.
_LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | cid=%(correlationId)s | %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Guard flag to avoid configuring duplicate handlers if setup_logging is called more than once.
_configured = False


class CorrelationIdFilter(logging.Filter):
    """Attach the current request's correlationId to every log record (R14.6)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlationId = correlation_id_var.get()
        return True


def get_correlation_id() -> str:
    """Return the correlationId of the current scope (or '-' if none set)."""
    return correlation_id_var.get()


def set_correlation_id(correlationId: str):
    """Set the correlationId for the current scope; return a token to reset after the request."""
    return correlation_id_var.set(correlationId)


def reset_correlation_id(token) -> None:
    """Restore the correlationId to its previous value (after the request ends)."""
    correlation_id_var.reset(token)


def _resolve_level(settings: Settings) -> int:
    """Pick the log level by environment: prod=INFO, dev=DEBUG (R14.7)."""
    return logging.INFO if settings.is_production else logging.DEBUG


def setup_logging(settings: Settings | None = None) -> None:
    """Configure centralized logging (console + file) — single config source (R14.1).

    Called once in the lifespan when the app starts. Safe to call again (idempotent):
    handlers are only configured once to avoid duplicate log entries.
    """
    global _configured
    if _configured:
        return

    settings = settings or get_settings()
    level = _resolve_level(settings)

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)
    correlation_filter = CorrelationIdFilter()

    # Console handler — force UTF-8 so Vietnamese displays correctly on Windows.
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(correlation_filter)

    # Rotating file handler — anchored absolutely to backend/logs/app.log, force UTF-8.
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        filename=str(_LOG_FILE),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(correlation_filter)

    root_logger = logging.getLogger(ROOT_LOGGER_NAME)
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    # Do not propagate to the default root logging to avoid duplicate logs.
    root_logger.propagate = False

    _configured = True
    root_logger.info(
        "Khoi tao logging tap trung (environment=%s, level=%s)",
        settings.environment,
        logging.getLevelName(level),
    )
