"""Global error handler: classify domain errors → HTTP code + correlationId (R14.3).

Registers the exception handlers on the FastAPI app (call `register_error_handlers(app)`
in `create_app`). Behavior:

- `AppError` (domain error) → returns JSON based on the error's `errorCode` + `httpStatus`.
- `RequestValidationError`/`pydantic.ValidationError` (invalid request DTO) → 400
  with errorCode "ValidationError".
- Any remaining `Exception` (unanticipated) → 500 "InternalError".

Every response includes a `correlationId` (taken from `get_correlation_id()`), and
every branch logs:
- 5xx → ERROR level with stack trace (`exc_info=True`).
- 4xx → WARNING level (client-side error, no stack).

Does NOT swallow errors silently: every branch logs before returning a response;
sensitive fields in `details` are masked with `mask_sensitive` before logging/returning
(R14.4).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from app.errors import AppError, ValidationError
from app.logging_config import get_correlation_id
from app.logging_redaction import mask_sensitive

logger = logging.getLogger(__name__)


def _build_body(
    errorCode: str,
    message: str,
    correlationId: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured JSON error body, always including correlationId for tracing."""
    error: dict[str, Any] = {
        "code": errorCode,
        "message": message,
        "correlationId": correlationId,
    }
    if details:
        error["details"] = mask_sensitive(details)
    return {"error": error}


def _log_error(
    exc: Exception, httpStatus: int, errorCode: str, correlationId: str
) -> None:
    """Log the error: 5xx with stack (ERROR), 4xx without stack (WARNING)."""
    if httpStatus >= 500:
        logger.error(
            "Loi %s (HTTP %s, cid=%s): %s",
            errorCode,
            httpStatus,
            correlationId,
            exc,
            exc_info=True,
        )
    else:
        logger.warning(
            "Loi %s (HTTP %s, cid=%s): %s",
            errorCode,
            httpStatus,
            correlationId,
            exc,
        )


async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """Map a domain error (`AppError`) → an HTTP response per httpStatus/errorCode."""
    correlationId = get_correlation_id()
    _log_error(exc, exc.httpStatus, exc.errorCode, correlationId)
    return JSONResponse(
        status_code=exc.httpStatus,
        content=_build_body(exc.errorCode, exc.message, correlationId, exc.details),
    )


async def handle_request_validation_error(
    request: Request, exc: RequestValidationError | PydanticValidationError
) -> JSONResponse:
    """Invalid request DTO format → 400 ValidationError."""
    correlationId = get_correlation_id()
    _log_error(exc, 400, ValidationError.errorCode, correlationId)
    return JSONResponse(
        status_code=400,
        content=_build_body(
            ValidationError.errorCode,
            "Du lieu yeu cau khong hop le",
            correlationId,
            # jsonable_encoder: convert ctx (e.g. a ValueError from model_validator)
            # to a JSON-safe form before rendering, avoiding serialization errors
            # (same as FastAPI's default handler).
            {"errors": jsonable_encoder(exc.errors())},
        ),
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Unanticipated error → 500 InternalError; log the stack, do NOT leak details."""
    correlationId = get_correlation_id()
    _log_error(exc, 500, "InternalError", correlationId)
    return JSONResponse(
        status_code=500,
        content=_build_body(
            "InternalError",
            "Da xay ra loi khong mong muon",
            correlationId,
        ),
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app (call inside create_app)."""
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(
        RequestValidationError, handle_request_validation_error
    )
    app.add_exception_handler(
        PydanticValidationError, handle_request_validation_error
    )
    app.add_exception_handler(Exception, handle_unexpected_error)
