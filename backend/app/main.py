"""FastAPI entry point for the Multi-User RAG Platform.

Task 1.1 (scaffold): create the FastAPI app + lifespan/DI skeleton + CORS. The
business components (centralized logging, middleware, routers, registry) are wired
in later tasks (1.2+). No business logic lives here.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.middleware.correlation import CorrelationIdMiddleware
from app.api.middleware.error_handler import register_error_handlers
from app.api.routes import (
    account,
    admin,
    auth,
    documents,
    history,
    query,
    workspaces,
)
from app.chunking.registry import discover_chunkers
from app.config import Settings, get_settings
from app.db.database import init_db
from app.logging_config import setup_logging
from app.providers.registry import discover_providers, validate_provider_config

# Shared logger; configured centrally via setup_logging() in the lifespan (R14.1).
logger = logging.getLogger(__name__)


# --- DI container skeleton --------------------------------------------------
class AppContext:
    """Skeleton holding app-wide shared dependencies (DI container).

    The real services/pipelines (Auth_Service, WorkspaceService, Query_Pipeline,
    Vector_Store, registry...) will be attached here in later tasks.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.services: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize and clean up resources over the application lifecycle."""
    settings = get_settings()
    setup_logging(settings)
    app.state.context = AppContext(settings)
    logger.info("Khoi tao ung dung (environment=%s)", settings.environment)
    init_db()
    # Load the self-registering registry (auto-discover) + fail-fast on provider
    # config (R13.2/.3/.5, R17.2, R21.2/.3): discover every *_provider.py/*_embedding.py
    # and *_chunker.py; if a configured provider does not exist or a required role is
    # missing → InitializationError stops initialization and the service does NOT start.
    discover_providers()
    discover_chunkers()
    validate_provider_config(settings)
    try:
        yield
    finally:
        logger.info("Dung ung dung, don dep tai nguyen")


def create_app() -> FastAPI:
    """Factory that creates the FastAPI app configured with CORS + lifespan."""
    settings = get_settings()

    app = FastAPI(
        title="Multi-User RAG Platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # CorrelationId middleware: placed outermost so every log within a request has a
    # cid (added after CORS => runs earlier in the middleware chain) — R14.2/R14.6.
    app.add_middleware(CorrelationIdMiddleware)

    # Global error handler: classifies domain errors → HTTP code + correlationId (R14.3).
    register_error_handlers(app)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Service health check."""
        return {"status": "ok"}

    # Register all REST routers (each router already carries its own /api... prefix):
    # auth + account self-deletion, workspaces/sharing/config, documents, queries,
    # history, admin, account API keys (R2.6, R24.1) — tasks 13.1-13.5.
    for module in (auth, workspaces, documents, query, history, admin, account):
        app.include_router(module.router)

    # Serve the frontend build (single-service): mount AFTER the routers so it does
    # NOT shadow `/api/*`. No dist directory (e.g. not built yet) → skip, do NOT crash startup.
    _mount_frontend(app)

    return app


# --- Serve the frontend dist (single-service) ------------------------------
# Project root = backend/app/main.py -> parents[2]; frontend build defaults to frontend/dist.
_FRONTEND_DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class _SpaStaticFiles(StaticFiles):
    """StaticFiles with SPA fallback: a path that doesn't match a file → return index.html.

    Lets the SPA's client-side routing work (e.g. refreshing at `/login` returns
    index.html instead of 404). `/api/*` requests are already handled by the routers
    earlier, so they never reach here.
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


def _mount_frontend(app: FastAPI) -> None:
    """Mount the frontend dist at `/` if built; missing directory → skip (R2.6)."""
    if _FRONTEND_DIST_DIR.is_dir():
        app.mount(
            "/",
            _SpaStaticFiles(directory=str(_FRONTEND_DIST_DIR), html=True),
            name="frontend",
        )
        logger.info("Phuc vu frontend dist tai /: %s", _FRONTEND_DIST_DIR)
    else:
        logger.info(
            "Bo qua phuc vu frontend: khong tim thay dist (%s)", _FRONTEND_DIST_DIR
        )


app = create_app()
