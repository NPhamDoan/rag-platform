"""Unit tests for the global error handler + domain error classification (R14.3).

Checks that every `AppError` maps to the correct HTTP code, the body includes a
`correlationId`, and the special branches: an invalid request DTO → 400, an
unexpected error → 500.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.middleware.correlation import CORRELATION_HEADER
from app.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InternalError,
    LockedError,
    NotFoundError,
    QuotaExceededError,
    RateLimitError,
    ValidationError,
)
from app.main import create_app


# --- Domain error class → default HTTP code mapping (per design.md) --------
DOMAIN_ERROR_CASES = [
    (ValidationError, 400, "ValidationError"),
    (AuthenticationError, 401, "AuthenticationError"),
    (AuthorizationError, 403, "AuthorizationError"),
    (NotFoundError, 404, "NotFoundError"),
    (ConflictError, 409, "ConflictError"),
    (QuotaExceededError, 409, "QuotaExceededError"),
    (RateLimitError, 429, "RateLimitError"),
    (LockedError, 423, "LockedError"),
    (InternalError, 500, "InternalError"),
]


class _EchoInput(BaseModel):
    """Minimal DTO to trigger a request validation error."""

    soLuong: int


def _build_test_app() -> FastAPI:
    """Create a real app (via create_app) + extra routes that raise errors to test the handler."""
    app = create_app()

    @app.get("/api/_test/domain/{ten}")
    async def raise_domain(ten: str):  # pragma: no cover - test route
        mapping = {code: cls for cls, _, code in DOMAIN_ERROR_CASES}
        raise mapping[ten]("loi mien thu nghiem")

    @app.get("/api/_test/quota-429")
    async def raise_quota_override():  # pragma: no cover - test route
        # QuotaExceededError may override the HTTP code to 429 depending on context.
        raise QuotaExceededError("vuot tan suat", httpStatus=429)

    @app.get("/api/_test/boom")
    async def raise_unexpected():  # pragma: no cover - test route
        raise RuntimeError("loi khong luong truoc")

    @app.post("/api/_test/echo")
    async def echo(payload: _EchoInput):  # pragma: no cover - test route
        return {"soLuong": payload.soLuong}

    return app


@pytest.fixture()
def client() -> TestClient:
    # raise_server_exceptions=False so the 500 handler returns a response instead of re-raising.
    return TestClient(_build_test_app(), raise_server_exceptions=False)


@pytest.mark.parametrize("exc_cls,httpStatus,errorCode", DOMAIN_ERROR_CASES)
def test_domain_error_maps_to_http_code(client, exc_cls, httpStatus, errorCode):
    resp = client.get(f"/api/_test/domain/{errorCode}")
    assert resp.status_code == httpStatus
    body = resp.json()["error"]
    assert body["code"] == errorCode
    assert body["message"] == "loi mien thu nghiem"
    # The body always includes a correlationId for tracing.
    assert body["correlationId"]


def test_correlation_id_in_body_matches_request(client):
    resp = client.get(
        "/api/_test/domain/NotFoundError",
        headers={CORRELATION_HEADER: "cid-test-404"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["correlationId"] == "cid-test-404"


def test_quota_error_can_override_status_to_429(client):
    resp = client.get("/api/_test/quota-429")
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "QuotaExceededError"


def test_request_validation_error_maps_to_400(client):
    resp = client.post("/api/_test/echo", json={"soLuong": "khong-phai-so"})
    assert resp.status_code == 400
    body = resp.json()["error"]
    assert body["code"] == "ValidationError"
    assert body["correlationId"]


def test_unexpected_error_maps_to_500_internal(client):
    resp = client.get("/api/_test/boom")
    assert resp.status_code == 500
    body = resp.json()["error"]
    assert body["code"] == "InternalError"
    # Does not leak the original error details to the client.
    assert "loi khong luong truoc" not in body["message"]
