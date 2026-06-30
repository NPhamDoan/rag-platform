"""Application-wide business-domain exception tree (R14.3).

Every business error inherits from `AppError` and carries `httpStatus` + `errorCode`
so the global error handler (`api/middleware/error_handler.py`) can map it to a
structured HTTP response, attach a `correlationId`, and log it. Placed in
`app/errors.py` so every service/pipeline can reuse it (avoiding a circular
dependency on the api layer).

Status code mapping (per design.md, "Error classification and status codes"):

| Error type          | HTTP    |
|---------------------|---------|
| ValidationError     | 400     |
| AuthenticationError | 401     |
| AuthorizationError  | 403     |
| NotFoundError       | 404     |
| ConflictError       | 409     |
| QuotaExceededError  | 409/429 |
| RateLimitError      | 429     |
| LockedError         | 423/429 |
| InternalError       | 500     |

`QuotaExceededError`/`LockedError` have two valid codes depending on context: the
default value is chosen per the design (409 for quota = resource conflict, 423 for
a temporary lock), and can be overridden via the `httpStatus` argument at
construction time if the caller wants to use 429.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base business error. Subclasses set default `httpStatus` + `errorCode`.

    - `message`: a safe message returned to the client (does not leak sensitive
      details).
    - `httpStatus`: overrides the class default HTTP code (e.g. quota → 429).
    - `details`: optional context data (sensitive fields are masked when logging
      and when returned to the client).
    """

    httpStatus: int = 500
    errorCode: str = "InternalError"

    def __init__(
        self,
        message: str | None = None,
        *,
        httpStatus: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.__name__
        if httpStatus is not None:
            self.httpStatus = httpStatus
        self.details = details
        super().__init__(self.message)


class ValidationError(AppError):
    """Invalid format/length (email, password, name, question, out-of-range config)."""

    httpStatus = 400
    errorCode = "ValidationError"


class AuthenticationError(AppError):
    """Missing/invalid/expired/revoked token; account disabled."""

    httpStatus = 401
    errorCode = "AuthenticationError"


class AuthorizationError(AppError):
    """Insufficient permission (not owner/not shared; writing while CHI_DOC only...)."""

    httpStatus = 403
    errorCode = "AuthorizationError"


class NotFoundError(AppError):
    """Target workspace/document/account/history item does not exist."""

    httpStatus = 404
    errorCode = "NotFoundError"


class ConflictError(AppError):
    """Duplicate email/tenDangNhap during registration."""

    httpStatus = 409
    errorCode = "ConflictError"


class QuotaExceededError(AppError):
    """Resource quota exceeded (default 409; may be overridden to 429)."""

    httpStatus = 409
    errorCode = "QuotaExceededError"


class RateLimitError(AppError):
    """Query frequency limit exceeded."""

    httpStatus = 429
    errorCode = "RateLimitError"


class LockedError(AppError):
    """Account is temporarily locked out from login (default 423; may be overridden to 429)."""

    httpStatus = 423
    errorCode = "LockedError"


class InternalError(AppError):
    """Unexpected error; includes correlationId, logs the stack."""

    httpStatus = 500
    errorCode = "InternalError"


class InitializationError(AppError):
    """Service initialization error (fail-fast).

    Raised when loading the provider/chunker registry at startup detects: a
    configured provider/strategy that does not exist in the registry, or a required
    role (synthesis / verification / embedding) that is not configured. In that case
    the service does NOT start (R13.3, R13.5, R21.3). The message always names the
    invalid provider/role.
    """

    httpStatus = 500
    errorCode = "InitializationError"
