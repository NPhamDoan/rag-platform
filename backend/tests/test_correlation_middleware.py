"""Unit tests for the correlationId middleware + health endpoint (R14.2, R14.6)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.middleware.correlation import CORRELATION_HEADER
from app.main import create_app


def test_health_ok():
    with TestClient(create_app()) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_response_has_generated_correlation_id():
    # R14.6: a request without a correlationId => the system generates one and attaches it to the response.
    with TestClient(create_app()) as client:
        resp = client.get("/api/health")
    assert resp.headers.get(CORRELATION_HEADER)


def test_response_echoes_incoming_correlation_id():
    with TestClient(create_app()) as client:
        resp = client.get("/api/health", headers={CORRELATION_HEADER: "cid-fixed"})
    assert resp.headers.get(CORRELATION_HEADER) == "cid-fixed"
