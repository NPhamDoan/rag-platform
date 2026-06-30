"""Centralized configuration for the Multi-User RAG Platform.

`Settings` (pydantic-settings) gathers every default value and valid range for the
platform. The valid ranges are declared as module-level constants so the
ConfigService (task 12.x) can reuse them when validating runtime configuration.

Per the project convention: `env_file` is anchored ABSOLUTELY to `backend/.env`
based on the location of this file (not the cwd), so running from `backend/` or
from the project root both read the same `.env`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Anchor the .env path absolutely to backend/.env -----------------------
# config.py lives at backend/app/config.py => parent.parent = backend/
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ENV_FILE = _BACKEND_DIR / ".env"


# --- Valid ranges (used to validate runtime config in ConfigService) -------
# Byte unit helpers
MB = 1024**2
GB = 1024**3

# R23: configurable operational limits (admin)
SESSION_TTL_MIN = 5          # minutes
SESSION_TTL_MAX = 24 * 60    # minutes (1 day)
LLM_TIMEOUT_MIN = 5          # seconds
LLM_TIMEOUT_MAX = 300        # seconds
MAX_FILE_SIZE_MB_MIN = 1
MAX_FILE_SIZE_MB_MAX = 1024

# R6/R19: retrieval thresholds and k
NGUONG_MIN = 0.0
NGUONG_MAX = 1.0
RETRIEVAL_K_MIN = 1
RETRIEVAL_K_MAX = 100

# R12: resource quotas
QUOTA_SO_KHONG_GIAN_MIN = 1
QUOTA_SO_KHONG_GIAN_MAX = 1_000
QUOTA_DUNG_LUONG_MIN = 1 * MB
QUOTA_DUNG_LUONG_MAX = 1_024 * GB
QUOTA_SO_TAI_LIEU_MIN = 1
QUOTA_SO_TAI_LIEU_MAX = 100_000


class Settings(BaseSettings):
    """Platform configuration read from the environment / backend/.env."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Environment / logging --------------------------------------------
    # R14.7: prod=INFO, dev=DEBUG (logging_config reads this value in task 1.2)
    environment: str = Field(default="development")

    # --- CORS -------------------------------------------------------------
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # --- Session / authentication (R2.1, R23) -----------------------------
    session_ttl_minutes: int = Field(default=60, ge=SESSION_TTL_MIN, le=SESSION_TTL_MAX)
    login_max_fails: int = Field(default=5, ge=1)
    login_lock_minutes: int = Field(default=15, ge=1)
    # R25.2-4: password reset link lifetime (minutes) — short-lived by design
    password_reset_ttl_minutes: int = Field(default=30, ge=1)
    # HMAC key used to sign session tokens
    secret_key: str = Field(default="dev-secret-change-me")

    # --- User API key encryption (R22.2 - Fernet) -------------------------
    secret_key_encrypt: str = Field(default="")

    # --- LLM (R8, R23) ----------------------------------------------------
    llm_timeout_seconds: int = Field(default=30, ge=LLM_TIMEOUT_MIN, le=LLM_TIMEOUT_MAX)
    # R13: provider config per role; empty normalize = reuse verify (R13.4)
    llm_primary_provider: str = Field(default="groq")
    llm_verify_provider: str = Field(default="gemini")
    llm_normalize_provider: str = Field(default="")
    embedding_provider: str = Field(default="huggingface")

    # --- Documents (R5.3, R23) --------------------------------------------
    max_file_size_mb: int = Field(
        default=50, ge=MAX_FILE_SIZE_MB_MIN, le=MAX_FILE_SIZE_MB_MAX
    )

    # --- Default retrieval config (R6.4-6, R19) ---------------------------
    nguong_khong_tim_thay: float = Field(default=0.3, ge=NGUONG_MIN, le=NGUONG_MAX)
    nguong_du_lien_quan: float = Field(default=0.5, ge=NGUONG_MIN, le=NGUONG_MAX)
    retrieval_k: int = Field(default=8, ge=RETRIEVAL_K_MIN, le=RETRIEVAL_K_MAX)
    trong_so_vector: float = Field(default=0.5, ge=0.0, le=1.0)
    trong_so_bm25: float = Field(default=0.5, ge=0.0, le=1.0)

    # --- Default resource quotas (R12) ------------------------------------
    quota_so_khong_gian: int = Field(
        default=50, ge=QUOTA_SO_KHONG_GIAN_MIN, le=QUOTA_SO_KHONG_GIAN_MAX
    )
    quota_dung_luong: int = Field(
        default=5 * GB, ge=QUOTA_DUNG_LUONG_MIN, le=QUOTA_DUNG_LUONG_MAX
    )
    quota_so_tai_lieu: int = Field(
        default=1_000, ge=QUOTA_SO_TAI_LIEU_MIN, le=QUOTA_SO_TAI_LIEU_MAX
    )
    # R24: per-TaiKhoan limit on query frequency per minute
    quota_tan_suat_truy_van: int = Field(default=60, ge=1)

    # --- Database ---------------------------------------------------------
    # SQLite by default; switch to Postgres via the connection string (R14.1)
    database_url: str = Field(
        default=f"sqlite:///{(_BACKEND_DIR / 'data' / 'app.db').as_posix()}"
    )

    # --- Vector store (ChromaDB) ------------------------------------------
    # Persistent ChromaDB storage directory (1 collection / KhongGianTaiLieu, R21).
    chroma_persist_path: str = Field(
        default=str(_BACKEND_DIR / "data" / "chroma")
    )

    @property
    def max_file_size_bytes(self) -> int:
        """File size limit in bytes."""
        return self.max_file_size_mb * MB

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the Settings as a singleton (used as a FastAPI dependency)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
