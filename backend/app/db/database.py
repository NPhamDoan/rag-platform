"""SQLAlchemy engine/session + init_db for the Multi-User RAG Platform.

Task 1.3: the single source of DB configuration — build the engine from
`Settings.database_url` (SQLite by default, switch to Postgres via the connection
string WITHOUT changing the models — R14.1), one shared session factory, and
`init_db()` to create the schema.

Conventions:
- SQLAlchemy 2.0 style: `Base` inherits `DeclarativeBase`; ORM models (task 2.1)
  are declared on this `Base` so `init_db()` creates the tables via
  `Base.metadata.create_all`.
- Logging via the centralized logger (`logging.getLogger(__name__)` => child of the
  root logger "app"); logs DB initialization events (R14.1).
- No business logic lives here.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse, unquote
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for every ORM model (task 2.1 registers on top of this)."""


def _is_sqlite(database_url: str) -> bool:
    """Check whether the connection string uses SQLite."""
    return database_url.startswith("sqlite")


def _ensure_sqlite_dir(database_url: str) -> None:
    """Create the directory holding the SQLite file if it does not exist yet.

    SQLite requires the parent directory to exist before opening the file. A
    file-based connection string looks like `sqlite:///<path>`; the in-memory DB
    (`sqlite://` empty) is skipped.
    """
    parsed = urlparse(database_url)
    # `sqlite:///abs/path` => path = "/abs/path"; `sqlite://` (in-memory) => empty.
    db_path = unquote(parsed.path).lstrip("/")
    if not db_path:
        return
    parent = Path(db_path).parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
        logger.debug("Tao thu muc luu DB SQLite: %s", parent)


def _build_engine(settings: Settings) -> Engine:
    """Build the SQLAlchemy engine from the configured connection string.

    SQLite needs `check_same_thread=False` to share a connection across FastAPI
    threads. Postgres/other databases use the default arguments — switching DB only
    requires changing `database_url`, not the models (R14.1).
    """
    database_url = settings.database_url
    connect_args: dict[str, object] = {}
    if _is_sqlite(database_url):
        _ensure_sqlite_dir(database_url)
        connect_args["check_same_thread"] = False
    return create_engine(database_url, connect_args=connect_args, future=True)


# Engine + session factory shared across the whole app (a single source).
engine: Engine = _build_engine(get_settings())

# Session factory: each unit of work creates a Session from here.
SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


def init_db() -> None:
    """Create the DB schema from `Base.metadata` (idempotent — only creates missing tables).

    Import `app.db.models` (task 2.1) so every ORM model registers into
    `Base.metadata` before `create_all`. The import is placed inside the function
    (lazy) to avoid a circular dependency and so the app still builds when models
    don't exist yet.
    """
    logger.info("Bat dau khoi tao schema CSDL (url=%s)", engine.url)
    try:
        # Lazy import: ensure every model registers in metadata before creating tables.
        import app.db.models  # noqa: F401
    except ModuleNotFoundError:
        # models.py is added in task 2.1; if absent => only create already-registered tables.
        logger.warning("Chua co app.db.models; tao bang tu metadata hien co")

    Base.metadata.create_all(bind=engine)
    logger.info(
        "Khoi tao schema CSDL hoan tat (so bang=%d)", len(Base.metadata.tables)
    )


def get_db():
    """FastAPI dependency: provide a Session and ensure it closes after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
