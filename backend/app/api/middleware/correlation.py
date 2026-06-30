"""Middleware that generates/attaches a correlationId for every HTTP request.

R14.2 / R14.6: every HTTP request is assigned a correlationId (taken from the
`X-Correlation-ID` header if present, otherwise newly generated), placed into the
log scope so that every log entry within the request carries a trace identifier, and
an INFO log is written containing the method + path + correlationId.
"""

from __future__ import annotations

import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.logging_config import reset_correlation_id, set_correlation_id

logger = logging.getLogger(__name__)

# Name of the header carrying the correlationId back and forth between client and server.
CORRELATION_HEADER = "X-Correlation-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach a correlationId to the log scope + log an INFO method/path per request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        correlationId = request.headers.get(CORRELATION_HEADER) or uuid.uuid4().hex
        token = set_correlation_id(correlationId)
        try:
            logger.info("%s %s", request.method, request.url.path)
            response = await call_next(request)
            response.headers[CORRELATION_HEADER] = correlationId
            return response
        finally:
            reset_correlation_id(token)
